"""
bot.py — 전체 파이프라인 통합 실행
메일수신 → 첨부파일 분석 → 승인/반려 판단 → 그룹웨어 처리 → 결과 메일 발송 → 로그 저장
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml

from engine.mail_client import MailClient
from engine.llm_reviewer import LLMReviewer
from engine.receipt_analyzer import ReceiptAnalyzer
from engine.approval_engine import ApprovalEngine
from engine.groupware_automation import GroupwareAutomation

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bot")


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_log(record: dict) -> None:
    log_file = LOG_DIR / f"{datetime.now().strftime('%Y%m%d')}_results.json"
    records = []
    if log_file.exists():
        with open(log_file, encoding="utf-8") as f:
            try:
                records = json.load(f)
            except json.JSONDecodeError:
                pass
    records.append(record)
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def run_once(config: dict) -> int:
    """
    한 번 실행 사이클: 새 메일 처리 후 처리 건수 반환.
    """
    mail_client = MailClient(config["mail"])
    llm_reviewer = LLMReviewer(config["llm"])
    ocr_analyzer = ReceiptAnalyzer(
        tesseract_cmd=config.get("tesseract_cmd")
    )
    approval_engine = ApprovalEngine(config)

    # LLM 가용 여부 확인
    use_llm = llm_reviewer.is_available()
    if use_llm:
        logger.info("LLM 사용 가능 (방법 B: %s)", config["llm"]["model"])
    else:
        logger.warning("LLM 미가동 → OCR 방법 A로 폴백")

    mails = mail_client.fetch_approval_mails()
    if not mails:
        logger.info("처리할 신규 메일 없음")
        return 0

    logger.info("처리 대상 메일: %d건", len(mails))
    processed_count = 0

    for mail in mails:
        mail_id = mail["mail_id"]
        subject = mail["subject"]
        sender = mail["sender"]
        purpose = _extract_purpose(mail["body"], subject)

        logger.info("── 처리 시작: %s (from: %s)", subject, sender)
        log_record = {
            "mail_id": mail_id,
            "subject": subject,
            "sender": sender,
            "purpose": purpose,
            "received_at": mail["received_at"],
            "processed_at": datetime.now().isoformat(),
            "attachments": [],
            "decision": None,
            "error": None,
        }

        if not mail["attachments"]:
            log_record["error"] = "첨부파일 없음"
            save_log(log_record)
            mail_client.mark_processed(mail_id)
            continue

        # 첨부파일별 분석 (영수증 1개 기준, 복수면 첫 번째 우선)
        receipt_path = mail["attachments"][0]
        try:
            if use_llm:
                analysis = llm_reviewer.review(receipt_path, purpose)
            else:
                analysis = ocr_analyzer.analyze(receipt_path)

            approval = approval_engine.evaluate(analysis, purpose)

            log_record["attachments"] = mail["attachments"]
            log_record["analysis"] = analysis.to_dict()
            log_record["decision"] = approval.decision
            log_record["approval_detail"] = approval.to_dict()

            logger.info("판단: %s — %s", approval.decision, approval.reason)

            # 그룹웨어 자동 처리 (승인 또는 반려만)
            gw_success = True
            if approval.decision in ("승인", "반려"):
                with GroupwareAutomation(config, headless=True) as gw:
                    if gw.login():
                        gw_success = gw.process_approval(
                            doc_url=None,
                            decision=approval.decision,
                            comment=approval.reason,
                            mail_subject=subject,
                        )
                    else:
                        gw_success = False

                if not gw_success:
                    logger.error("그룹웨어 처리 실패 → 수동검토로 전환")
                    approval.decision = "수동검토"
                    approval.reason = f"그룹웨어 자동 처리 실패. 원래 판단: {approval.reason}"

            # 결과 회신 메일
            mail_client.send_result(
                to=sender,
                original_subject=subject,
                decision=approval.decision,
                reason=approval.reason,
                body_extra=_build_mail_body(approval),
            )

            log_record["gw_success"] = gw_success
            mail_client.mark_processed(mail_id)
            processed_count += 1

        except Exception as e:
            logger.exception("메일 처리 중 예외: %s", e)
            log_record["error"] = str(e)

        save_log(log_record)
        logger.info("── 처리 완료: %s → %s", subject, log_record.get("decision", "오류"))

    return processed_count


def _extract_purpose(body: str, subject: str) -> str:
    """메일 본문/제목에서 결재 목적 추출."""
    import re
    patterns = [
        r"결재\s*목적\s*[:\s]+(.+)",
        r"사용\s*목적\s*[:\s]+(.+)",
        r"지출\s*목적\s*[:\s]+(.+)",
        r"용도\s*[:\s]+(.+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:100]
    # 추출 실패 시 제목에서 키워드 제거 후 반환
    return subject.replace("결재 합의", "").replace("결재요청", "").strip()


def _build_mail_body(approval) -> str:
    lines = []
    if approval.merchant_name:
        lines.append(f"상호명: {approval.merchant_name}")
    if approval.date:
        lines.append(f"날짜: {approval.date}")
    if approval.amount:
        lines.append(f"금액: {approval.amount:,}원")
    if approval.category:
        lines.append(f"카테고리: {approval.category}")
    if approval.mismatches:
        lines.append(f"불일치 사항: {', '.join(approval.mismatches)}")
    return "\n".join(lines)


if __name__ == "__main__":
    cfg = load_config()
    count = run_once(cfg)
    logger.info("처리 완료: %d건", count)

"""
mail_client.py — POP3 수신 + SMTP 발송
"""
import poplib
import smtplib
import email
import hashlib
import json
import os
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

PROCESSED_MAILS_FILE = Path("processed_mails.json")
SUBJECT_KEYWORDS = ["결재 합의", "결재합의", "결재요청", "결재 요청"]
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".tiff", ".tif"}


def _load_processed() -> set:
    if PROCESSED_MAILS_FILE.exists():
        with open(PROCESSED_MAILS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _save_processed(processed: set) -> None:
    with open(PROCESSED_MAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, ensure_ascii=False, indent=2)


def _decode_str(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _mail_id(msg: email.message.Message) -> str:
    msg_id = msg.get("Message-ID", "")
    if msg_id:
        return msg_id.strip()
    # Message-ID 없을 경우 주요 헤더 해시
    raw = (msg.get("From", "") + msg.get("Date", "") + msg.get("Subject", ""))
    return hashlib.md5(raw.encode()).hexdigest()


class MailClient:
    def __init__(self, config: dict):
        self.pop_server = config["pop_server"]
        self.pop_port = config.get("pop_port", 995)
        self.smtp_server = config["smtp_server"]
        self.smtp_port = config.get("smtp_port", 587)
        self.user = config["user"]
        self.password = self._get_password(config)
        self.download_dir = Path(config.get("download_dir", "./receipts"))
        self.download_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _get_password(config: dict) -> str:
        pw = config.get("password", "")
        if not pw:
            pw = os.environ.get("MAIL_PASSWORD", "")
        if not pw:
            try:
                import keyring
                pw = keyring.get_password("receipt-check-mail", config["user"]) or ""
            except Exception:
                pass
        return pw

    # ------------------------------------------------------------------ #
    # POP3 수신
    # ------------------------------------------------------------------ #

    def fetch_approval_mails(self) -> list[dict]:
        """결재 합의 키워드가 포함된 미처리 메일 목록을 반환."""
        processed = _load_processed()
        results = []

        try:
            conn = poplib.POP3_SSL(self.pop_server, self.pop_port)
            conn.user(self.user)
            conn.pass_(self.password)
        except Exception as e:
            logger.error("POP3 연결 실패: %s", e)
            return []

        try:
            num_messages = len(conn.list()[1])
            logger.info("메일함 메시지 수: %d", num_messages)

            for i in range(num_messages, 0, -1):  # 최신 메일부터
                try:
                    raw_lines = conn.retr(i)[1]
                    raw = b"\n".join(raw_lines)
                    msg = email.message_from_bytes(raw)

                    mail_id = _mail_id(msg)
                    if mail_id in processed:
                        continue

                    subject = _decode_str(msg.get("Subject", ""))
                    if not any(kw in subject for kw in SUBJECT_KEYWORDS):
                        continue

                    sender = _decode_str(msg.get("From", ""))
                    body, attachments = self._parse_body_and_attachments(msg)

                    if not attachments:
                        logger.debug("첨부파일 없음, 건너뜀: %s", subject)
                        continue

                    results.append({
                        "mail_id": mail_id,
                        "subject": subject,
                        "sender": sender,
                        "body": body,
                        "attachments": attachments,  # list of file paths
                        "received_at": msg.get("Date", ""),
                    })
                    logger.info("수신 메일 처리 대상: %s (from: %s)", subject, sender)

                except Exception as e:
                    logger.warning("메일 #%d 파싱 오류: %s", i, e)
        finally:
            conn.quit()

        return results

    def _parse_body_and_attachments(self, msg: email.message.Message) -> tuple[str, list[str]]:
        body = ""
        saved_paths = []

        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = part.get("Content-Disposition", "")

            if "attachment" in disposition or part.get_filename():
                filename = _decode_str(part.get_filename() or "unknown")
                ext = Path(filename).suffix.lower()
                if ext in ALLOWED_EXTENSIONS:
                    safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
                    save_path = self.download_dir / safe_name
                    with open(save_path, "wb") as f:
                        f.write(part.get_payload(decode=True))
                    saved_paths.append(str(save_path))
                    logger.info("첨부파일 저장: %s", save_path)
            elif content_type == "text/plain" and not body:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")

        return body, saved_paths

    def mark_processed(self, mail_id: str) -> None:
        processed = _load_processed()
        processed.add(mail_id)
        _save_processed(processed)

    # ------------------------------------------------------------------ #
    # SMTP 발송
    # ------------------------------------------------------------------ #

    def send_result(
        self,
        to: str,
        original_subject: str,
        decision: str,           # "승인" | "반려" | "수동검토"
        reason: str,
        body_extra: Optional[str] = None,
    ) -> None:
        tag_map = {"승인": "[승인]", "반려": "[반려]", "수동검토": "[수동검토 요청]"}
        tag = tag_map.get(decision, f"[{decision}]")
        subject = f"{tag} {original_subject}"

        body_lines = [
            f"결재 처리 결과: {decision}",
            "",
            f"사유: {reason}",
        ]
        if body_extra:
            body_lines += ["", body_extra]
        body_lines += [
            "",
            "---",
            "본 메일은 영수증 자동 검토 시스템에 의해 발송되었습니다.",
        ]

        msg = MIMEMultipart()
        msg["From"] = self.user
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText("\n".join(body_lines), "plain", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(self.user, self.password)
                smtp.sendmail(self.user, [to], msg.as_bytes())
            logger.info("결과 메일 발송 완료 → %s (%s)", to, decision)
        except Exception as e:
            logger.error("SMTP 발송 실패: %s", e)
            raise

"""
groupware_automation.py — Playwright 기반 웹 그룹웨어 자동화
로그인 → 결재 문서 접근 → 승인/반려 처리 → 스크린샷 저장
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("logs/screenshots")


class GroupwareAutomation:
    def __init__(self, config: dict, headless: bool = True):
        self.url = config.get("groupware_url", "")
        self.gw_id = config.get("credentials", {}).get("id", "")
        self.gw_pw = self._get_password(config)
        self.headless = headless
        self._browser = None
        self._page = None
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _get_password(config: dict) -> str:
        pw = config.get("credentials", {}).get("pw", "")
        if not pw:
            pw = os.environ.get("GW_PASSWORD", "")
        if not pw:
            try:
                import keyring
                gw_id = config.get("credentials", {}).get("id", "")
                pw = keyring.get_password("receipt-check-gw", gw_id) or ""
            except Exception:
                pass
        return pw

    # ------------------------------------------------------------------ #
    # 브라우저 생명주기
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
        )
        self._page = context.new_page()
        logger.info("브라우저 시작 (headless=%s)", self.headless)

    def stop(self) -> None:
        if self._browser:
            self._browser.close()
        if hasattr(self, "_pw"):
            self._pw.stop()
        logger.info("브라우저 종료")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    # ------------------------------------------------------------------ #
    # 로그인
    # ------------------------------------------------------------------ #

    def login(self) -> bool:
        try:
            self._page.goto(self.url, timeout=30_000)
            # 그룹웨어별로 셀렉터 조정 필요
            self._page.fill("input[name='userId'], input[id='userId'], input[type='text']", self.gw_id)
            self._page.fill("input[name='password'], input[id='password'], input[type='password']", self.gw_pw)
            self._page.click("button[type='submit'], input[type='submit'], button:has-text('로그인')")
            self._page.wait_for_load_state("networkidle", timeout=15_000)
            self._screenshot("login_success")
            logger.info("그룹웨어 로그인 성공")
            return True
        except Exception as e:
            self._screenshot("login_failed")
            logger.error("그룹웨어 로그인 실패: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # 결재 처리 (메인 인터페이스)
    # ------------------------------------------------------------------ #

    def process_approval(
        self,
        doc_url: Optional[str],
        decision: str,        # "승인" | "반려" | "수동검토"
        comment: str,
        mail_subject: str = "",
    ) -> bool:
        """
        결재 문서로 이동 후 승인 또는 반려 처리.
        doc_url: 결재 문서 직접 URL (None이면 대기함에서 검색)
        """
        try:
            if doc_url:
                self._page.goto(doc_url, timeout=20_000)
            else:
                if not self._navigate_to_pending(mail_subject):
                    return False

            self._page.wait_for_load_state("networkidle", timeout=10_000)
            self._screenshot(f"before_{decision}")

            if decision == "승인":
                return self._click_approve(comment)
            elif decision == "반려":
                return self._click_reject(comment)
            else:
                # 수동검토: 담당자에게 알림만 (그룹웨어 처리 안 함)
                logger.info("수동검토 대상 — 그룹웨어 자동 처리 건너뜀")
                return True

        except Exception as e:
            self._screenshot("process_error")
            logger.error("결재 처리 중 오류: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # 내부 — 승인/반려 클릭
    # ------------------------------------------------------------------ #

    def _click_approve(self, comment: str) -> bool:
        """
        그룹웨어별 승인 버튼 셀렉터를 여기에 추가.
        실제 배포 전 Playwright codegen으로 정확한 셀렉터 확인 필요.
        """
        approve_selectors = [
            "button:has-text('승인')",
            "a:has-text('승인')",
            "input[value='승인']",
            "#btnApprove",
            ".btn-approve",
        ]
        for sel in approve_selectors:
            try:
                btn = self._page.locator(sel).first
                if btn.is_visible(timeout=2_000):
                    # 의견 입력
                    self._fill_comment(comment)
                    btn.click()
                    self._page.wait_for_load_state("networkidle", timeout=10_000)
                    self._screenshot("approved")
                    logger.info("결재 승인 완료")
                    return True
            except Exception:
                continue

        logger.error("승인 버튼을 찾을 수 없음")
        self._screenshot("approve_button_not_found")
        return False

    def _click_reject(self, comment: str) -> bool:
        reject_selectors = [
            "button:has-text('반려')",
            "a:has-text('반려')",
            "input[value='반려']",
            "#btnReject",
            ".btn-reject",
        ]
        for sel in reject_selectors:
            try:
                btn = self._page.locator(sel).first
                if btn.is_visible(timeout=2_000):
                    self._fill_comment(comment)
                    btn.click()
                    self._page.wait_for_load_state("networkidle", timeout=10_000)
                    self._screenshot("rejected")
                    logger.info("결재 반려 완료")
                    return True
            except Exception:
                continue

        logger.error("반려 버튼을 찾을 수 없음")
        self._screenshot("reject_button_not_found")
        return False

    def _fill_comment(self, comment: str) -> None:
        comment_selectors = [
            "textarea[name='opinion']",
            "textarea[name='comment']",
            "textarea[name='reason']",
            "textarea.opinion",
            "#opinion",
            "#comment",
        ]
        for sel in comment_selectors:
            try:
                el = self._page.locator(sel).first
                if el.is_visible(timeout=1_000):
                    el.fill(comment)
                    return
            except Exception:
                continue
        logger.warning("의견 입력 필드를 찾을 수 없음, 건너뜀")

    def _navigate_to_pending(self, subject: str) -> bool:
        """결재 대기함에서 제목으로 문서 검색."""
        try:
            # 그룹웨어별 대기함 URL 경로는 실제 환경에 맞게 수정
            pending_url = self.url.rstrip("/") + "/approval/pending"
            self._page.goto(pending_url, timeout=20_000)
            self._page.wait_for_load_state("networkidle")

            if subject:
                link = self._page.locator(f"a:has-text('{subject[:20]}')").first
                if link.is_visible(timeout=3_000):
                    link.click()
                    self._page.wait_for_load_state("networkidle")
                    return True

            logger.warning("결재 문서 검색 실패: %s", subject)
            return False
        except Exception as e:
            logger.error("대기함 이동 실패: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # 유틸
    # ------------------------------------------------------------------ #

    def _screenshot(self, label: str) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"{ts}_{label}.png"
        try:
            self._page.screenshot(path=str(path))
            logger.debug("스크린샷 저장: %s", path)
        except Exception as e:
            logger.warning("스크린샷 저장 실패: %s", e)

"""
llm_reviewer.py — 로컬 LLM 기반 영수증 분석 (방법 B, 권장)
Ollama + LLaVA 모델을 사용하여 영수증 이미지를 직접 분석.
외부 API 호출 없이 로컬에서 동작.
"""
import base64
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """당신은 한국 회사의 경비 결재 검토 담당자입니다.
첨부된 영수증 이미지를 분석하고, 아래 결재 목적과 일치하는지 판단해주세요.

[결재 목적]
{purpose}

다음 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
{{
  "merchant_name": "상호명",
  "date": "YYYY-MM-DD",
  "amount": 숫자(원),
  "items": ["구매항목1", "구매항목2"],
  "category": "식비|교통비|사무용품|접대비|출장비|의료비|교육비|기타",
  "matches_purpose": true또는false,
  "confidence": 0.0~1.0사이숫자,
  "mismatches": ["불일치사항1", "불일치사항2"],
  "reason": "판단 근거 한 문장"
}}"""


@dataclass
class LLMResult:
    merchant_name: str = ""
    date: str = ""
    amount: int = 0
    items: list[str] = field(default_factory=list)
    category: str = "기타"
    matches_purpose: bool = False
    confidence: float = 0.0
    mismatches: list[str] = field(default_factory=list)
    reason: str = ""
    raw_response: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class LLMReviewer:
    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "http://localhost:11434")
        self.model = config.get("model", "llava:7b")
        self.timeout = config.get("timeout", 120)

    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def review(self, file_path: str, purpose: str) -> LLMResult:
        """영수증 파일과 결재 목적을 받아 LLM 분석 결과를 반환."""
        path = Path(file_path)
        if not path.exists():
            return LLMResult(error=f"파일 없음: {file_path}")

        # PDF는 첫 페이지를 이미지로 변환
        image_b64 = self._load_image_as_base64(path)
        if not image_b64:
            return LLMResult(error="이미지 로드 실패")

        prompt = ANALYSIS_PROMPT.format(purpose=purpose)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {
                "temperature": 0.1,   # 일관성 있는 응답을 위해 낮은 temperature
                "num_predict": 512,
            },
        }

        try:
            logger.info("LLM 분석 요청 — 모델: %s, 파일: %s", self.model, path.name)
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw_text = resp.json().get("response", "")
            logger.debug("LLM 원문 응답: %s", raw_text[:300])
            return self._parse_response(raw_text)

        except requests.Timeout:
            logger.error("LLM 응답 타임아웃 (%ds)", self.timeout)
            return LLMResult(error=f"LLM 타임아웃 ({self.timeout}s)")
        except requests.RequestException as e:
            logger.error("LLM 요청 실패: %s", e)
            return LLMResult(error=str(e))

    # ------------------------------------------------------------------ #
    # 내부 메서드
    # ------------------------------------------------------------------ #

    def _load_image_as_base64(self, path: Path) -> Optional[str]:
        ext = path.suffix.lower()
        try:
            if ext == ".pdf":
                return self._pdf_first_page_to_b64(path)
            else:
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error("이미지 로드 실패 (%s): %s", path.name, e)
            return None

    def _pdf_first_page_to_b64(self, path: Path) -> Optional[str]:
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(path), dpi=150, first_page=1, last_page=1)
            if not images:
                return None
            import io
            buf = io.BytesIO()
            images[0].save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error("PDF → 이미지 변환 실패: %s", e)
            return None

    def _parse_response(self, raw_text: str) -> LLMResult:
        # JSON 블록 추출 (마크다운 코드블록 처리 포함)
        json_match = re.search(r"\{[\s\S]*\}", raw_text)
        if not json_match:
            logger.warning("LLM 응답에서 JSON을 찾을 수 없음: %s", raw_text[:200])
            return LLMResult(raw_response=raw_text, error="JSON 파싱 실패")

        try:
            data = json.loads(json_match.group())
            result = LLMResult(
                merchant_name=str(data.get("merchant_name", "")),
                date=str(data.get("date", "")),
                amount=int(data.get("amount", 0)),
                items=list(data.get("items", [])),
                category=str(data.get("category", "기타")),
                matches_purpose=bool(data.get("matches_purpose", False)),
                confidence=float(data.get("confidence", 0.0)),
                mismatches=list(data.get("mismatches", [])),
                reason=str(data.get("reason", "")),
                raw_response=raw_text,
            )
            logger.info(
                "LLM 분석 완료 — 상호: %s | 금액: %d | 일치: %s | 신뢰도: %.2f",
                result.merchant_name, result.amount,
                result.matches_purpose, result.confidence,
            )
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("JSON 파싱 오류: %s\n원문: %s", e, raw_text[:300])
            return LLMResult(raw_response=raw_text, error=f"JSON 파싱 오류: {e}")

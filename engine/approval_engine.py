"""
approval_engine.py — 승인/반려/수동검토 판단 로직
신뢰도(confidence) 기반 3단계 판단.
"""
import logging
from dataclasses import dataclass, asdict
from typing import Literal, Union

from .llm_reviewer import LLMResult
from .receipt_analyzer import OCRResult

logger = logging.getLogger(__name__)

Decision = Literal["승인", "반려", "수동검토"]


@dataclass
class ApprovalResult:
    decision: Decision
    reason: str
    mismatches: list[str]
    confidence: float
    merchant_name: str
    date: str
    amount: int
    category: str
    source: str  # "llm" | "ocr"

    def to_dict(self) -> dict:
        return asdict(self)


class ApprovalEngine:
    def __init__(self, config: dict):
        thresholds = config.get("approval", {})
        self.approve_threshold = float(thresholds.get("auto_approve_threshold", 0.85))
        self.reject_threshold = float(thresholds.get("auto_reject_threshold", 0.40))

    def evaluate(
        self,
        analysis: Union[LLMResult, OCRResult],
        purpose: str,
    ) -> ApprovalResult:
        """분석 결과를 받아 승인/반려/수동검토 결정을 반환."""

        if isinstance(analysis, LLMResult):
            return self._evaluate_llm(analysis, purpose)
        else:
            return self._evaluate_ocr(analysis, purpose)

    # ------------------------------------------------------------------ #
    # LLM 결과 평가 (주요 경로)
    # ------------------------------------------------------------------ #

    def _evaluate_llm(self, result: LLMResult, purpose: str) -> ApprovalResult:
        if result.error:
            logger.warning("LLM 분석 오류로 수동검토: %s", result.error)
            return ApprovalResult(
                decision="수동검토",
                reason=f"LLM 분석 오류: {result.error}",
                mismatches=[],
                confidence=0.0,
                merchant_name="",
                date="",
                amount=0,
                category="기타",
                source="llm",
            )

        decision = self._decide(result.matches_purpose, result.confidence)

        if decision == "승인":
            reason = f"결재 목적과 일치 (신뢰도 {result.confidence:.0%}). {result.reason}"
        elif decision == "반려":
            mismatch_text = "; ".join(result.mismatches) if result.mismatches else result.reason
            reason = f"결재 목적 불일치 (신뢰도 {result.confidence:.0%}). {mismatch_text}"
        else:
            reason = f"신뢰도 중간 ({result.confidence:.0%}), 수동 검토 필요. {result.reason}"

        logger.info("판단 결과: %s (신뢰도 %.2f)", decision, result.confidence)

        return ApprovalResult(
            decision=decision,
            reason=reason,
            mismatches=result.mismatches,
            confidence=result.confidence,
            merchant_name=result.merchant_name,
            date=result.date,
            amount=result.amount,
            category=result.category,
            source="llm",
        )

    # ------------------------------------------------------------------ #
    # OCR 결과 평가 (폴백 경로)
    # ------------------------------------------------------------------ #

    def _evaluate_ocr(self, result: OCRResult, purpose: str) -> ApprovalResult:
        """OCR은 의미 이해가 없으므로 카테고리-목적 키워드 매칭으로 간단히 판단."""
        purpose_lower = purpose.lower()

        # 결재 목적에서 카테고리 힌트 추출
        category_hints = {
            "식비": ["식대", "식비", "점심", "저녁", "밥", "회식"],
            "교통비": ["교통", "출장", "택시", "주유", "ktx"],
            "사무용품": ["사무", "소모품", "문구"],
            "접대비": ["접대", "거래처"],
            "출장비": ["출장", "숙박", "호텔"],
        }

        matched_categories = []
        for category, hints in category_hints.items():
            if any(hint in purpose_lower for hint in hints):
                matched_categories.append(category)

        if not matched_categories:
            # 목적에서 카테고리 판단 불가 → 수동검토
            return ApprovalResult(
                decision="수동검토",
                reason="결재 목적에서 카테고리를 특정할 수 없어 수동 검토 필요",
                mismatches=[],
                confidence=0.3,
                merchant_name=result.merchant_name,
                date=result.date,
                amount=result.amount,
                category=result.category,
                source="ocr",
            )

        matches = result.category in matched_categories
        confidence = 0.7 if matches else 0.2
        decision = self._decide(matches, confidence)

        if decision == "승인":
            reason = f"영수증 카테고리({result.category})가 결재 목적과 일치"
        elif decision == "반려":
            reason = f"영수증 카테고리({result.category})가 결재 목적({', '.join(matched_categories)})과 불일치"
        else:
            reason = "OCR 기반 판단, 정확도 확인 필요"

        return ApprovalResult(
            decision=decision,
            reason=reason,
            mismatches=[] if matches else [f"카테고리 불일치: {result.category} ≠ {matched_categories}"],
            confidence=confidence,
            merchant_name=result.merchant_name,
            date=result.date,
            amount=result.amount,
            category=result.category,
            source="ocr",
        )

    # ------------------------------------------------------------------ #
    # 공통 판단 로직
    # ------------------------------------------------------------------ #

    def _decide(self, matches_purpose: bool, confidence: float) -> Decision:
        if matches_purpose and confidence >= self.approve_threshold:
            return "승인"
        if not matches_purpose or confidence <= self.reject_threshold:
            return "반려"
        return "수동검토"

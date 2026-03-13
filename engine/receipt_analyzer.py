"""
receipt_analyzer.py — OCR 기반 영수증 분석 (방법 A)
pytesseract + Pillow 사용. 영수증 품질이 일정하고 정형화된 경우 적합.
"""
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# 카테고리별 키워드 맵
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "식비": ["식당", "카페", "커피", "배달", "음식", "밥", "점심", "저녁", "아침", "레스토랑", "분식", "치킨", "피자", "맥도날드", "스타벅스"],
    "교통비": ["택시", "주유", "주차", "ktx", "기차", "버스", "지하철", "항공", "카카오택시", "우버", "고속도로", "하이패스"],
    "사무용품": ["문구", "복사", "프린트", "인쇄", "사무", "소모품", "토너", "용지", "펜", "노트", "바인더"],
    "접대비": ["접대", "회식", "거래처", "바", "술", "맥주", "와인", "호프", "노래방", "룸살롱"],
    "출장비": ["호텔", "숙박", "모텔", "여관", "항공권", "출장", "숙소"],
    "의료비": ["병원", "약국", "의원", "한의원", "치과", "안과", "의료"],
    "교육비": ["교육", "세미나", "강의", "수강", "도서", "책", "교재"],
}

AMOUNT_PATTERNS = [
    r"합\s*계\s*[:\s]*([0-9,]+)\s*원?",
    r"총\s*금\s*액\s*[:\s]*([0-9,]+)\s*원?",
    r"결\s*제\s*금\s*액\s*[:\s]*([0-9,]+)\s*원?",
    r"([0-9]{1,3}(?:,[0-9]{3})+)\s*원",
    r"₩\s*([0-9,]+)",
]

DATE_PATTERNS = [
    r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})[일]?",
    r"(\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})",
]


@dataclass
class OCRResult:
    raw_text: str = ""
    merchant_name: str = ""
    date: str = ""
    amount: int = 0
    category: str = "기타"
    category_keywords_found: list[str] = field(default_factory=list)
    confidence_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ReceiptAnalyzer:
    def __init__(self, tesseract_cmd: Optional[str] = None):
        try:
            import pytesseract
            if tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            self._pytesseract = pytesseract
        except ImportError:
            logger.warning("pytesseract 미설치. OCR 방법 A 사용 불가.")
            self._pytesseract = None

    def analyze(self, file_path: str) -> OCRResult:
        path = Path(file_path)
        if not path.exists():
            logger.error("파일 없음: %s", file_path)
            return OCRResult(confidence_note="파일 없음")

        ext = path.suffix.lower()
        if ext == ".pdf":
            text = self._ocr_pdf(path)
        else:
            text = self._ocr_image(path)

        if not text:
            return OCRResult(confidence_note="OCR 결과 없음")

        result = OCRResult(raw_text=text)
        result.amount = self._extract_amount(text)
        result.date = self._extract_date(text)
        result.merchant_name = self._extract_merchant(text)
        result.category, result.category_keywords_found = self._classify_category(text)
        result.confidence_note = "OCR 추출 완료"

        logger.info(
            "OCR 결과 — 상호: %s | 금액: %d | 날짜: %s | 카테고리: %s",
            result.merchant_name, result.amount, result.date, result.category,
        )
        return result

    # ------------------------------------------------------------------ #
    # 내부 메서드
    # ------------------------------------------------------------------ #

    def _ocr_image(self, path: Path) -> str:
        if not self._pytesseract:
            return ""
        try:
            from PIL import Image
            img = Image.open(path)
            # 전처리: 그레이스케일 변환으로 OCR 정확도 향상
            img = img.convert("L")
            text = self._pytesseract.image_to_string(img, lang="kor+eng")
            return text
        except Exception as e:
            logger.error("이미지 OCR 실패 (%s): %s", path.name, e)
            return ""

    def _ocr_pdf(self, path: Path) -> str:
        try:
            import PyPDF2
            text_parts = []
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text_parts.append(page.extract_text() or "")
            text = "\n".join(text_parts)
            if text.strip():
                return text
        except Exception as e:
            logger.warning("PDF 텍스트 추출 실패, 이미지 변환 시도: %s", e)

        # PDF에서 텍스트 추출 실패 시 이미지로 변환 후 OCR (pdf2image 필요)
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(path), dpi=200)
            texts = []
            for img in images:
                from PIL import Image as PILImage
                img = img.convert("L")
                texts.append(self._pytesseract.image_to_string(img, lang="kor+eng"))
            return "\n".join(texts)
        except Exception as e:
            logger.error("PDF → 이미지 OCR 실패: %s", e)
            return ""

    def _extract_amount(self, text: str) -> int:
        for pattern in AMOUNT_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    return int(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        return 0

    def _extract_date(self, text: str) -> str:
        for pattern in DATE_PATTERNS:
            m = re.search(pattern, text)
            if m:
                groups = m.groups()
                if len(groups) == 3:
                    y, mo, d = groups
                    if len(y) == 2:
                        y = "20" + y
                    return f"{y}-{int(mo):02d}-{int(d):02d}"
        return ""

    def _extract_merchant(self, text: str) -> str:
        # 첫 줄이나 상호명 패턴에서 추출 시도
        patterns = [
            r"상\s*호\s*[:\s]*(.+)",
            r"가\s*맹\s*점\s*[:\s]*(.+)",
            r"^(.{2,20})(?:\s*영수증|\s*receipt)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if m:
                return m.group(1).strip()[:30]

        # 패턴 실패 시 첫 번째 비어있지 않은 줄 반환
        for line in text.splitlines():
            stripped = line.strip()
            if 2 <= len(stripped) <= 30 and not re.match(r"^\d", stripped):
                return stripped
        return ""

    def _classify_category(self, text: str) -> tuple[str, list[str]]:
        text_lower = text.lower()
        scores: dict[str, int] = {}
        found_keywords: dict[str, list[str]] = {}

        for category, keywords in CATEGORY_KEYWORDS.items():
            hits = [kw for kw in keywords if kw in text_lower]
            if hits:
                scores[category] = len(hits)
                found_keywords[category] = hits

        if not scores:
            return "기타", []

        best = max(scores, key=lambda c: scores[c])
        return best, found_keywords[best]

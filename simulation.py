"""
simulation.py — 웹 자동 시뮬레이션 모드
실제 메일서버 / 그룹웨어 / LLM 없이 브라우저에서 파이프라인 전체를 시뮬레이션.

실행: streamlit run simulation.py
"""
import json
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw, ImageFont

# 엔진 임포트 (실제 로직 재사용)
from engine.approval_engine import ApprovalEngine, ApprovalResult
from engine.llm_reviewer import LLMResult


# ──────────────────────────────────────────────
# 헬퍼 함수 (UI 코드보다 먼저 정의)
# ──────────────────────────────────────────────

def _font(size: int):
    for name in ["malgun.ttf", "gulim.ttc", "arial.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _make_receipt_image(receipt: dict) -> Image.Image:
    W, H = 400, 520
    img = Image.new("RGB", (W, H), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    def t(x, y, msg, size=14, color=(30, 30, 30)):
        draw.text((x, y), msg, font=_font(size), fill=color)

    draw.rectangle([10, 10, W - 10, H - 10], outline=(180, 180, 180), width=2)
    draw.rectangle([10, 10, W - 10, 70], fill=(245, 245, 245))
    t(30, 20, receipt["merchant_name"], size=20, color=(10, 10, 10))
    t(30, 48, "영  수  증", size=13, color=(100, 100, 100))
    draw.line([20, 75, W - 20, 75], fill=(200, 200, 200), width=1)
    t(30, 90, f"날짜:  {receipt['date']}", size=14)
    t(30, 118, "─── 주문 내역 ───", size=13, color=(120, 120, 120))
    y = 143
    for item in receipt["items"]:
        t(40, y, f"• {item}", size=14)
        y += 28
    draw.line([20, y + 5, W - 20, y + 5], fill=(200, 200, 200), width=1)
    t(30, y + 18, "합  계", size=16)
    t(240, y + 18, f"{receipt['amount']:,} 원", size=16, color=(200, 50, 50))
    draw.rectangle([W - 120, H - 55, W - 20, H - 25], fill=(230, 245, 255), outline=(100, 160, 220))
    t(W - 115, H - 52, receipt["category"], size=13, color=(50, 100, 180))
    t(30, H - 40, "감사합니다 :)", size=12, color=(160, 160, 160))
    return img


def _make_groupware_screenshot(scenario: dict, approval: ApprovalResult) -> Image.Image:
    W, H = 800, 420
    img = Image.new("RGB", (W, H), color=(240, 242, 246))
    draw = ImageDraw.Draw(img)

    def t(x, y, msg, size=14, color=(30, 30, 30)):
        draw.text((x, y), msg, font=_font(size), fill=color)

    draw.rectangle([0, 0, W, 45], fill=(44, 62, 80))
    t(20, 12, "그룹웨어 — 전자결재", size=16, color=(255, 255, 255))
    t(W - 200, 14, "admin@company.com", size=12, color=(180, 190, 200))
    draw.rectangle([20, 60, W - 20, 110], fill=(255, 255, 255), outline=(220, 220, 220))
    t(30, 68, "결재 문서", size=11, color=(120, 120, 120))
    t(30, 85, scenario["mail"]["subject"], size=15)
    draw.rectangle([20, 125, W - 20, 270], fill=(255, 255, 255), outline=(220, 220, 220))
    info = [
        ("결재 목적", scenario["mail"]["purpose"]),
        ("신청자",    scenario["mail"]["sender"]),
        ("상호명",    scenario["receipt"]["merchant_name"]),
        ("금액",      f"{scenario['receipt']['amount']:,} 원"),
        ("신뢰도",    f"{approval.confidence:.0%}"),
    ]
    y = 135
    for label, value in info:
        t(35, y, label, size=12, color=(100, 100, 100))
        t(160, y, value, size=12)
        y += 24
    draw.rectangle([20, 280, W - 20, 340], fill=(250, 250, 250), outline=(200, 200, 200))
    t(30, 288, "검토 의견:", size=12, color=(100, 100, 100))
    reason_text = approval.reason[:72] + ("..." if len(approval.reason) > 72 else "")
    t(30, 308, reason_text, size=12, color=(60, 60, 60))
    btn_color = (39, 174, 96) if approval.decision == "승인" else (192, 57, 43) if approval.decision == "반려" else (230, 126, 34)
    draw.rectangle([W - 200, 360, W - 60, 400], fill=btn_color)
    t(W - 185, 371, f"{approval.decision}  완료", size=14, color=(255, 255, 255))
    draw.rectangle([W - 340, 360, W - 220, 400], fill=(189, 195, 199))
    t(W - 325, 371, "목록으로", size=14, color=(80, 80, 80))
    return img


def _save_simulation_log(record: dict) -> None:
    log_file = LOG_DIR / f"{datetime.now().strftime('%Y%m%d')}_results.json"
    records = []
    if log_file.exists():
        try:
            with open(log_file, encoding="utf-8") as f:
                records = json.load(f)
        except json.JSONDecodeError:
            pass
    records.append(record)
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

STEP_DELAY = 0.6  # 각 단계 사이 딜레이(초)

# ──────────────────────────────────────────────
# 시나리오 정의
# ──────────────────────────────────────────────

SCENARIOS = [
    {
        "id": "S1",
        "name": "✅ 정상 식대 영수증 — 자동 승인",
        "mail": {
            "subject": "결재 합의 요청 — 팀 점심 식대",
            "sender": "lee@company.com",
            "purpose": "팀 점심 식비 (3인)",
            "received_at": "2026-03-14 12:05",
        },
        "receipt": {
            "merchant_name": "한식당 맛나",
            "date": "2026-03-14",
            "amount": 45000,
            "items": ["정식 3인분", "된장찌개"],
            "category": "식비",
        },
        "llm": {
            "matches_purpose": True,
            "confidence": 0.93,
            "mismatches": [],
            "reason": "팀 식대 목적과 한식 음식점 영수증이 일치합니다.",
        },
    },
    {
        "id": "S2",
        "name": "❌ 카테고리 불일치 — 자동 반려",
        "mail": {
            "subject": "결재 합의 요청 — 출장 교통비",
            "sender": "park@company.com",
            "purpose": "KTX 출장 교통비",
            "received_at": "2026-03-14 09:30",
        },
        "receipt": {
            "merchant_name": "스타벅스 강남점",
            "date": "2026-03-14",
            "amount": 12500,
            "items": ["아메리카노 2잔", "샌드위치"],
            "category": "식비",
        },
        "llm": {
            "matches_purpose": False,
            "confidence": 0.15,
            "mismatches": [
                "결재 목적은 교통비이나 영수증은 카페 식음료",
                "KTX 또는 교통 관련 항목 없음",
            ],
            "reason": "교통비 결재 목적과 카페 영수증이 일치하지 않습니다.",
        },
    },
    {
        "id": "S3",
        "name": "👤 신뢰도 경계값 — 수동 검토",
        "mail": {
            "subject": "결재 합의 요청 — 거래처 접대비",
            "sender": "choi@company.com",
            "purpose": "거래처 미팅 접대비",
            "received_at": "2026-03-14 19:15",
        },
        "receipt": {
            "merchant_name": "한우 전문점 황소",
            "date": "2026-03-14",
            "amount": 185000,
            "items": ["한우 세트 2인", "주류"],
            "category": "접대비",
        },
        "llm": {
            "matches_purpose": True,
            "confidence": 0.62,
            "mismatches": ["주류 포함 여부 추가 확인 필요"],
            "reason": "접대 목적과 음식점 영수증은 일치하나, 주류 항목으로 신뢰도 낮음.",
        },
    },
    {
        "id": "S4",
        "name": "✅ 사무용품 구매 — 자동 승인",
        "mail": {
            "subject": "결재 합의 요청 — 사무소모품",
            "sender": "kim@company.com",
            "purpose": "사무용 소모품 구매 (복사지, 토너)",
            "received_at": "2026-03-13 14:22",
        },
        "receipt": {
            "merchant_name": "오피스디포",
            "date": "2026-03-13",
            "amount": 67800,
            "items": ["A4 복사지 5박스", "흑백 토너"],
            "category": "사무용품",
        },
        "llm": {
            "matches_purpose": True,
            "confidence": 0.97,
            "mismatches": [],
            "reason": "사무소모품 목적과 문구/소모품 영수증이 정확히 일치합니다.",
        },
    },
    {
        "id": "S5",
        "name": "❌ 개인 경비 의심 — 자동 반려",
        "mail": {
            "subject": "결재 합의 요청 — 출장 숙박비",
            "sender": "jung@company.com",
            "purpose": "부산 출장 숙박비",
            "received_at": "2026-03-12 08:50",
        },
        "receipt": {
            "merchant_name": "노래방 SING SING",
            "date": "2026-03-12",
            "amount": 55000,
            "items": ["룸 이용 2시간", "음료"],
            "category": "접대비",
        },
        "llm": {
            "matches_purpose": False,
            "confidence": 0.05,
            "mismatches": [
                "결재 목적은 숙박비이나 영수증은 노래방",
                "출장 관련 항목 없음",
                "개인 유흥 경비로 판단됨",
            ],
            "reason": "출장 숙박비와 노래방 영수증은 전혀 일치하지 않습니다.",
        },
    },
]

DEFAULT_CONFIG = {
    "approval": {
        "auto_approve_threshold": 0.85,
        "auto_reject_threshold": 0.40,
    }
}

# ──────────────────────────────────────────────
# 페이지 설정
# ──────────────────────────────────────────────

st.set_page_config(
    page_title="시뮬레이션 모드",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 자동 결재 시뮬레이션")
st.caption("실제 메일서버 · 그룹웨어 · LLM 없이 전체 파이프라인을 체험합니다.")

st.divider()

# ──────────────────────────────────────────────
# 시나리오 선택 + 임계값 설정
# ──────────────────────────────────────────────

col_left, col_right = st.columns([2, 1])

with col_left:
    scenario_names = [s["name"] for s in SCENARIOS]
    selected_name = st.selectbox("시나리오 선택", scenario_names)
    scenario = next(s for s in SCENARIOS if s["name"] == selected_name)

with col_right:
    st.markdown("**판단 임계값 조정**")
    approve_th = st.slider("자동 승인 (≥)", 0.5, 1.0, 0.85, 0.01)
    reject_th = st.slider("자동 반려 (≤)", 0.0, 0.5, 0.40, 0.01)

st.divider()

# ──────────────────────────────────────────────
# 시나리오 미리보기
# ──────────────────────────────────────────────

with st.expander("📋 시나리오 상세 미리보기", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**수신 메일**")
        m = scenario["mail"]
        st.markdown(f"- 제목: `{m['subject']}`")
        st.markdown(f"- 발신: `{m['sender']}`")
        st.markdown(f"- 결재 목적: `{m['purpose']}`")
    with c2:
        st.markdown("**영수증 정보**")
        r = scenario["receipt"]
        st.markdown(f"- 상호명: `{r['merchant_name']}`")
        st.markdown(f"- 날짜: `{r['date']}`")
        st.markdown(f"- 금액: `{r['amount']:,}원`")
        st.markdown(f"- 카테고리: `{r['category']}`")
        st.markdown(f"- 항목: {', '.join(r['items'])}")

# ──────────────────────────────────────────────
# 시뮬레이션 실행
# ──────────────────────────────────────────────

run_btn = st.button("▶ 시뮬레이션 실행", type="primary", use_container_width=True)

if run_btn:
    config = {
        "approval": {
            "auto_approve_threshold": approve_th,
            "auto_reject_threshold": reject_th,
        }
    }
    engine = ApprovalEngine(config)

    st.divider()
    st.subheader("⚙️ 파이프라인 실행")

    log_record = {
        "mail_id": f"SIM-{scenario['id']}-{datetime.now().strftime('%H%M%S')}",
        "subject": scenario["mail"]["subject"],
        "sender": scenario["mail"]["sender"],
        "purpose": scenario["mail"]["purpose"],
        "received_at": scenario["mail"]["received_at"],
        "processed_at": datetime.now().isoformat(),
        "simulation": True,
    }

    # ── STEP 1: 메일 수신 ──────────────────────
    with st.status("📧 Step 1 — 메일 수신 및 첨부파일 다운로드", expanded=True) as s1:
        time.sleep(STEP_DELAY)
        st.write(f"📨 수신: **{scenario['mail']['subject']}**")
        st.write(f"👤 발신자: `{scenario['mail']['sender']}`")
        time.sleep(STEP_DELAY)
        st.write(f"📎 첨부파일 다운로드: `receipt_{scenario['id']}.jpg`")
        st.write(f"🎯 결재 목적 추출: `{scenario['mail']['purpose']}`")
        time.sleep(STEP_DELAY)
        s1.update(label="✅ Step 1 — 메일 수신 완료", state="complete")

    # ── STEP 2: 영수증 이미지 생성 ─────────────
    with st.status("🖼️ Step 2 — 영수증 이미지 생성", expanded=True) as s2:
        time.sleep(STEP_DELAY)
        receipt_img = _make_receipt_image(scenario["receipt"])
        st.image(receipt_img, caption="모의 영수증", width=300)
        time.sleep(STEP_DELAY)
        s2.update(label="✅ Step 2 — 영수증 이미지 준비 완료", state="complete")

    # ── STEP 3: LLM 분석 ───────────────────────
    with st.status("🤖 Step 3 — LLM 영수증 분석 (Ollama LLaVA 시뮬레이션)", expanded=True) as s3:
        time.sleep(STEP_DELAY)
        st.write("🔍 영수증 이미지 인코딩 → Ollama 전송...")
        time.sleep(STEP_DELAY * 1.5)
        st.write("💬 LLM 응답 수신 중...")
        time.sleep(STEP_DELAY * 1.5)

        llm_data = scenario["llm"]
        rec = scenario["receipt"]

        llm_result = LLMResult(
            merchant_name=rec["merchant_name"],
            date=rec["date"],
            amount=rec["amount"],
            items=rec["items"],
            category=rec["category"],
            matches_purpose=llm_data["matches_purpose"],
            confidence=llm_data["confidence"],
            mismatches=llm_data["mismatches"],
            reason=llm_data["reason"],
        )

        match_icon = "✅" if llm_result.matches_purpose else "❌"
        conf_color = (
            "green" if llm_result.confidence >= approve_th
            else "red" if llm_result.confidence <= reject_th
            else "orange"
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("상호명", llm_result.merchant_name)
        c2.metric("금액", f"{llm_result.amount:,}원")
        c3.metric("카테고리", llm_result.category)

        st.markdown(f"목적 일치: **{match_icon} {'일치' if llm_result.matches_purpose else '불일치'}**")
        st.markdown(f"신뢰도: :{conf_color}[**{llm_result.confidence:.0%}**]")
        if llm_result.mismatches:
            st.warning("불일치 사항: " + " / ".join(llm_result.mismatches))

        log_record["analysis"] = llm_result.to_dict()
        time.sleep(STEP_DELAY)
        s3.update(label="✅ Step 3 — LLM 분석 완료", state="complete")

    # ── STEP 4: 승인 판단 ──────────────────────
    with st.status("⚖️ Step 4 — 승인/반려 판단", expanded=True) as s4:
        time.sleep(STEP_DELAY)
        st.write(f"승인 임계값: **≥ {approve_th:.0%}** / 반려 임계값: **≤ {reject_th:.0%}**")
        time.sleep(STEP_DELAY)

        approval: ApprovalResult = engine.evaluate(llm_result, scenario["mail"]["purpose"])

        decision_style = {
            "승인":     ("✅", "green",  "success"),
            "반려":     ("❌", "red",    "error"),
            "수동검토": ("👤", "orange", "warning"),
        }
        icon, color, alert = decision_style.get(approval.decision, ("❓", "gray", "info"))

        st.markdown(f"### :{color}[{icon} 최종 판단: **{approval.decision}**]")
        getattr(st, alert)(f"판단 근거: {approval.reason}")

        log_record["decision"] = approval.decision
        log_record["approval_detail"] = approval.to_dict()
        time.sleep(STEP_DELAY)
        s4.update(label=f"✅ Step 4 — 판단 완료: {icon} {approval.decision}", state="complete")

    # ── STEP 5: 그룹웨어 처리 ──────────────────
    with st.status("🏢 Step 5 — 그룹웨어 결재 처리 (Playwright 시뮬레이션)", expanded=True) as s5:
        time.sleep(STEP_DELAY)
        if approval.decision == "수동검토":
            st.info("수동검토 대상 — 그룹웨어 자동 처리를 건너뜁니다.")
        else:
            st.write("🌐 그룹웨어 접속 중...")
            time.sleep(STEP_DELAY)
            st.write("🔑 로그인 완료")
            time.sleep(STEP_DELAY * 0.8)
            st.write(f"📄 결재 문서 이동: `{scenario['mail']['subject']}`")
            time.sleep(STEP_DELAY * 0.8)
            btn_label = "승인" if approval.decision == "승인" else "반려"
            st.write(f"🖱️ **[{btn_label}]** 버튼 클릭 → 의견 입력 → 처리 완료")
            time.sleep(STEP_DELAY)
            st.write("📸 처리 스크린샷 저장: `logs/screenshots/sim_processed.png`")
            gw_img = _make_groupware_screenshot(scenario, approval)
            st.image(gw_img, caption="그룹웨어 처리 화면 (시뮬레이션)", use_container_width=True)
        log_record["gw_success"] = True
        time.sleep(STEP_DELAY)
        s5.update(label="✅ Step 5 — 그룹웨어 처리 완료", state="complete")

    # ── STEP 6: 결과 메일 발송 ─────────────────
    with st.status("📤 Step 6 — 결과 회신 메일 발송 (SMTP 시뮬레이션)", expanded=True) as s6:
        time.sleep(STEP_DELAY)
        tag = {"승인": "[승인]", "반려": "[반려]", "수동검토": "[수동검토 요청]"}[approval.decision]
        reply_subject = f"{tag} {scenario['mail']['subject']}"
        st.write(f"📧 수신: `{scenario['mail']['sender']}`")
        st.write(f"📝 제목: `{reply_subject}`")
        st.code(
            f"결재 처리 결과: {approval.decision}\n\n"
            f"사유: {approval.reason}\n\n"
            f"상호명: {rec['merchant_name']}\n"
            f"날짜: {rec['date']}\n"
            f"금액: {rec['amount']:,}원\n\n"
            "---\n본 메일은 영수증 자동 검토 시스템에 의해 발송되었습니다.",
            language=None,
        )
        time.sleep(STEP_DELAY)
        s6.update(label="✅ Step 6 — 회신 메일 발송 완료", state="complete")

    # ── STEP 7: 로그 저장 ──────────────────────
    with st.status("💾 Step 7 — 처리 로그 저장", expanded=True) as s7:
        time.sleep(STEP_DELAY)
        _save_simulation_log(log_record)
        st.write(f"📋 저장 완료: `logs/{datetime.now().strftime('%Y%m%d')}_results.json`")
        time.sleep(STEP_DELAY * 0.5)
        s7.update(label="✅ Step 7 — 로그 저장 완료", state="complete")

    # ── 최종 결과 요약 ─────────────────────────
    st.divider()
    st.subheader("📊 시뮬레이션 결과 요약")

    res_cols = st.columns(4)
    res_cols[0].metric("판단 결과", f"{icon} {approval.decision}")
    res_cols[1].metric("신뢰도", f"{llm_result.confidence:.0%}")
    res_cols[2].metric("금액", f"{rec['amount']:,}원")
    res_cols[3].metric("카테고리", llm_result.category)

    decision_color_map = {"승인": "green", "반려": "red", "수동검토": "orange"}
    c = decision_color_map.get(approval.decision, "gray")
    st.markdown(f"#### :{c}[{icon} {approval.decision} — {approval.reason}]")

    st.info("📊 처리 이력은 `streamlit run dashboard.py` 에서 확인할 수 있습니다.")



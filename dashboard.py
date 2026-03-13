"""
dashboard.py — Streamlit 모니터링 대시보드
실행: streamlit run dashboard.py
"""
import json
from datetime import datetime, date
from pathlib import Path

import streamlit as st

LOG_DIR = Path("logs")
SCREENSHOT_DIR = LOG_DIR / "screenshots"

st.set_page_config(
    page_title="영수증 결재 모니터링",
    page_icon="🧾",
    layout="wide",
)

st.title("영수증 자동 결재 검토 시스템")
st.caption("Receipt Auto-Review Dashboard")


# ------------------------------------------------------------------ #
# 로그 로딩
# ------------------------------------------------------------------ #

@st.cache_data(ttl=30)
def load_all_logs() -> list[dict]:
    records = []
    if not LOG_DIR.exists():
        return records
    for log_file in sorted(LOG_DIR.glob("*_results.json"), reverse=True):
        try:
            with open(log_file, encoding="utf-8") as f:
                records.extend(json.load(f))
        except Exception:
            pass
    return records


records = load_all_logs()

# ------------------------------------------------------------------ #
# 상단 지표
# ------------------------------------------------------------------ #

col1, col2, col3, col4 = st.columns(4)
total = len(records)
approved = sum(1 for r in records if r.get("decision") == "승인")
rejected = sum(1 for r in records if r.get("decision") == "반려")
manual = sum(1 for r in records if r.get("decision") == "수동검토")
errors = sum(1 for r in records if r.get("error"))

col1.metric("전체 처리", f"{total}건")
col2.metric("승인", f"{approved}건", delta=None)
col3.metric("반려", f"{rejected}건", delta=None)
col4.metric("수동검토", f"{manual}건", delta=None)

st.divider()

# ------------------------------------------------------------------ #
# 필터
# ------------------------------------------------------------------ #

with st.sidebar:
    st.header("필터")
    decision_filter = st.multiselect(
        "처리 결과",
        ["승인", "반려", "수동검토"],
        default=["승인", "반려", "수동검토"],
    )
    show_errors = st.checkbox("오류 포함", value=True)
    search_subject = st.text_input("제목 검색")

# ------------------------------------------------------------------ #
# 처리 이력 테이블
# ------------------------------------------------------------------ #

st.subheader("처리 이력")

filtered = [
    r for r in records
    if (r.get("decision") in decision_filter or (show_errors and r.get("error")))
    and (not search_subject or search_subject in r.get("subject", ""))
]

if not filtered:
    st.info("조건에 맞는 이력이 없습니다.")
else:
    for r in filtered:
        decision = r.get("decision", "오류")
        error = r.get("error")
        color = {"승인": "green", "반려": "red", "수동검토": "orange"}.get(decision, "gray")

        with st.expander(
            f":{color}[{decision or '오류'}] {r.get('subject', '(제목 없음)')} — {r.get('processed_at', '')[:16]}"
        ):
            col_a, col_b = st.columns(2)

            with col_a:
                st.markdown("**메일 정보**")
                st.write(f"- 발신: {r.get('sender', '')}")
                st.write(f"- 결재 목적: {r.get('purpose', '')}")
                st.write(f"- 수신: {r.get('received_at', '')}")

            with col_b:
                if r.get("approval_detail"):
                    detail = r["approval_detail"]
                    st.markdown("**분석 결과**")
                    st.write(f"- 상호명: {detail.get('merchant_name', '')}")
                    st.write(f"- 날짜: {detail.get('date', '')}")
                    amount = detail.get('amount', 0)
                    st.write(f"- 금액: {amount:,}원" if amount else "- 금액: -")
                    st.write(f"- 카테고리: {detail.get('category', '')}")
                    st.write(f"- 신뢰도: {detail.get('confidence', 0):.0%}")

            if r.get("approval_detail", {}).get("reason"):
                st.info(f"판단 근거: {r['approval_detail']['reason']}")

            if r.get("approval_detail", {}).get("mismatches"):
                st.warning("불일치 사항: " + ", ".join(r["approval_detail"]["mismatches"]))

            if error:
                st.error(f"오류: {error}")

st.divider()

# ------------------------------------------------------------------ #
# 통계 차트
# ------------------------------------------------------------------ #

if records:
    st.subheader("처리 현황")

    import pandas as pd

    # 일별 처리 건수
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["processed_at"], errors="coerce").dt.date
    df["decision"] = df["decision"].fillna("오류")

    daily = df.groupby(["date", "decision"]).size().unstack(fill_value=0).reset_index()
    st.bar_chart(daily.set_index("date"))

# ------------------------------------------------------------------ #
# 스크린샷 뷰어
# ------------------------------------------------------------------ #

st.divider()
st.subheader("그룹웨어 처리 스크린샷")

screenshots = sorted(SCREENSHOT_DIR.glob("*.png"), reverse=True)[:20] if SCREENSHOT_DIR.exists() else []

if screenshots:
    cols = st.columns(3)
    for i, shot in enumerate(screenshots):
        with cols[i % 3]:
            st.image(str(shot), caption=shot.stem, use_container_width=True)
else:
    st.info("저장된 스크린샷이 없습니다.")

# ------------------------------------------------------------------ #
# 새로고침
# ------------------------------------------------------------------ #

if st.button("새로고침"):
    st.cache_data.clear()
    st.rerun()

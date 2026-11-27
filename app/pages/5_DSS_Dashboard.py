"""
app/pages/5_DSS_Dashboard.py
------------------------------
Page E — Decision Support Dashboard (Task 10, Section 7).

Chỉ MANAGER truy cập.
Tích hợp trực tiếp core.dss.run_dss() — không tự viết rule engine.

Quy tắc an toàn:
- Không ghi Decision vào PostgreSQL.
- Không gửi voucher, không thay đổi lịch chiếu hay nhân sự.
- status="PROPOSED" không thay đổi.
- HISTORICAL_ANALYSIS_ONLY phân biệt rõ với OPERATIONAL.
- evidence và limitations không được bỏ qua.
"""

from __future__ import annotations

import sys
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import setup_page, COLOR_PRIMARY, CHART_PALETTE, PRIORITY_COLORS
from app.ui.auth import check_permission, get_current_user
from app.ui.components import (
    section_header, notice, empty_state, summary_line, kpi_card,
    badge, data_table
)
from app.ui.charts import apply_chart_theme
from app.ui.format import format_date, format_decimal
from app.data_access import CINEMA_MAP, get_db_engine

# ---------------------------------------------------------------------------
setup_page("DSS Dashboard — Hỗ Trợ Quyết Định", "💡")

# ---------------------------------------------------------------------------
check_permission(required_role="MANAGER", auto_stop=True)
user = get_current_user()

from app.main import render_sidebar_navigation  # type: ignore
render_sidebar_navigation(user)

# ---------------------------------------------------------------------------
section_header(
    "DSS Recommendations — Hỗ Trợ Quyết Định Vận Hành",
    subtitle="Tích hợp trực tiếp core/dss.py — tất cả Decision có status=PROPOSED",
    icon="💡",
)

# ---------------------------------------------------------------------------
# CẢNH BÁO AN TOÀN DSS
# ---------------------------------------------------------------------------
notice(
    "⚠️ **An toàn DSS:** Mọi Decision đều có `status=PROPOSED`. "
    "Dashboard này chỉ đọc và hiển thị — không ghi dữ liệu, không gửi voucher, "
    "không thay đổi lịch chiếu hay nhân sự. "
    "Người dùng phải tự đánh giá và phê duyệt qua quy trình nội bộ.",
    type="warning",
)

# ---------------------------------------------------------------------------
# BỘ LỌC DSS
# ---------------------------------------------------------------------------
st.markdown("#### Cấu hình DSS")
col_d1, col_d2, col_d3 = st.columns(3)

with col_d1:
    cinema_opts = ["Tất cả cụm rạp"] + list(CINEMA_MAP.values())
    cinema_sel = st.selectbox("Cụm rạp:", cinema_opts, key="dss_cinema")
    dss_cinema_key = None
    dss_cinema_label = "Tất cả cụm rạp"
    if cinema_sel != "Tất cả cụm rạp":
        dss_cinema_key = next((k for k, v in CINEMA_MAP.items() if v == cinema_sel), None)
        dss_cinema_label = cinema_sel

with col_d2:
    dss_mode = st.selectbox(
        "Chế độ đánh giá:",
        ["operational", "historical_audit"],
        format_func=lambda x: "Vận hành (Operational)" if x == "operational" else "Hậu kiểm lịch sử (Historical Audit)",
        key="dss_mode",
        help=(
            "Operational: đánh giá tại ngày tham chiếu hiện tại.\n"
            "Historical Audit: đánh giá lại tại ngày cụ thể trong quá khứ."
        ),
    )

with col_d3:
    # Reference date — phân biệt rõ với bộ lọc KPI
    today = date.today()
    dss_ref_date = st.date_input(
        "Ngày tham chiếu DSS:",
        value=today,
        key="dss_ref_date",
        help=(
            "reference_date dùng để đánh giá hiệu lực DSS — "
            "KHÁC với khoảng ngày lọc KPI/biểu đồ. "
            "Mặc định: hôm nay."
        ),
    )

notice(
    f"**Ngày tham chiếu DSS:** {format_date(dss_ref_date)} | **Chế độ:** {dss_mode} | **Rạp:** {dss_cinema_label}\n\n"
    "⚠️ Ngày tham chiếu DSS KHÁC với bộ lọc ngày xem KPI/biểu đồ. "
    "Không tự dùng ngày cuối bộ lọc KPI làm reference_date DSS.",
    type="info",
)

# ---------------------------------------------------------------------------
# CHẠY DSS
# ---------------------------------------------------------------------------
run_col, _ = st.columns([1, 3])
with run_col:
    run_dss_btn = st.button("🚀 Chạy DSS Engine", type="primary", key="run_dss")

if run_dss_btn or "dss_decisions" not in st.session_state:
    if run_dss_btn:
        # Xóa cache cũ khi chạy lại
        if "dss_decisions" in st.session_state:
            del st.session_state["dss_decisions"]
        if "dss_error" in st.session_state:
            del st.session_state["dss_error"]

    with st.spinner("Đang chạy DSS Engine (đọc PostgreSQL, không ghi dữ liệu)..."):
        try:
            from core.dss import run_dss  # type: ignore
            eng = get_db_engine()
            decisions = run_dss(
                engine=eng,
                cinema_key=dss_cinema_key,
                reference_date=dss_ref_date,
                mode=dss_mode,
            )
            st.session_state["dss_decisions"] = decisions
            st.session_state["dss_error"] = None
        except Exception as e:
            st.session_state["dss_decisions"] = []
            st.session_state["dss_error"] = str(e)

decisions = st.session_state.get("dss_decisions", [])
dss_error  = st.session_state.get("dss_error")

if dss_error:
    st.error(f"❌ Lỗi khi chạy DSS Engine: {dss_error}")
    notice(
        "Kiểm tra kết nối PostgreSQL và cấu hình `etl/db.py`. "
        "Không có dữ liệu giả nào được tạo ra.",
        type="warning",
    )
    st.stop()

if not decisions:
    empty_state(
        "DSS Engine không tạo ra Decision nào với các bộ lọc hiện tại.",
        icon="📭",
    )
    st.stop()

# ---------------------------------------------------------------------------
# 7.1. DECISION SUMMARY
# ---------------------------------------------------------------------------
st.markdown("---")
section_header("7.1. Decision Summary", icon="📊")

total = len(decisions)
by_sev = {s: sum(1 for d in decisions if d.severity == s) for s in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]}
by_cat: dict[str, int] = {}
by_rule: dict[str, int] = {}
mode_operational = 0
mode_historical  = 0
mode_audit       = 0

def _get_decision_mode(d) -> str:
    if isinstance(d.explanation, dict):
        if "decision_mode" in d.explanation:
            return str(d.explanation["decision_mode"])
        if isinstance(d.explanation.get("freshness"), dict) and "decision_mode" in d.explanation["freshness"]:
            return str(d.explanation["freshness"]["decision_mode"])
    return "OPERATIONAL"


for d in decisions:
    by_cat[d.category] = by_cat.get(d.category, 0) + 1
    by_rule[d.rule_id] = by_rule.get(d.rule_id, 0) + 1
    fm = _get_decision_mode(d)
    if fm == "OPERATIONAL":
        mode_operational += 1
    elif fm == "HISTORICAL_ANALYSIS_ONLY":
        mode_historical += 1
    elif fm == "HISTORICAL_AUDIT":
        mode_audit += 1

col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
with col_s1:
    kpi_card(title="Tổng Decision", value=total, unit="")
with col_s2:
    kpi_card(title="OPERATIONAL", value=mode_operational, unit="")
with col_s3:
    kpi_card(title="HISTORICAL", value=mode_historical, unit="")
with col_s4:
    kpi_card(title="AUDIT", value=mode_audit, unit="")
with col_s5:
    kpi_card(title="HIGH + CRITICAL", value=by_sev.get("HIGH", 0) + by_sev.get("CRITICAL", 0), unit="")

# Severity bar
col_sev, col_cat = st.columns(2)
with col_sev:
    sev_data = [(s, by_sev.get(s, 0)) for s in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]]
    fig_sev = go.Figure(go.Bar(
        y=[s[0] for s in sev_data],
        x=[s[1] for s in sev_data],
        orientation="h",
        marker_color=["#DC2626", "#EA580C", "#CA8A04", "#475569"],
        hovertemplate="%{y}: %{x} decisions<extra></extra>",
    ))
    apply_chart_theme(fig_sev, title="Decision theo Severity", height=250, show_legend=False)
    st.plotly_chart(fig_sev, use_container_width=True)

with col_cat:
    fig_cat = go.Figure(go.Bar(
        x=list(by_cat.keys()),
        y=list(by_cat.values()),
        marker_color=CHART_PALETTE[2],
        hovertemplate="%{x}: %{y} decisions<extra></extra>",
    ))
    apply_chart_theme(fig_cat, title="Decision theo Category", height=250, show_legend=False)
    st.plotly_chart(fig_cat, use_container_width=True)

# ---------------------------------------------------------------------------
# 7.4. BỘ LỌC DECISION LIST
# ---------------------------------------------------------------------------
st.markdown("---")
section_header("7.2–7.4. Decision List & Filters", icon="🔍")

col_f1, col_f2, col_f3, col_f4 = st.columns(4)
with col_f1:
    filter_sev = st.multiselect(
        "Severity:", ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        default=["LOW", "MEDIUM", "HIGH", "CRITICAL"], key="dss_filter_sev"
    )
with col_f2:
    all_cats = list(by_cat.keys())
    filter_cat = st.multiselect("Category:", all_cats, default=all_cats, key="dss_filter_cat")
with col_f3:
    all_rules = list(by_rule.keys())
    filter_rule = st.multiselect("Rule ID:", all_rules, default=all_rules, key="dss_filter_rule")
with col_f4:
    filter_mode = st.multiselect(
        "Decision Mode:",
        ["OPERATIONAL", "HISTORICAL_ANALYSIS_ONLY", "HISTORICAL_AUDIT"],
        default=["OPERATIONAL", "HISTORICAL_ANALYSIS_ONLY", "HISTORICAL_AUDIT"],
        key="dss_filter_mode",
    )

# Áp dụng bộ lọc
filtered = [
    d for d in decisions
    if d.severity in filter_sev
    and d.category in filter_cat
    and d.rule_id in filter_rule
    and _get_decision_mode(d) in filter_mode
]

# Chú ý: Decision RFM cấp toàn hệ thống (cinema_key=None) phân biệt với rạp cụ thể
rfm_global = [d for d in filtered if d.cinema_key is None]
cinema_specific = [d for d in filtered if d.cinema_key is not None]

if rfm_global:
    notice(
        f"ℹ️ Có {len(rfm_global)} Decision toàn hệ thống (cinema_key=None) — "
        "bao gồm phân tích RFM không gắn với rạp cụ thể.",
        type="info",
    )

st.caption(f"Hiển thị {len(filtered)}/{total} Decision sau khi lọc.")

# ---------------------------------------------------------------------------
# 7.2. DECISION LIST
# ---------------------------------------------------------------------------
SEVERITY_COLORS: dict[str, str] = {
    "CRITICAL": "🔴",
    "HIGH":     "🟠",
    "MEDIUM":   "🟡",
    "LOW":      "🟢",
}
MODE_LABEL: dict[str, str] = {
    "OPERATIONAL":             "✅ Vận hành",
    "HISTORICAL_ANALYSIS_ONLY":"📂 Lịch sử [HISTORICAL]",
    "HISTORICAL_AUDIT":        "🔍 Hậu kiểm [AUDIT]",
}

for idx, d in enumerate(filtered):
    d_mode = _get_decision_mode(d)
    sev_icon = SEVERITY_COLORS.get(d.severity, "⚪")
    mode_lbl = MODE_LABEL.get(d_mode, d_mode)

    # HISTORICAL_ANALYSIS_ONLY: style khác
    is_historical = d_mode in ("HISTORICAL_ANALYSIS_ONLY", "HISTORICAL_AUDIT")
    border_color = "#CBD5E1" if is_historical else {
        "CRITICAL": "#DC2626", "HIGH": "#EA580C", "MEDIUM": "#CA8A04", "LOW": "#10B981"
    }.get(d.severity, "#64748B")
    bg_color = "#F8FAFC" if is_historical else "#FFFFFF"
    opacity = "0.75" if is_historical else "1"

    with st.expander(
        f"{sev_icon} [{d.rule_id}] {d.title[:80]}{'...' if len(d.title) > 80 else ''} | {mode_lbl}",
        expanded=False,
    ):
        # Thẻ header
        st.markdown(
            f'<div style="border-left:4px solid {border_color};padding:12px;background:{bg_color};'
            f'border-radius:4px;opacity:{opacity};">',
            unsafe_allow_html=True,
        )

        col_i1, col_i2, col_i3 = st.columns(3)
        with col_i1:
            st.markdown(f"**Rule ID:** `{d.rule_id}`")
            st.markdown(f"**Severity:** {sev_icon} `{d.severity}`")
            st.markdown(f"**Status:** `{d.status}`")  # Luôn PROPOSED
        with col_i2:
            cinema_txt = CINEMA_MAP.get(d.cinema_key, "Toàn hệ thống") if d.cinema_key else "Toàn hệ thống (N/A)"
            st.markdown(f"**Rạp:** {cinema_txt}")
            st.markdown(f"**Category:** `{d.category}`")
            st.markdown(f"**Time Scope:** {d.time_scope}")
        with col_i3:
            st.markdown(f"**As of Date:** {d.as_of_date}")
            st.markdown(f"**Decision Mode:** {mode_lbl}")
            st.markdown(f"**Source Model:** `{d.source_model}`")

        st.markdown("</div>", unsafe_allow_html=True)

        # Lịch sử warning nổi bật
        if is_historical:
            st.warning(
                f"⚠️ **Decision này thuộc chế độ {d_mode}** — "
                "không phải cảnh báo cần hành động ngay. "
                "Chỉ dùng cho phân tích hậu kiểm hoặc xem xét lịch sử."
            )

        # Recommendation
        st.markdown(f"**📌 Khuyến nghị:**")
        st.info(d.recommendation)

        # 7.3. Decision Details
        st.markdown("**📖 Giải thích chi tiết:**")
        if isinstance(d.explanation, dict):
            exp_tabs = st.tabs(["What happened", "Why triggered", "Why recommended", "Manager checks", "Freshness"])
            with exp_tabs[0]:
                st.write(d.explanation.get("what_happened", "N/A"))
            with exp_tabs[1]:
                st.write(d.explanation.get("why_triggered", "N/A"))
            with exp_tabs[2]:
                st.write(d.explanation.get("why_recommended", "N/A"))
            with exp_tabs[3]:
                checks = d.explanation.get("manager_checks", [])
                if isinstance(checks, list):
                    for c in checks:
                        st.markdown(f"• {c}")
                else:
                    st.write(checks)
            with exp_tabs[4]:
                freshness = d.explanation.get("freshness", {})
                if freshness:
                    for k, v in freshness.items():
                        st.markdown(f"**{k}:** `{v}`")

        # Evidence
        if d.evidence:
            st.markdown("**🔍 Evidence (Bằng chứng):**")
            try:
                # Hiển thị dạng bảng nếu có thể
                ev_df = pd.DataFrame(d.evidence) if isinstance(d.evidence, list) else None
                if ev_df is not None and not ev_df.empty:
                    st.dataframe(ev_df, use_container_width=True)
                else:
                    st.json(d.evidence)
            except Exception:
                st.json(d.evidence)
        else:
            st.caption("Không có evidence kèm theo.")

        # Limitations
        if d.limitations:
            st.markdown("**⚠️ Limitations (Giới hạn):**")
            for lim in d.limitations:
                st.markdown(f"• {lim}")
        else:
            st.caption("Không có giới hạn được ghi nhận.")

# ---------------------------------------------------------------------------
# DOWNLOAD DSS RESULTS
# ---------------------------------------------------------------------------
st.markdown("---")
if filtered:
    # Xuất CSV tóm tắt (không bao gồm raw evidence/explanation phức tạp)
    rows = []
    for d in filtered:
        rows.append({
            "decision_id":  d.decision_id,
            "rule_id":      d.rule_id,
            "cinema_key":   d.cinema_key,
            "time_scope":   d.time_scope,
            "category":     d.category,
            "severity":     d.severity,
            "status":       d.status,
            "title":        d.title,
            "recommendation": d.recommendation,
            "as_of_date":   d.as_of_date,
            "source_model": d.source_model,
            "decision_mode": _get_decision_mode(d),
        })
    dss_csv_df = pd.DataFrame(rows)
    data_table(dss_csv_df, download_name="dss_decisions.csv", key="dss_export", max_rows=50)

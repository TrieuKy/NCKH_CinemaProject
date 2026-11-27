"""
app/pages/6_What_If_Analysis.py
--------------------------------
Page F — What-If Scenario Simulation (Task 11 — UC07).

Chỉ MANAGER truy cập.
Tích hợp trực tiếp core/what_if.py — không ghi database, không dùng forecast đã hết hạn,
không tuyên bố nhân quả, và tách biệt rõ Historical Baseline vs Hypothetical Scenario.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import setup_page, COLOR_PRIMARY, COLOR_SECONDARY, CHART_PALETTE
from app.ui.auth import check_permission, get_current_user
from app.ui.components import (
    kpi_card,
    section_header,
    notice,
    empty_state,
    summary_line,
    badge,
)
from app.ui.charts import apply_chart_theme
from app.ui.format import (
    format_currency,
    format_percent,
    format_decimal,
    format_date,
)
from app.data_access import (
    CINEMA_MAP,
    enforce_cinema_scope,
    load_data_date_range,
    load_what_if_baseline,
)
from core.what_if import (
    ATTACH_RATE_MAX,
    ATTACH_RATE_MIN,
    PRICE_CHANGE_MAX,
    PRICE_CHANGE_MIN,
    QTY_CHANGE_MAX,
    QTY_CHANGE_MIN,
    BaselineMetrics,
    ScenarioResult,
    compare_scenarios,
    simulate_fnb_attach_rate,
    simulate_ticket_price_change,
    simulate_ticket_volume_change,
)

# ---------------------------------------------------------------------------
setup_page("What-If Simulation — Mô Phỏng Kịch Bản", "🎛️")

# ---------------------------------------------------------------------------
# 1. KIỂM TRA QUYỀN — MANAGER ONLY (UC07)
# ---------------------------------------------------------------------------
check_permission(required_role="MANAGER", auto_stop=True)
user = get_current_user() or {}

from app.main import render_sidebar_navigation  # type: ignore
render_sidebar_navigation(user)

# Cô lập session state theo người dùng hiện tại
current_owner = str(user.get("user_id") or user.get("username") or "")
if st.session_state.get("whatif_owner") != current_owner:
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith("whatif_"):
            del st.session_state[k]
    st.session_state["whatif_owner"] = current_owner

# ---------------------------------------------------------------------------
# 2. HEADER & CẢNH BÁO KHOA HỌC
# ---------------------------------------------------------------------------
section_header(
    "Mô Phỏng Kịch Bản Vận Hành (What-If Analysis — UC07)",
    subtitle="Phân tích giả định có điều kiện trên Historical Baseline — nguồn: PostgreSQL cinema_dw (Read-Only)",
    icon="🎛️",
)

notice(
    "**Nguyên tắc khoa học & An toàn:** Kết quả mô phỏng là **Hypothetical Scenario** (tính toán số học có điều kiện), "
    "**không phải dự báo chắc chắn** và **không phải quan hệ nhân quả**. "
    "Hệ thống không ghi thay đổi xuống PostgreSQL và không sử dụng forecast đã hết hạn.",
    type="info",
)

# ---------------------------------------------------------------------------
# 3. CHỌN PHẠM VI BASELINE (RẠP & KHOẢNG NGÀY)
# ---------------------------------------------------------------------------
min_dt, max_dt = load_data_date_range()

st.markdown("#### A. Baseline Overview — Phạm vi dữ liệu nền")
col_f1, col_f2 = st.columns([1, 2])

with col_f1:
    cinema_options = ["Tất cả cụm rạp"] + list(CINEMA_MAP.values())
    cinema_sel = st.selectbox("Cụm rạp:", cinema_options, key="whatif_cinema_sel")
    requested_ck: Optional[int] = None
    if cinema_sel != "Tất cả cụm rạp":
        requested_ck = next((k for k, v in CINEMA_MAP.items() if v == cinema_sel), None)

    # Kiểm tra phạm vi quyền rạp fail-closed
    try:
        cinema_key = enforce_cinema_scope(
            user_role=str(user.get("role", "")),
            assigned_cinema_key=user.get("cinema_key"),
            requested_cinema_key=requested_ck,
        )
    except (PermissionError, ValueError) as exc:
        st.error(f"⛔ {exc}")
        st.stop()

with col_f2:
    default_start = date(2026, 9, 1)
    default_end = max_dt or date(2026, 9, 30)
    if min_dt and default_start < min_dt:
        default_start = min_dt
    if default_start > default_end:
        default_start = default_end

    preset_opts = [
        "Tháng 9/2026 (Kỳ mới nhất)",
        "Quý 3/2026",
        "Toàn bộ dữ liệu lịch sử",
        "Tùy chọn...",
    ]
    preset = st.selectbox("Khoảng thời gian nền:", preset_opts, key="whatif_date_preset")
    if preset == "Tháng 9/2026 (Kỳ mới nhất)":
        start_d, end_d = date(2026, 9, 1), date(2026, 9, 30)
    elif preset == "Quý 3/2026":
        start_d, end_d = date(2026, 7, 1), date(2026, 9, 30)
    elif preset == "Toàn bộ dữ liệu lịch sử":
        start_d, end_d = (min_dt or date(2025, 10, 1)), (max_dt or date(2026, 9, 30))
    else:
        cr = st.date_input(
            "Chọn ngày bắt đầu - kết thúc:",
            value=(default_start, default_end),
            key="whatif_custom_dates",
        )
        if isinstance(cr, (tuple, list)) and len(cr) == 2:
            start_d, end_d = cr[0], cr[1]
        else:
            start_d = end_d = default_end

# ---------------------------------------------------------------------------
# 4. TẢI BASELINE TỪ POSTGRESQL
# ---------------------------------------------------------------------------
with st.spinner("Đang tải dữ liệu nền (Historical Baseline) từ PostgreSQL..."):
    res_base = load_what_if_baseline(cinema_key, start_d, end_d)

status_base = res_base.get("status")
if status_base == "ERROR":
    st.error(f"❌ {res_base.get('error', 'Không thể kết nối PostgreSQL.')}")
    st.stop()
if status_base == "INVALID":
    st.warning(f"⚠️ Tham số phạm vi không hợp lệ: {res_base.get('error')}")
    st.stop()
if status_base == "NO_DATA" or res_base.get("baseline") is None:
    empty_state(
        f"Không có dữ liệu baseline trong khoảng {format_date(start_d)} → {format_date(end_d)} "
        f"cho {cinema_sel}.",
        icon="📭",
    )
    st.stop()

baseline: BaselineMetrics = res_base["baseline"]

# Kiểm tra thay đổi baseline để vô hiệu các scenario đã lưu không còn khớp phạm vi
prev_fp = st.session_state.get("whatif_baseline_fp")
if prev_fp is not None and prev_fp != baseline.fingerprint:
    if st.session_state.get("whatif_scenarios"):
        st.session_state["whatif_scenarios"] = []
        st.warning(
            "⚠️ Phạm vi rạp hoặc ngày đã thay đổi — danh sách kịch bản so sánh trước đó đã được làm mới "
            "để đảm bảo chỉ so sánh trên cùng một Baseline."
        )
st.session_state["whatif_baseline_fp"] = baseline.fingerprint

if "whatif_scenarios" not in st.session_state:
    st.session_state["whatif_scenarios"] = []

# Hiển thị thông tin Baseline Overview
max_obs_str = format_date(baseline.max_observed_date) if baseline.max_observed_date else "N/A"
st.markdown(
    f"{badge('HISTORICAL BASELINE', level='info')} "
    f"&nbsp; **Phạm vi:** {baseline.cinema_name} | "
    f"**Kỳ dữ liệu:** {format_date(baseline.start_date)} → {format_date(baseline.end_date)} "
    f"({baseline.data_days} ngày có giao dịch) | "
    f"**Ngày dữ liệu cuối:** {max_obs_str}",
    unsafe_allow_html=True,
)

b_cols = st.columns(6)
with b_cols[0]:
    kpi_card("Doanh thu vé (Clean)", format_currency(baseline.ticket_revenue_clean, compact=True))
with b_cols[1]:
    kpi_card("Số vé bán (Clean)", format_decimal(baseline.ticket_qty_clean, decimals=0))
with b_cols[2]:
    kpi_card("ATP (Clean)", format_currency(baseline.atp_clean) if baseline.atp_clean is not None else "N/A")
with b_cols[3]:
    kpi_card("Doanh thu F&B (Clean)", format_currency(baseline.fnb_revenue_clean, compact=True))
with b_cols[4]:
    kpi_card(
        "Weighted Occupancy",
        format_percent(baseline.weighted_occupancy) if baseline.weighted_occupancy is not None else "N/A",
    )
with b_cols[5]:
    kpi_card(
        "F&B Attach Rate (Raw)",
        format_percent(baseline.attach_rate) if baseline.attach_rate is not None else "N/A",
    )

for w in baseline.warnings:
    st.warning(f"⚠️ {w}")

st.markdown("---")

# ---------------------------------------------------------------------------
# 5. B. SCENARIO CONFIGURATION
# ---------------------------------------------------------------------------
st.markdown("#### B. Cấu hình kịch bản mô phỏng (Scenario Configuration)")

scenario_choice = st.radio(
    "Chọn loại kịch bản:",
    [
        "Scenario A — Thay đổi Giá vé bình quân (Ticket Price)",
        "Scenario B — Thay đổi Lượng vé & Sức chứa (Ticket Volume & Capacity)",
        "Scenario C — Thay đổi Tỷ lệ mua kèm F&B (F&B Attach Rate)",
    ],
    key="whatif_scenario_type",
)

col_cfg1, col_cfg2 = st.columns([2, 1])

current_result: Optional[ScenarioResult] = None

if scenario_choice.startswith("Scenario A"):
    with col_cfg1:
        price_pct_ui = st.slider(
            "Tỷ lệ điều chỉnh giá vé (%):",
            min_value=int(PRICE_CHANGE_MIN * 100),
            max_value=int(PRICE_CHANGE_MAX * 100),
            value=5,
            step=1,
            key="whatif_price_pct",
            help="Áp dụng tỷ lệ thay đổi trên ATP Clean của phạm vi đang chọn.",
        )
    with col_cfg2:
        custom_name = st.text_input(
            "Tên kịch bản (tùy chọn):",
            value=f"Giá vé {price_pct_ui:+d}%",
            key="whatif_name_a",
        )
    notice(
        "**Giả định giữ nguyên lượng vé bán; chưa mô hình hóa phản ứng nhu cầu của khách hàng đối với thay đổi giá.**",
        type="warning",
    )
    current_result = simulate_ticket_price_change(
        baseline,
        price_change_pct=price_pct_ui / 100.0,
        scenario_name=custom_name.strip() or None,
    )

elif scenario_choice.startswith("Scenario B"):
    with col_cfg1:
        qty_pct_ui = st.slider(
            "Tỷ lệ thay đổi lượng vé bán (%):",
            min_value=int(QTY_CHANGE_MIN * 100),
            max_value=int(QTY_CHANGE_MAX * 100),
            value=10,
            step=1,
            key="whatif_qty_pct",
            help="Số vé được làm tròn thành số nguyên (ROUND_HALF_UP) và đối chiếu với tổng sức chứa suất chiếu quan sát được.",
        )
    with col_cfg2:
        custom_name = st.text_input(
            "Tên kịch bản (tùy chọn):",
            value=f"Lượng vé {qty_pct_ui:+d}%",
            key="whatif_name_b",
        )
    notice(
        "Kiểm soát sức chứa ở grain suất chiếu: sử dụng tổng ghế quan sát được "
        f"({format_decimal(baseline.available_showtime_capacity, 0) if baseline.available_showtime_capacity else 'N/A'} ghế). "
        "Không tự giả định mở thêm suất chiếu mới.",
        type="info",
    )
    current_result = simulate_ticket_volume_change(
        baseline,
        qty_change_pct=qty_pct_ui / 100.0,
        scenario_name=custom_name.strip() or None,
    )

else:
    default_attach_pct = int(round((baseline.attach_rate or 0.5) * 100))
    with col_cfg1:
        attach_pct_ui = st.slider(
            "Tỷ lệ giao dịch mua kèm F&B mục tiêu (%):",
            min_value=int(ATTACH_RATE_MIN * 100),
            max_value=int(ATTACH_RATE_MAX * 100),
            value=min(100, default_attach_pct + 5),
            step=1,
            key="whatif_attach_pct",
            help="Attach Rate = fnb_txn_count / ticket_txn_count (Raw). Giữ nguyên số giao dịch vé và giá trị giỏ F&B trung bình.",
        )
    with col_cfg2:
        custom_name = st.text_input(
            "Tên kịch bản (tùy chọn):",
            value=f"F&B Attach Rate {attach_pct_ui}%",
            key="whatif_name_c",
        )
    notice(
        "Mô hình kết hợp (Hybrid): Attach Rate dùng số giao dịch Raw, doanh thu F&B dùng Clean. "
        "`spp_clean` là Spend Per Ticket Transaction, KHÔNG phải Spend Per Patron.",
        type="info",
    )
    current_result = simulate_fnb_attach_rate(
        baseline,
        scenario_attach_rate=attach_pct_ui / 100.0,
        scenario_name=custom_name.strip() or None,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# 6. C. SCENARIO RESULTS
# ---------------------------------------------------------------------------
st.markdown("#### C. Kết quả mô phỏng (Hypothetical Scenario Results)")

if current_result is not None:
    status_badge_level = (
        "success"
        if current_result.validation_status == "VALID"
        else ("warning" if current_result.validation_status == "CAPACITY_EXCEEDED" else "error")
    )
    st.markdown(
        f"{badge('HYPOTHETICAL SCENARIO', level='warning')} &nbsp; "
        f"{badge(current_result.validation_status, level=status_badge_level)} &nbsp; "
        f"**Mã kịch bản:** `{current_result.scenario_id}` — **{current_result.scenario_name}**",
        unsafe_allow_html=True,
    )

    for w in current_result.warnings:
        st.warning(f"⚠️ {w}")

    if current_result.validation_status == "INSUFFICIENT_DATA":
        empty_state("Không đủ dữ liệu nền để thực hiện phép tính cho kịch bản này.", icon="⚠️")
    else:
        sm = current_result.scenario_metrics
        ad = current_result.absolute_deltas
        pd_deltas = current_result.percentage_deltas

        base_total = baseline.ticket_revenue_clean + baseline.fnb_revenue_clean
        sc_total = float(sm.get("total_revenue_clean", base_total))
        total_delta = sc_total - base_total
        total_delta_pct = (total_delta / base_total) if base_total > 0 else 0.0

        r_cols = st.columns(4)
        with r_cols[0]:
            kpi_card(
                "Doanh thu vé mô phỏng",
                format_currency(sm.get("ticket_revenue_clean"), compact=True),
                delta=f"{format_currency(ad.get('ticket_revenue_delta', 0), compact=True)} ({format_percent(pd_deltas.get('ticket_revenue_delta_pct', 0))})",
            )
        with r_cols[1]:
            kpi_card(
                "Doanh thu F&B mô phỏng",
                format_currency(sm.get("fnb_revenue_clean"), compact=True),
                delta=f"{format_currency(ad.get('fnb_revenue_delta', 0), compact=True)} ({format_percent(pd_deltas.get('fnb_revenue_delta_pct', 0))})",
            )
        with r_cols[2]:
            kpi_card(
                "Tổng doanh thu giả định",
                format_currency(sc_total, compact=True),
                delta=f"{format_currency(total_delta, compact=True)} ({format_percent(total_delta_pct)})",
            )
        with r_cols[3]:
            if current_result.scenario_type == "TICKET_VOLUME":
                occ_val = sm.get("occupancy")
                kpi_card(
                    "Occupancy mô phỏng",
                    format_percent(occ_val) if occ_val is not None else "N/A",
                    delta=f"Không đáp ứng: {format_decimal(sm.get('unmet_demand_tickets', 0), 0)} vé"
                    if sm.get("unmet_demand_tickets", 0) > 0
                    else "Trong giới hạn sức chứa",
                )
            elif current_result.scenario_type == "TICKET_PRICE":
                kpi_card(
                    "ATP mô phỏng",
                    format_currency(sm.get("atp_clean")),
                    delta=f"{format_currency(ad.get('atp_delta', 0))} ({format_percent(pd_deltas.get('atp_delta_pct', 0))})",
                )
            else:
                kpi_card(
                    "Attach Rate mô phỏng",
                    format_percent(sm.get("attach_rate")),
                    delta=f"{ad.get('attach_rate_delta_pp', 0):+.1f} điểm %",
                )

        # Biểu đồ so sánh Baseline vs Scenario
        fig = go.Figure(
            data=[
                go.Bar(
                    name="Historical Baseline",
                    x=["Doanh thu vé", "Doanh thu F&B", "Tổng doanh thu"],
                    y=[baseline.ticket_revenue_clean, baseline.fnb_revenue_clean, base_total],
                    marker_color="#64748B",
                ),
                go.Bar(
                    name=f"Hypothetical: {current_result.scenario_name}",
                    x=["Doanh thu vé", "Doanh thu F&B", "Tổng doanh thu"],
                    y=[
                        float(sm.get("ticket_revenue_clean", baseline.ticket_revenue_clean)),
                        float(sm.get("fnb_revenue_clean", baseline.fnb_revenue_clean)),
                        sc_total,
                    ],
                    marker_color=COLOR_PRIMARY,
                ),
            ]
        )
        fig.update_layout(
            barmode="group",
            title="So sánh Doanh thu: Historical Baseline vs Hypothetical Scenario",
            yaxis_title="VND",
            height=360,
        )
        apply_chart_theme(fig)
        st.plotly_chart(fig, use_container_width=True)

        # Bảng giải thích phép tính
        calc_rows = [
            {
                "Chỉ số": "Doanh thu vé (Clean)",
                "Baseline": format_currency(baseline.ticket_revenue_clean),
                "Mô phỏng": format_currency(sm.get("ticket_revenue_clean")),
                "Chênh lệch (VND)": format_currency(ad.get("ticket_revenue_delta", 0)),
                "Chênh lệch (%)": format_percent(pd_deltas.get("ticket_revenue_delta_pct", 0)),
            },
            {
                "Chỉ số": "Doanh thu F&B (Clean)",
                "Baseline": format_currency(baseline.fnb_revenue_clean),
                "Mô phỏng": format_currency(sm.get("fnb_revenue_clean")),
                "Chênh lệch (VND)": format_currency(ad.get("fnb_revenue_delta", 0)),
                "Chênh lệch (%)": format_percent(pd_deltas.get("fnb_revenue_delta_pct", 0)),
            },
            {
                "Chỉ số": "Số vé bán (Clean)",
                "Baseline": format_decimal(baseline.ticket_qty_clean, 0),
                "Mô phỏng": format_decimal(sm.get("ticket_qty_clean"), 0),
                "Chênh lệch (VND)": format_decimal(ad.get("ticket_qty_delta", 0), 0),
                "Chênh lệch (%)": format_percent(pd_deltas.get("ticket_qty_delta_pct", 0)),
            },
        ]
        st.dataframe(pd.DataFrame(calc_rows), use_container_width=True, hide_index=True)

        # Nút lưu vào session để so sánh
        btn_col1, btn_col2, _ = st.columns([1.2, 1.2, 2])
        with btn_col1:
            if st.button("💾 Lưu kịch bản để so sánh", type="primary", key="whatif_save_btn"):
                saved_list: list[ScenarioResult] = st.session_state.get("whatif_scenarios", [])
                if any(s.scenario_id == current_result.scenario_id for s in saved_list):
                    st.info("Kịch bản này đã có trong danh sách so sánh.")
                else:
                    saved_list.append(current_result)
                    st.session_state["whatif_scenarios"] = saved_list
                    st.success(f"Đã lưu '{current_result.scenario_name}' ({len(saved_list)} kịch bản).")
        with btn_col2:
            if st.button("🗑️ Xóa danh sách so sánh", key="whatif_clear_btn"):
                st.session_state["whatif_scenarios"] = []
                st.rerun()

st.markdown("---")

# ---------------------------------------------------------------------------
# 7. D. SCENARIO COMPARISON
# ---------------------------------------------------------------------------
st.markdown("#### D. So sánh các kịch bản (Scenario Comparison)")
saved_scenarios: list[ScenarioResult] = st.session_state.get("whatif_scenarios", [])

if not saved_scenarios:
    notice(
        "Chưa có kịch bản nào được lưu. Hãy cấu hình và bấm **💾 Lưu kịch bản để so sánh** "
        "(khuyến nghị lưu tối thiểu 3 kịch bản A, B, C).",
        type="info",
    )
else:
    try:
        comp_rows = compare_scenarios(baseline, saved_scenarios)
        comp_df = pd.DataFrame(comp_rows)

        display_df = pd.DataFrame({
            "Mã": comp_df["scenario_id"],
            "Tên kịch bản": comp_df["scenario_name"],
            "Loại": comp_df["scenario_type"],
            "Doanh thu vé": comp_df["ticket_revenue_clean"].map(lambda v: format_currency(v)),
            "Doanh thu F&B": comp_df["fnb_revenue_clean"].map(lambda v: format_currency(v)),
            "Tổng doanh thu giả định": comp_df["total_revenue_clean"].map(lambda v: format_currency(v)),
            "Chênh lệch (VND)": comp_df["revenue_delta_vnd"].map(lambda v: format_currency(v)),
            "Chênh lệch (%)": comp_df["revenue_delta_pct"].map(lambda v: format_percent(v)),
            "Số vé": comp_df["ticket_qty_clean"].map(lambda v: format_decimal(v, 0)),
            "Occupancy": comp_df["occupancy"].map(lambda v: format_percent(v) if pd.notna(v) else "N/A"),
            "Attach Rate": comp_df["attach_rate"].map(lambda v: format_percent(v) if pd.notna(v) else "N/A"),
            "Trạng thái": comp_df["validation_status"],
        })
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        # Biểu đồ so sánh chênh lệch doanh thu
        sc_only = comp_df[comp_df["scenario_type"] != "BASELINE"]
        if not sc_only.empty:
            fig_cmp = go.Figure(
                data=[
                    go.Bar(
                        x=sc_only["scenario_name"],
                        y=sc_only["revenue_delta_vnd"],
                        marker_color=[
                            COLOR_PRIMARY if v >= 0 else "#EF4444"
                            for v in sc_only["revenue_delta_vnd"]
                        ],
                    )
                ]
            )
            fig_cmp.update_layout(
                title="Chênh lệch doanh thu giả định so với Baseline (VND)",
                yaxis_title="Chênh lệch (VND)",
                height=320,
            )
            apply_chart_theme(fig_cmp)
            st.plotly_chart(fig_cmp, use_container_width=True)

        # Xuất CSV (tuyệt đối không chứa thông tin tài khoản người dùng)
        export_cols = [
            "scenario_id", "scenario_name", "scenario_type",
            "ticket_revenue_clean", "fnb_revenue_clean", "total_revenue_clean",
            "revenue_delta_vnd", "revenue_delta_pct",
            "ticket_qty_clean", "occupancy", "attach_rate", "validation_status",
        ]
        csv_bytes = comp_df[export_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 Tải CSV kết quả so sánh",
            data=csv_bytes,
            file_name=f"what_if_comparison_{baseline.start_date}_{baseline.end_date}.csv",
            mime="text/csv",
            key="whatif_export_csv",
        )

        notice(
            "**Quy tắc so sánh:** Các kịch bản trên được tính trên cùng một Baseline. "
            "Không tự cộng dồn tác động của các kịch bản đơn biến như thể chúng độc lập về mặt kinh tế; "
            "không xếp hạng kịch bản 'tốt nhất' chỉ dựa trên doanh thu giả định.",
            type="warning",
        )
    except ValueError as exc:
        st.error(f"⚠️ {exc}")

st.markdown("---")

# ---------------------------------------------------------------------------
# 8. E. EVIDENCE & LIMITATIONS
# ---------------------------------------------------------------------------
with st.expander("📚 E. Bằng chứng dữ liệu, Công thức & Giới hạn khoa học", expanded=False):
    st.markdown(f"**Nguồn dữ liệu nền:** `{baseline.data_source}`")
    st.markdown(f"**Phân loại dữ liệu nền:** `{baseline.data_freshness}` (Ngày cuối ghi nhận: {max_obs_str})")
    st.markdown("**Công thức chuẩn hóa (khớp `docs/kpi_dictionary.md`):**")
    st.markdown(
        "- **Scenario A (Giá vé):** `Scenario ATP = Baseline ATP × (1 + price_change_pct)`; "
        "`Scenario Ticket Revenue = Baseline Ticket Qty Clean × Scenario ATP`.\n"
        "- **Scenario B (Lượng vé & Sức chứa):** `Scenario Ticket Qty = round_half_up(Baseline Ticket Qty × (1 + qty_change_pct))`; "
        "`Scenario Occupancy = Scenario Ticket Qty / Observed Showtime Capacity`.\n"
        "- **Scenario C (F&B Attach Rate):** `Scenario F&B Txns = ticket_txn_count × scenario_attach_rate`; "
        "`Scenario F&B Revenue = Scenario F&B Txns × (fnb_revenue_clean / fnb_txn_count)`."
    )
    st.markdown("**Giới hạn khoa học bắt buộc:**")
    for lim in (current_result.limitations if current_result else baseline.limitations):
        st.markdown(f"- {lim}")

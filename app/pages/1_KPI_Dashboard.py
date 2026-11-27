"""
app/pages/1_KPI_Dashboard.py
-----------------------------
Page A — Executive Overview & KPI Dashboard (Task 10, Section 3).

Chỉ MANAGER truy cập trang này.
Dữ liệu lấy trực tiếp từ PostgreSQL cinema_dw qua Data Access Layer.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import setup_page, COLOR_PRIMARY, COLOR_SECONDARY
from app.ui.auth import check_permission, get_current_user
from app.ui.components import (
    kpi_card, section_header, notice, empty_state, summary_line, badge, data_table
)
from app.ui.charts import apply_chart_theme, plot_occupancy_heatmap
from app.ui.format import format_currency, format_percent, format_decimal, format_date
from app.data_access import (
    load_daily_kpi, load_kpi_summary, load_occupancy_summary,
    load_fnb_attach_summary, load_anomaly_summary, load_data_date_range,
)

# ---------------------------------------------------------------------------
setup_page("KPI Dashboard — Tổng Quan Vận Hành", "📊")

# ---------------------------------------------------------------------------
# 1. KIỂM TRA QUYỀN — MANAGER ONLY
# ---------------------------------------------------------------------------
check_permission(required_role="MANAGER", auto_stop=True)
user = get_current_user()

# ---------------------------------------------------------------------------
# 2. SIDEBAR NAVIGATION
# ---------------------------------------------------------------------------
from app.main import render_sidebar_navigation, render_standard_filters, CINEMA_MAP  # type: ignore
render_sidebar_navigation(user)

# ---------------------------------------------------------------------------
# 3. HEADER
# ---------------------------------------------------------------------------
section_header(
    "Tổng Quan KPI Vận Hành",
    subtitle="Doanh thu, Số vé, ATP, Occupancy và F&B — nguồn: PostgreSQL cinema_dw",
    icon="📊",
)

# ---------------------------------------------------------------------------
# 4. NGÀY DỮ LIỆU THỰC TẾ
# ---------------------------------------------------------------------------
min_dt, max_dt = load_data_date_range()
if max_dt:
    notice(
        f"📅 Dữ liệu giao dịch có trong hệ thống từ **{format_date(min_dt)}** đến **{format_date(max_dt)}**. "
        f"Nếu chọn khoảng ngày ngoài phạm vi này sẽ không có dữ liệu.",
        type="info",
    )
else:
    notice("⚠️ Không thể xác định phạm vi dữ liệu — PostgreSQL chưa kết nối.", type="warning")

# ---------------------------------------------------------------------------
# 5. BỘ LỌC CHUẨN
# ---------------------------------------------------------------------------
st.markdown("#### Bộ lọc")
col_q1, col_q2 = st.columns([2, 1])
with col_q1:
    # Quick presets tương thích với dữ liệu thực
    if max_dt:
        preset_opts = [
            "Toàn bộ dữ liệu",
            "Kỳ dữ liệu mới nhất (tháng 9/2026)",
            "Quý 3/2026",
            "Tùy chọn...",
        ]
        preset = st.selectbox("Khoảng thời gian nhanh:", preset_opts, key="kpi_preset")
        if preset == "Toàn bộ dữ liệu":
            start_d, end_d = min_dt or date(2026, 1, 1), max_dt or date(2026, 9, 30)
        elif "tháng 9" in preset:
            start_d, end_d = date(2026, 9, 1), date(2026, 9, 30)
        elif "Quý 3" in preset:
            start_d, end_d = date(2026, 7, 1), date(2026, 9, 30)
        else:
            cr = st.date_input(
                "Chọn khoảng ngày:",
                value=(date(2026, 9, 1), date(2026, 9, 30)),
                key="kpi_custom_range",
            )
            if isinstance(cr, (tuple, list)) and len(cr) == 2:
                start_d, end_d = cr
            else:
                start_d = end_d = date(2026, 9, 30)
    else:
        start_d, end_d = date(2026, 9, 1), date(2026, 9, 30)
        st.warning("Không thể tải ngày dữ liệu — dùng mặc định tháng 9/2026.")

with col_q2:
    cinema_opts = ["Tất cả cụm rạp"] + list(CINEMA_MAP.values())
    cinema_sel = st.selectbox("Cụm rạp:", cinema_opts, key="kpi_cinema")
    if cinema_sel == "Tất cả cụm rạp":
        cinema_key = None
        cinema_label = "Tất cả cụm rạp"
    else:
        cinema_key = next((k for k, v in CINEMA_MAP.items() if v == cinema_sel), None)
        cinema_label = cinema_sel

st.caption(f"**Áp dụng bộ lọc:** {cinema_label} | {format_date(start_d)} → {format_date(end_d)}")

# ---------------------------------------------------------------------------
# 6. LOAD DỮ LIỆU
# ---------------------------------------------------------------------------
with st.spinner("Đang tải dữ liệu KPI từ PostgreSQL..."):
    kpi_summary = load_kpi_summary(cinema_key, start_d, end_d)
    daily_df    = load_daily_kpi(cinema_key, start_d, end_d)
    occ_df      = load_occupancy_summary(cinema_key, start_d, end_d)
    fnb_df      = load_fnb_attach_summary(cinema_key, start_d, end_d)
    anom_sum    = load_anomaly_summary(cinema_key, start_d, end_d)

# Kiểm tra empty state
if not kpi_summary or kpi_summary.get("data_days", 0) == 0:
    empty_state(
        f"Không có dữ liệu trong khoảng {format_date(start_d)} → {format_date(end_d)}.\n"
        f"Dữ liệu có sẵn từ {format_date(min_dt)} đến {format_date(max_dt)}.",
        icon="📭",
    )
    st.stop()

# ---------------------------------------------------------------------------
# 7. KPI CARDS
# ---------------------------------------------------------------------------
section_header("Chỉ Số KPI Tổng Hợp", icon="🎯")
summary_line([
    ("Số ngày có dữ liệu", f"{kpi_summary.get('data_days', 0)} ngày"),
    ("Khoảng lọc", f"{format_date(start_d)} → {format_date(end_d)}"),
    ("Rạp", cinema_label),
])

c1, c2, c3 = st.columns(3)
with c1:
    kpi_card(
        title="Doanh thu vé (Clean)",
        value=kpi_summary.get("ticket_revenue_clean", 0),
        kpi_key="revenue",
        compact_currency=True,
        help_text="SUM(ticket_revenue_clean) — chỉ bao gồm giao dịch không bất thường (is_anomaly=0).",
    )
with c2:
    kpi_card(
        title="Doanh thu F&B (Clean)",
        value=kpi_summary.get("fnb_revenue_clean", 0),
        kpi_key="revenue",
        compact_currency=True,
        help_text="SUM(fnb_revenue_clean) — doanh thu bắp nước sạch (is_anomaly=0).",
    )
with c3:
    total_rev = kpi_summary.get("ticket_revenue_clean", 0) + kpi_summary.get("fnb_revenue_clean", 0)
    kpi_card(
        title="Tổng doanh thu (Vé + F&B)",
        value=total_rev,
        compact_currency=True,
        help_text="Ticket Revenue Clean + F&B Revenue Clean. Không tổng hợp từ bảng khác để tránh double counting.",
    )

c4, c5, c6 = st.columns(3)
with c4:
    kpi_card(
        title="Tổng vé sạch",
        value=kpi_summary.get("ticket_qty_clean", 0),
        kpi_key="tickets",
        unit="vé",
        help_text="SUM(ticket_qty_clean) — số vé không bất thường.",
    )
with c5:
    atp = kpi_summary.get("atp_clean")
    kpi_card(
        title="Giá vé trung bình (ATP Clean)",
        value=atp,
        kpi_key="atp",
        help_text="ticket_revenue_clean / ticket_qty_clean. Chỉ trên dữ liệu Clean.",
    )
with c6:
    anom_pct = anom_sum.get("anomaly_pct", 0) if anom_sum else 0
    kpi_card(
        title="Tỷ lệ suất bất thường",
        value=anom_pct / 100 if anom_pct else 0,
        kpi_key="anomaly_count",
        unit="%",
        help_text=(
            f"Suất có giao dịch bất thường / Tổng suất = "
            f"{anom_sum.get('anomaly_showtimes',0):,}/{anom_sum.get('total_showtimes',0):,}. "
            "Nguồn: vw_anomaly_showtime."
        ),
        reverse_delta_color=True,
    )

# ---------------------------------------------------------------------------
# 8. ATTACH RATE & WEIGHTED OCCUPANCY (derived from views)
# ---------------------------------------------------------------------------
c7, c8 = st.columns(2)
with c7:
    # Attach rate tổng hợp từ fnb_df
    if not fnb_df.empty:
        total_ticket_txn = fnb_df["ticket_txn_count"].sum()
        total_fnb_txn    = fnb_df["fnb_txn_count"].sum()
        attach_rate = total_fnb_txn / total_ticket_txn if total_ticket_txn > 0 else 0
        kpi_card(
            title="F&B Attach Rate (Raw)",
            value=attach_rate,
            kpi_key="fnb_attach_rate",
            help_text=(
                "Attach Rate = fnb_txn_count / ticket_txn_count. Nhóm Raw — phân mẫu số là tổng giao dịch vé, "
                "không lọc is_anomaly (theo KPI Dictionary: Attach Rate thuộc nhóm Raw)."
            ),
        )
    else:
        kpi_card(title="F&B Attach Rate", value=None, unit="%")

with c8:
    # Weighted occupancy từ occ_df
    if not occ_df.empty:
        total_qty_clean  = occ_df["total_qty_clean"].sum()
        total_cap        = occ_df["total_capacity"].sum()
        weighted_occ = total_qty_clean / total_cap if total_cap > 0 else 0
        kpi_card(
            title="Weighted Occupancy",
            value=weighted_occ,
            kpi_key="occupancy",
            help_text=(
                "SUM(qty_clean) / SUM(capacity) — gia quyền theo sức chứa phòng. "
                "Denominator là tổng capacity của tất cả suất chiếu, không phải số phòng."
            ),
        )
    else:
        kpi_card(title="Weighted Occupancy", value=None, unit="%")

# ---------------------------------------------------------------------------
# 9. BIỂU ĐỒ DOANH THU THEO NGÀY
# ---------------------------------------------------------------------------
st.markdown("---")
section_header("Xu Hướng Doanh Thu Theo Ngày", icon="📈")

if not daily_df.empty:
    # Pivot theo cinema
    fig_rev = go.Figure()
    for ck, grp in daily_df.groupby("cinema_key"):
        cname = grp["cinema_name"].iloc[0]
        fig_rev.add_trace(go.Scatter(
            x=grp["full_date"],
            y=grp["ticket_revenue_clean"],
            name=f"Vé — {cname}",
            mode="lines+markers",
            hovertemplate="%{x|%d/%m/%Y}<br>Doanh thu vé: %{y:,.0f} đ<extra></extra>",
        ))
        fig_rev.add_trace(go.Scatter(
            x=grp["full_date"],
            y=grp["fnb_revenue_clean"],
            name=f"F&B — {cname}",
            mode="lines",
            line=dict(dash="dot"),
            hovertemplate="%{x|%d/%m/%Y}<br>Doanh thu F&B: %{y:,.0f} đ<extra></extra>",
        ))
    apply_chart_theme(fig_rev, title="Doanh Thu Vé & F&B Theo Ngày (đồng)", height=380,
                      x_title="Ngày", y_title="Doanh thu (đ)")
    st.plotly_chart(fig_rev, use_container_width=True)
else:
    empty_state("Không có dữ liệu doanh thu theo ngày.", icon="📉")

# ---------------------------------------------------------------------------
# 10. DOANH THU THEO RẠP (BAR)
# ---------------------------------------------------------------------------
col_b1, col_b2 = st.columns(2)
with col_b1:
    section_header("Doanh Thu Theo Rạp", icon="🏢")
    if not daily_df.empty:
        cinema_rev = daily_df.groupby(["cinema_key", "cinema_name"]).agg(
            ticket_rev=("ticket_revenue_clean", "sum"),
            fnb_rev=("fnb_revenue_clean", "sum"),
        ).reset_index()
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=cinema_rev["cinema_name"],
            y=cinema_rev["ticket_rev"],
            name="Doanh thu vé",
            hovertemplate="%{x}<br>Vé: %{y:,.0f} đ<extra></extra>",
        ))
        fig_bar.add_trace(go.Bar(
            x=cinema_rev["cinema_name"],
            y=cinema_rev["fnb_rev"],
            name="Doanh thu F&B",
            hovertemplate="%{x}<br>F&B: %{y:,.0f} đ<extra></extra>",
        ))
        fig_bar.update_layout(barmode="stack")
        apply_chart_theme(fig_bar, title="Doanh Thu Vé + F&B Theo Rạp (đ)", height=350)
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        empty_state("Không có dữ liệu.")

with col_b2:
    section_header("Xu Hướng Số Vé Theo Ngày", icon="🎫")
    if not daily_df.empty:
        fig_qty = go.Figure()
        for ck, grp in daily_df.groupby("cinema_key"):
            cname = grp["cinema_name"].iloc[0]
            fig_qty.add_trace(go.Bar(
                x=grp["full_date"],
                y=grp["ticket_qty_clean"],
                name=cname,
                hovertemplate="%{x|%d/%m/%Y}<br>Số vé: %{y:,.0f}<extra></extra>",
            ))
        fig_qty.update_layout(barmode="stack")
        apply_chart_theme(fig_qty, title="Số Vé Sạch Theo Ngày", height=350, x_title="Ngày", y_title="Vé")
        st.plotly_chart(fig_qty, use_container_width=True)
    else:
        empty_state("Không có dữ liệu số vé.")

# ---------------------------------------------------------------------------
# 11. OCCUPANCY HEATMAP
# ---------------------------------------------------------------------------
section_header("Occupancy Theo Rạp & Ngày", icon="🔥")
if not occ_df.empty:
    pivot = occ_df.pivot_table(
        index="cinema_name", columns="full_date", values="weighted_occupancy", aggfunc="mean"
    )
    if not pivot.empty:
        x_labs = [format_date(c) for c in pivot.columns]
        fig_hm = plot_occupancy_heatmap(
            pivot,
            x_labels=x_labs,
            y_labels=list(pivot.index),
            title="Weighted Occupancy theo Rạp × Ngày (%)",
            height=300,
        )
        st.plotly_chart(fig_hm, use_container_width=True)
        st.caption(
            "⚠️ Occupancy = SUM(qty_clean) / SUM(capacity) theo ngày × rạp. "
            "Denominator là tổng capacity tất cả suất chiếu — không phải số phòng hoặc số ghế đơn lẻ."
        )
    else:
        empty_state("Không đủ dữ liệu để vẽ heatmap.")
else:
    empty_state("Không có dữ liệu occupancy.")

# ---------------------------------------------------------------------------
# 12. THỐNG KÊ DỮ LIỆU BẤT THƯỜNG
# ---------------------------------------------------------------------------
section_header("Thống Kê Dữ Liệu Bất Thường", icon="⚠️")
if anom_sum:
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        st.metric("Tổng suất chiếu", f"{anom_sum.get('total_showtimes', 0):,}")
    with col_a2:
        st.metric("Suất có bất thường", f"{anom_sum.get('anomaly_showtimes', 0):,}")
    with col_a3:
        st.metric("Tỷ lệ bất thường", f"{format_percent(anom_sum.get('anomaly_pct', 0)/100)}")

    if anom_sum.get("anomaly_pct", 0) >= 30:
        notice(
            f"⚠️ Tỷ lệ suất bất thường {anom_sum['anomaly_pct']:.1f}% ≥ 30% — "
            "ngưỡng rule KPI-ANOM-HIGH. Cần kiểm toán dữ liệu giao dịch.",
            type="warning",
        )
    notice(
        "Dữ liệu bất thường được phát hiện bởi ETL pipeline (is_anomaly=1). "
        "KPI Clean đã loại trừ các giao dịch này. "
        "Nguồn: vw_anomaly_showtime.",
        type="info",
    )
else:
    empty_state("Không có dữ liệu bất thường.")

# ---------------------------------------------------------------------------
# 13. F&B ATTACH RATE TREND
# ---------------------------------------------------------------------------
section_header("Xu Hướng F&B Attach Rate", icon="🍿")
if not fnb_df.empty:
    fig_fnb = go.Figure()
    for ck, grp in fnb_df.groupby("cinema_key"):
        cname = grp["cinema_name"].iloc[0]
        fig_fnb.add_trace(go.Scatter(
            x=grp["full_date"],
            y=grp["attach_rate"],
            name=cname,
            mode="lines+markers",
            hovertemplate="%{x|%d/%m/%Y}<br>Attach Rate: %{y:.1%}<extra></extra>",
        ))
    fig_fnb.add_hline(y=0.35, line_dash="dash", line_color="orange",
                      annotation_text="Ngưỡng 35% (KPI-FNB-DROP MEDIUM)")
    apply_chart_theme(fig_fnb, title="F&B Attach Rate Theo Ngày (nhóm Raw)", height=350,
                      x_title="Ngày", y_title="Attach Rate")
    fig_fnb.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig_fnb, use_container_width=True)
    st.caption(
        "📌 Attach Rate = fnb_txn_count / ticket_txn_count (nhóm Raw — không lọc is_anomaly). "
        "Mẫu số là số giao dịch vé, không phải số khách hàng. Nguồn: vw_fnb_attach."
    )
else:
    empty_state("Không có dữ liệu F&B Attach Rate.")

# ---------------------------------------------------------------------------
# 14. DOWNLOAD DỮ LIỆU
# ---------------------------------------------------------------------------
st.markdown("---")
section_header("Tải Dữ Liệu Thô", icon="📥")
if not daily_df.empty:
    data_table(daily_df, download_name="kpi_daily.csv", key="kpi_daily", max_rows=100)

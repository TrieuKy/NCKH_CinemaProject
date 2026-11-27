"""
app/pages/2_RFM_Segmentation.py
--------------------------------
Page B — Customer Segmentation (RFM) (Task 10, Section 4).

Chỉ MANAGER truy cập.
Dữ liệu: rfm_result, rfm_cluster_profile, model_metrics.
Hiển thị k=3 thực tế, phân biệt rõ với 6 nhóm nghiệp vụ tham chiếu.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import setup_page, COLOR_PRIMARY, CHART_PALETTE
from app.ui.auth import check_permission, get_current_user
from app.ui.components import (
    section_header, notice, empty_state, summary_line, badge, data_table, kpi_card
)
from app.ui.charts import apply_chart_theme
from app.ui.format import format_currency, format_decimal, format_percent, format_date
from app.data_access import (
    load_rfm_cluster_profile, load_rfm_distribution, load_rfm_model_metrics, load_rfm_dates
)

# ---------------------------------------------------------------------------
setup_page("RFM Segmentation — Phân Khúc Khách Hàng", "👥")

# ---------------------------------------------------------------------------
check_permission(required_role="MANAGER", auto_stop=True)
user = get_current_user()

from app.main import render_sidebar_navigation  # type: ignore
render_sidebar_navigation(user)

# ---------------------------------------------------------------------------
section_header(
    "Phân Khúc Khách Hàng RFM (K-Means)",
    subtitle="Kết quả mô hình đã lưu — k=3 theo Silhouette Score. Nguồn: rfm_result, rfm_cluster_profile",
    icon="👥",
)

# ---------------------------------------------------------------------------
# LOAD DỮ LIỆU
# ---------------------------------------------------------------------------
with st.spinner("Đang tải dữ liệu RFM từ PostgreSQL..."):
    profile_df  = load_rfm_cluster_profile()
    dist_df     = load_rfm_distribution()
    metrics_df  = load_rfm_model_metrics()
    rfm_dates   = load_rfm_dates()

calc_date = rfm_dates.get("calculated_at")
last_txn_date = rfm_dates.get("last_member_txn_date")

# Cảnh báo data freshness — phân biệt rõ calculated_at vs ngày giao dịch hội viên cuối cùng
if last_txn_date or calc_date:
    today = date.today()
    ref_freshness = last_txn_date or calc_date
    age_days = (today - ref_freshness).days
    date_detail = (
        f"• Ngày giao dịch hội viên cuối cùng: **{format_date(last_txn_date) if last_txn_date else 'N/A'}**\n"
        f"• Ngày chạy mô hình (`calculated_at`): **{format_date(calc_date) if calc_date else 'N/A'}**"
    )
    if age_days > 30:
        notice(
            f"⚠️ Dữ liệu giao dịch hội viên đã **{age_days} ngày** kể từ giao dịch cuối cùng.\n\n"
            f"{date_detail}\n\n"
            "Không dùng ngày chạy mô hình mới để coi dữ liệu giao dịch là mới.",
            type="warning",
        )
    else:
        notice(date_detail, type="info")
else:
    notice("📅 Không xác định được ngày nguồn RFM — không có dữ liệu trong rfm_result.", type="warning")

# ---------------------------------------------------------------------------
# CẢNH BÁO KHOA HỌC QUAN TRỌNG
# ---------------------------------------------------------------------------
notice(
    "**Lưu ý khoa học:** Mô hình K-Means hiện tại chọn **k=3** theo Silhouette Score. "
    "Có 6 nhóm nghiệp vụ tham chiếu (VIP, Trung thành, Tiềm năng, Mới, Thụ động, Có nguy cơ rời bỏ) "
    "nhưng đây là **thiết kế tham chiếu** — không phải kết quả mô hình. "
    "Dashboard hiển thị kết quả k=3 thực tế.",
    type="warning",
)

if dist_df.attrs.get("db_error") or profile_df.attrs.get("db_error"):
    st.error("❌ ERROR / UNAVAILABLE: Không thể tải dữ liệu RFM từ PostgreSQL.")
    st.stop()

# Empty check
if dist_df.empty and profile_df.empty:
    empty_state(
        "Không có dữ liệu RFM trong database. Chạy core/rfm.py để phân tích.",
        icon="📭",
    )
    st.stop()

# ---------------------------------------------------------------------------
# KPI SUMMARY
# ---------------------------------------------------------------------------
section_header("Tổng Quan Mô Hình", icon="📊")
total_members = int(dist_df["member_count"].sum()) if not dist_df.empty else 0
actual_k      = len(dist_df) if not dist_df.empty else 0

c1, c2, c3 = st.columns(3)
with c1:
    kpi_card(title="Tổng hội viên được phân tích", value=total_members, unit="hội viên")
with c2:
    kpi_card(title="Số cụm thực tế (k theo Silhouette)", value=actual_k, unit="cụm")
with c3:
    if not metrics_df.empty:
        # Tìm silhouette score từ metrics
        sil_col = None
        for col in metrics_df.columns:
            if "silhouette" in col.lower():
                sil_col = col
                break
        if sil_col:
            sil_val = metrics_df[sil_col].dropna().iloc[0] if not metrics_df[sil_col].dropna().empty else None
            kpi_card(title="Silhouette Score", value=sil_val, unit="")
        else:
            kpi_card(title="Mô hình metrics", value="Xem bên dưới", unit="")
    else:
        kpi_card(title="Model Metrics", value="N/A", unit="")

# ---------------------------------------------------------------------------
# PHÂN BỐ KHÁCH HÀNG THEO CỤM
# ---------------------------------------------------------------------------
section_header("Phân Bố Khách Hàng Theo Cụm", icon="📊")
if not dist_df.empty:
    col_pie, col_bar = st.columns([1, 1])

    with col_pie:
        fig_pie = go.Figure(data=[go.Pie(
            labels=[f"Cluster {r['cluster_id']}" for _, r in dist_df.iterrows()],
            values=dist_df["member_count"].tolist(),
            hole=0.5,
            textinfo="label+percent+value",
            hovertemplate="Cluster %{label}<br>%{value:,} hội viên (%{percent})<extra></extra>",
            marker=dict(colors=CHART_PALETTE[:len(dist_df)]),
        )])
        apply_chart_theme(fig_pie, title=f"Phân bố hội viên — k={actual_k} cụm", height=380)
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_bar:
        fig_bar = go.Figure()
        for _, row in dist_df.iterrows():
            fig_bar.add_trace(go.Bar(
                x=[f"Cluster {row['cluster_id']}"],
                y=[row["member_count"]],
                name=f"Cluster {row['cluster_id']}",
                hovertemplate=f"Cluster {row['cluster_id']}<br>%{{y:,}} hội viên<extra></extra>",
            ))
        apply_chart_theme(fig_bar, title="Số hội viên theo cụm", height=380, x_title="Cụm", y_title="Hội viên")
        st.plotly_chart(fig_bar, use_container_width=True)
else:
    empty_state("Không có dữ liệu phân bố cụm.")

# ---------------------------------------------------------------------------
# RFM PROFILE THEO CỤM
# ---------------------------------------------------------------------------
section_header("RFM Profile Theo Cụm", icon="📋")
if not dist_df.empty:
    st.caption(
        "Recency: Số ngày từ lần mua gần nhất (thấp = khách gần đây). "
        "Frequency: Số lần giao dịch. Monetary: Tổng chi tiêu (đ)."
    )

    # Radar/Spider chart
    categories = ["Recency (ngày)", "Frequency (lần)", "Monetary (triệu đ)"]
    fig_radar = go.Figure()
    for _, row in dist_df.iterrows():
        r_val = float(row.get("avg_recency", 0) or 0)
        f_val = float(row.get("avg_frequency", 0) or 0)
        m_val = float(row.get("avg_monetary", 0) or 0) / 1_000_000  # quy đổi triệu
        fig_radar.add_trace(go.Scatterpolar(
            r=[r_val, f_val, m_val],
            theta=categories,
            fill="toself",
            name=f"Cluster {row['cluster_id']}",
        ))
    fig_radar.update_layout(polar=dict(radialaxis=dict(visible=True)), showlegend=True, height=400)
    st.plotly_chart(fig_radar, use_container_width=True)

    # Bảng số liệu
    rfm_table = dist_df.copy()
    rfm_table["Cụm"] = rfm_table["cluster_id"].apply(lambda x: f"Cluster {x}")
    rfm_table["Số hội viên"] = rfm_table["member_count"].apply(lambda x: f"{int(x):,}")
    rfm_table["Recency TB (ngày)"] = rfm_table["avg_recency"].apply(lambda x: format_decimal(x, 1))
    rfm_table["Frequency TB (lần)"] = rfm_table["avg_frequency"].apply(lambda x: format_decimal(x, 1))
    rfm_table["Monetary TB (đ)"] = rfm_table["avg_monetary"].apply(lambda x: format_currency(x, compact=True))
    display_cols = ["Cụm", "Số hội viên", "Recency TB (ngày)", "Frequency TB (lần)", "Monetary TB (đ)"]
    st.dataframe(rfm_table[display_cols], use_container_width=True, hide_index=True)
else:
    empty_state("Không có dữ liệu RFM Distribution.")

# ---------------------------------------------------------------------------
# CLUSTER PROFILE CHI TIẾT
# ---------------------------------------------------------------------------
section_header("Hồ Sơ Chi Tiết Từng Cụm", icon="🔍")
if not profile_df.empty:
    st.caption("Dữ liệu từ bảng rfm_cluster_profile — tóm tắt đặc trưng nghiệp vụ từng cụm.")
    # Hiển thị từng cluster theo tab hoặc expander
    cluster_ids = sorted(profile_df["cluster_id"].unique().tolist()) if "cluster_id" in profile_df.columns else []
    if cluster_ids:
        tabs = st.tabs([f"Cluster {c}" for c in cluster_ids])
        for tab, cid in zip(tabs, cluster_ids):
            with tab:
                sub = profile_df[profile_df["cluster_id"] == cid]
                if sub.empty:
                    st.write("Không có dữ liệu.")
                    continue
                row = sub.iloc[0]
                # Hiển thị các cột có sẵn
                col_a, col_b = st.columns(2)
                with col_a:
                    for col in profile_df.columns:
                        if col not in ("cluster_id",):
                            val = row.get(col)
                            if val is not None and str(val) != "nan":
                                st.markdown(f"**{col}:** {val}")
                with col_b:
                    # Nếu có top_genre hoặc combo_rate
                    if "top_genre" in profile_df.columns:
                        st.markdown(f"**Top Genre:** {row.get('top_genre', 'N/A')}")
                    if "combo_rate" in profile_df.columns:
                        st.markdown(f"**Combo Rate:** {format_percent(row.get('combo_rate', 0), multiply=False)}")
    else:
        data_table(profile_df, download_name="rfm_cluster_profile.csv", key="rfm_profile")
else:
    empty_state("Không có dữ liệu cluster profile.")

# ---------------------------------------------------------------------------
# 6 NHÓM NGHIỆP VỤ THAM CHIẾU — rõ ràng là thiết kế tham chiếu
# ---------------------------------------------------------------------------
section_header("Thiết Kế Nhóm Nghiệp Vụ Tham Chiếu (6 Nhóm)", icon="📖")
notice(
    "⚠️ **Đây là thiết kế tham chiếu** — không phải kết quả mô hình K-Means hiện hành. "
    "Mô hình thực tế chọn k=3 theo Silhouette. "
    "6 nhóm này chỉ dùng để tham chiếu chiến lược nghiệp vụ, không dùng để phân tích số liệu.",
    type="warning",
)
ref_groups = [
    {"Nhóm": "VIP",                    "Mô tả": "Recency thấp, Frequency cao, Monetary cao",   "Hành động": "Duy trì quyền lợi, cá nhân hóa"},
    {"Nhóm": "Trung thành",            "Mô tả": "Frequency cao, quay lại đều đặn",             "Hành động": "Chương trình loyalty"},
    {"Nhóm": "Khách hàng tiềm năng",   "Mô tả": "Monetary cao nhưng Frequency trung bình",    "Hành động": "Upsell, cross-sell"},
    {"Nhóm": "Khách hàng mới",         "Mô tả": "Recency thấp, Frequency thấp",                "Hành động": "Welcome offer"},
    {"Nhóm": "Thụ động",               "Mô tả": "Recency cao, Frequency thấp",                 "Hành động": "Reactivation campaign"},
    {"Nhóm": "Có nguy cơ rời bỏ",      "Mô tả": "Recency rất cao, Frequency giảm",             "Hành động": "Win-back campaign khẩn cấp"},
]
st.dataframe(pd.DataFrame(ref_groups), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# MODEL METRICS
# ---------------------------------------------------------------------------
if not metrics_df.empty:
    with st.expander("📊 Model Metrics chi tiết", expanded=False):
        st.dataframe(metrics_df, use_container_width=True)

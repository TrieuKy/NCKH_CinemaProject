"""
app/pages/3_Forecasting.py
---------------------------
Page C — Demand Forecasting (Task 10, Section 5).

Chỉ MANAGER truy cập.
Dữ liệu: forecast_result, model_metrics.

Quy tắc nghiêm ngặt:
- Forecast tháng 10/2026 đã quá hạn vào ngày 27/11/2026 → hiển thị EXPIRED/HISTORICAL.
- Không trình bày forecast cũ là "dự báo 14 ngày sắp tới".
- Hiển thị cảnh báo FC-DATA-GAP khi không có forecast hợp lệ.
- Không gọi prediction interval heuristic là khoảng tin cậy 95% đã hiệu chuẩn.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import setup_page, COLOR_PRIMARY, CHART_PALETTE
from app.ui.auth import check_permission, get_current_user
from app.ui.components import (
    section_header, notice, empty_state, kpi_card, data_table
)
from app.ui.charts import apply_chart_theme
from app.ui.format import format_date, format_decimal
from app.data_access import (
    load_forecast_results, load_forecast_model_metrics,
    load_historical_tickets, load_data_date_range, CINEMA_MAP,
)

# ---------------------------------------------------------------------------
setup_page("Demand Forecasting — Dự Báo Nhu Cầu Vé", "📈")

# ---------------------------------------------------------------------------
check_permission(required_role="MANAGER", auto_stop=True)
user = get_current_user()

from app.main import render_sidebar_navigation  # type: ignore
render_sidebar_navigation(user)

# ---------------------------------------------------------------------------
section_header(
    "Dự Báo Nhu Cầu Vé",
    subtitle="Kết quả forecast đã lưu — nguồn: forecast_result, model_metrics",
    icon="📈",
)


# ---------------------------------------------------------------------------
# HELPER: Vẽ forecast chart
# ---------------------------------------------------------------------------
def _plot_forecast(df: pd.DataFrame, label: str = "Forecast") -> None:
    """Vẽ biểu đồ forecast yhat, yhat_lower, yhat_upper theo ngày × rạp."""
    if df.empty:
        empty_state("Không có dữ liệu để hiển thị.")
        return

    group_col = "cinema_key" if "cinema_key" in df.columns else None
    groups = df.groupby(group_col) if group_col else [(None, df)]

    for ck, grp in groups:
        cname = grp["cinema_name"].iloc[0] if "cinema_name" in grp.columns else f"Rạp {ck}"
        fig = go.Figure()

        # Confidence band first (behind)
        if "yhat_lower" in grp.columns and "yhat_upper" in grp.columns:
            x_band = list(grp["target_date"]) + list(reversed(grp["target_date"].tolist()))
            y_band = list(grp["yhat_upper"]) + list(reversed(grp["yhat_lower"].tolist()))
            fig.add_trace(go.Scatter(
                x=x_band, y=y_band,
                fill="toself", fillcolor="rgba(59,130,246,0.15)",
                line=dict(color="rgba(0,0,0,0)"),
                name="Khoảng ước lượng heuristic [yhat_lower, yhat_upper]",
                hoverinfo="skip",
            ))

        fig.add_trace(go.Scatter(
            x=grp["target_date"], y=grp["yhat"],
            name=f"yhat — {cname}", mode="lines+markers",
            line=dict(color=COLOR_PRIMARY),
            hovertemplate="%{x|%d/%m/%Y}<br>yhat: %{y:.0f} vé<extra></extra>",
        ))

        apply_chart_theme(fig, title=f"{label} — {cname}", height=350,
                          x_title="Ngày dự báo", y_title="Số vé dự báo (yhat)")
        st.plotly_chart(fig, use_container_width=True)

    # Data table
    display_cols = [c for c in [
        "target_date", "cinema_name", "time_slot",
        "yhat", "yhat_lower", "yhat_upper", "model_name"
    ] if c in df.columns]
    data_table(df[display_cols].head(200), download_name=f"forecast_{label}.csv",
               key=f"fc_tbl_{label[:10]}")


# ---------------------------------------------------------------------------
# BỘ LỌC RẠP
# ---------------------------------------------------------------------------
col_f1, _ = st.columns([2, 2])
with col_f1:
    cinema_opts = ["Tất cả cụm rạp"] + list(CINEMA_MAP.values())
    cinema_sel = st.selectbox("Cụm rạp:", cinema_opts, key="fc_cinema")
    fc_cinema_key = None
    if cinema_sel != "Tất cả cụm rạp":
        fc_cinema_key = next((k for k, v in CINEMA_MAP.items() if v == cinema_sel), None)

# ---------------------------------------------------------------------------
# LOAD DỮ LIỆU
# ---------------------------------------------------------------------------
with st.spinner("Đang tải dữ liệu forecast từ PostgreSQL..."):
    fc_df       = load_forecast_results(fc_cinema_key)
    metrics_df  = load_forecast_model_metrics()
    min_dt, max_dt = load_data_date_range()

today_ref = date.today()  # 2026-11-27 theo context

# ---------------------------------------------------------------------------
# PHÂN TÍCH TRẠNG THÁI FORECAST
# ---------------------------------------------------------------------------
fc_status = "NO_DATA"
max_target_date = None
min_target_date = None
future_days_count = 0

if not fc_df.empty:
    target_dates = fc_df["target_date"].apply(
        lambda x: x.date() if isinstance(x, pd.Timestamp) else x
    )
    max_target_date = target_dates.max()
    min_target_date = target_dates.min()
    future_days_count = int((target_dates > today_ref).sum())

    if future_days_count >= 14:
        fc_status = "VALID"
    elif future_days_count > 0:
        fc_status = "PARTIAL"
    else:
        fc_status = "EXPIRED"

# ---------------------------------------------------------------------------
# HIỂN THỊ TRẠNG THÁI
# ---------------------------------------------------------------------------
section_header("Trạng Thái Dự Báo", icon="🔍")

if fc_status == "VALID":
    notice(
        f"✅ **FORECAST HỢP LỆ** — Có {future_days_count} ngày dự báo từ hôm nay ({format_date(today_ref)}) trở đi.",
        type="success",
    )
elif fc_status == "PARTIAL":
    notice(
        f"⚠️ **FORECAST KHÔNG ĐẦY ĐỦ (PARTIAL)** — Chỉ có {future_days_count}/14 ngày dự báo hợp lệ. "
        f"Forecast đến {format_date(max_target_date)} — một phần đã qua hạn.",
        type="warning",
    )
elif fc_status == "EXPIRED":
    notice(
        f"❌ **FC-DATA-GAP — FORECAST ĐÃ HẾT HẠN (EXPIRED / HISTORICAL)**\n\n"
        f"Dữ liệu forecast hiện có target_date từ **{format_date(min_target_date)}** "
        f"đến **{format_date(max_target_date)}**. "
        f"Tất cả các ngày này đã qua tại ngày tham chiếu {format_date(today_ref)}. "
        "Dữ liệu này **không phải dự báo 14 ngày sắp tới** — xem trong khu vực Phân tích lịch sử bên dưới.",
        type="danger",
    )
elif fc_status == "NO_DATA":
    notice(
        "❌ **FC-DATA-GAP** — Không có dữ liệu trong bảng forecast_result. "
        "Cần chạy lại pipeline forecast để có dự báo mới.",
        type="danger",
    )

col_s1, col_s2, col_s3 = st.columns(3)
with col_s1:
    kpi_card(title="Trạng thái Forecast", value=fc_status)
with col_s2:
    kpi_card(title="Ngày target cuối cùng", value=format_date(max_target_date) if max_target_date else "N/A")
with col_s3:
    kpi_card(title="Ngày dự báo tương lai", value=future_days_count, unit="ngày")

# ---------------------------------------------------------------------------
# MODEL METRICS
# ---------------------------------------------------------------------------
section_header("Hiệu Năng Mô Hình Forecast", icon="📊")
if not metrics_df.empty:
    st.caption("Nguồn: model_metrics — kết quả đánh giá trên dữ liệu kiểm định đã lưu.")
    st.dataframe(metrics_df, use_container_width=True)
    notice(
        "⚠️ Prediction intervals (yhat_lower, yhat_upper) là khoảng ước lượng heuristic "
        "từ mô hình — **không phải khoảng tin cậy 95% đã được hiệu chuẩn thống kê**.",
        type="warning",
    )
else:
    empty_state("Không có model metrics. Chạy pipeline forecast để lưu kết quả đánh giá.")

# ---------------------------------------------------------------------------
# KHU VỰC FORECAST HỢP LỆ
# ---------------------------------------------------------------------------
if fc_status in ("VALID", "PARTIAL") and not fc_df.empty:
    section_header("📅 Dự Báo Hiện Hành (Hợp Lệ)", icon="✅")
    target_col = fc_df["target_date"].apply(lambda x: x.date() if isinstance(x, pd.Timestamp) else x)
    valid_fc = fc_df[target_col > today_ref].sort_values("target_date")
    _plot_forecast(valid_fc, label="Forecast hiện hành")

# ---------------------------------------------------------------------------
# KHU VỰC LỊCH SỬ / AUDIT
# ---------------------------------------------------------------------------
st.markdown("---")
section_header(
    "📂 Phân Tích Lịch Sử [HISTORICAL / AUDIT]",
    subtitle=(
        "⚠️ Dữ liệu này có target_date trong quá khứ — chỉ dùng để hậu kiểm (backtesting)."
    ),
    icon="📂",
)

if not fc_df.empty:
    notice(
        "🔴 **TRẠNG THÁI: HISTORICAL** — Dữ liệu forecast bên dưới dùng để phân tích hậu kiểm, "
        "không phải kế hoạch vận hành hiện tại.",
        type="warning",
    )
    _plot_forecast(fc_df.sort_values("target_date"), label="Lịch sử forecast [HISTORICAL]")

    # So sánh với thực tế
    with st.expander("📊 So sánh Forecast vs. Thực tế (hậu kiểm)", expanded=False):
        if min_target_date and max_target_date:
            actual_df = load_historical_tickets(fc_cinema_key, min_target_date, max_target_date)
            if not actual_df.empty:
                fc_agg = fc_df.groupby("target_date").agg(yhat_total=("yhat", "sum")).reset_index()
                fc_agg["target_date"] = pd.to_datetime(fc_agg["target_date"])
                act_agg = actual_df.groupby("full_date").agg(
                    actual_total=("ticket_qty_clean", "sum")
                ).reset_index()
                act_agg["full_date"] = pd.to_datetime(act_agg["full_date"])
                merged = pd.merge(
                    fc_agg.rename(columns={"target_date": "date"}),
                    act_agg.rename(columns={"full_date": "date"}),
                    on="date", how="outer"
                ).sort_values("date")

                fig_cmp = go.Figure()
                fig_cmp.add_trace(go.Scatter(
                    x=merged["date"], y=merged["actual_total"],
                    name="Thực tế (ticket_qty_clean)", mode="lines+markers",
                    line=dict(color="#10B981"),
                ))
                fig_cmp.add_trace(go.Scatter(
                    x=merged["date"], y=merged["yhat_total"],
                    name="Dự báo (yhat tổng hợp)", mode="lines", line=dict(dash="dash"),
                ))
                apply_chart_theme(fig_cmp, title="Thực tế vs. Dự báo — Hậu kiểm",
                                  height=350, x_title="Ngày", y_title="Số vé")
                st.plotly_chart(fig_cmp, use_container_width=True)
                st.caption("⚠️ So sánh hậu kiểm — không phải dự báo tương lai.")
            else:
                empty_state("Không có dữ liệu giao dịch thực tế trong khoảng forecast để so sánh.")
        else:
            empty_state("Không đủ thông tin để tải dữ liệu thực tế.")
else:
    empty_state(
        "Không có dữ liệu forecast trong database. Vui lòng chạy pipeline forecast.",
        icon="📭",
    )

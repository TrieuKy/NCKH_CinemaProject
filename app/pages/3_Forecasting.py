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
    load_historical_tickets, load_data_date_range, load_expected_cinemas,
    CINEMA_MAP, analyze_forecast_status, prepare_backtest_comparison,
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
# HELPER: Vẽ forecast chart (tách theo model_name để không cộng trùng mô hình)
# ---------------------------------------------------------------------------
def _plot_forecast(df: pd.DataFrame, label: str = "Forecast") -> None:
    """Vẽ biểu đồ forecast yhat, yhat_lower, yhat_upper theo ngày × rạp × mô hình."""
    if df.empty:
        empty_state("Không có dữ liệu để hiển thị.")
        return

    # Kiểm tra trùng grain trước khi tổng hợp
    grain_cols = [
        c for c in ["target_date", "cinema_key", "time_slot", "model_name", "is_test", "metric_type"]
        if c in df.columns
    ]
    if grain_cols and df.duplicated(subset=grain_cols).any():
        st.error("❌ Phát hiện bản ghi trùng tại grain forecast. Không thể tổng hợp để vẽ biểu đồ.")
        return

    group_cols = [c for c in ["cinema_key", "model_name"] if c in df.columns]
    groups = df.groupby(group_cols) if group_cols else [(None, df)]

    for keys, grp in groups:
        cname = grp["cinema_name"].iloc[0] if "cinema_name" in grp.columns else "Tất cả rạp"
        mname = grp["model_name"].iloc[0] if "model_name" in grp.columns else "model"
        # Tổng hợp theo ngày trong cùng (cinema, model_name) qua các time_slot
        daily_grp = (
            grp.groupby("target_date", as_index=False)
            .agg(
                yhat=("yhat", "sum"),
                yhat_lower=("yhat_lower", "sum"),
                yhat_upper=("yhat_upper", "sum"),
            )
            .sort_values("target_date")
        )
        fig = go.Figure()

        # Confidence band first (behind)
        if "yhat_lower" in daily_grp.columns and "yhat_upper" in daily_grp.columns:
            x_band = list(daily_grp["target_date"]) + list(reversed(daily_grp["target_date"].tolist()))
            y_band = list(daily_grp["yhat_upper"]) + list(reversed(daily_grp["yhat_lower"].tolist()))
            fig.add_trace(go.Scatter(
                x=x_band, y=y_band,
                fill="toself", fillcolor="rgba(59,130,246,0.15)",
                line=dict(color="rgba(0,0,0,0)"),
                name="Khoảng ước lượng heuristic [yhat_lower, yhat_upper]",
                hoverinfo="skip",
            ))

        fig.add_trace(go.Scatter(
            x=daily_grp["target_date"], y=daily_grp["yhat"],
            name=f"yhat ({mname}) — {cname}", mode="lines+markers",
            line=dict(color=COLOR_PRIMARY),
            hovertemplate="%{x|%d/%m/%Y}<br>yhat: %{y:.0f} vé<extra></extra>",
        ))

        apply_chart_theme(fig, title=f"{label} — {cname} ({mname})", height=350,
                          x_title="Ngày dự báo", y_title="Số vé dự báo (yhat)")
        st.plotly_chart(fig, use_container_width=True)

    # Data table
    display_cols = [c for c in [
        "target_date", "cinema_name", "time_slot", "model_name",
        "metric_type", "is_test", "yhat", "yhat_lower", "yhat_upper", "trained_date"
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
    expected_cinemas = load_expected_cinemas() if fc_cinema_key is None else None

if fc_df.attrs.get("db_error"):
    st.error("❌ ERROR / UNAVAILABLE: Không thể tải dữ liệu dự báo từ PostgreSQL.")
    st.stop()

today_ref = date.today()

# ---------------------------------------------------------------------------
# PHÂN TÍCH TRẠNG THÁI FORECAST (CHUẨN HÓA KHOA HỌC)
# ---------------------------------------------------------------------------
fc_meta = analyze_forecast_status(
    fc_df,
    reference_date=today_ref,
    last_observed_date=max_dt,
    horizon_days=14,
    expected_cinemas=expected_cinemas,
    selected_cinema_key=fc_cinema_key,
)
fc_status = fc_meta["status"]
max_target_date = fc_meta["max_target_date"]
min_target_date = fc_meta["min_target_date"]
future_days_count = fc_meta["future_unique_days"]

# ---------------------------------------------------------------------------
# HIỂN THỊ TRẠNG THÁI & DATA FRESHNESS
# ---------------------------------------------------------------------------
section_header("Trạng Thái Dự Báo & Data Freshness", icon="🔍")

obs_display = format_date(fc_meta["last_observed_date"]) if fc_meta["last_observed_date"] else "N/A (MISSING)"
freshness_msg = (
    f"• Ngày tham chiếu hiện tại: **{format_date(today_ref)}**\n"
    f"• Ngày chạy mô hình (`trained_at`): **{format_date(fc_meta['trained_date']) if fc_meta['trained_date'] else 'N/A'}**\n"
    f"• Ngày cuối dữ liệu quan sát: **{obs_display}** (`{fc_meta.get('source_status', 'UNKNOWN')}`)"
)

if fc_status == "VALID":
    notice(
        f"✅ **FORECAST HỢP LỆ** — Đủ {future_days_count}/14 ngày duy nhất trong horizon "
        f"({format_date(today_ref)} → {format_date(fc_meta['horizon_end'])}) cho toàn bộ phạm vi rạp đã chọn.\n\n{freshness_msg}",
        type="success",
    )
elif fc_status == "SOURCE_UNVERIFIED":
    if fc_meta.get("source_status") == "FUTURE_DATE":
        reason_src = (
            f"ngày dữ liệu quan sát cuối ({format_date(fc_meta['last_observed_date'])}) "
            f"nằm sau ngày tham chiếu ({format_date(today_ref)}) — không có cơ sở lịch sử hợp lệ."
        )
    else:
        reason_src = "thiếu ngày dữ liệu quan sát cuối (`last_observed_date = NULL`)."
    notice(
        f"❌ **SOURCE UNVERIFIED — KHÔNG XÁC MINH ĐƯỢC DỮ LIỆU NGUỒN** — Dù có {fc_meta.get('horizon_unique_days_any', future_days_count)} ngày "
        f"target trong horizon, {reason_src} **Fail-closed: không coi forecast là hợp lệ để vận hành.**\n\n{freshness_msg}",
        type="danger",
    )
elif fc_status == "STALE_SOURCE":
    notice(
        f"⚠️ **STALE SOURCE — DỮ LIỆU NGUỒN QUÁ CŨ** — Dù có {fc_meta.get('horizon_unique_days_any', future_days_count)} ngày target tương lai, "
        f"dữ liệu quan sát cuối cùng ({format_date(fc_meta['last_observed_date'])}) đã quá cũ "
        f"({fc_meta['source_age_days']} ngày). **Không dùng làm khuyến nghị vận hành hiện hành.**\n\n{freshness_msg}",
        type="danger",
    )
elif fc_status == "PARTIAL":
    notice(
        f"⚠️ **FORECAST KHÔNG ĐẦY ĐỦ (PARTIAL)** — Độ phủ đồng bộ toàn phạm vi rạp: {future_days_count}/14 ngày "
        f"(có {fc_meta.get('horizon_unique_days_any', future_days_count)} ngày có dữ liệu cục bộ trong horizon 14 ngày). "
        f"Xem chi tiết từng rạp bên dưới.\n\n{freshness_msg}",
        type="warning",
    )
elif fc_status == "OUT_OF_HORIZON":
    notice(
        f"⚠️ **OUT OF HORIZON — NGOÀI KHUNG DỰ BÁO 14 NGÀY** — Có dữ liệu future forecast (`is_test=False`) từ "
        f"**{format_date(fc_meta['future_min_date']) if fc_meta['future_min_date'] else 'N/A'}** "
        f"đến **{format_date(fc_meta['future_max_date']) if fc_meta['future_max_date'] else 'N/A'}**, "
        f"nhưng toàn bộ nằm ngoài horizon 14 ngày ({format_date(today_ref)} → {format_date(fc_meta['horizon_end'])}). "
        f"**Không tham gia tính Operational Readiness.**\n\n{freshness_msg}",
        type="danger",
    )
elif fc_status == "NO_VALID_HORIZON":
    notice(
        f"❌ **NO VALID HORIZON — KHÔNG CÓ NGÀY HỢP LỆ TRONG HORIZON 14 NGÀY** — Dữ liệu future forecast (`is_test=False`) "
        f"chỉ chứa bản ghi quá khứ (≤ {format_date(today_ref)}) và bản ghi ngoài horizon (> {format_date(fc_meta['horizon_end'])}), "
        f"thiếu hoàn toàn các ngày trong horizon yêu cầu.\n\n{freshness_msg}",
        type="danger",
    )
elif fc_status == "NO_FUTURE_FORECAST":
    notice(
        f"❌ **FC-DATA-GAP — CHỈ CÓ TEST PREDICTIONS (`is_test=True`)** — Không có bản ghi future forecast (`is_test=False`).\n\n{freshness_msg}",
        type="danger",
    )
elif fc_status == "EXPIRED":
    notice(
        f"❌ **FC-DATA-GAP — FORECAST ĐÃ HẾT HẠN (EXPIRED / HISTORICAL)**\n\n"
        f"Dữ liệu future forecast (`is_test=False`) có target_date từ "
        f"**{format_date(fc_meta['future_min_date']) if fc_meta['future_min_date'] else 'N/A'}** "
        f"đến **{format_date(fc_meta['future_max_date']) if fc_meta['future_max_date'] else 'N/A'}**. "
        f"Tất cả các ngày này đã qua tại ngày tham chiếu {format_date(today_ref)}.\n\n{freshness_msg}",
        type="danger",
    )
elif fc_status == "NO_DATA":
    notice(
        "❌ **FC-DATA-GAP** — Không có dữ liệu trong bảng forecast_result. "
        "Cần chạy lại pipeline forecast để có dự báo mới.",
        type="danger",
    )

col_s1, col_s2, col_s3, col_s4 = st.columns(4)
with col_s1:
    kpi_card(title="Trạng thái Forecast", value=fc_status)
with col_s2:
    kpi_card(title="Ngày target cuối (prod)", value=format_date(fc_meta["future_max_date"]) if fc_meta["future_max_date"] else "N/A")
with col_s3:
    kpi_card(title="Ngày đủ độ phủ horizon", value=future_days_count, unit="ngày")
with col_s4:
    kpi_card(title="Dữ liệu quan sát cuối", value=obs_display)

# Bảng độ phủ horizon theo từng rạp
cov_map = fc_meta.get("cinema_coverage") or {}
if cov_map:
    cov_rows = []
    for ck, info in cov_map.items():
        miss_list = info.get("missing_dates", [])
        if not miss_list:
            miss_str = "Không thiếu"
        elif len(miss_list) > 5:
            miss_str = f"{format_date(miss_list[0])} .. {format_date(miss_list[-1])} ({len(miss_list)} ngày)"
        else:
            miss_str = ", ".join(format_date(d) for d in miss_list)
        cov_rows.append({
            "Cụm rạp": info["cinema_name"],
            "Ngày hợp lệ": f"{info['valid_days']}/{info['expected_days']}",
            "Số ngày thiếu": info["missing_days"],
            "Ngày thiếu trong horizon": miss_str,
            "Trạng thái": "✅ Đủ" if info["is_complete"] else "⚠️ Thiếu",
        })
    st.caption("Độ phủ horizon 14 ngày theo từng cụm rạp:")
    st.dataframe(pd.DataFrame(cov_rows), use_container_width=True, hide_index=True)

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
# KHU VỰC FORECAST HỢP LỆ (CHỈ is_test=False TRONG HORIZON 14 NGÀY)
# ---------------------------------------------------------------------------
if fc_meta["is_operational_ready"] and not fc_df.empty:
    section_header("📅 Dự Báo Hiện Hành (Hợp Lệ)", icon="✅")
    target_col = fc_df["target_date"].apply(lambda x: x.date() if isinstance(x, pd.Timestamp) else x)
    valid_fc = fc_df[
        (~fc_df["is_test"])
        & (target_col > today_ref)
        & (target_col <= fc_meta["horizon_end"])
    ].sort_values("target_date")
    _plot_forecast(valid_fc, label="Forecast hiện hành")

# ---------------------------------------------------------------------------
# KHU VỰC LỊCH SỬ / AUDIT (TÁCH RÕ TEST PREDICTIONS VS EXPIRED FUTURE FORECASTS)
# ---------------------------------------------------------------------------
st.markdown("---")
section_header(
    "📂 Phân Tích Lịch Sử & Hậu Kiểm [HISTORICAL / AUDIT]",
    subtitle=(
        "⚠️ Tách biệt Test Predictions (is_test=True) và Expired Future Forecasts (is_test=False). "
        "Không cộng dự báo của nhiều mô hình khác nhau."
    ),
    icon="📂",
)

if not fc_df.empty:
    pred_type = st.radio(
        "Loại bản ghi hiển thị:",
        ["Future/Expired Forecasts (is_test=False)", "Test Predictions (is_test=True)"],
        horizontal=True,
        key="fc_hist_type",
    )
    want_test = "is_test=True" in pred_type
    hist_fc_df = fc_df[fc_df["is_test"] == want_test].sort_values("target_date")

    if hist_fc_df.empty:
        empty_state("Không có bản ghi thuộc loại đã chọn.")
    else:
        _plot_forecast(hist_fc_df, label="Test_Predictions" if want_test else "Expired_Forecasts")

        # So sánh với thực tế — bắt buộc tách theo model_name
        with st.expander("📊 So sánh Forecast vs. Thực tế (hậu kiểm theo mô hình)", expanded=False):
            avail_models = sorted(hist_fc_df["model_name"].dropna().unique().tolist()) if "model_name" in hist_fc_df.columns else []
            model_choices = ["Tất cả mô hình (vẽ riêng từng mô hình)"] + avail_models
            sel_model_opt = st.selectbox("Chọn mô hình hậu kiểm:", model_choices, key="fc_backtest_model")
            sel_model = None if sel_model_opt.startswith("Tất cả") else sel_model_opt

            h_min = hist_fc_df["target_date"].min()
            h_max = hist_fc_df["target_date"].max()
            if h_min and h_max:
                actual_df = load_historical_tickets(fc_cinema_key, h_min, h_max)
                if actual_df.attrs.get("db_error"):
                    st.error("❌ ERROR / UNAVAILABLE: Không thể tải dữ liệu vé thực tế.")
                elif not actual_df.empty:
                    merged, err_msg = prepare_backtest_comparison(
                        hist_fc_df, actual_df, selected_model=sel_model, metric_type="tickets"
                    )
                    if err_msg:
                        st.warning(f"⚠️ {err_msg}")
                    elif not merged.empty:
                        fig_cmp = go.Figure()
                        act_line = merged[["date", "actual_total"]].drop_duplicates().sort_values("date")
                        fig_cmp.add_trace(go.Scatter(
                            x=act_line["date"], y=act_line["actual_total"],
                            name="Thực tế (ticket_qty_clean)", mode="lines+markers",
                            line=dict(color="#10B981", width=2.5),
                        ))
                        for m_name, m_grp in merged.groupby("model_name"):
                            m_grp = m_grp.sort_values("date")
                            fig_cmp.add_trace(go.Scatter(
                                x=m_grp["date"], y=m_grp["yhat_total"],
                                name=f"Dự báo ({m_name})", mode="lines", line=dict(dash="dash"),
                            ))
                        apply_chart_theme(fig_cmp, title="Thực tế vs. Dự báo — Hậu kiểm theo từng mô hình",
                                          height=350, x_title="Ngày", y_title="Số vé (tickets)")
                        st.plotly_chart(fig_cmp, use_container_width=True)
                        st.caption("⚠️ So sánh hậu kiểm — không cộng trùng nhiều mô hình, không phải dự báo tương lai.")
                else:
                    empty_state("Không có dữ liệu giao dịch thực tế trong khoảng forecast để so sánh.")
else:
    empty_state(
        "Không có dữ liệu forecast trong database. Vui lòng chạy pipeline forecast.",
        icon="📭",
    )

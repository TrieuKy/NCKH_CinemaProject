"""
app/pages/4_Review_Analytics.py
---------------------------------
Page D — Review Analytics & Service Quality (Task 10, Section 6).

MANAGER + STAFF truy cập.
STAFF chỉ thấy dữ liệu rạp được phân công.

Quy tắc nghiêm ngặt:
- CN01 có review → hiển thị CN01.
- CN02, CN03 chưa có review → hiển thị NO DATA.
- Không dùng review CN01 làm số liệu toàn hệ thống.
- Phân biệt sentiment dự đoán (NLP) với nhãn người gán.
- Không công bố F1 chính thức khi chưa có Human Ground Truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import setup_page, SENTIMENT_COLORS, CHART_PALETTE
from app.ui.auth import check_permission, get_current_user
from app.ui.components import (
    section_header, notice, empty_state, summary_line, kpi_card, data_table
)
from app.ui.charts import apply_chart_theme, plot_sentiment_donut
from app.ui.format import format_date, format_decimal, format_percent
from app.data_access import (
    load_review_summary, load_review_nlp_detail, load_topic_distribution, CINEMA_MAP,
)

# ---------------------------------------------------------------------------
setup_page("Review Analytics — Phân Tích Đánh Giá", "⭐")

# ---------------------------------------------------------------------------
check_permission(required_role=["MANAGER", "STAFF"], auto_stop=True)
user = get_current_user()
role = user.get("role", "STAFF")
user_cinema_key = user.get("cinema_key")  # None for MANAGER

from app.main import render_sidebar_navigation  # type: ignore
render_sidebar_navigation(user)

# ---------------------------------------------------------------------------
section_header(
    "Review Analytics & Chất Lượng Dịch Vụ",
    subtitle="Phân tích cảm xúc NLP và topic mining từ đánh giá khách hàng",
    icon="⭐",
)

# ---------------------------------------------------------------------------
# CẢNH BÁO KHOA HỌC
# ---------------------------------------------------------------------------
notice(
    "**Lưu ý khoa học:**\n"
    "- Nhãn sentiment (positive/neutral/negative) là **kết quả dự đoán của mô hình NLP**, "
    "không phải nhãn người gán.\n"
    "- Human Ground Truth annotation chưa hoàn thành → "
    "**không có F1 score chính thức** cho mô hình NLP.\n"
    "- Các trường `root_causes`, `quick_win`, `kpi_target` trong insights là template tham chiếu, "
    "không phải kết quả suy luận nguyên nhân của AI.\n"
    "- Review hiện chỉ có dữ liệu tại CN01 (CGV Landmark 81).",
    type="warning",
)

# ---------------------------------------------------------------------------
# LOAD TỔNG HỢP REVIEW
# ---------------------------------------------------------------------------
with st.spinner("Đang tải dữ liệu review từ PostgreSQL..."):
    summary_df = load_review_summary()

# ---------------------------------------------------------------------------
# HIỂN THỊ THEO RẠP — TÁCH BIỆT RÕ RÀNG
# ---------------------------------------------------------------------------
section_header("Tổng Quan Theo Rạp", icon="🏢")
notice(
    "Dữ liệu review chỉ có tại **CN01 (CGV Landmark 81)**. "
    "CN02 và CN03 chưa có dữ liệu review — hiển thị NO DATA.",
    type="info",
)

all_cinemas = {1: "CGV Landmark 81 (CN01)", 2: "CGV Vincom Đồng Khởi (CN02)", 3: "CGV Pearl Plaza (CN03)"}

# STAFF bị giới hạn rạp
visible_cinemas = all_cinemas
if role == "STAFF" and user_cinema_key is not None:
    visible_cinemas = {user_cinema_key: all_cinemas[user_cinema_key]}
    notice(f"🔒 STAFF: Chỉ xem dữ liệu rạp được phân công — {all_cinemas[user_cinema_key]}", type="info")

cols_cinema = st.columns(len(visible_cinemas))
for col, (ck, cname) in zip(cols_cinema, visible_cinemas.items()):
    with col:
        # Tìm trong summary_df
        cinema_row = summary_df[summary_df["cinema_key"] == ck] if not summary_df.empty else pd.DataFrame()
        if cinema_row.empty:
            st.markdown(f"**{cname}**")
            st.markdown(
                '<div style="padding:16px;background:#F8FAFC;border:1px dashed #CBD5E1;'
                'border-radius:8px;text-align:center;">'
                '<span style="font-size:24px;">📭</span><br>'
                '<strong style="color:#64748B;">NO DATA</strong><br>'
                '<small style="color:#94A3B8;">Chưa có review tại rạp này</small>'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            r = cinema_row.iloc[0]
            st.markdown(f"**{cname}**")
            kpi_card(
                title="Tổng review",
                value=int(r.get("total_reviews", 0)),
                unit="đánh giá",
                help_text="Nguồn: review_raw",
            )
            avg_rating = r.get("avg_rating")
            if avg_rating:
                st.metric("Điểm đánh giá TB", f"{float(avg_rating):.1f} ⭐")
            if r.get("latest_review_date"):
                st.caption(f"Review gần nhất: {format_date(r['latest_review_date'])}")

st.markdown("---")

# ---------------------------------------------------------------------------
# CHỌN RẠP ĐỂ PHÂN TÍCH CHI TIẾT
# ---------------------------------------------------------------------------
# Với STAFF, khoá rạp; với MANAGER, cho chọn
if role == "STAFF" and user_cinema_key is not None:
    selected_cinema_key = user_cinema_key
    selected_cinema_name = all_cinemas.get(user_cinema_key, f"Rạp {user_cinema_key}")
else:
    sel_opt = st.selectbox("Chọn rạp để xem chi tiết:", list(visible_cinemas.values()), key="rev_cinema_sel")
    selected_cinema_key = next((k for k, v in visible_cinemas.items() if v == sel_opt), None)
    selected_cinema_name = sel_opt

# Kiểm tra có dữ liệu không
has_data = (not summary_df.empty and
            not summary_df[summary_df["cinema_key"] == selected_cinema_key].empty)

if not has_data:
    empty_state(
        f"Rạp {selected_cinema_name} chưa có dữ liệu review.\n"
        "Chỉ có CN01 (CGV Landmark 81) có dữ liệu tại thời điểm này.",
        icon="📭",
    )
    st.stop()

# ---------------------------------------------------------------------------
# LOAD CHI TIẾT
# ---------------------------------------------------------------------------
with st.spinner(f"Đang tải chi tiết review cho {selected_cinema_name}..."):
    nlp_df    = load_review_nlp_detail(selected_cinema_key, limit=200)
    topic_df  = load_topic_distribution(selected_cinema_key)

section_header(f"Phân Tích Chi Tiết — {selected_cinema_name}", icon="🔍")

# ---------------------------------------------------------------------------
# SENTIMENT DISTRIBUTION
# ---------------------------------------------------------------------------
col_sent, col_tbl = st.columns([1, 1])
with col_sent:
    section_header("Phân Bố Sentiment (NLP Dự Đoán)", icon="💬")
    if not nlp_df.empty and "sentiment_final" in nlp_df.columns:
        sent_counts_raw = nlp_df["sentiment_final"].astype(str).str.upper().value_counts().to_dict()
        # Map sang tiếng Việt
        sent_map = {
            "POSITIVE": "Tích cực",
            "NEUTRAL":  "Trung tính",
            "NEGATIVE": "Tiêu cực",
        }
        sent_counts = {sent_map.get(k, k): v for k, v in sent_counts_raw.items()}
        fig_sent = plot_sentiment_donut(
            sent_counts,
            title=f"Phân bố cảm xúc — {selected_cinema_name}",
        )
        st.plotly_chart(fig_sent, use_container_width=True)
        notice(
            "⚠️ Nhãn sentiment là **kết quả dự đoán NLP** (chưa có human ground truth). "
            "Không dùng làm chỉ số đo lường chính thức.",
            type="warning",
        )
    else:
        empty_state("Không có dữ liệu sentiment NLP.")

with col_tbl:
    section_header("Thống Kê Sentiment Theo Rạp", icon="📋")
    if not summary_df.empty:
        s = summary_df[summary_df["cinema_key"] == selected_cinema_key]
        if not s.empty:
            row = s.iloc[0]
            pos = int(row.get("positive_count", 0) or 0)
            neu = int(row.get("neutral_count",  0) or 0)
            neg = int(row.get("negative_count", 0) or 0)
            total = pos + neu + neg or 1
            tbl = pd.DataFrame([
                {"Nhãn": "Tích cực 😊", "Số lượng": pos, "Tỷ lệ": f"{pos/total:.1%}"},
                {"Nhãn": "Trung tính 😐", "Số lượng": neu, "Tỷ lệ": f"{neu/total:.1%}"},
                {"Nhãn": "Tiêu cực 😞", "Số lượng": neg, "Tỷ lệ": f"{neg/total:.1%}"},
            ])
            st.dataframe(tbl, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# TOPIC DISTRIBUTION
# ---------------------------------------------------------------------------
section_header("Phân Bố Chủ Đề (Topic Mining)", icon="🏷️")
if not topic_df.empty:
    # Tổng hợp theo topic
    topic_agg = topic_df.groupby("topics").agg(
        total=("review_count", "sum"),
        avg_rating=("avg_rating", "mean"),
    ).reset_index().sort_values("total", ascending=False)

    fig_topic = go.Figure(go.Bar(
        x=topic_agg["topics"],
        y=topic_agg["total"],
        marker_color=CHART_PALETTE[2],
        hovertemplate="%{x}<br>%{y} reviews<extra></extra>",
    ))
    apply_chart_theme(fig_topic, title="Số Review Theo Chủ Đề", height=350,
                      x_title="Chủ đề", y_title="Số đánh giá")
    st.plotly_chart(fig_topic, use_container_width=True)

    # Tỷ lệ tiêu cực theo topic
    if "sentiment_final" in topic_df.columns:
        neg_by_topic = topic_df[topic_df["sentiment_final"].astype(str).str.upper() == "NEGATIVE"].groupby("topics").agg(
            neg_count=("review_count", "sum")
        ).reset_index()
        all_by_topic = topic_df.groupby("topics").agg(total=("review_count", "sum")).reset_index()
        neg_rate_df = pd.merge(neg_by_topic, all_by_topic, on="topics")
        neg_rate_df["neg_rate"] = neg_rate_df["neg_count"] / neg_rate_df["total"]
        neg_rate_df = neg_rate_df.sort_values("neg_rate", ascending=False)

        section_header("Tỷ Lệ Review Tiêu Cực Theo Chủ Đề", icon="⚠️")
        fig_neg = go.Figure(go.Bar(
            x=neg_rate_df["topics"],
            y=neg_rate_df["neg_rate"],
            marker_color="#EF4444",
            hovertemplate="%{x}<br>Tỷ lệ tiêu cực: %{y:.1%}<extra></extra>",
        ))
        apply_chart_theme(fig_neg, title="Tỷ Lệ Review Tiêu Cực Theo Chủ Đề", height=350)
        fig_neg.update_yaxes(tickformat=".0%")
        st.plotly_chart(fig_neg, use_container_width=True)
else:
    empty_state("Không có dữ liệu topic distribution.")

# ---------------------------------------------------------------------------
# OPERATIONAL INSIGHTS từ reviews/insights.py
# ---------------------------------------------------------------------------
section_header("Operational Insights", icon="💡")
notice(
    "Insights bên dưới được sinh bởi `reviews/insights.py` từ kết quả NLP đã lưu. "
    "Các trường `root_causes`, `quick_win`, `kpi_target` là **template tham chiếu**, "
    "không phải kết quả suy luận nguyên nhân của AI.",
    type="info",
)

try:
    from reviews.insights import generate_operational_insights  # type: ignore
    from etl.db import get_engine  # type: ignore
    with st.spinner("Đang tải insights từ reviews/insights.py..."):
        from sqlalchemy import text as _text
        eng = get_engine()
        with eng.connect() as conn:
            rr = pd.read_sql(
                _text("""
                    SELECT rr.review_id, rr.cinema_key, rr.rating, rr.published_date,
                           nl.sentiment_final, nl.topics
                    FROM cinema_dw.review_raw rr
                    LEFT JOIN cinema_dw.review_nlp nl ON rr.review_id = nl.review_id
                    WHERE rr.cinema_key = :ck
                """),
                conn, params={"ck": selected_cinema_key}
            )
        if not rr.empty:
            insights = generate_operational_insights(rr)
            if insights:
                for ins in insights[:5]:  # Giới hạn 5 insights
                    with st.expander(f"💡 {ins.get('title', 'Insight')}", expanded=False):
                        st.markdown(f"**Mức ưu tiên:** {ins.get('priority', 'N/A')}")
                        st.markdown(f"**Chủ đề:** {ins.get('topic', 'N/A')}")
                        st.markdown(f"**Số review:** {ins.get('n', 0)}")
                        if ins.get("negative_rate"):
                            st.markdown(f"**Tỷ lệ tiêu cực:** {ins['negative_rate']:.1%}")
                        if ins.get("avg_rating"):
                            st.markdown(f"**Điểm TB:** {ins['avg_rating']:.1f} ⭐")
            else:
                empty_state("Không đủ dữ liệu để sinh insights (cần ≥ 10 reviews).")
        else:
            empty_state("Không có dữ liệu review để sinh insights.")
except Exception as e:
    notice(f"Không thể tải operational insights: {e}", type="warning")

# ---------------------------------------------------------------------------
# CHI TIẾT REVIEW GỐC (MANAGER chỉ)
# ---------------------------------------------------------------------------
if role == "MANAGER":
    with st.expander(f"📋 Dữ liệu review gốc — {selected_cinema_name} (tối đa 200 dòng)", expanded=False):
        if not nlp_df.empty:
            # Không lộ dữ liệu cá nhân (chỉ hiện review_id, rating, date, sentiment, topics)
            safe_cols = [c for c in [
                "review_id", "cinema_name", "rating", "published_date",
                "sentiment_final", "topics", "sentiment_score"
            ] if c in nlp_df.columns]
            data_table(nlp_df[safe_cols], download_name="reviews_cn01.csv", key="review_detail")
            notice(
                "⚠️ Dữ liệu review ẩn các thông tin định danh cá nhân (PII) không cần thiết "
                "theo yêu cầu khoa học.", type="info"
            )
        else:
            empty_state("Không có dữ liệu review chi tiết.")

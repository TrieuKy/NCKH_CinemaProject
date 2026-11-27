"""
app/data_access.py
------------------
Data Access Layer (DAL) dành riêng cho Streamlit Dashboard.
- Chỉ đọc (read-only), không ghi dữ liệu.
- Dùng lại etl.db.get_engine().
- SQL có tham số (parameterized queries).
- Ưu tiên Views và bảng tổng hợp, không tải toàn bộ fact_ticket.
- Cache TTL hợp lý (5 phút) theo bộ lọc.
- An toàn cache: không rò rỉ dữ liệu giữa người dùng / rạp khác nhau.
- Không chạy ML (RFM/Prophet/XGBoost) trong quá trình render.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Engine helper (singleton, không tạo kết nối mới mỗi lần rerun)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def get_db_engine() -> Engine:
    """Trả về SQLAlchemy Engine dùng lại trong suốt phiên Streamlit."""
    from etl.db import get_engine  # type: ignore
    return get_engine()


def _engine() -> Engine:
    return get_db_engine()


# ---------------------------------------------------------------------------
# Hằng số
# ---------------------------------------------------------------------------

CINEMA_MAP = {
    1: "CGV Landmark 81 (CN01)",
    2: "CGV Vincom Đồng Khởi (CN02)",
    3: "CGV Pearl Plaza (CN03)",
}
CINEMA_KEY_MAP = {
    "CGV Landmark 81 (CN01)": 1,
    "CGV Vincom Đồng Khởi (CN02)": 2,
    "CGV Pearl Plaza (CN03)": 3,
}

DEMO_ENABLED = os.environ.get("CINEMA_DEMO_MODE", "0").strip() == "1"
DEMO_CREDENTIALS: dict[str, dict] = {}
if DEMO_ENABLED:
    DEMO_CREDENTIALS = {
        "manager": {"user_id": "demo-manager-uuid", "username": "manager", "role": "MANAGER", "cinema_key": None},
        "staff":   {"user_id": "demo-staff-uuid",   "username": "staff",   "role": "STAFF",   "cinema_key": 1},
    }

# ---------------------------------------------------------------------------
# Kiểm tra kết nối PostgreSQL
# ---------------------------------------------------------------------------

def check_db_connection() -> tuple[bool, str]:
    """Kiểm tra kết nối PostgreSQL. Trả về (ok, message)."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Kết nối PostgreSQL thành công."
    except Exception as e:
        return False, f"Lỗi kết nối PostgreSQL: {e}"


# ---------------------------------------------------------------------------
# Hàm helper date range → date_key
# ---------------------------------------------------------------------------

def _date_range_params(start_date: date, end_date: date) -> dict:
    return {
        "start_key": int(start_date.strftime("%Y%m%d")),
        "end_key":   int(end_date.strftime("%Y%m%d")),
    }


# ---------------------------------------------------------------------------
# A. KPI Daily
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def load_daily_kpi(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
    source_filter: Optional[str] = None,
) -> pd.DataFrame:
    """
    Tải KPI theo ngày từ vw_daily_kpi (ticket_revenue_clean, fnb_revenue_clean, atp_clean, ticket_qty_clean).
    source_filter: 'MOCK', 'MANUAL', hoặc None (tất cả).
    Lưu ý: vw_daily_kpi không có cột source, nên source_filter ở đây chỉ ghi chú mà không ảnh hưởng kết quả
    — hiển thị cảnh báo nếu người dùng lọc source.
    """
    try:
        eng = _engine()
        cinema_clause = "AND k.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = _date_range_params(start_date, end_date)
        if cinema_key is not None:
            params["cinema_key"] = cinema_key

        sql = text(f"""
            SELECT
                k.full_date,
                k.cinema_key,
                k.cinema_name,
                k.ticket_revenue_clean,
                k.fnb_revenue_clean,
                k.ticket_qty_clean,
                k.atp_clean
            FROM cinema_dw.vw_daily_kpi k
            WHERE k.date_key BETWEEN :start_key AND :end_key
            {cinema_clause}
            ORDER BY k.full_date, k.cinema_key
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["full_date"] = pd.to_datetime(df["full_date"]).dt.date
        return df
    except Exception as e:
        st.error(f"Lỗi tải KPI hàng ngày: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_kpi_summary(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """
    Tổng hợp KPI cho KPI Cards: tổng doanh thu vé, F&B, số vé, ATP, occupancy tổng.
    """
    try:
        eng = _engine()
        cinema_clause = "AND k.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = _date_range_params(start_date, end_date)
        if cinema_key is not None:
            params["cinema_key"] = cinema_key

        sql = text(f"""
            SELECT
                SUM(k.ticket_revenue_clean) AS ticket_revenue_clean,
                SUM(k.fnb_revenue_clean)    AS fnb_revenue_clean,
                SUM(k.ticket_qty_clean)     AS ticket_qty_clean,
                CASE WHEN SUM(k.ticket_qty_clean) > 0
                     THEN SUM(k.ticket_revenue_clean)::NUMERIC / SUM(k.ticket_qty_clean)
                     ELSE NULL END AS atp_clean,
                COUNT(DISTINCT k.full_date) AS data_days
            FROM cinema_dw.vw_daily_kpi k
            WHERE k.date_key BETWEEN :start_key AND :end_key
            {cinema_clause}
        """)
        with eng.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row:
            return {
                "ticket_revenue_clean": float(row[0] or 0),
                "fnb_revenue_clean":    float(row[1] or 0),
                "ticket_qty_clean":     int(row[2] or 0),
                "atp_clean":            float(row[3] or 0) if row[3] else None,
                "data_days":            int(row[4] or 0),
            }
    except Exception as e:
        st.error(f"Lỗi tải KPI tổng hợp: {e}")
    return {}


@st.cache_data(ttl=300, show_spinner=False)
def load_occupancy_summary(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """
    Tải occupancy tổng hợp theo ngày × rạp từ vw_showtime_occupancy.
    Weighted occupancy = SUM(qty_clean) / SUM(capacity).
    """
    try:
        eng = _engine()
        cinema_clause = "AND o.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = _date_range_params(start_date, end_date)
        if cinema_key is not None:
            params["cinema_key"] = cinema_key

        # Lấy cinema_key từ dim_room qua room_key của vw_showtime_occupancy
        sql = text(f"""
            SELECT
                o.date_key,
                d.full_date,
                dc.cinema_key,
                dc.cinema_name,
                SUM(o.qty_clean)  AS total_qty_clean,
                SUM(o.capacity)   AS total_capacity,
                CASE WHEN SUM(o.capacity) > 0
                     THEN SUM(o.qty_clean)::NUMERIC / SUM(o.capacity)
                     ELSE NULL END AS weighted_occupancy,
                COUNT(*) AS showtime_count,
                COUNT(CASE WHEN o.is_low_demand THEN 1 END) AS low_demand_count
            FROM cinema_dw.vw_showtime_occupancy o
            JOIN cinema_dw.dim_cinema dc ON o.cinema_name = dc.cinema_name
            JOIN cinema_dw.dim_date d ON o.date_key = d.date_key
            WHERE o.date_key BETWEEN :start_key AND :end_key
            {cinema_clause}
            GROUP BY o.date_key, d.full_date, dc.cinema_key, dc.cinema_name
            ORDER BY d.full_date, dc.cinema_key
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["full_date"] = pd.to_datetime(df["full_date"]).dt.date
        return df
    except Exception as e:
        st.error(f"Lỗi tải Occupancy: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_fnb_attach_summary(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """
    Tải Attach Rate và SPP theo ngày × rạp từ vw_fnb_attach.
    """
    try:
        eng = _engine()
        cinema_clause = "AND a.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = _date_range_params(start_date, end_date)
        if cinema_key is not None:
            params["cinema_key"] = cinema_key

        sql = text(f"""
            SELECT
                d.full_date,
                a.cinema_key,
                a.cinema_name,
                SUM(a.ticket_txn_count)  AS ticket_txn_count,
                SUM(a.fnb_txn_count)     AS fnb_txn_count,
                SUM(a.combo_txn_count)   AS combo_txn_count,
                SUM(a.fnb_revenue_clean) AS fnb_revenue_clean,
                CASE WHEN SUM(a.ticket_txn_count) > 0
                     THEN SUM(a.fnb_txn_count)::NUMERIC / SUM(a.ticket_txn_count)
                     ELSE 0 END AS attach_rate,
                CASE WHEN SUM(a.ticket_txn_count) > 0
                     THEN SUM(a.fnb_revenue_clean)::NUMERIC / SUM(a.ticket_txn_count)
                     ELSE 0 END AS spp_clean
            FROM cinema_dw.vw_fnb_attach a
            JOIN cinema_dw.dim_date d ON a.date_key = d.date_key
            WHERE a.date_key BETWEEN :start_key AND :end_key
            {cinema_clause}
            GROUP BY d.full_date, a.cinema_key, a.cinema_name
            ORDER BY d.full_date, a.cinema_key
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["full_date"] = pd.to_datetime(df["full_date"]).dt.date
        return df
    except Exception as e:
        st.error(f"Lỗi tải F&B Attach: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_anomaly_summary(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Thống kê bất thường trong khoảng ngày."""
    try:
        eng = _engine()
        cinema_clause = "AND an.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = _date_range_params(start_date, end_date)
        if cinema_key is not None:
            params["cinema_key"] = cinema_key

        sql = text(f"""
            SELECT
                COUNT(*) AS total_showtimes,
                COUNT(CASE WHEN an.is_anomaly_showtime THEN 1 END) AS anomaly_showtimes,
                ROUND(COUNT(CASE WHEN an.is_anomaly_showtime THEN 1 END)::NUMERIC
                      / NULLIF(COUNT(*), 0) * 100, 2) AS anomaly_pct
            FROM cinema_dw.vw_anomaly_showtime an
            WHERE an.date_key BETWEEN :start_key AND :end_key
            {cinema_clause}
        """)
        with eng.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row:
            return {
                "total_showtimes":   int(row[0] or 0),
                "anomaly_showtimes": int(row[1] or 0),
                "anomaly_pct":       float(row[2] or 0),
            }
    except Exception as e:
        st.error(f"Lỗi tải Anomaly: {e}")
    return {}


@st.cache_data(ttl=300, show_spinner=False)
def load_data_date_range() -> tuple[Optional[date], Optional[date]]:
    """Trả về (min_date, max_date) có dữ liệu trong vw_daily_kpi."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT MIN(full_date), MAX(full_date)
                FROM cinema_dw.vw_daily_kpi
            """)).fetchone()
        if row and row[0]:
            return (
                row[0].date() if hasattr(row[0], "date") else row[0],
                row[1].date() if hasattr(row[1], "date") else row[1],
            )
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------------------
# B. RFM
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner=False)
def load_rfm_cluster_profile() -> pd.DataFrame:
    """Tải rfm_cluster_profile từ PostgreSQL."""
    try:
        eng = _engine()
        sql = text("""
            SELECT *
            FROM cinema_dw.rfm_cluster_profile
            ORDER BY cluster_id
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn)
        return df
    except Exception as e:
        st.error(f"Lỗi tải RFM Cluster Profile: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def load_rfm_distribution() -> pd.DataFrame:
    """Tải phân bố khách hàng theo cluster từ rfm_result."""
    try:
        eng = _engine()
        sql = text("""
            SELECT
                cluster_id,
                COUNT(*) AS member_count,
                AVG(recency_days) AS avg_recency,
                AVG(frequency)    AS avg_frequency,
                AVG(monetary)     AS avg_monetary
            FROM cinema_dw.rfm_result
            GROUP BY cluster_id
            ORDER BY cluster_id
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn)
        return df
    except Exception as e:
        st.error(f"Lỗi tải RFM Distribution: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def load_rfm_model_metrics() -> pd.DataFrame:
    """Tải metrics mô hình RFM (silhouette, inertia…) từ model_metrics."""
    try:
        eng = _engine()
        sql = text("""
            SELECT *
            FROM cinema_dw.model_metrics
            WHERE model_name ILIKE '%rfm%' OR model_name ILIKE '%kmeans%' OR model_name ILIKE '%cluster%'
            ORDER BY evaluated_at DESC
            LIMIT 10
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn)
        return df
    except Exception as e:
        st.error(f"Lỗi tải RFM Model Metrics: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def load_rfm_as_of_date() -> Optional[date]:
    """Lấy ngày dữ liệu RFM gần nhất."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT MAX(calculated_at) FROM cinema_dw.rfm_result
            """)).fetchone()
        if row and row[0]:
            d = row[0]
            return d.date() if hasattr(d, "date") else d
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# C. Forecast
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def load_forecast_results(cinema_key: Optional[int] = None) -> pd.DataFrame:
    """Tải kết quả dự báo từ forecast_result."""
    try:
        eng = _engine()
        cinema_clause = "WHERE f.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = {"cinema_key": cinema_key} if cinema_key is not None else {}
        sql = text(f"""
            SELECT
                f.target_date,
                f.cinema_key,
                c.cinema_name,
                f.time_slot,
                f.yhat,
                f.yhat_lower,
                f.yhat_upper,
                f.model_name,
                f.created_at AS trained_at
            FROM cinema_dw.forecast_result f
            JOIN cinema_dw.dim_cinema c ON f.cinema_key = c.cinema_key
            {cinema_clause}
            ORDER BY f.target_date, f.cinema_key, f.time_slot
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["target_date"] = pd.to_datetime(df["target_date"]).dt.date
        return df
    except Exception as e:
        st.error(f"Lỗi tải Forecast: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def load_forecast_model_metrics() -> pd.DataFrame:
    """Tải metrics mô hình forecast từ model_metrics."""
    try:
        eng = _engine()
        sql = text("""
            SELECT *
            FROM cinema_dw.model_metrics
            WHERE model_name NOT ILIKE '%rfm%'
              AND model_name NOT ILIKE '%kmeans%'
            ORDER BY evaluated_at DESC
            LIMIT 20
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn)
        return df
    except Exception as e:
        st.error(f"Lỗi tải Forecast Model Metrics: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_historical_tickets(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Tải lịch sử vé bán theo ngày × rạp cho biểu đồ so sánh forecast."""
    try:
        eng = _engine()
        cinema_clause = "AND k.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = _date_range_params(start_date, end_date)
        if cinema_key is not None:
            params["cinema_key"] = cinema_key
        sql = text(f"""
            SELECT k.full_date, k.cinema_key, k.cinema_name,
                   k.ticket_qty_clean
            FROM cinema_dw.vw_daily_kpi k
            WHERE k.date_key BETWEEN :start_key AND :end_key
            {cinema_clause}
            ORDER BY k.full_date
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["full_date"] = pd.to_datetime(df["full_date"]).dt.date
        return df
    except Exception as e:
        st.error(f"Lỗi tải lịch sử vé: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# D. Reviews / NLP
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def load_review_summary() -> pd.DataFrame:
    """Tổng hợp số lượng review và sentiment theo rạp."""
    try:
        eng = _engine()
        sql = text("""
            SELECT
                rr.cinema_key,
                c.cinema_name,
                COUNT(*) AS total_reviews,
                AVG(rr.rating) AS avg_rating,
                COUNT(CASE WHEN UPPER(nl.sentiment_final) = 'POSITIVE' THEN 1 END) AS positive_count,
                COUNT(CASE WHEN UPPER(nl.sentiment_final) = 'NEUTRAL'  THEN 1 END) AS neutral_count,
                COUNT(CASE WHEN UPPER(nl.sentiment_final) = 'NEGATIVE' THEN 1 END) AS negative_count,
                MAX(rr.published_date) AS latest_review_date,
                MIN(rr.published_date) AS earliest_review_date
            FROM cinema_dw.review_raw rr
            JOIN cinema_dw.dim_cinema c ON rr.cinema_key = c.cinema_key
            LEFT JOIN cinema_dw.review_nlp nl ON rr.review_id = nl.review_id
            GROUP BY rr.cinema_key, c.cinema_name
            ORDER BY rr.cinema_key
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn)
        return df
    except Exception as e:
        st.error(f"Lỗi tải Review Summary: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_review_nlp_detail(cinema_key: Optional[int] = None, limit: int = 200) -> pd.DataFrame:
    """Tải chi tiết reviews + NLP (giới hạn hàng để không quá tải)."""
    try:
        eng = _engine()
        cinema_clause = "AND rr.cinema_key = :cinema_key" if cinema_key is not None else ""
        params: dict = {}
        if cinema_key is not None:
            params["cinema_key"] = cinema_key
        params["limit"] = limit
        sql = text(f"""
            SELECT
                rr.review_id,
                rr.cinema_key,
                c.cinema_name,
                rr.rating,
                rr.published_date,
                nl.sentiment_final,
                array_to_string(nl.topics, ', ') AS topics,
                nl.sentiment_score
            FROM cinema_dw.review_raw rr
            JOIN cinema_dw.dim_cinema c ON rr.cinema_key = c.cinema_key
            LEFT JOIN cinema_dw.review_nlp nl ON rr.review_id = nl.review_id
            WHERE 1=1
            {cinema_clause}
            ORDER BY rr.published_date DESC
            LIMIT :limit
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        return df
    except Exception as e:
        st.error(f"Lỗi tải Review Detail: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_topic_distribution(cinema_key: Optional[int] = None) -> pd.DataFrame:
    """Tải phân bố topics từ review_nlp (CN01 only)."""
    try:
        eng = _engine()
        cinema_clause = "AND rr.cinema_key = :cinema_key" if cinema_key is not None else ""
        params: dict = {}
        if cinema_key is not None:
            params["cinema_key"] = cinema_key
        sql = text(f"""
            SELECT
                t.topic AS topics,
                nl.sentiment_final,
                COUNT(*) AS review_count,
                AVG(rr.rating) AS avg_rating
            FROM cinema_dw.review_nlp nl
            JOIN cinema_dw.review_raw rr ON nl.review_id = rr.review_id
            CROSS JOIN LATERAL UNNEST(nl.topics) AS t(topic)
            WHERE 1=1
            {cinema_clause}
            GROUP BY t.topic, nl.sentiment_final
            ORDER BY review_count DESC
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        return df
    except Exception as e:
        st.error(f"Lỗi tải Topic Distribution: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# E. App Users
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def load_app_users() -> pd.DataFrame:
    """Tải danh sách user (không có password_hash) để quản trị."""
    try:
        eng = _engine()
        sql = text("""
            SELECT user_id, username, role, cinema_key, is_active
            FROM cinema_dw.app_user
            ORDER BY role, username
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn)
        return df
    except Exception as e:
        st.error(f"Lỗi tải App Users: {e}")
        return pd.DataFrame()

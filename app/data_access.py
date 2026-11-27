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

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

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


def enforce_cinema_scope(
    user_role: str,
    assigned_cinema_key: Optional[int],
    requested_cinema_key: Optional[int] = None,
) -> Optional[int]:
    """
    Fail-closed cinema scope enforcement cho DAL/UI.
    - MANAGER: được phép xem toàn hệ thống (None) hoặc rạp hợp lệ bất kỳ.
    - STAFF: bắt buộc có assigned_cinema_key hợp lệ trong CINEMA_MAP;
      từ chối nếu thiếu, không hợp lệ, hoặc yêu cầu rạp khác phạm vi.
    """
    role_norm = str(user_role or "").strip().upper()
    if role_norm == "MANAGER":
        if requested_cinema_key is not None and requested_cinema_key not in CINEMA_MAP:
            raise ValueError(f"cinema_key không hợp lệ: {requested_cinema_key}")
        return requested_cinema_key
    if role_norm == "STAFF":
        if assigned_cinema_key is None or assigned_cinema_key not in CINEMA_MAP:
            raise PermissionError("Tài khoản STAFF chưa được phân công cinema_key hợp lệ.")
        if requested_cinema_key is not None and requested_cinema_key != assigned_cinema_key:
            raise PermissionError("STAFF không có quyền truy cập dữ liệu rạp khác.")
        return assigned_cinema_key
    raise PermissionError(f"Vai trò không hợp lệ: {user_role}")


def _error_df(message: str) -> pd.DataFrame:
    """Trả về DataFrame rỗng có gắn cờ lỗi DB để phân biệt ERROR với NO_DATA."""
    df = pd.DataFrame()
    df.attrs["db_error"] = True
    df.attrs["error_message"] = message
    return df

# ---------------------------------------------------------------------------
# Kiểm tra kết nối PostgreSQL
# ---------------------------------------------------------------------------

def check_db_connection() -> tuple[bool, str]:
    """Kiểm tra kết nối PostgreSQL. Trả về (ok, message) không lộ credentials."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Kết nối PostgreSQL thành công."
    except Exception:
        logger.exception("PostgreSQL connection check failed")
        return False, "Không thể kết nối cơ sở dữ liệu PostgreSQL."


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
    except Exception:
        logger.exception("Error loading daily KPI")
        return _error_df("Không thể tải dữ liệu KPI hàng ngày từ cơ sở dữ liệu.")


@st.cache_data(ttl=300, show_spinner=False)
def load_kpi_summary(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """
    Tổng hợp KPI cho KPI Cards: tổng doanh thu vé, F&B, số vé, ATP, số ngày có giao dịch.
    Phân biệt rõ status='OK', 'NO_DATA', và 'ERROR'.
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
        data_days = int(row[4] or 0) if row else 0
        if not row or data_days == 0:
            return {
                "status": "NO_DATA",
                "ticket_revenue_clean": 0.0,
                "fnb_revenue_clean": 0.0,
                "ticket_qty_clean": 0,
                "atp_clean": None,
                "data_days": 0,
            }
        return {
            "status": "OK",
            "ticket_revenue_clean": float(row[0] or 0),
            "fnb_revenue_clean":    float(row[1] or 0),
            "ticket_qty_clean":     int(row[2] or 0),
            "atp_clean":            float(row[3]) if row[3] is not None else None,
            "data_days":            data_days,
        }
    except Exception:
        logger.exception("Error loading KPI summary")
        return {
            "status": "ERROR",
            "error": "Không thể tải KPI tổng hợp từ cơ sở dữ liệu.",
        }


@st.cache_data(ttl=300, show_spinner=False)
def load_kpi_coverage_by_cinema(
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """
    Kiểm tra phạm vi và độ phủ ngày có giao dịch theo từng rạp trong vw_daily_kpi.
    Phân biệt số ngày có giao dịch (active_days) với số ngày lịch (calendar_days).
    """
    try:
        eng = _engine()
        params = _date_range_params(start_date, end_date)
        calendar_days = max((end_date - start_date).days + 1, 0)
        sql = text("""
            SELECT
                c.cinema_key,
                c.cinema_name,
                COUNT(DISTINCT k.full_date) AS active_days,
                MIN(k.full_date) AS min_date,
                MAX(k.full_date) AS max_date
            FROM cinema_dw.dim_cinema c
            LEFT JOIN cinema_dw.vw_daily_kpi k
              ON c.cinema_key = k.cinema_key
             AND k.date_key BETWEEN :start_key AND :end_key
            GROUP BY c.cinema_key, c.cinema_name
            ORDER BY c.cinema_key
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["calendar_days"] = calendar_days
        return df
    except Exception:
        logger.exception("Error loading KPI coverage by cinema")
        return _error_df("Không thể tải độ phủ dữ liệu KPI theo rạp.")


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
        cinema_clause = "AND dc.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = _date_range_params(start_date, end_date)
        if cinema_key is not None:
            params["cinema_key"] = cinema_key

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
    except Exception:
        logger.exception("Error loading occupancy summary")
        return _error_df("Không thể tải dữ liệu Occupancy từ cơ sở dữ liệu.")


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
    except Exception:
        logger.exception("Error loading F&B attach summary")
        return _error_df("Không thể tải dữ liệu F&B Attach từ cơ sở dữ liệu.")


@st.cache_data(ttl=300, show_spinner=False)
def load_anomaly_summary(
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """
    Thống kê bất thường trong khoảng ngày từ vw_anomaly_showtime.
    Join cinema_dw.dim_cinema qua cinema_name để lọc theo cinema_key
    vì vw_anomaly_showtime không xuất cột cinema_key.
    Phân biệt rõ status='OK', 'NO_DATA', và 'ERROR' (không trả KPI=0 khi lỗi SQL).
    """
    try:
        eng = _engine()
        cinema_clause = "AND dc.cinema_key = :cinema_key" if cinema_key is not None else ""
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
            JOIN cinema_dw.dim_cinema dc ON an.cinema_name = dc.cinema_name
            WHERE an.date_key BETWEEN :start_key AND :end_key
            {cinema_clause}
        """)
        with eng.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        total_showtimes = int(row[0] or 0) if row else 0
        if total_showtimes == 0:
            return {
                "status": "NO_DATA",
                "total_showtimes": 0,
                "anomaly_showtimes": 0,
                "anomaly_pct": None,
            }
        return {
            "status": "OK",
            "total_showtimes": total_showtimes,
            "anomaly_showtimes": int(row[1] or 0),
            "anomaly_pct": float(row[2] or 0),
        }
    except Exception:
        logger.exception("Error loading anomaly summary")
        return {
            "status": "ERROR",
            "error": "Không thể tải thống kê bất thường từ cơ sở dữ liệu.",
        }


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
    except Exception:
        logger.exception("Error loading RFM cluster profile")
        return _error_df("Không thể tải hồ sơ cụm RFM từ cơ sở dữ liệu.")


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
    except Exception:
        logger.exception("Error loading RFM distribution")
        return _error_df("Không thể tải phân bố RFM từ cơ sở dữ liệu.")


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
    except Exception:
        logger.exception("Error loading RFM model metrics")
        return _error_df("Không thể tải metrics mô hình RFM.")


@st.cache_data(ttl=600, show_spinner=False)
def load_rfm_dates() -> dict[str, Optional[date]]:
    """
    Trả về tách biệt:
    - calculated_at: ngày chạy mô hình RFM (MAX(rfm_result.calculated_at)).
    - last_member_txn_date: ngày giao dịch hội viên cuối cùng trong fact_ticket.
    """
    calculated_at: Optional[date] = None
    last_member_txn_date: Optional[date] = None
    try:
        eng = _engine()
        with eng.connect() as conn:
            row_calc = conn.execute(text("""
                SELECT MAX(calculated_at) FROM cinema_dw.rfm_result
            """)).fetchone()
            if row_calc and row_calc[0]:
                d = row_calc[0]
                calculated_at = d.date() if hasattr(d, "date") else d

            row_txn = conn.execute(text("""
                SELECT MAX(d.full_date)
                FROM cinema_dw.fact_ticket t
                JOIN cinema_dw.dim_date d ON t.date_key = d.date_key
                JOIN cinema_dw.dim_customer c ON t.customer_key = c.customer_key
                WHERE c.is_guest = FALSE
                  AND t.is_anomaly = 0
            """)).fetchone()
            if row_txn and row_txn[0]:
                d2 = row_txn[0]
                last_member_txn_date = d2.date() if hasattr(d2, "date") else d2
    except Exception:
        logger.exception("Error loading RFM dates")
    return {
        "calculated_at": calculated_at,
        "last_member_txn_date": last_member_txn_date,
    }


@st.cache_data(ttl=600, show_spinner=False)
def load_rfm_as_of_date() -> Optional[date]:
    """Lấy ngày chạy mô hình RFM gần nhất (giữ tương thích ngược)."""
    return load_rfm_dates().get("calculated_at")


# ---------------------------------------------------------------------------
# C. Forecast
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def load_forecast_results(cinema_key: Optional[int] = None) -> pd.DataFrame:
    """Tải kết quả dự báo từ forecast_result kèm is_test, metric_type và trained_date."""
    try:
        eng = _engine()
        cinema_clause = "WHERE f.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = {"cinema_key": cinema_key} if cinema_key is not None else {}
        sql = text(f"""
            SELECT
                f.forecast_id,
                f.target_date,
                f.cinema_key,
                c.cinema_name,
                f.time_slot,
                f.model_name,
                f.metric_type,
                f.yhat,
                f.yhat_lower,
                f.yhat_upper,
                f.is_test,
                f.created_at AS trained_at
            FROM cinema_dw.forecast_result f
            JOIN cinema_dw.dim_cinema c ON f.cinema_key = c.cinema_key
            {cinema_clause}
            ORDER BY f.target_date, f.cinema_key, f.time_slot, f.model_name
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["target_date"] = pd.to_datetime(df["target_date"]).dt.date
            df["is_test"] = df["is_test"].fillna(False).astype(bool)
            if "trained_at" in df.columns:
                df["trained_date"] = pd.to_datetime(df["trained_at"]).dt.date
        return df
    except Exception:
        logger.exception("Error loading forecast results")
        return _error_df("Không thể tải kết quả dự báo từ cơ sở dữ liệu.")


@st.cache_data(ttl=600, show_spinner=False)
def load_expected_cinemas() -> dict[int, str]:
    """Lấy danh sách cụm rạp từ cinema_dw.dim_cinema (fallback về CINEMA_MAP)."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT cinema_key, cinema_name FROM cinema_dw.dim_cinema ORDER BY cinema_key"
            )).fetchall()
        if rows:
            return {int(r[0]): str(r[1]) for r in rows}
    except Exception:
        logger.warning("Fallback to CINEMA_MAP for expected cinemas", exc_info=True)
    return dict(CINEMA_MAP)


def _evaluate_source_freshness(
    reference_date: date,
    last_observed_date: Optional[date],
    max_source_staleness_days: int,
) -> tuple[str, Optional[int], bool, bool]:
    """
    Đánh giá độ tươi của dữ liệu nguồn theo nguyên tắc fail-closed:
    - None / NaT -> ("MISSING", None, False, False)
    - > reference_date -> ("FUTURE_DATE", age_days, False, False)
    - age_days > max_source_staleness_days -> ("STALE", age_days, True, True)
    - ngược lại -> ("FRESH", age_days, False, True)
    Trả về (source_status, source_age_days, source_stale, source_verified).
    """
    if last_observed_date is None or pd.isna(last_observed_date):
        return "MISSING", None, False, False
    obs_date = last_observed_date.date() if isinstance(last_observed_date, (datetime, pd.Timestamp)) else last_observed_date
    age_days = (reference_date - obs_date).days
    if age_days < 0:
        return "FUTURE_DATE", age_days, False, False
    if age_days > max_source_staleness_days:
        return "STALE", age_days, True, True
    return "FRESH", age_days, False, True


def analyze_forecast_status(
    fc_df: pd.DataFrame,
    reference_date: date,
    last_observed_date: Optional[date] = None,
    horizon_days: int = 14,
    max_source_staleness_days: int = 14,
    expected_cinemas: Optional[dict[int, str] | list[int] | set[int]] = None,
    selected_cinema_key: Optional[int] = None,
    required_time_slots: Optional[list[str] | set[str]] = None,
) -> dict[str, Any]:
    """
    Phân tích trạng thái forecast theo đúng quy tắc khoa học:
    - Tách biệt is_test=True (test predictions) và is_test=False (future/production forecasts).
    - Horizon hợp lệ: (reference_date, reference_date + horizon_days].
      Ngày thứ horizon_days + 1 trở đi không làm horizon hợp lệ (OUT_OF_HORIZON).
    - Fail-closed khi thiếu last_observed_date (SOURCE_UNVERIFIED) hoặc nguồn > reference_date.
    - Kiểm tra độ đầy đủ theo từng cinema_key (và khung giờ bắt buộc nếu được cung cấp).
    - Đối chiếu data freshness giữa target_date, trained_at và last_observed_date.
    """
    horizon_end = reference_date + timedelta(days=horizon_days)
    source_status, source_age_days, source_stale, source_verified = _evaluate_source_freshness(
        reference_date=reference_date,
        last_observed_date=last_observed_date,
        max_source_staleness_days=max_source_staleness_days,
    )
    obs_norm = (
        last_observed_date.date()
        if isinstance(last_observed_date, (datetime, pd.Timestamp))
        else (None if last_observed_date is None or pd.isna(last_observed_date) else last_observed_date)
    )

    if fc_df is None or fc_df.empty:
        return {
            "status": "NO_DATA",
            "is_operational_ready": False,
            "future_unique_days": 0,
            "horizon_unique_days_any": 0,
            "horizon_end": horizon_end,
            "min_target_date": None,
            "max_target_date": None,
            "future_min_date": None,
            "future_max_date": None,
            "trained_date": None,
            "last_observed_date": obs_norm,
            "source_status": source_status,
            "source_verified": source_verified,
            "source_age_days": source_age_days,
            "source_stale": source_stale,
            "has_duplicate_grain": False,
            "cinema_coverage": {},
            "all_cinemas_complete": False,
        }

    df = fc_df.copy()
    df["target_date"] = df["target_date"].apply(
        lambda x: x.date() if isinstance(x, (datetime, pd.Timestamp)) else x
    )
    if "is_test" not in df.columns:
        df["is_test"] = False
    df["is_test"] = df["is_test"].fillna(False).astype(bool)

    grain_cols = [
        c for c in ["target_date", "cinema_key", "time_slot", "model_name", "is_test", "metric_type"]
        if c in df.columns
    ]
    has_duplicate_grain = bool(df.duplicated(subset=grain_cols).any()) if grain_cols else False

    prod_df = df[~df["is_test"]]
    horizon_df = prod_df[
        (prod_df["target_date"] > reference_date)
        & (prod_df["target_date"] <= horizon_end)
    ]
    horizon_days_any = int(horizon_df["target_date"].nunique()) if not horizon_df.empty else 0

    trained_date: Optional[date] = None
    if "trained_at" in df.columns and df["trained_at"].notna().any():
        max_tr = pd.to_datetime(df["trained_at"]).max()
        trained_date = max_tr.date() if pd.notna(max_tr) else None

    # Xác định phạm vi rạp cần kiểm tra
    if selected_cinema_key is not None:
        cinema_name = (
            expected_cinemas.get(selected_cinema_key)
            if isinstance(expected_cinemas, dict)
            else CINEMA_MAP.get(selected_cinema_key, f"Cinema {selected_cinema_key}")
        )
        target_cinemas: dict[int, str] = {int(selected_cinema_key): str(cinema_name)}
    elif isinstance(expected_cinemas, dict) and expected_cinemas:
        target_cinemas = {int(k): str(v) for k, v in expected_cinemas.items()}
    elif isinstance(expected_cinemas, (list, set, tuple)) and expected_cinemas:
        target_cinemas = {int(k): CINEMA_MAP.get(int(k), f"Cinema {k}") for k in sorted(expected_cinemas)}
    elif "cinema_key" in df.columns and df["cinema_key"].notna().any():
        target_cinemas = {}
        for ck in sorted(df["cinema_key"].dropna().astype(int).unique().tolist()):
            cname = CINEMA_MAP.get(ck)
            if not cname and "cinema_name" in df.columns:
                sub = df.loc[df["cinema_key"] == ck, "cinema_name"].dropna()
                cname = str(sub.iloc[0]) if not sub.empty else f"Cinema {ck}"
            target_cinemas[ck] = cname or f"Cinema {ck}"
    else:
        target_cinemas = {0: "Tất cả rạp"}

    expected_dates = [reference_date + timedelta(days=i) for i in range(1, horizon_days + 1)]
    req_slots = sorted({str(s) for s in required_time_slots if s is not None}) if required_time_slots else []
    check_slots = bool(req_slots) and ("time_slot" in horizon_df.columns)

    cinema_coverage: dict[int, dict[str, Any]] = {}
    common_dates: Optional[set[date]] = None

    for ck, cname in target_cinemas.items():
        if "cinema_key" in horizon_df.columns and ck != 0:
            c_df = horizon_df[horizon_df["cinema_key"] == ck]
        else:
            c_df = horizon_df

        present_dates = sorted(set(c_df["target_date"].tolist())) if not c_df.empty else []
        present_set = set(present_dates)
        missing_dates = [d for d in expected_dates if d not in present_set]

        missing_slots_by_date: dict[date, list[str]] = {}
        if check_slots and present_dates:
            for d in present_dates:
                day_slots = set(c_df.loc[c_df["target_date"] == d, "time_slot"].dropna().astype(str).tolist())
                miss_s = [s for s in req_slots if s not in day_slots]
                if miss_s:
                    missing_slots_by_date[d] = miss_s

        is_complete = (len(missing_dates) == 0) and (len(missing_slots_by_date) == 0)
        cinema_coverage[ck] = {
            "cinema_key": ck,
            "cinema_name": cname,
            "valid_days": len(present_dates),
            "expected_days": horizon_days,
            "missing_days": len(missing_dates),
            "missing_dates": missing_dates,
            "missing_slots_by_date": missing_slots_by_date,
            "is_complete": is_complete,
        }
        common_dates = present_set if common_dates is None else (common_dates & present_set)

    future_unique_days = len(common_dates) if common_dates is not None else 0
    all_cinemas_complete = bool(cinema_coverage) and all(
        info["is_complete"] for info in cinema_coverage.values()
    )

    if prod_df.empty and df["is_test"].all():
        status = "NO_FUTURE_FORECAST"
    elif horizon_days_any == 0:
        has_past = bool((prod_df["target_date"] <= reference_date).any())
        has_beyond = bool((prod_df["target_date"] > horizon_end).any())
        if has_past and has_beyond:
            status = "NO_VALID_HORIZON"
        elif has_beyond:
            status = "OUT_OF_HORIZON"
        else:
            status = "EXPIRED"
    elif not source_verified:
        status = "SOURCE_UNVERIFIED"
    elif source_stale:
        status = "STALE_SOURCE"
    elif all_cinemas_complete:
        status = "VALID"
    else:
        status = "PARTIAL"

    is_operational_ready = (
        (status == "VALID")
        and source_verified
        and (not source_stale)
        and all_cinemas_complete
        and (not has_duplicate_grain)
    )

    return {
        "status": status,
        "is_operational_ready": is_operational_ready,
        "future_unique_days": future_unique_days,
        "horizon_unique_days_any": horizon_days_any,
        "horizon_end": horizon_end,
        "min_target_date": df["target_date"].min(),
        "max_target_date": df["target_date"].max(),
        "future_min_date": prod_df["target_date"].min() if not prod_df.empty else None,
        "future_max_date": prod_df["target_date"].max() if not prod_df.empty else None,
        "trained_date": trained_date,
        "last_observed_date": obs_norm,
        "source_status": source_status,
        "source_verified": source_verified,
        "source_age_days": source_age_days,
        "source_stale": source_stale,
        "has_duplicate_grain": has_duplicate_grain,
        "cinema_coverage": cinema_coverage,
        "all_cinemas_complete": all_cinemas_complete,
    }


def prepare_backtest_comparison(
    fc_df: pd.DataFrame,
    actual_df: pd.DataFrame,
    selected_model: Optional[str] = None,
    metric_type: str = "tickets",
) -> tuple[pd.DataFrame, Optional[str]]:
    """
    Chuẩn bị dữ liệu hậu kiểm Forecast vs Actual ở đúng grain:
    - Lọc theo metric_type (mặc định 'tickets' để khớp ticket_qty_clean).
    - Kiểm tra trùng lặp tại grain (target_date, cinema_key, time_slot, model_name, is_test, metric_type).
    - Không bao giờ cộng yhat của nhiều model_name khác nhau thành một đường.
    Trả về (merged_df, warning_or_error_msg).
    """
    if fc_df is None or fc_df.empty:
        return pd.DataFrame(), "Không có dữ liệu dự báo để hậu kiểm."

    df = fc_df.copy()
    df["target_date"] = pd.to_datetime(df["target_date"]).dt.date

    if "metric_type" in df.columns:
        df = df[df["metric_type"] == metric_type]
        if df.empty:
            return pd.DataFrame(), f"Không có dữ liệu dự báo cho metric_type='{metric_type}'."

    if selected_model:
        df = df[df["model_name"] == selected_model]
        if df.empty:
            return pd.DataFrame(), f"Không có dữ liệu cho mô hình '{selected_model}'."

    grain_cols = [
        c for c in ["target_date", "cinema_key", "time_slot", "model_name", "is_test", "metric_type"]
        if c in df.columns
    ]
    if grain_cols and df.duplicated(subset=grain_cols).any():
        return pd.DataFrame(), (
            "Phát hiện bản ghi trùng tại grain (target_date × cinema × time_slot × model_name × is_test × metric_type). "
            "Từ chối tổng hợp để tránh cộng trùng dự báo."
        )

    # Nếu cùng (target_date, cinema_key, time_slot, model_name) có cả is_test=True và is_test=False,
    # phải giữ phân biệt hoặc chỉ lấy nhóm không trùng chéo trước khi sum theo ngày × model_name.
    per_slot_cols = [c for c in ["target_date", "cinema_key", "time_slot", "model_name", "metric_type"] if c in df.columns]
    if per_slot_cols and df.duplicated(subset=per_slot_cols).any():
        return pd.DataFrame(), (
            "Phát hiện chồng lấn giữa bản ghi test và production trên cùng slot × mô hình. "
            "Vui lòng lọc rõ loại dự báo trước khi tổng hợp."
        )

    fc_agg = (
        df.groupby(["target_date", "model_name"], as_index=False)
        .agg(yhat_total=("yhat", "sum"))
    )
    fc_agg["date"] = pd.to_datetime(fc_agg["target_date"])

    if actual_df is None or actual_df.empty:
        return fc_agg, None

    act = actual_df.copy()
    act["date"] = pd.to_datetime(act["full_date"])
    act_agg = act.groupby("date", as_index=False).agg(actual_total=("ticket_qty_clean", "sum"))

    merged = pd.merge(fc_agg, act_agg, on="date", how="left").sort_values(["model_name", "date"])
    return merged, None


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
    except Exception:
        logger.exception("Error loading forecast model metrics")
        return _error_df("Không thể tải metrics mô hình dự báo.")


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
    except Exception:
        logger.exception("Error loading historical tickets")
        return _error_df("Không thể tải lịch sử vé bán.")


# ---------------------------------------------------------------------------
# D. Reviews / NLP
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def load_review_summary(cinema_key: Optional[int] = None) -> pd.DataFrame:
    """
    Tổng hợp số lượng review và sentiment theo rạp.
    Hỗ trợ lọc cinema_key trực tiếp trong SQL để đảm bảo STAFF chỉ đọc rạp được phân công.
    """
    try:
        eng = _engine()
        cinema_clause = "WHERE rr.cinema_key = :cinema_key" if cinema_key is not None else ""
        params = {"cinema_key": cinema_key} if cinema_key is not None else {}
        sql = text(f"""
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
            {cinema_clause}
            GROUP BY rr.cinema_key, c.cinema_name
            ORDER BY rr.cinema_key
        """)
        with eng.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        return df
    except Exception:
        logger.exception("Error loading review summary")
        return _error_df("Không thể tải tổng hợp review từ cơ sở dữ liệu.")


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
    except Exception:
        logger.exception("Error loading review NLP detail")
        return _error_df("Không thể tải chi tiết review từ cơ sở dữ liệu.")


@st.cache_data(ttl=300, show_spinner=False)
def load_topic_distribution(cinema_key: Optional[int] = None) -> pd.DataFrame:
    """Tải phân bố topics từ review_nlp."""
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
    except Exception:
        logger.exception("Error loading topic distribution")
        return _error_df("Không thể tải phân bố chủ đề review.")


@st.cache_data(ttl=300, show_spinner=False)
def load_review_insights_data(cinema_key: int) -> pd.DataFrame:
    """Tải dữ liệu review + NLP theo cinema_key phục vụ reviews/insights.py."""
    try:
        eng = _engine()
        sql = text("""
            SELECT rr.review_id, rr.cinema_key, rr.rating, rr.published_date,
                   nl.sentiment_final, nl.topics
            FROM cinema_dw.review_raw rr
            LEFT JOIN cinema_dw.review_nlp nl ON rr.review_id = nl.review_id
            WHERE rr.cinema_key = :ck
        """)
        with eng.connect() as conn:
            return pd.read_sql(sql, conn, params={"ck": cinema_key})
    except Exception:
        logger.exception("Error loading review insights data")
        return _error_df("Không thể tải dữ liệu insights review.")


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
    except Exception:
        logger.exception("Error loading app users")
        return _error_df("Không thể tải danh sách tài khoản.")

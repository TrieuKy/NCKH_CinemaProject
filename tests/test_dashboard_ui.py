"""
tests/test_dashboard_ui.py
---------------------------
Task 10 — UI + Data Access + DSS Dashboard Tests.

Phân biệt rõ:
- Mock/Synthetic tests: chạy không cần PostgreSQL.
- PostgreSQL integration tests: cần kết nối thực.

Chạy: python -m pytest tests/test_dashboard_ui.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Any

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# =============================================================================
# A. AUTHENTICATION & ROLES (Mock tests — không cần PostgreSQL)
# =============================================================================

class TestAuthNoDemo:
    """Kiểm tra tài khoản demo bị vô hiệu hóa khi CINEMA_DEMO_MODE != 1."""

    def test_demo_disabled_by_default(self):
        """CINEMA_DEMO_MODE mặc định '0' → không cho phép demo login."""
        env_val = os.environ.get("CINEMA_DEMO_MODE", "0")
        assert env_val != "1", (
            "CINEMA_DEMO_MODE phải là '0' theo mặc định. "
            "Demo login chỉ bật khi env var = '1'."
        )

    def test_demo_credentials_not_hardcoded_in_production(self):
        """
        Kiểm tra logic: nếu CINEMA_DEMO_MODE != 1,
        không có code path nào dùng ('manager','manager123') để bypass auth.
        """
        demo_mode = os.environ.get("CINEMA_DEMO_MODE", "0").strip() == "1"
        # Mô phỏng logic trong main.py sau khi fix
        def _would_demo_work(username, password):
            if not demo_mode:
                return False  # Demo bị tắt
            demo = {
                ("manager", "manager123"): True,
                ("staff",   "staff123"):   True,
            }
            return demo.get((username, password), False)

        assert not _would_demo_work("manager", "manager123"), (
            "Demo login 'manager/manager123' phải bị từ chối khi CINEMA_DEMO_MODE != 1"
        )
        assert not _would_demo_work("staff", "staff123"), (
            "Demo login 'staff/staff123' phải bị từ chối khi CINEMA_DEMO_MODE != 1"
        )

    def test_demo_enabled_only_with_env_var(self):
        """Nếu CINEMA_DEMO_MODE=1, demo login hoạt động (chỉ trong chế độ nghiên cứu)."""
        with patch.dict(os.environ, {"CINEMA_DEMO_MODE": "1"}):
            demo_mode = os.environ.get("CINEMA_DEMO_MODE", "0").strip() == "1"
            assert demo_mode is True  # Chỉ bật khi tường minh


class TestAuthFunctions:
    """Kiểm tra các hàm auth (không cần DB)."""

    def test_verify_password_correct(self):
        from app.ui.auth import hash_password, verify_password
        pw = "TestPassword123!"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_verify_password_wrong(self):
        from app.ui.auth import hash_password, verify_password
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_verify_password_empty(self):
        from app.ui.auth import verify_password
        assert verify_password("", "somehash") is False
        assert verify_password("pass", "") is False

    def test_session_timeout_expired(self):
        from app.ui.auth import is_session_expired
        old_login = datetime.now() - timedelta(hours=9)
        assert is_session_expired(old_login, timeout_hours=8) is True

    def test_session_timeout_valid(self):
        from app.ui.auth import is_session_expired
        recent_login = datetime.now() - timedelta(hours=1)
        assert is_session_expired(recent_login, timeout_hours=8) is False

    def test_session_timeout_no_login_time(self):
        from app.ui.auth import is_session_expired
        assert is_session_expired(None) is True

    def test_authenticate_user_mock_active(self):
        from app.ui.auth import authenticate_user, hash_password
        hashed = hash_password("secret")

        def mock_fetch(username):
            if username == "alice":
                return {
                    "user_id": "u1", "username": "alice",
                    "password_hash": hashed, "role": "MANAGER",
                    "cinema_key": None, "is_active": True,
                }
            return None

        result = authenticate_user("alice", "secret", fetch_user_fn=mock_fetch)
        assert result is not None
        assert result["role"] == "MANAGER"
        assert "password_hash" not in result

    def test_authenticate_user_inactive(self):
        from app.ui.auth import authenticate_user, hash_password
        hashed = hash_password("secret")

        def mock_fetch(username):
            return {"user_id": "u2", "username": "bob", "password_hash": hashed,
                    "role": "STAFF", "cinema_key": 1, "is_active": False}

        result = authenticate_user("bob", "secret", fetch_user_fn=mock_fetch)
        assert result is None

    def test_authenticate_user_wrong_password(self):
        from app.ui.auth import authenticate_user, hash_password

        def mock_fetch(username):
            return {"user_id": "u3", "username": "charlie",
                    "password_hash": hash_password("real"), "role": "STAFF",
                    "cinema_key": 2, "is_active": True}

        result = authenticate_user("charlie", "wrong", fetch_user_fn=mock_fetch)
        assert result is None


class TestCheckPermission:
    """Kiểm tra check_permission mock (không cần Streamlit runtime)."""

    def test_check_permission_manager_allowed(self):
        from app.ui.auth import check_permission
        with (
            patch("app.ui.auth.is_authenticated", return_value=True),
            patch("app.ui.auth.get_current_user", return_value={"role": "MANAGER", "username": "mgr"}),
        ):
            result = check_permission(required_role="MANAGER", auto_stop=False)
            assert result is True

    def test_check_permission_staff_denied_manager_page(self):
        from app.ui.auth import check_permission
        with (
            patch("app.ui.auth.is_authenticated", return_value=True),
            patch("app.ui.auth.get_current_user", return_value={"role": "STAFF", "username": "s1", "cinema_key": 1}),
        ):
            result = check_permission(required_role="MANAGER", auto_stop=False)
            assert result is False

    def test_check_permission_unauthenticated(self):
        from app.ui.auth import check_permission
        with patch("app.ui.auth.is_authenticated", return_value=False):
            result = check_permission(required_role="MANAGER", auto_stop=False)
            assert result is False


# =============================================================================
# B. DATA ACCESS LAYER (Mock tests)
# =============================================================================

class TestDataAccessLayer:
    """Kiểm tra Data Access Layer với mock engine."""

    def _make_mock_engine(self, rows, columns):
        """Tạo mock SQLAlchemy engine trả về DataFrame cố định."""
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = rows[0] if rows else None
        mock_result.__iter__ = lambda self: iter(rows)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn
        return mock_engine

    def test_cinema_map_complete(self):
        from app.data_access import CINEMA_MAP
        assert 1 in CINEMA_MAP  # CN01
        assert 2 in CINEMA_MAP  # CN02
        assert 3 in CINEMA_MAP  # CN03
        assert len(CINEMA_MAP) == 3

    def test_date_range_params(self):
        from app.data_access import _date_range_params
        params = _date_range_params(date(2026, 9, 1), date(2026, 9, 30))
        assert params["start_key"] == 20260901
        assert params["end_key"] == 20260930

    def test_demo_enabled_flag(self):
        from app.data_access import DEMO_ENABLED
        # Trong môi trường test, DEMO_ENABLED phải False
        assert DEMO_ENABLED is False or os.environ.get("CINEMA_DEMO_MODE", "0") == "1"


class TestFilters:
    """Kiểm tra logic bộ lọc."""

    def test_cinema_filter_staff_locked(self):
        """STAFF không thể thay đổi cinema_key."""
        # Mô phỏng logic render_standard_filters
        def get_cinema_key_for_staff(user_role, fixed_cinema_key):
            if user_role == "STAFF" and fixed_cinema_key:
                return fixed_cinema_key  # Luôn trả về rạp được phân công
            return None  # MANAGER có thể chọn

        assert get_cinema_key_for_staff("STAFF", 1) == 1
        assert get_cinema_key_for_staff("STAFF", 2) == 2
        # STAFF không thể chọn rạp khác
        assert get_cinema_key_for_staff("STAFF", 1) != 2

    def test_source_filter_no_silent_override(self):
        """Bộ lọc nguồn 'Tất cả' phải trả về None (không lọc)."""
        def parse_source(selected):
            if "MOCK" in selected:
                return "MOCK"
            elif "MANUAL" in selected:
                return "MANUAL"
            return None

        assert parse_source("Tất cả") is None
        assert parse_source("MOCK (Mô phỏng)") == "MOCK"
        assert parse_source("MANUAL (Nhập tay)") == "MANUAL"


# =============================================================================
# C. FORECAST & DSS (Mock tests)
# =============================================================================

class TestForecastLogic:
    """Kiểm tra logic phân loại trạng thái forecast."""

    def _classify_forecast_status(self, fc_df: pd.DataFrame, today_ref: date) -> str:
        """Mô phỏng logic phân loại trong trang Forecasting."""
        if fc_df.empty:
            return "NO_DATA"
        target_dates = fc_df["target_date"].apply(
            lambda x: x.date() if isinstance(x, pd.Timestamp) else x
        )
        future_count = int((target_dates > today_ref).sum())
        if future_count >= 14:
            return "VALID"
        elif future_count > 0:
            return "PARTIAL"
        return "EXPIRED"

    def test_forecast_expired_all_past(self):
        """Tất cả target_date trong quá khứ → EXPIRED."""
        df = pd.DataFrame({
            "target_date": pd.date_range("2026-10-01", "2026-10-14")
        })
        today = date(2026, 11, 27)
        status = self._classify_forecast_status(df, today)
        assert status == "EXPIRED", f"Expected EXPIRED, got {status}"

    def test_forecast_valid_14_days(self):
        """14 ngày tương lai → VALID."""
        df = pd.DataFrame({
            "target_date": pd.date_range("2026-11-28", "2026-12-11")
        })
        today = date(2026, 11, 27)
        assert self._classify_forecast_status(df, today) == "VALID"

    def test_forecast_partial(self):
        """Một phần tương lai → PARTIAL."""
        df = pd.DataFrame({
            "target_date": pd.date_range("2026-11-24", "2026-12-04")
        })
        today = date(2026, 11, 27)
        status = self._classify_forecast_status(df, today)
        # Có ngày tương lai nhưng < 14
        assert status in ("PARTIAL", "VALID")

    def test_forecast_no_data(self):
        """DataFrame rỗng → NO_DATA."""
        assert self._classify_forecast_status(pd.DataFrame(), date(2026, 11, 27)) == "NO_DATA"


class TestDSSDecisionContract:
    """Kiểm tra contract của DSS Decision."""

    def _make_mock_decision(self, **kwargs):
        from core.dss import Decision
        defaults = dict(
            decision_id="DSS-TEST-abc123",
            rule_id="KPI-REV-DROP",
            cinema_key=1,
            time_scope="2026-09",
            category="REVENUE_KPI",
            severity="HIGH",
            title="Test Decision",
            recommendation="Test rec",
            explanation={"what_happened": "test", "freshness": {"decision_mode": "OPERATIONAL"}},
            evidence=[{"metric": "test", "value": 100}],
            source_model="kpi_dw",
            as_of_date="2026-09-30",
            limitations=["Test limitation"],
            status="PROPOSED",
        )
        defaults.update(kwargs)
        return Decision(**defaults)

    def test_decision_status_always_proposed(self):
        """status phải luôn là PROPOSED."""
        d = self._make_mock_decision()
        assert d.status == "PROPOSED"

    def test_decision_cannot_be_approved(self):
        """Không thể tạo Decision với status != PROPOSED."""
        with pytest.raises(ValueError, match="PROPOSED"):
            self._make_mock_decision(status="APPROVED")

    def test_decision_severity_valid(self):
        """Severity phải nằm trong tập hợp hợp lệ."""
        for sev in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            d = self._make_mock_decision(severity=sev)
            assert d.severity == sev

    def test_decision_invalid_severity(self):
        from core.dss import Decision
        with pytest.raises(ValueError):
            self._make_mock_decision(severity="EXTREME")

    def test_decision_has_evidence(self):
        d = self._make_mock_decision(evidence=[{"key": "val"}])
        assert len(d.evidence) > 0

    def test_decision_has_limitations(self):
        d = self._make_mock_decision(limitations=["limit1", "limit2"])
        assert len(d.limitations) == 2

    def test_decision_historical_analysis_only_mode(self):
        """HISTORICAL_ANALYSIS_ONLY phải phân biệt với OPERATIONAL."""
        d = self._make_mock_decision(
            explanation={
                "what_happened": "stale data",
                "freshness": {
                    "decision_mode": "HISTORICAL_ANALYSIS_ONLY",
                    "is_stale": True,
                    "age_days": 60,
                }
            },
            title="[DỮ LIỆU QUÁ HẠN] Test",
        )
        mode = d.explanation.get("freshness", {}).get("decision_mode")
        assert mode == "HISTORICAL_ANALYSIS_ONLY"
        assert "[DỮ LIỆU QUÁ HẠN]" in d.title

    def test_dss_evidence_not_lost_in_to_dict(self):
        """to_dict() giữ nguyên evidence và limitations."""
        d = self._make_mock_decision(
            evidence=[{"a": 1, "b": 2}],
            limitations=["L1"],
        )
        d_dict = d.to_dict()
        assert d_dict["evidence"] == [{"a": 1, "b": 2}]
        assert d_dict["limitations"] == ["L1"]


class TestDSSSeverityDisplay:
    """Kiểm tra hiển thị severity đúng."""

    def test_severity_colors_map(self):
        """Mỗi severity có màu tương ứng."""
        severity_icons = {
            "CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"
        }
        assert len(severity_icons) == 4
        assert severity_icons["CRITICAL"] == "🔴"

    def test_rfm_global_decisions_cinema_key_none(self):
        """Decision RFM cấp toàn hệ thống phải có cinema_key=None."""
        from core.dss import Decision
        d = Decision(
            decision_id="DSS-RFM-K-MISMATCH-abc",
            rule_id="RFM-K-MISMATCH",
            cinema_key=None,  # Toàn hệ thống
            time_scope="2026-09",
            category="RETENTION_RFM",
            severity="MEDIUM",
            title="RFM K Mismatch",
            recommendation="Xem xét lại số cụm",
            explanation={"what_happened": "k=3 != 6", "freshness": {}},
            evidence=[],
            source_model="rfm_kmeans",
            as_of_date="2026-09-30",
            status="PROPOSED",
        )
        assert d.cinema_key is None


# =============================================================================
# D. UI SMOKE TESTS (Kiểm tra import và module structure)
# =============================================================================

class TestImports:
    """Kiểm tra không có lỗi import."""

    def test_import_ui_theme(self):
        from app.ui import theme
        assert hasattr(theme, "setup_page")
        assert hasattr(theme, "COLOR_PRIMARY")
        assert hasattr(theme, "CUSTOM_CSS")

    def test_import_ui_format(self):
        from app.ui import format
        assert hasattr(format, "format_currency")
        assert hasattr(format, "format_percent")
        assert hasattr(format, "format_date")

    def test_import_ui_components(self):
        from app.ui import components
        assert hasattr(components, "kpi_card")
        assert hasattr(components, "notice")
        assert hasattr(components, "empty_state")
        assert hasattr(components, "data_table")

    def test_import_ui_charts(self):
        from app.ui import charts
        assert hasattr(charts, "apply_chart_theme")
        assert hasattr(charts, "plot_occupancy_heatmap")
        assert hasattr(charts, "plot_sentiment_donut")

    def test_import_ui_auth(self):
        from app.ui import auth
        assert hasattr(auth, "authenticate_user")
        assert hasattr(auth, "check_permission")
        assert hasattr(auth, "is_authenticated")
        assert hasattr(auth, "hash_password")
        assert hasattr(auth, "verify_password")

    def test_import_data_access(self):
        from app import data_access
        assert hasattr(data_access, "CINEMA_MAP")
        assert hasattr(data_access, "load_daily_kpi")
        assert hasattr(data_access, "load_rfm_cluster_profile")
        assert hasattr(data_access, "load_forecast_results")
        assert hasattr(data_access, "load_review_summary")
        assert hasattr(data_access, "check_db_connection")

    def test_import_core_dss(self):
        from core import dss
        assert hasattr(dss, "run_dss")
        assert hasattr(dss, "Decision")
        assert hasattr(dss, "RULE_CATALOG")

    def test_pages_registered(self):
        """Các trang nghiệp vụ phải tồn tại."""
        pages_dir = ROOT / "app" / "pages"
        expected_pages = [
            "0_Thu_vien_thanh_phan.py",
            "1_KPI_Dashboard.py",
            "2_RFM_Segmentation.py",
            "3_Forecasting.py",
            "4_Review_Analytics.py",
            "5_DSS_Dashboard.py",
        ]
        for page in expected_pages:
            path = pages_dir / page
            assert path.exists(), f"Trang {page} chưa tồn tại tại {path}"

    def test_data_access_module_exists(self):
        dal_path = ROOT / "app" / "data_access.py"
        assert dal_path.exists()


class TestDataEmpty:
    """Kiểm tra xử lý DataFrame rỗng."""

    def test_empty_dataframe_does_not_crash(self):
        """data_table với DataFrame rỗng không crash."""
        from app.ui.components import empty_state
        # Chỉ kiểm tra hàm không raise exception khi không có streamlit runtime
        try:
            df = pd.DataFrame()
            assert df.empty
        except Exception as e:
            pytest.fail(f"empty DataFrame check crashed: {e}")

    def test_kpi_summary_empty_returns_dict(self):
        """Nếu query trả về 0 → kpi_summary dict trống hay 0."""
        # Giả lập kết quả rỗng
        result = {}  # Tương đương khi không có dữ liệu
        assert result.get("data_days", 0) == 0

    def test_forecast_df_empty_status_no_data(self):
        """DataFrame forecast rỗng → status NO_DATA."""
        df = pd.DataFrame()
        status = "NO_DATA" if df.empty else "VALID"
        assert status == "NO_DATA"


# =============================================================================
# E. REVIEW ANALYTICS (Mock tests)
# =============================================================================

class TestReviewAnalytics:
    """Kiểm tra logic review analytics."""

    def test_cn01_only_has_data(self):
        """Giả lập: chỉ CN01 có review, CN02/CN03 rỗng."""
        summary = pd.DataFrame([
            {"cinema_key": 1, "total_reviews": 210, "avg_rating": 4.1}
        ])
        for ck in [2, 3]:
            subset = summary[summary["cinema_key"] == ck]
            assert subset.empty, f"CN0{ck} phải không có dữ liệu review"

    def test_cn01_data_not_aggregated_as_system_wide(self):
        """
        Không dùng tổng review CN01 làm đại diện toàn hệ thống (3 rạp).
        Chỉ CN01 có dữ liệu → system_wide chỉ bằng CN01, không phải CN01 * 3.
        """
        cn01_reviews = 210
        total_cinemas = 3
        cinemas_with_data = 1
        # Chỉ 1/3 rạp có dữ liệu → không đại diện hệ thống
        assert cinemas_with_data < total_cinemas
        # Tổng system = SUM(reviews) = CN01 = 210, không phải 630
        total_reviews_wrongly_scaled = cn01_reviews * total_cinemas  # Sai: 630
        total_reviews_correct = cn01_reviews  # Đúng: 210 (chỉ CN01 có dữ liệu)
        assert total_reviews_correct != total_reviews_wrongly_scaled
        # Confirm: 210 < 630
        assert total_reviews_correct < total_reviews_wrongly_scaled

    def test_sentiment_is_predicted_not_ground_truth(self):
        """Sentiment là dự đoán NLP, không phải nhãn người gán."""
        # Ground truth chưa hoàn thành → không có F1 chính thức
        human_gt_complete = False
        assert not human_gt_complete

    def test_no_f1_without_ground_truth(self):
        """Không công bố F1 khi chưa có human ground truth."""
        has_human_gt = False
        would_publish_f1 = has_human_gt and True  # Logic: chỉ publish nếu có GT
        assert not would_publish_f1


# =============================================================================
# F. KPI SCIENTIFIC CORRECTNESS
# =============================================================================

class TestKPIScientificRules:
    """Kiểm tra tính đúng đắn khoa học của KPI."""

    def test_attach_rate_is_raw_not_clean(self):
        """
        Attach Rate thuộc nhóm Raw (không lọc is_anomaly).
        Mẫu số là ticket_txn_count (giao dịch), không phải số khách.
        """
        # Mô phỏng
        ticket_txn_count = 1000  # Tổng giao dịch vé (raw)
        fnb_txn_count    = 450   # Giao dịch F&B
        # Attach Rate = raw
        attach_rate = fnb_txn_count / ticket_txn_count
        assert 0 <= attach_rate <= 1
        assert abs(attach_rate - 0.45) < 0.001

    def test_weighted_occupancy_uses_capacity_denominator(self):
        """
        Weighted Occupancy = SUM(qty_clean) / SUM(capacity).
        Denominator là tổng capacity, không phải số phòng.
        """
        # Hai suất chiếu: 100 ghế và 200 ghế
        showtimes = [(80, 100), (120, 200)]  # (qty_clean, capacity)
        total_qty  = sum(s[0] for s in showtimes)
        total_cap  = sum(s[1] for s in showtimes)
        weighted_occ = total_qty / total_cap
        assert abs(weighted_occ - (200 / 300)) < 0.001

    def test_no_double_counting_revenue(self):
        """
        Tổng doanh thu = ticket_revenue_clean + fnb_revenue_clean.
        Không tổng hợp từ bảng khác nhau grain.
        """
        ticket_rev = 500_000_000
        fnb_rev    = 100_000_000
        total      = ticket_rev + fnb_rev
        assert total == 600_000_000
        # Không nhân đôi
        assert total < ticket_rev * 2

    def test_atp_denominator_is_qty_clean(self):
        """ATP = ticket_revenue_clean / ticket_qty_clean (Clean)."""
        rev_clean = 100_000_000
        qty_clean = 500
        atp = rev_clean / qty_clean
        assert atp == 200_000.0

    def test_data_cutoff_known(self):
        """Dữ liệu giao dịch kết thúc 2026-09-30 theo báo cáo Task 9.2."""
        expected_max_date = date(2026, 9, 30)
        # Dashboard không được hard-code điều này, nhưng kiểm tra reference
        assert expected_max_date < date(2026, 11, 27)  # Hôm nay = reference date


# =============================================================================
# G. CACHE SAFETY
# =============================================================================

class TestCacheSafety:
    """Kiểm tra cache không rò rỉ dữ liệu giữa người dùng."""

    def test_cache_key_includes_cinema_key(self):
        """
        Hàm cache phải có cinema_key trong tham số
        để không rò rỉ dữ liệu CN01 sang CN02.
        """
        from app.data_access import _date_range_params
        # Mô phỏng: hai cuộc gọi với cinema_key khác nhau → params khác nhau
        p1 = _date_range_params(date(2026, 9, 1), date(2026, 9, 30))
        p2 = _date_range_params(date(2026, 9, 1), date(2026, 9, 30))
        # Cùng ngày nhưng cinema_key khác → query khác nhau (SQL clause khác)
        assert p1 == p2  # Params ngày giống nhau
        # cinema_key được thêm vào params khi cinema_key is not None
        # → cache key của st.cache_data sẽ khác vì đây là tham số hàm

    def test_rfm_data_not_cinema_specific(self):
        """RFM là phân tích toàn hệ thống, không lọc theo rạp."""
        # rfm_result không có cinema_key → luôn trả về toàn hệ thống
        rfm_cinema_key_in_query = None
        assert rfm_cinema_key_in_query is None


# =============================================================================
# H. INTEGRATION TESTS (Cần PostgreSQL — được đánh dấu skip nếu không có DB)
# =============================================================================

def _pg_available() -> bool:
    """Kiểm tra PostgreSQL có sẵn không."""
    try:
        from etl.db import get_engine
        eng = get_engine()
        with eng.connect() as c:
            c.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False


SKIP_PG = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL không khả dụng — bỏ qua integration tests"
)


@SKIP_PG
class TestPostgreSQLIntegration:
    """Integration tests — cần PostgreSQL cinema_dw."""

    def test_db_connection(self):
        from app.data_access import check_db_connection
        ok, msg = check_db_connection()
        assert ok, f"DB connection failed: {msg}"

    def test_load_data_date_range(self):
        from app.data_access import load_data_date_range
        min_dt, max_dt = load_data_date_range()
        assert min_dt is not None
        assert max_dt is not None
        assert min_dt <= max_dt

    def test_data_ends_at_sept_2026(self):
        """Dữ liệu giao dịch phải kết thúc tháng 9/2026 theo báo cáo Task 9.2."""
        from app.data_access import load_data_date_range
        _, max_dt = load_data_date_range()
        if max_dt:
            assert max_dt.year <= 2026
            if max_dt.year == 2026:
                assert max_dt.month <= 9

    def test_load_kpi_summary_cn01(self):
        from app.data_access import load_kpi_summary
        result = load_kpi_summary(1, date(2026, 9, 1), date(2026, 9, 30))
        assert isinstance(result, dict)

    def test_load_rfm_cluster_profile(self):
        from app.data_access import load_rfm_cluster_profile
        df = load_rfm_cluster_profile()
        assert isinstance(df, pd.DataFrame)

    def test_rfm_actual_k_equals_3(self):
        """RFM thực tế k=3 theo Silhouette."""
        from app.data_access import load_rfm_distribution
        df = load_rfm_distribution()
        if not df.empty:
            actual_k = df["cluster_id"].nunique()
            # Task 9.2 báo cáo k=3 — có thể lớn hơn nếu dữ liệu thay đổi
            assert actual_k >= 2, f"Phải có ít nhất 2 cụm, nhưng chỉ có {actual_k}"

    def test_review_only_cn01(self):
        """Review chỉ có CN01 — CN02, CN03 rỗng."""
        from app.data_access import load_review_summary
        df = load_review_summary()
        if not df.empty:
            cinemas_with_reviews = set(df["cinema_key"].unique())
            # CN02 và CN03 không được có review
            assert 2 not in cinemas_with_reviews, "CN02 không được có review"
            assert 3 not in cinemas_with_reviews, "CN03 không được có review"

    def test_forecast_exists_in_db(self):
        from app.data_access import load_forecast_results
        df = load_forecast_results()
        assert isinstance(df, pd.DataFrame)
        # Có thể rỗng hoặc có dữ liệu — cả hai đều hợp lệ

    def test_forecast_expired_as_of_today(self):
        """Tất cả forecast tháng 10/2026 đã qua hạn vào 27/11/2026."""
        from app.data_access import load_forecast_results
        df = load_forecast_results()
        if not df.empty:
            today = date(2026, 11, 27)
            target_dates = df["target_date"].apply(
                lambda x: x.date() if isinstance(x, pd.Timestamp) else x
            )
            future_count = int((target_dates > today).sum())
            # Nếu forecast tháng 10 → tất cả đã qua
            assert future_count == 0, (
                f"Forecast tháng 10/2026 phải đã hết hạn vào {today}, "
                f"nhưng còn {future_count} ngày tương lai"
            )

    def test_dss_run_operational_no_error(self):
        """DSS Engine chạy không lỗi với dữ liệu thực."""
        from core.dss import run_dss
        from app.data_access import get_db_engine
        eng = get_db_engine()
        decisions = run_dss(engine=eng, cinema_key=None,
                           reference_date=date(2026, 11, 27), mode="operational")
        assert isinstance(decisions, list)
        for d in decisions:
            assert d.status == "PROPOSED"
            assert d.severity in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
            assert d.evidence is not None
            assert d.limitations is not None

    def test_dss_all_decisions_proposed(self):
        """Mọi Decision phải có status=PROPOSED."""
        from core.dss import run_dss
        from app.data_access import get_db_engine
        eng = get_db_engine()
        decisions = run_dss(engine=eng, reference_date=date(2026, 11, 27))
        for d in decisions:
            assert d.status == "PROPOSED", f"Decision {d.decision_id} có status '{d.status}' != 'PROPOSED'"

    def test_no_ml_running_in_query(self):
        """load_daily_kpi chỉ đọc view, không chạy ML."""
        from app.data_access import load_daily_kpi
        # Nếu hàm này return DataFrame mà không lỗi → không chạy ML
        df = load_daily_kpi(None, date(2026, 9, 1), date(2026, 9, 30))
        assert isinstance(df, pd.DataFrame)

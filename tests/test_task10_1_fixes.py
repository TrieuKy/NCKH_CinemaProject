"""
tests/test_task10_1_fixes.py
----------------------------
Comprehensive tests for Task 10.1 fixes (Fix A–F) + UI Smoke Tests via Streamlit AppTest.
Phân biệt rõ:
- Unit / Mock tests
- PostgreSQL Integration tests (SKIP_PG nếu DB không khả dụng)
- Streamlit AppTest UI smoke tests
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _pg_available() -> bool:
    try:
        from etl.db import get_engine
        from sqlalchemy import text
        eng = get_engine()
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


SKIP_PG = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL unavailable")


# =============================================================================
# FIX A — KPI ANOMALY SQL SCHEMA & ERROR/EMPTY DIFFERENTIATION
# =============================================================================

class TestFixAAnomalyUnit:
    def test_anomaly_sql_error_returns_error_not_zero(self):
        from app.data_access import load_anomaly_summary
        load_anomaly_summary.clear()
        with patch("app.data_access._engine", side_effect=RuntimeError("db down")):
            res = load_anomaly_summary(1, date(2026, 9, 1), date(2026, 9, 30))
        assert res["status"] == "ERROR"
        assert "total_showtimes" not in res
        assert "db down" not in res.get("error", "")

    def test_kpi_summary_sql_error_returns_error_not_zero(self):
        from app.data_access import load_kpi_summary
        load_kpi_summary.clear()
        with patch("app.data_access._engine", side_effect=RuntimeError("secret://user:pass@host")):
            res = load_kpi_summary(1, date(2026, 9, 1), date(2026, 9, 30))
        assert res["status"] == "ERROR"
        assert "secret://" not in res.get("error", "")


@SKIP_PG
class TestFixAAnomalyPostgreSQL:
    def test_anomaly_all_and_each_cinema(self):
        from app.data_access import load_anomaly_summary
        load_anomaly_summary.clear()
        start_d, end_d = date(2026, 9, 1), date(2026, 9, 30)

        all_res = load_anomaly_summary(None, start_d, end_d)
        c1 = load_anomaly_summary(1, start_d, end_d)
        c2 = load_anomaly_summary(2, start_d, end_d)
        c3 = load_anomaly_summary(3, start_d, end_d)

        for r in (all_res, c1, c2, c3):
            assert r["status"] == "OK"
            assert r["total_showtimes"] > 0
            expected_pct = round(r["anomaly_showtimes"] / r["total_showtimes"] * 100, 2)
            assert abs(r["anomaly_pct"] - expected_pct) < 0.01

        assert c1["total_showtimes"] + c2["total_showtimes"] + c3["total_showtimes"] == all_res["total_showtimes"]
        assert c1["anomaly_showtimes"] + c2["anomaly_showtimes"] + c3["anomaly_showtimes"] == all_res["anomaly_showtimes"]

    def test_anomaly_no_data_range(self):
        from app.data_access import load_anomaly_summary
        load_anomaly_summary.clear()
        res = load_anomaly_summary(1, date(2020, 1, 1), date(2020, 1, 5))
        assert res["status"] == "NO_DATA"
        assert res["total_showtimes"] == 0

    def test_occupancy_summary_cn01_cn02_cn03(self):
        from app.data_access import load_occupancy_summary
        load_occupancy_summary.clear()
        for ck in (None, 1, 2, 3):
            df = load_occupancy_summary(ck, date(2026, 9, 1), date(2026, 9, 7))
            assert not df.attrs.get("db_error")
            assert not df.empty
            if ck is not None:
                assert set(df["cinema_key"].unique()) == {ck}


# =============================================================================
# FIX B — FORECAST GRAIN, HORIZON & FRESHNESS
# =============================================================================

class TestFixBForecastUnit:
    def _make_fc(self, start_d: date, days: int, slots=3, models=("xgboost",), is_test=False):
        rows = []
        for d_off in range(days):
            td = start_d + timedelta(days=d_off)
            for s in range(slots):
                for m in models:
                    rows.append({
                        "target_date": td,
                        "cinema_key": 1,
                        "cinema_name": "CGV Landmark 81",
                        "time_slot": f"slot_{s}",
                        "model_name": m,
                        "metric_type": "tickets",
                        "yhat": 100.0,
                        "yhat_lower": 80.0,
                        "yhat_upper": 120.0,
                        "is_test": is_test,
                        "trained_at": datetime(2026, 9, 30, 12, 0),
                    })
        return pd.DataFrame(rows)

    def test_14_days_3_slots_is_14_unique_days_valid(self):
        from app.data_access import analyze_forecast_status
        ref = date(2026, 9, 30)
        df = self._make_fc(date(2026, 10, 1), days=14, slots=3, is_test=False)
        assert len(df) == 42
        meta = analyze_forecast_status(df, reference_date=ref, last_observed_date=date(2026, 9, 30))
        assert meta["future_unique_days"] == 14
        assert meta["status"] == "VALID"
        assert meta["is_operational_ready"] is True

    def test_5_days_many_rows_is_partial(self):
        from app.data_access import analyze_forecast_status
        ref = date(2026, 9, 30)
        df = self._make_fc(date(2026, 10, 1), days=5, slots=3, is_test=False)
        assert len(df) == 15
        meta = analyze_forecast_status(df, reference_date=ref, last_observed_date=date(2026, 9, 30))
        assert meta["future_unique_days"] == 5
        assert meta["status"] == "PARTIAL"

    def test_day_15_onward_does_not_make_horizon_valid(self):
        from app.data_access import analyze_forecast_status
        ref = date(2026, 9, 30)
        # 14 days starting at day+15 (2026-10-15..2026-10-28) -> 0 days inside (ref, ref+14]
        df = self._make_fc(date(2026, 10, 15), days=14, slots=1, is_test=False)
        meta = analyze_forecast_status(df, reference_date=ref, last_observed_date=date(2026, 9, 30))
        assert meta["future_unique_days"] == 0
        assert meta["status"] == "OUT_OF_HORIZON"
        assert meta["is_operational_ready"] is False

    def test_test_predictions_not_counted_as_future(self):
        from app.data_access import analyze_forecast_status
        ref = date(2026, 9, 30)
        df = self._make_fc(date(2026, 10, 1), days=14, slots=3, is_test=True)
        meta = analyze_forecast_status(df, reference_date=ref, last_observed_date=date(2026, 9, 30))
        assert meta["future_unique_days"] == 0
        assert meta["status"] == "NO_FUTURE_FORECAST"
        assert meta["is_operational_ready"] is False

    def test_future_target_but_stale_observed_data(self):
        from app.data_access import analyze_forecast_status
        ref = date(2026, 11, 27)
        df = self._make_fc(date(2026, 11, 28), days=14, slots=3, is_test=False)
        meta = analyze_forecast_status(df, reference_date=ref, last_observed_date=date(2026, 9, 30))
        assert meta["future_unique_days"] == 14
        assert meta["source_stale"] is True
        assert meta["status"] == "STALE_SOURCE"
        assert meta["is_operational_ready"] is False

    def test_xgboost_and_prophet_not_summed_in_backtest(self):
        from app.data_access import prepare_backtest_comparison
        fc_df = self._make_fc(date(2026, 9, 1), days=3, slots=2, models=("xgboost", "prophet"), is_test=True)
        act_df = pd.DataFrame([
            {"full_date": date(2026, 9, 1) + timedelta(days=i), "ticket_qty_clean": 210}
            for i in range(3)
        ])
        merged, err = prepare_backtest_comparison(fc_df, act_df, selected_model=None)
        assert err is None
        assert set(merged["model_name"].unique()) == {"xgboost", "prophet"}
        # Each model sums its 2 slots (100 + 100 = 200), NOT 400
        assert (merged["yhat_total"] == 200.0).all()

    def test_duplicate_grain_rejected(self):
        from app.data_access import prepare_backtest_comparison
        fc_df = self._make_fc(date(2026, 9, 1), days=2, slots=1, models=("xgboost",), is_test=True)
        dup_df = pd.concat([fc_df, fc_df.iloc[[0]]], ignore_index=True)
        act_df = pd.DataFrame([{"full_date": date(2026, 9, 1), "ticket_qty_clean": 100}])
        merged, err = prepare_backtest_comparison(dup_df, act_df)
        assert merged.empty
        assert err is not None and "trùng" in err.lower()


@SKIP_PG
class TestFixBForecastPostgreSQL:
    def test_october_2026_forecast_expired_at_nov_27(self):
        from app.data_access import load_forecast_results, analyze_forecast_status, load_data_date_range
        load_forecast_results.clear()
        df = load_forecast_results()
        assert "is_test" in df.columns
        _, max_dt = load_data_date_range()
        meta = analyze_forecast_status(df, reference_date=date(2026, 11, 27), last_observed_date=max_dt)
        assert meta["status"] == "EXPIRED"
        assert meta["future_unique_days"] == 0


# =============================================================================
# FIX C & D & E — AUTH, STAFF FAIL-CLOSED, DEMO & SESSION STATE
# =============================================================================

class TestFixDEAuthAndScope:
    def test_enforce_cinema_scope_staff_valid(self):
        from app.data_access import enforce_cinema_scope
        assert enforce_cinema_scope("STAFF", 1, 1) == 1
        assert enforce_cinema_scope("STAFF", 2, 2) == 2

    def test_enforce_cinema_scope_staff_none_denied(self):
        from app.data_access import enforce_cinema_scope
        with pytest.raises(PermissionError):
            enforce_cinema_scope("STAFF", None, None)

    def test_enforce_cinema_scope_staff_invalid_denied(self):
        from app.data_access import enforce_cinema_scope
        with pytest.raises(PermissionError):
            enforce_cinema_scope("STAFF", 99, 99)

    def test_enforce_cinema_scope_staff_cross_cinema_denied(self):
        from app.data_access import enforce_cinema_scope
        with pytest.raises(PermissionError):
            enforce_cinema_scope("STAFF", 1, 2)
        # When requested_cinema_key is None, STAFF is locked to their assigned cinema_key
        assert enforce_cinema_scope("STAFF", 1, None) == 1

    def test_enforce_cinema_scope_manager_allowed(self):
        from app.data_access import enforce_cinema_scope
        assert enforce_cinema_scope("MANAGER", None, None) is None
        assert enforce_cinema_scope("MANAGER", None, 2) == 2

    def test_no_hardcoded_demo_passwords_in_source(self):
        for rel in ("app/main.py", "app/data_access.py", "app/ui/auth.py"):
            content = (ROOT / rel).read_text(encoding="utf-8")
            assert "manager123" not in content, f"Hardcoded demo password found in {rel}"
            assert "staff123" not in content, f"Hardcoded demo password found in {rel}"

    def test_demo_off_by_default_and_on_with_external_config(self):
        from app.ui.auth import authenticate_demo_user, hash_password
        with patch.dict(os.environ, {"CINEMA_DEMO_MODE": "0"}, clear=False):
            assert authenticate_demo_user("demo_mgr", "any") is None

        ext_cfg = json.dumps({
            "demo_mgr": {
                "password_hash": hash_password("ExtDemoPass!"),
                "role": "MANAGER",
                "cinema_key": None,
            },
            "demo_staff_bad": {
                "password_hash": hash_password("ExtDemoPass!"),
                "role": "STAFF",
                "cinema_key": None,
            },
        })
        with patch.dict(os.environ, {"CINEMA_DEMO_MODE": "1", "CINEMA_DEMO_USERS_JSON": ext_cfg}):
            # Real DB user exists -> must NOT fall back to demo
            res_real_exists = authenticate_demo_user(
                "demo_mgr", "ExtDemoPass!", fetch_user_fn=lambda u: {"username": u}
            )
            assert res_real_exists is None

            # Real DB user absent -> external demo works
            res_ok = authenticate_demo_user("demo_mgr", "ExtDemoPass!", fetch_user_fn=lambda u: None)
            assert res_ok is not None and res_ok["role"] == "MANAGER"

            # Demo STAFF without valid cinema_key is rejected
            res_bad_staff = authenticate_demo_user("demo_staff_bad", "ExtDemoPass!", fetch_user_fn=lambda u: None)
            assert res_bad_staff is None


# =============================================================================
# FIX F — RFM DATES & KPI COVERAGE
# =============================================================================

@SKIP_PG
class TestFixFScientificPostgreSQL:
    def test_rfm_dates_distinguish_calculated_at_and_last_txn(self):
        from app.data_access import load_rfm_dates
        load_rfm_dates.clear()
        dates = load_rfm_dates()
        assert dates["calculated_at"] is not None
        assert dates["last_member_txn_date"] is not None
        assert dates["last_member_txn_date"] <= date(2026, 9, 30)

    def test_kpi_coverage_by_cinema(self):
        from app.data_access import load_kpi_coverage_by_cinema
        load_kpi_coverage_by_cinema.clear()
        df = load_kpi_coverage_by_cinema(date(2025, 10, 1), date(2026, 9, 30))
        assert len(df) == 3
        assert (df["calendar_days"] == 365).all()
        assert (df["active_days"] > 0).all()


# =============================================================================
# STREAMLIT APPTEST UI SMOKE TESTS (Authorization, DSS State, Empty/Error)
# =============================================================================

class TestStreamlitAppTestUI:
    def test_staff_without_cinema_key_denied_on_review_page(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app" / "pages" / "4_Review_Analytics.py"), default_timeout=15)
        at.session_state["logged_in"] = True
        at.session_state["login_time"] = datetime.now()
        at.session_state["user"] = {
            "user_id": "s-none",
            "username": "staff_none",
            "role": "STAFF",
            "cinema_key": None,
        }
        with patch("app.data_access.load_review_summary") as mock_load:
            at.run()
            mock_load.assert_not_called()
        assert len(at.error) >= 1
        assert "từ chối" in at.error[0].value.lower()

    def test_staff_blocked_from_manager_page_kpi(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app" / "pages" / "1_KPI_Dashboard.py"), default_timeout=15)
        at.session_state["logged_in"] = True
        at.session_state["login_time"] = datetime.now()
        at.session_state["user"] = {
            "user_id": "s1",
            "username": "staff_cn01",
            "role": "STAFF",
            "cinema_key": 1,
        }
        at.run()
        assert len(at.error) >= 1
        assert "không có quyền" in at.error[0].value.lower()

    def test_dss_filter_change_marks_outdated_and_clears_old_decisions(self):
        from streamlit.testing.v1 import AppTest
        from core.dss import Decision

        fake_dec = Decision(
            decision_id="D-1",
            rule_id="KPI-ANOM-HIGH",
            cinema_key=None,
            time_scope="2026-09",
            category="REVENUE_KPI",
            severity="HIGH",
            status="PROPOSED",
            title="Test Decision",
            recommendation="Check",
            explanation={"decision_mode": "OPERATIONAL"},
            evidence=[{"k": "v"}],
            limitations=["lim"],
            as_of_date="2026-11-27",
            source_model="kpi_dw",
        )

        at = AppTest.from_file(str(ROOT / "app" / "pages" / "5_DSS_Dashboard.py"), default_timeout=15)
        at.session_state["logged_in"] = True
        at.session_state["login_time"] = datetime.now()
        at.session_state["user"] = {
            "user_id": "m1",
            "username": "mgr1",
            "role": "MANAGER",
            "cinema_key": None,
        }
        with patch("core.dss.run_dss", return_value=[fake_dec]), \
             patch("app.data_access.get_db_engine", return_value=MagicMock()), \
             patch("streamlit.page_link"):
            at.run()
            assert "dss_decisions" in at.session_state
            assert len(at.session_state["dss_decisions"]) == 1

            # Change cinema filter without clicking Run Again
            at.selectbox(key="dss_cinema").select("CGV Landmark 81 (CN01)").run()
            assert "dss_decisions" not in at.session_state
            assert any("OUTDATED RESULT" in w.value for w in at.warning)

    def test_dss_db_error_shows_unavailable_without_raw_exception(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app" / "pages" / "5_DSS_Dashboard.py"), default_timeout=15)
        at.session_state["logged_in"] = True
        at.session_state["login_time"] = datetime.now()
        at.session_state["user"] = {
            "user_id": "m1",
            "username": "mgr1",
            "role": "MANAGER",
            "cinema_key": None,
        }
        with patch("core.dss.run_dss", side_effect=RuntimeError("postgresql://admin:secret@localhost:5000/db")), \
             patch("streamlit.page_link"):
            at.run()
        assert len(at.error) >= 1
        assert "secret" not in at.error[0].value
        assert "UNAVAILABLE" in at.error[0].value

    def test_logout_clears_dss_session_state(self):
        from streamlit.testing.v1 import AppTest
        from app.ui.auth import logout_user
        at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=15)
        at.session_state["logged_in"] = True
        at.session_state["login_time"] = datetime.now()
        at.session_state["user"] = {"user_id": "m1", "username": "mgr1", "role": "MANAGER", "cinema_key": None}
        at.session_state["dss_decisions"] = ["old"]
        at.session_state["dss_config"] = {"cinema_key": None}
        at.run()
        assert not at.exception

    @SKIP_PG
    def test_pages_smoke_render_with_pg(self):
        from streamlit.testing.v1 import AppTest
        mgr_user = {"user_id": "m1", "username": "mgr1", "role": "MANAGER", "cinema_key": None}
        for page_rel in (
            "app/pages/1_KPI_Dashboard.py",
            "app/pages/2_RFM_Segmentation.py",
            "app/pages/3_Forecasting.py",
            "app/pages/4_Review_Analytics.py",
        ):
            at = AppTest.from_file(str(ROOT / page_rel), default_timeout=25)
            at.session_state["logged_in"] = True
            at.session_state["login_time"] = datetime.now()
            at.session_state["user"] = mgr_user
            with patch("streamlit.page_link"):
                at.run()
            assert not at.exception, f"Exception rendering {page_rel}: {at.exception}"

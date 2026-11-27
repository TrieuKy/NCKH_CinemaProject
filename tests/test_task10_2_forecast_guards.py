"""
tests/test_task10_2_forecast_guards.py
--------------------------------------
Task 10.2 — Final Forecast Dashboard Scientific Guards:
- Fix A: Missing / future source date must fail closed (SOURCE_UNVERIFIED).
- Fix B: EXPIRED vs OUT_OF_HORIZON vs NO_VALID_HORIZON vs PARTIAL vs VALID.
- Fix C: Per-cinema horizon completeness (+ optional time_slot coverage).
- PostgreSQL integration check (2026-11-27 reference date -> EXPIRED).
- Streamlit AppTest UI smoke tests on app/pages/3_Forecasting.py.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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

CINEMAS_ALL = {
    1: "CGV Landmark 81 (CN01)",
    2: "CGV Vincom Đồng Khởi (CN02)",
    3: "CGV Pearl Plaza (CN03)",
}


def _make_fc(
    start_d: date,
    days: int,
    cinemas: tuple[int, ...] = (1, 2, 3),
    slots: tuple[str, ...] = ("Sáng", "Chiều", "Tối"),
    models: tuple[str, ...] = ("xgboost",),
    is_test: bool = False,
    trained_at: datetime | None = None,
) -> pd.DataFrame:
    rows = []
    tr = trained_at or datetime(2026, 11, 27, 8, 0)
    for d_off in range(days):
        td = start_d + timedelta(days=d_off)
        for ck in cinemas:
            for sl in slots:
                for m in models:
                    rows.append({
                        "target_date": td,
                        "cinema_key": ck,
                        "cinema_name": CINEMAS_ALL.get(ck, f"Cinema {ck}"),
                        "time_slot": sl,
                        "model_name": m,
                        "metric_type": "tickets",
                        "yhat": 120.0,
                        "yhat_lower": 100.0,
                        "yhat_upper": 140.0,
                        "is_test": is_test,
                        "trained_at": tr,
                    })
    return pd.DataFrame(rows)


# =============================================================================
# FIX A — MISSING / STALE / FUTURE SOURCE DATE (FAIL CLOSED)
# =============================================================================

class TestFixAMissingSourceDate:
    REF = date(2026, 11, 27)

    def test_14_days_null_source_is_source_unverified_not_operational(self):
        from app.data_access import analyze_forecast_status
        df = _make_fc(date(2026, 11, 28), days=14)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=None,
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "SOURCE_UNVERIFIED"
        assert meta["source_status"] == "MISSING"
        assert meta["source_verified"] is False
        assert meta["last_observed_date"] is None
        assert meta["is_operational_ready"] is False

    def test_14_days_fresh_source_is_valid_and_operational(self):
        from app.data_access import analyze_forecast_status
        df = _make_fc(date(2026, 11, 28), days=14)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 26),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "VALID"
        assert meta["source_status"] == "FRESH"
        assert meta["source_verified"] is True
        assert meta["source_stale"] is False
        assert meta["is_operational_ready"] is True

    def test_14_days_stale_source_not_operational(self):
        from app.data_access import analyze_forecast_status
        df = _make_fc(date(2026, 11, 28), days=14)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 9, 30),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "STALE_SOURCE"
        assert meta["source_status"] == "STALE"
        assert meta["source_stale"] is True
        assert meta["is_operational_ready"] is False

    def test_source_after_reference_date_not_valid_by_default(self):
        from app.data_access import analyze_forecast_status
        df = _make_fc(date(2026, 11, 28), days=14)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 12, 5),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "SOURCE_UNVERIFIED"
        assert meta["source_status"] == "FUTURE_DATE"
        assert meta["source_verified"] is False
        assert meta["is_operational_ready"] is False


# =============================================================================
# FIX B — EXPIRED VS OUT_OF_HORIZON VS NO_VALID_HORIZON
# =============================================================================

class TestFixBExpiredVsOutOfHorizon:
    REF = date(2026, 11, 27)  # Horizon: 2026-11-28 .. 2026-12-11

    def test_oct_01_14_is_expired(self):
        from app.data_access import analyze_forecast_status
        df = _make_fc(date(2026, 10, 1), days=14)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "EXPIRED"
        assert meta["future_unique_days"] == 0
        assert meta["is_operational_ready"] is False

    def test_dec_12_25_is_out_of_horizon_not_expired(self):
        from app.data_access import analyze_forecast_status
        df = _make_fc(date(2026, 12, 12), days=14)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "OUT_OF_HORIZON"
        assert meta["future_unique_days"] == 0
        assert meta["is_operational_ready"] is False

    def test_mixed_past_and_beyond_horizon_with_zero_in_horizon_is_no_valid_horizon(self):
        from app.data_access import analyze_forecast_status
        past_df = _make_fc(date(2026, 10, 1), days=5)
        beyond_df = _make_fc(date(2026, 12, 12), days=5)
        df = pd.concat([past_df, beyond_df], ignore_index=True)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "NO_VALID_HORIZON"
        assert meta["future_unique_days"] == 0
        assert meta["is_operational_ready"] is False

    def test_nov_28_dec_11_valid_when_complete_and_fresh(self):
        from app.data_access import analyze_forecast_status
        df = _make_fc(date(2026, 11, 28), days=14)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "VALID"
        assert meta["future_unique_days"] == 14
        assert meta["horizon_end"] == date(2026, 12, 11)
        assert meta["is_operational_ready"] is True

    def test_partial_horizon_plus_out_of_horizon_still_partial(self):
        from app.data_access import analyze_forecast_status
        # Only 10 days inside (11-28..12-07) + 10 days beyond (12-12..12-21)
        in_df = _make_fc(date(2026, 11, 28), days=10)
        beyond_df = _make_fc(date(2026, 12, 12), days=10)
        df = pd.concat([in_df, beyond_df], ignore_index=True)
        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            expected_cinemas=CINEMAS_ALL,
        )
        assert meta["status"] == "PARTIAL"
        assert meta["future_unique_days"] == 10
        assert meta["is_operational_ready"] is False


# =============================================================================
# FIX C — PER-CINEMA HORIZON COMPLETENESS
# =============================================================================

class TestFixCPerCinemaCompleteness:
    REF = date(2026, 11, 27)

    def test_all_cinemas_cn01_full_cn02_missing_is_partial_not_valid(self):
        from app.data_access import analyze_forecast_status
        # CN01 has all 14 days, CN02 has 5 days, CN03 has 0 days
        df1 = _make_fc(date(2026, 11, 28), days=14, cinemas=(1,))
        df2 = _make_fc(date(2026, 11, 28), days=5, cinemas=(2,))
        df = pd.concat([df1, df2], ignore_index=True)

        meta = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            expected_cinemas=CINEMAS_ALL,
            selected_cinema_key=None,
        )
        assert meta["status"] == "PARTIAL"
        assert meta["is_operational_ready"] is False
        assert meta["all_cinemas_complete"] is False
        cov = meta["cinema_coverage"]
        assert cov[1]["valid_days"] == 14 and cov[1]["missing_days"] == 0 and cov[1]["is_complete"] is True
        assert cov[2]["valid_days"] == 5 and cov[2]["missing_days"] == 9 and cov[2]["is_complete"] is False
        assert cov[3]["valid_days"] == 0 and cov[3]["missing_days"] == 14 and cov[3]["is_complete"] is False

    def test_single_cinema_selected_ignores_other_missing_cinemas(self):
        from app.data_access import analyze_forecast_status
        df1 = _make_fc(date(2026, 11, 28), days=14, cinemas=(1,))
        meta = analyze_forecast_status(
            df1,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            expected_cinemas=CINEMAS_ALL,
            selected_cinema_key=1,
        )
        assert meta["status"] == "VALID"
        assert meta["is_operational_ready"] is True
        assert list(meta["cinema_coverage"].keys()) == [1]

    def test_optional_required_time_slots_checked_only_when_provided(self):
        from app.data_access import analyze_forecast_status
        # Only ("Sáng", "Chiều") present, "Tối" missing
        df = _make_fc(date(2026, 11, 28), days=14, cinemas=(1,), slots=("Sáng", "Chiều"))
        # Without required_time_slots -> does not assume all slots must exist
        meta_no_req = analyze_forecast_status(
            df, reference_date=self.REF, last_observed_date=date(2026, 11, 27), selected_cinema_key=1
        )
        assert meta_no_req["status"] == "VALID"
        assert meta_no_req["is_operational_ready"] is True

        # With required_time_slots -> detects missing "Tối"
        meta_req = analyze_forecast_status(
            df,
            reference_date=self.REF,
            last_observed_date=date(2026, 11, 27),
            selected_cinema_key=1,
            required_time_slots=["Sáng", "Chiều", "Tối"],
        )
        assert meta_req["status"] == "PARTIAL"
        assert meta_req["is_operational_ready"] is False
        assert len(meta_req["cinema_coverage"][1]["missing_slots_by_date"]) == 14


# =============================================================================
# POSTGRESQL INTEGRATION CHECK
# =============================================================================

@SKIP_PG
class TestTask102PostgreSQL:
    def test_current_db_forecast_expired_at_2026_11_27(self):
        from app.data_access import (
            load_forecast_results, load_data_date_range,
            load_expected_cinemas, analyze_forecast_status,
        )
        load_forecast_results.clear()
        load_data_date_range.clear()
        load_expected_cinemas.clear()

        fc_df = load_forecast_results()
        _, max_dt = load_data_date_range()
        expected = load_expected_cinemas()
        meta = analyze_forecast_status(
            fc_df,
            reference_date=date(2026, 11, 27),
            last_observed_date=max_dt,
            expected_cinemas=expected,
        )
        assert meta["status"] == "EXPIRED"
        assert meta["is_operational_ready"] is False
        assert set(meta["cinema_coverage"].keys()) == {1, 2, 3}


# =============================================================================
# STREAMLIT APPTEST UI SMOKE TESTS (3_Forecasting.py)
# =============================================================================

class TestForecastingAppTestUI:
    def _run_forecast_page(
        self,
        fc_df: pd.DataFrame,
        max_dt: date | None,
        expected_cinemas: dict[int, str] | None = None,
    ):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app" / "pages" / "3_Forecasting.py"), default_timeout=20)
        at.session_state["logged_in"] = True
        at.session_state["login_time"] = datetime.now()
        at.session_state["user"] = {
            "user_id": "m1",
            "username": "mgr1",
            "role": "MANAGER",
            "cinema_key": None,
        }
        empty_df = pd.DataFrame()
        with (
            patch("app.data_access.load_forecast_results", return_value=fc_df),
            patch("app.data_access.load_forecast_model_metrics", return_value=empty_df),
            patch("app.data_access.load_data_date_range", return_value=(date(2025, 10, 1), max_dt)),
            patch("app.data_access.load_expected_cinemas", return_value=expected_cinemas or CINEMAS_ALL),
            patch("app.data_access.load_historical_tickets", return_value=empty_df),
            patch("streamlit.page_link"),
        ):
            at.run()
        return at

    def test_ui_expired_forecast(self):
        df = _make_fc(date(2026, 10, 1), days=14)
        at = self._run_forecast_page(df, max_dt=date(2026, 9, 30))
        assert not at.exception
        md_text = "\n".join(m.value for m in at.markdown)
        assert "EXPIRED" in md_text

    def test_ui_out_of_horizon(self):
        today = date.today()
        df = _make_fc(today + timedelta(days=20), days=14)
        at = self._run_forecast_page(df, max_dt=today)
        assert not at.exception
        md_text = "\n".join(m.value for m in at.markdown)
        assert "OUT OF HORIZON" in md_text
        assert "OUT_OF_HORIZON" in md_text

    def test_ui_missing_source_date(self):
        today = date.today()
        df = _make_fc(today + timedelta(days=1), days=14)
        at = self._run_forecast_page(df, max_dt=None)
        assert not at.exception
        md_text = "\n".join(m.value for m in at.markdown)
        assert "SOURCE UNVERIFIED" in md_text
        assert "SOURCE_UNVERIFIED" in md_text

    def test_ui_partial_all_cinemas_incomplete(self):
        today = date.today()
        # CN01 full 14 days, CN02 & CN03 missing
        df = _make_fc(today + timedelta(days=1), days=14, cinemas=(1,))
        at = self._run_forecast_page(df, max_dt=today)
        assert not at.exception
        md_text = "\n".join(m.value for m in at.markdown)
        assert "PARTIAL" in md_text
        assert len(at.dataframe) >= 1

    def test_ui_empty_state(self):
        at = self._run_forecast_page(pd.DataFrame(), max_dt=None)
        assert not at.exception
        md_text = "\n".join(m.value for m in at.markdown)
        assert "FC-DATA-GAP" in md_text
        assert "NO_DATA" in md_text

    def test_ui_db_error_state(self):
        err_df = pd.DataFrame()
        err_df.attrs["db_error"] = True
        at = self._run_forecast_page(err_df, max_dt=None)
        assert not at.exception
        assert any("ERROR / UNAVAILABLE" in m.value for m in at.error)

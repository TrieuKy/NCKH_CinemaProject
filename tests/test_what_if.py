"""
tests/test_what_if.py
---------------------
Kiểm thử toàn diện Task 11 — What-If Scenario Simulation (UC07):
1. Unit Tests: Baseline & Numeric Correctness
2. Scientific Correctness Tests
3. Security & Reproducibility Tests
4. PostgreSQL Integration Tests (@SKIP_PG)
5. Streamlit AppTest UI Validation
"""

from __future__ import annotations

import math
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.what_if import (
    BaselineMetrics,
    ScenarioResult,
    compare_scenarios,
    fetch_baseline_from_db,
    simulate_fnb_attach_rate,
    simulate_ticket_price_change,
    simulate_ticket_volume_change,
)


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


def _sample_baseline(
    *,
    cinema_key: int | None = 1,
    ticket_revenue_clean: float = 100_000_000.0,
    ticket_qty_clean: int = 1000,
    fnb_revenue_clean: float = 30_000_000.0,
    available_showtime_capacity: int | None = 1500,
    ticket_txn_count: int = 500,
    fnb_txn_count: int = 200,
    sold_out_showtime_count: int = 0,
    capacity_reconciled: bool = True,
) -> BaselineMetrics:
    atp = (ticket_revenue_clean / ticket_qty_clean) if ticket_qty_clean > 0 else None
    occ = (
        (ticket_qty_clean / available_showtime_capacity)
        if (available_showtime_capacity and available_showtime_capacity > 0)
        else None
    )
    attach = (fnb_txn_count / ticket_txn_count) if ticket_txn_count > 0 else None
    avg_fnb = (fnb_revenue_clean / fnb_txn_count) if fnb_txn_count > 0 else None
    spp = (fnb_revenue_clean / ticket_txn_count) if ticket_txn_count > 0 else None

    return BaselineMetrics(
        cinema_key=cinema_key,
        cinema_name="CGV Landmark 81 (CN01)" if cinema_key == 1 else "Tất cả cụm rạp (CN01–CN03)",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
        data_days=30,
        max_observed_date=date(2026, 9, 30),
        ticket_revenue_clean=ticket_revenue_clean,
        ticket_qty_clean=ticket_qty_clean,
        atp_clean=atp,
        fnb_revenue_clean=fnb_revenue_clean,
        available_showtime_capacity=available_showtime_capacity,
        showtime_count=15,
        showtime_qty_clean=ticket_qty_clean,
        weighted_occupancy=occ,
        ticket_txn_count=ticket_txn_count,
        fnb_txn_count=fnb_txn_count,
        attach_rate=attach,
        avg_fnb_revenue_per_fnb_txn=avg_fnb,
        spp_clean=spp,
        limitations=(
            "Dữ liệu nền là Historical Baseline đã ghi nhận trong quá khứ.",
            "Không có dữ liệu chi phí nên không tính lợi nhuận/margin.",
            "Không sử dụng forecast đã hết hạn.",
        ),
        sold_out_showtime_count=sold_out_showtime_count,
        capacity_reconciled=capacity_reconciled,
    )


# =============================================================================
# 1. UNIT TESTS — BASELINE & NUMERIC CORRECTNESS
# =============================================================================

class TestWhatIfNumericCorrectness:
    def test_price_zero_percent_matches_baseline(self):
        base = _sample_baseline()
        res = simulate_ticket_price_change(base, 0.0)
        assert res.validation_status == "VALID"
        assert res.scenario_metrics["ticket_revenue_clean"] == pytest.approx(base.ticket_revenue_clean)
        assert res.scenario_metrics["atp_clean"] == pytest.approx(base.atp_clean)
        assert res.scenario_metrics["ticket_qty_clean"] == base.ticket_qty_clean
        assert res.absolute_deltas["ticket_revenue_delta"] == pytest.approx(0.0)

    def test_price_plus_minus_10_percent_keeps_qty_constant(self):
        base = _sample_baseline()
        res_up = simulate_ticket_price_change(base, 0.10)
        res_down = simulate_ticket_price_change(base, -0.10)

        assert res_up.scenario_metrics["ticket_qty_clean"] == base.ticket_qty_clean
        assert res_up.scenario_metrics["atp_clean"] == pytest.approx(110_000.0)
        assert res_up.scenario_metrics["ticket_revenue_clean"] == pytest.approx(110_000_000.0)
        assert res_up.absolute_deltas["ticket_revenue_delta"] == pytest.approx(10_000_000.0)

        assert res_down.scenario_metrics["ticket_qty_clean"] == base.ticket_qty_clean
        assert res_down.scenario_metrics["atp_clean"] == pytest.approx(90_000.0)
        assert res_down.scenario_metrics["ticket_revenue_clean"] == pytest.approx(90_000_000.0)
        assert res_down.absolute_deltas["ticket_revenue_delta"] == pytest.approx(-10_000_000.0)

    def test_volume_within_capacity_and_zero_pct(self):
        base = _sample_baseline(ticket_qty_clean=1000, available_showtime_capacity=1500)
        res0 = simulate_ticket_volume_change(base, 0.0)
        assert res0.scenario_metrics["ticket_qty_clean"] == 1000
        assert res0.scenario_metrics["ticket_revenue_clean"] == pytest.approx(base.ticket_revenue_clean)

        res_up = simulate_ticket_volume_change(base, 0.20)
        assert res_up.validation_status == "VALID"
        assert res_up.scenario_metrics["ticket_qty_clean"] == 1200
        assert res_up.scenario_metrics["occupancy"] == pytest.approx(1200 / 1500)
        assert res_up.scenario_metrics["unmet_demand_tickets"] == 0

        res_down = simulate_ticket_volume_change(base, -0.15)
        assert res_down.validation_status == "VALID"
        assert res_down.scenario_metrics["ticket_qty_clean"] == 850
        assert res_down.scenario_metrics["occupancy"] == pytest.approx(850 / 1500)

    def test_volume_exceeding_capacity_detected_and_capped(self):
        base = _sample_baseline(
            ticket_qty_clean=1000,
            ticket_revenue_clean=100_000_000.0,
            fnb_revenue_clean=30_000_000.0,
            available_showtime_capacity=1200,
        )
        res = simulate_ticket_volume_change(base, 0.50)  # 1500 > 1200
        assert res.validation_status == "CAPACITY_EXCEEDED"
        assert res.capacity_check_scope == "AGGREGATE_CAPACITY_CHECK"
        assert res.operational_feasibility_status == "CAPACITY_EXCEEDED_AGGREGATE"
        assert res.scenario_metrics["ticket_qty_clean"] == 1500
        assert res.scenario_metrics["potential_ticket_qty"] == 1500
        assert res.scenario_metrics["aggregate_capacity_upper_bound"] == 1200
        assert res.scenario_metrics["feasible_ticket_qty"] == 1200
        assert res.scenario_metrics["unmet_demand_tickets"] == 300
        assert res.scenario_metrics["potential_ticket_revenue_clean"] == pytest.approx(150_000_000.0)
        assert res.scenario_metrics["feasible_ticket_revenue_clean"] == pytest.approx(120_000_000.0)
        assert res.scenario_metrics["capacity_constrained_ticket_revenue_upper_bound"] == pytest.approx(120_000_000.0)
        # total_revenue_clean MUST NOT include unmet demand revenue (180M); must be capped at 150M (120M + 30M)
        assert res.scenario_metrics["total_revenue_clean"] == pytest.approx(150_000_000.0)
        assert res.scenario_metrics["potential_total_revenue_clean"] == pytest.approx(180_000_000.0)
        assert res.scenario_metrics["capacity_constrained_total_revenue_upper_bound"] == pytest.approx(150_000_000.0)
        assert res.absolute_deltas["potential_ticket_revenue_delta"] == pytest.approx(50_000_000.0)
        assert res.absolute_deltas["capacity_constrained_ticket_revenue_delta"] == pytest.approx(20_000_000.0)
        assert res.scenario_metrics["occupancy"] == pytest.approx(1.0)
        assert any("VƯỢT SỨC CHỨA" in w for w in res.warnings)

    def test_volume_within_capacity_is_aggregate_only_not_operationally_feasible(self):
        base = _sample_baseline(ticket_qty_clean=1000, available_showtime_capacity=1500)
        res = simulate_ticket_volume_change(base, 0.20)
        assert res.validation_status == "VALID"
        assert res.capacity_check_scope == "AGGREGATE_CAPACITY_CHECK"
        assert res.operational_feasibility_status == "UNVERIFIED_AT_SHOWTIME_GRAIN"
        assert res.operational_feasibility_status != "OPERATIONALLY_FEASIBLE"
        assert any("AGGREGATE_CAPACITY_CHECK" in lim for lim in res.limitations)

    def test_sold_out_showtime_warning_even_when_aggregate_capacity_available(self):
        base = _sample_baseline(
            ticket_qty_clean=1000,
            available_showtime_capacity=1500,
            sold_out_showtime_count=3,
        )
        # Replicate fetch_baseline_from_db warning for sold-out showtimes
        base_with_warn = BaselineMetrics(
            **{
                **{k: getattr(base, k) for k in base.__dataclass_fields__},
                "warnings": (
                    "Có 3/15 suất chiếu đã đạt hoặc vượt 100% sức chứa trong kỳ baseline; "
                    "ghế trống ở suất khác không tự động đáp ứng cho suất đã bán hết.",
                ),
            }
        )
        res = simulate_ticket_volume_change(base_with_warn, 0.10)
        assert res.validation_status == "VALID"
        assert res.scenario_metrics["sold_out_showtime_count"] == 3
        assert any("bán hết" in w.lower() for w in res.warnings)

    def test_compare_scenarios_separates_potential_and_constrained_revenue(self):
        base = _sample_baseline(
            ticket_qty_clean=1000,
            ticket_revenue_clean=100_000_000.0,
            fnb_revenue_clean=30_000_000.0,
            available_showtime_capacity=1200,
        )
        sc_a = simulate_ticket_price_change(base, 0.10)
        sc_b = simulate_ticket_volume_change(base, 0.50)  # 1500 > 1200
        sc_c = simulate_fnb_attach_rate(base, 0.50)
        rows = compare_scenarios(base, [sc_a, sc_b, sc_c])
        row_b = next(r for r in rows if r["scenario_type"] == "TICKET_VOLUME")
        assert row_b["potential_ticket_revenue_clean"] == pytest.approx(150_000_000.0)
        assert row_b["capacity_constrained_ticket_revenue_upper_bound"] == pytest.approx(120_000_000.0)
        assert row_b["ticket_revenue_clean"] == pytest.approx(120_000_000.0)
        assert row_b["total_revenue_clean"] == pytest.approx(150_000_000.0)
        assert row_b["potential_total_revenue_clean"] == pytest.approx(180_000_000.0)
        assert row_b["unmet_demand_tickets"] == 300
        assert row_b["validation_status"] == "CAPACITY_EXCEEDED"

    def test_missing_capacity_does_not_compute_fake_occupancy(self):
        base = _sample_baseline(available_showtime_capacity=None)
        res = simulate_ticket_volume_change(base, 0.10)
        assert res.validation_status == "VALID"
        assert res.scenario_metrics["occupancy"] is None
        assert any("capacity" in w.lower() or "sức chứa" in w.lower() for w in res.warnings)

    def test_fnb_attach_rate_0_50_100(self):
        # ticket_txn_count=500, fnb_txn_count=200 (40%), fnb_rev=30M => avg_fnb_per_txn=150k
        base = _sample_baseline()
        res0 = simulate_fnb_attach_rate(base, 0.0)
        assert res0.scenario_metrics["fnb_revenue_clean"] == pytest.approx(0.0)

        res50 = simulate_fnb_attach_rate(base, 0.50)
        assert res50.scenario_metrics["fnb_txn_count_estimated"] == 250
        assert res50.scenario_metrics["fnb_revenue_clean"] == pytest.approx(37_500_000.0)

        res100 = simulate_fnb_attach_rate(base, 1.00)
        assert res100.scenario_metrics["fnb_txn_count_estimated"] == 500
        assert res100.scenario_metrics["fnb_revenue_clean"] == pytest.approx(75_000_000.0)

    def test_zero_denominator_safe_handling(self):
        base_zero_qty = _sample_baseline(ticket_revenue_clean=0.0, ticket_qty_clean=0)
        res_a = simulate_ticket_price_change(base_zero_qty, 0.10)
        res_b = simulate_ticket_volume_change(base_zero_qty, 0.10)
        assert res_a.validation_status == "INSUFFICIENT_DATA"
        assert res_b.validation_status == "INSUFFICIENT_DATA"

        base_zero_txn = _sample_baseline(ticket_txn_count=0, fnb_txn_count=0, fnb_revenue_clean=0.0)
        res_c = simulate_fnb_attach_rate(base_zero_txn, 0.50)
        assert res_c.validation_status == "INSUFFICIENT_DATA"

    @pytest.mark.parametrize("bad_val", [math.nan, math.inf, -math.inf, True, None, "abc"])
    def test_invalid_numeric_inputs_raise(self, bad_val):
        base = _sample_baseline()
        with pytest.raises(ValueError):
            simulate_ticket_price_change(base, bad_val)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            simulate_ticket_volume_change(base, bad_val)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            simulate_fnb_attach_rate(base, bad_val)  # type: ignore[arg-type]

    def test_out_of_range_parameters_raise(self):
        base = _sample_baseline()
        with pytest.raises(ValueError):
            simulate_ticket_price_change(base, -0.90)
        with pytest.raises(ValueError):
            simulate_ticket_price_change(base, 2.50)
        with pytest.raises(ValueError):
            simulate_ticket_volume_change(base, -0.95)
        with pytest.raises(ValueError):
            simulate_fnb_attach_rate(base, -0.05)
        with pytest.raises(ValueError):
            simulate_fnb_attach_rate(base, 1.05)

    def test_deterministic_integer_rounding_half_up(self):
        # 105 * (1 + 0.10) = 115.5 -> ROUND_HALF_UP => 116
        base = _sample_baseline(ticket_qty_clean=105, ticket_revenue_clean=10_500_000.0)
        res = simulate_ticket_volume_change(base, 0.10)
        assert res.scenario_metrics["ticket_qty_clean"] == 116
        assert isinstance(res.scenario_metrics["ticket_qty_clean"], int)


# =============================================================================
# 2. SCIENTIFIC CORRECTNESS TESTS
# =============================================================================

class TestWhatIfScientificCorrectness:
    def test_historical_and_hypothetical_labels(self):
        base = _sample_baseline()
        assert base.data_freshness == "HISTORICAL_BASELINE"
        res = simulate_ticket_price_change(base, 0.05)
        assert res.data_classification == "HYPOTHETICAL_SCENARIO"

    def test_price_scenario_assumptions_and_no_elasticity_or_profit(self):
        base = _sample_baseline()
        res = simulate_ticket_price_change(base, 0.05)
        joined_assump = " ".join(res.assumptions)
        joined_limits = " ".join(res.limitations)
        assert "giữ nguyên lượng vé bán" in joined_assump
        assert "Price Elasticity" in joined_limits
        assert "chi phí" in joined_limits
        assert "profit" not in res.scenario_metrics
        assert "margin" not in res.scenario_metrics

    def test_attach_rate_scenario_does_not_change_tickets_and_explains_spp(self):
        base = _sample_baseline()
        res = simulate_fnb_attach_rate(base, 0.60)
        assert res.scenario_metrics["ticket_qty_clean"] == base.ticket_qty_clean
        assert res.scenario_metrics["ticket_revenue_clean"] == pytest.approx(base.ticket_revenue_clean)
        joined_limits = " ".join(res.limitations)
        assert "Hybrid" in joined_limits
        assert "Spend Per Ticket Transaction" in joined_limits
        assert "KHÔNG phải Spend Per Patron" in joined_limits


# =============================================================================
# 3. SECURITY & REPRODUCIBILITY TESTS
# =============================================================================

class TestWhatIfSecurityAndReproducibility:
    def test_reproducibility_same_inputs_identical_id_and_metrics(self):
        base = _sample_baseline()
        r1 = simulate_ticket_price_change(base, 0.08)
        r2 = simulate_ticket_price_change(base, 0.08)
        assert r1.scenario_id == r2.scenario_id
        assert r1.scenario_metrics == r2.scenario_metrics
        assert r1.absolute_deltas == r2.absolute_deltas

    def test_compare_rejects_mismatched_baseline(self):
        base1 = _sample_baseline(cinema_key=1)
        base2 = _sample_baseline(cinema_key=2, ticket_revenue_clean=80_000_000.0)
        sc1 = simulate_ticket_price_change(base1, 0.10)
        with pytest.raises(ValueError, match="fingerprint"):
            compare_scenarios(base2, [sc1])

    def test_core_what_if_has_no_sql_write_statements(self):
        src = (ROOT / "core" / "what_if.py").read_text(encoding="utf-8").upper()
        for kw in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE", "CREATE TABLE"):
            assert kw not in src

    def test_logout_clears_whatif_session_state(self):
        import streamlit as st
        from app.ui.auth import clear_sensitive_session_state

        st.session_state["whatif_scenarios"] = ["dummy"]
        st.session_state["whatif_owner"] = "manager"
        clear_sensitive_session_state()
        assert "whatif_scenarios" not in st.session_state
        assert "whatif_owner" not in st.session_state


# =============================================================================
# 4. POSTGRESQL INTEGRATION TESTS
# =============================================================================

@SKIP_PG
class TestWhatIfPostgreSQLIntegration:
    def test_fetch_baseline_all_and_each_cinema(self):
        from etl.db import get_engine

        eng = get_engine()
        start_d, end_d = date(2026, 9, 1), date(2026, 9, 30)

        b_all = fetch_baseline_from_db(eng, None, start_d, end_d)
        b1 = fetch_baseline_from_db(eng, 1, start_d, end_d)
        b2 = fetch_baseline_from_db(eng, 2, start_d, end_d)
        b3 = fetch_baseline_from_db(eng, 3, start_d, end_d)

        assert b_all is not None and b1 is not None and b2 is not None and b3 is not None
        assert b1.ticket_qty_clean + b2.ticket_qty_clean + b3.ticket_qty_clean == b_all.ticket_qty_clean
        assert b1.ticket_revenue_clean + b2.ticket_revenue_clean + b3.ticket_revenue_clean == pytest.approx(
            b_all.ticket_revenue_clean
        )
        assert b1.available_showtime_capacity + b2.available_showtime_capacity + b3.available_showtime_capacity == (
            b_all.available_showtime_capacity
        )
        assert b_all.atp_clean == pytest.approx(b_all.ticket_revenue_clean / b_all.ticket_qty_clean)
        assert b_all.attach_rate == pytest.approx(b_all.fnb_txn_count / b_all.ticket_txn_count)

    def test_no_data_date_range_returns_none(self):
        from etl.db import get_engine

        eng = get_engine()
        assert fetch_baseline_from_db(eng, 1, date(2020, 1, 1), date(2020, 1, 5)) is None

    def test_run_three_scenarios_and_verify_no_db_writes(self):
        from etl.db import get_engine
        from sqlalchemy import text

        eng = get_engine()
        with eng.connect() as conn:
            before_count = conn.execute(text("SELECT COUNT(*) FROM cinema_dw.whatif_run")).scalar()

        base = fetch_baseline_from_db(eng, 1, date(2026, 9, 1), date(2026, 9, 30))
        assert base is not None
        sc_a = simulate_ticket_price_change(base, 0.05)
        sc_b = simulate_ticket_volume_change(base, 0.10)
        sc_c = simulate_fnb_attach_rate(base, min(1.0, (base.attach_rate or 0.5) + 0.05))
        comp = compare_scenarios(base, [sc_a, sc_b, sc_c])
        assert len(comp) == 4  # Baseline + 3 scenarios

        with eng.connect() as conn:
            after_count = conn.execute(text("SELECT COUNT(*) FROM cinema_dw.whatif_run")).scalar()
        assert after_count == before_count

    def test_reconcile_clean_ticket_qty_and_fnb_across_sources(self):
        from etl.db import get_engine
        from sqlalchemy import text

        eng = get_engine()
        with eng.connect() as conn:
            kpi_rows = conn.execute(text("""
                SELECT cinema_key, SUM(ticket_qty_clean)::bigint AS qty, SUM(fnb_revenue_clean)::numeric AS frev
                FROM cinema_dw.vw_daily_kpi
                WHERE date_key BETWEEN 20251001 AND 20260930
                GROUP BY cinema_key ORDER BY cinema_key
            """)).fetchall()
            occ_rows = conn.execute(text("""
                SELECT dc.cinema_key, SUM(o.qty_clean)::bigint AS qty
                FROM cinema_dw.vw_showtime_occupancy o
                JOIN cinema_dw.dim_cinema dc ON o.cinema_name = dc.cinema_name
                WHERE o.date_key BETWEEN 20251001 AND 20260930
                GROUP BY dc.cinema_key ORDER BY dc.cinema_key
            """)).fetchall()
            fact_rows = conn.execute(text("""
                SELECT cinema_key, SUM(CASE WHEN is_anomaly=0 THEN qty ELSE 0 END)::bigint AS qty
                FROM cinema_dw.fact_ticket
                WHERE date_key BETWEEN 20251001 AND 20260930
                GROUP BY cinema_key ORDER BY cinema_key
            """)).fetchall()
            attach_rows = conn.execute(text("""
                SELECT cinema_key, SUM(fnb_revenue_clean)::numeric AS frev
                FROM cinema_dw.vw_fnb_attach
                WHERE date_key BETWEEN 20251001 AND 20260930
                GROUP BY cinema_key ORDER BY cinema_key
            """)).fetchall()

        assert [(r[0], int(r[1])) for r in kpi_rows] == [(r[0], int(r[1])) for r in occ_rows]
        assert [(r[0], int(r[1])) for r in kpi_rows] == [(r[0], int(r[1])) for r in fact_rows]
        assert sum(int(r[1]) for r in kpi_rows) == 805_664
        assert [(r[0], float(r[2])) for r in kpi_rows] == pytest.approx(
            [(r[0], float(r[1])) for r in attach_rows]
        )


# =============================================================================
# 5. STREAMLIT APPTEST UI VALIDATION
# =============================================================================

class TestWhatIfAppTest:
    def _set_user(self, at, role: str, cinema_key: int | None = None, username: str = "mgr"):
        at.session_state["logged_in"] = True
        at.session_state["login_time"] = datetime.now()
        at.session_state["user"] = {
            "user_id": f"u-{username}",
            "username": username,
            "role": role,
            "cinema_key": cinema_key,
        }

    def test_staff_blocked_from_what_if_page(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / "app" / "pages" / "6_What_If_Analysis.py"))
        self._set_user(at, role="STAFF", cinema_key=1, username="staff_cn01")
        at.run(timeout=15)
        assert not at.exception
        # Page stops at check_permission; no simulation radio/slider should render
        assert len(at.radio) == 0

    def test_manager_db_error_handled_gracefully(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / "app" / "pages" / "6_What_If_Analysis.py"))
        self._set_user(at, role="MANAGER", username="mgr1")
        with patch("app.data_access.load_data_date_range", return_value=(None, None)), \
             patch("app.data_access.load_what_if_baseline", return_value={"status": "ERROR", "error": "DB unavailable"}), \
             patch("streamlit.page_link"):
            at.run(timeout=15)
        assert not at.exception
        assert any("DB unavailable" in e.value for e in at.error)

    def test_manager_empty_data_handled_gracefully(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / "app" / "pages" / "6_What_If_Analysis.py"))
        self._set_user(at, role="MANAGER", username="mgr1")
        with patch("app.data_access.load_data_date_range", return_value=(date(2025, 10, 1), date(2026, 9, 30))), \
             patch("app.data_access.load_what_if_baseline", return_value={"status": "NO_DATA", "baseline": None}), \
             patch("streamlit.page_link"):
            at.run(timeout=15)
        assert not at.exception
        assert len(at.radio) == 0

    def test_manager_simulation_save_compare_and_cinema_switch(self):
        from streamlit.testing.v1 import AppTest

        base_cn1 = _sample_baseline(cinema_key=1)
        base_cn2 = _sample_baseline(cinema_key=2, ticket_revenue_clean=80_000_000.0)

        def _fake_load(ck, sd, ed):
            return {"status": "OK", "baseline": base_cn2 if ck == 2 else base_cn1}

        at = AppTest.from_file(str(ROOT / "app" / "pages" / "6_What_If_Analysis.py"))
        self._set_user(at, role="MANAGER", username="mgr1")

        with patch("app.data_access.load_data_date_range", return_value=(date(2025, 10, 1), date(2026, 9, 30))), \
             patch("app.data_access.load_what_if_baseline", side_effect=_fake_load), \
             patch("streamlit.page_link"):
            at.run(timeout=15)
            assert not at.exception
            # Save Scenario A
            at.button(key="whatif_save_btn").click().run(timeout=15)
            assert len(at.session_state["whatif_scenarios"]) == 1

            # Switch to Scenario B and save
            at.radio(key="whatif_scenario_type").set_value(
                "Scenario B — Thay đổi Lượng vé & Sức chứa (Ticket Volume & Capacity)"
            ).run(timeout=15)
            at.button(key="whatif_save_btn").click().run(timeout=15)
            assert len(at.session_state["whatif_scenarios"]) == 2

            # Switch to Scenario C and save
            at.radio(key="whatif_scenario_type").set_value(
                "Scenario C — Thay đổi Tỷ lệ mua kèm F&B (F&B Attach Rate)"
            ).run(timeout=15)
            at.button(key="whatif_save_btn").click().run(timeout=15)
            assert len(at.session_state["whatif_scenarios"]) == 3

            # Change cinema -> old scenarios must be cleared
            at.selectbox(key="whatif_cinema_sel").set_value("CGV Vincom Đồng Khởi (CN02)").run(timeout=15)
            assert len(at.session_state["whatif_scenarios"]) == 0

    def test_manager_capacity_exceeded_displays_separate_revenues_and_warning(self):
        from streamlit.testing.v1 import AppTest

        base = _sample_baseline(
            cinema_key=1,
            ticket_qty_clean=1000,
            ticket_revenue_clean=100_000_000.0,
            fnb_revenue_clean=30_000_000.0,
            available_showtime_capacity=1200,
        )
        at = AppTest.from_file(str(ROOT / "app" / "pages" / "6_What_If_Analysis.py"))
        self._set_user(at, role="MANAGER", username="mgr1")

        with patch("app.data_access.load_data_date_range", return_value=(date(2025, 10, 1), date(2026, 9, 30))), \
             patch("app.data_access.load_what_if_baseline", return_value={"status": "OK", "baseline": base}), \
             patch("streamlit.page_link"):
            at.run(timeout=15)
            at.radio(key="whatif_scenario_type").set_value(
                "Scenario B — Thay đổi Lượng vé & Sức chứa (Ticket Volume & Capacity)"
            ).run(timeout=15)
            at.slider(key="whatif_qty_pct").set_value(50).run(timeout=15)

        assert not at.exception
        assert any("CAPACITY_EXCEEDED" in e.value for e in at.error)
        assert any("300" in e.value for e in at.error)
        assert any("120.000.000" in e.value for e in at.error)
        assert any("150.000.000" in e.value for e in at.error)

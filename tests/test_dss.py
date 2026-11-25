"""
tests/test_dss.py
-----------------
Unit & integration tests cho core/dss.py (Task 9 — DSS Scientific Validation).
Bao phủ toàn bộ các nhóm tình huống kiểm thử:
  - Điều kiện biên & ngưỡng kích hoạt
  - Thiếu dữ liệu / NULL / NaN / dữ liệu quá cũ (stale)
  - Phân loại severity & cấu trúc Decision Output Contract
  - Trùng lặp & xung đột quy tắc (deduplication & conflict resolution)
  - Tương thích phân khúc RFM (k=3 vs 6 nhóm nghiệp vụ)
  - Ràng buộc nghiệp vụ Forecasting (không bịa định mức nhân sự, caveat interval)
  - Cô lập rạp Review Mining (CN01 không gán sang CN02/CN03, min_sample=10)
  - Tính tất định (deterministic) & an toàn (read-only, status=PROPOSED, không PII)
"""

from __future__ import annotations

from datetime import date, timedelta
import pandas as pd
import pytest

from core.dss import (
    Decision,
    VALID_SEVERITIES,
    evaluate_forecast_rules,
    evaluate_kpi_rules,
    evaluate_review_rules,
    evaluate_rfm_rules,
    resolve_conflicts_and_deduplicate,
    run_dss_from_dataframes,
)


def _make_daily_kpi(
    cinema_key: int = 1,
    cinema_name: str = "CGV Landmark 81",
    prev_rev: float = 100_000_000.0,
    curr_rev: float = 100_000_000.0,
    end_date: date = date(2026, 9, 30),
) -> pd.DataFrame:
    rows = []
    for i in range(14):
        d = end_date - timedelta(days=13 - i)
        rev = prev_rev if i < 7 else curr_rev
        rows.append(
            {
                "cinema_key": cinema_key,
                "cinema_name": cinema_name,
                "full_date": d,
                "ticket_revenue_clean": rev * 0.7,
                "fnb_revenue_clean": rev * 0.3,
                "ticket_qty_clean": 1000,
                "atp_clean": 70_000.0,
            }
        )
    return pd.DataFrame(rows)


def test_kpi_rev_drop_exact_boundary_and_severity():
    # Giảm đúng -15.0% -> kích hoạt HIGH
    df_15 = _make_daily_kpi(prev_rev=100_000_000.0, curr_rev=85_000_000.0)
    res_15 = evaluate_kpi_rules(df_15)
    assert len(res_15) == 1
    assert res_15[0].rule_id == "KPI-REV-DROP"
    assert res_15[0].severity == "HIGH"

    # Giảm -14.9% -> không kích hoạt
    df_149 = _make_daily_kpi(prev_rev=100_000_000.0, curr_rev=85_100_000.0)
    assert evaluate_kpi_rules(df_149) == []

    # Giảm đúng -25.0% -> kích hoạt CRITICAL
    df_25 = _make_daily_kpi(prev_rev=100_000_000.0, curr_rev=75_000_000.0)
    res_25 = evaluate_kpi_rules(df_25)
    assert len(res_25) == 1
    assert res_25[0].severity == "CRITICAL"


def test_kpi_occupancy_weighted_and_low_demand():
    end_d = date(2026, 9, 30)
    occ_df = pd.DataFrame(
        [
            {"cinema_key": 3, "cinema_name": "CGV Pearl Plaza", "full_date": end_d, "qty_clean": 20, "capacity": 100},
            {"cinema_key": 3, "cinema_name": "CGV Pearl Plaza", "full_date": end_d - timedelta(days=1), "qty_clean": 25, "capacity": 100},
        ]
    )
    # weighted = 45/200 = 0.225 < 0.25 -> HIGH
    res = evaluate_kpi_rules(_make_daily_kpi(cinema_key=3, cinema_name="CGV Pearl Plaza"), occupancy_df=occ_df)
    occ_decisions = [d for d in res if d.rule_id == "KPI-OCC-LOW"]
    assert len(occ_decisions) == 1
    assert occ_decisions[0].severity == "HIGH"
    assert occ_decisions[0].evidence[0]["value"] == 0.225


def test_kpi_handles_null_nan_and_empty_inputs():
    assert evaluate_kpi_rules(None) == []
    assert evaluate_kpi_rules(pd.DataFrame()) == []
    bad_df = pd.DataFrame([{"cinema_key": None, "full_date": None, "ticket_revenue_clean": float("nan")}])
    assert evaluate_kpi_rules(bad_df) == []


def test_kpi_stale_data_flagged_in_limitations():
    old_df = _make_daily_kpi(prev_rev=100_000_000.0, curr_rev=80_000_000.0, end_date=date(2026, 1, 1))
    res = evaluate_kpi_rules(old_df, reference_date=date(2026, 9, 30))
    assert len(res) == 1
    assert any("cũ" in lim for lim in res[0].limitations)


def test_rfm_k_mismatch_and_churn_risk():
    prof_k3 = pd.DataFrame(
        [
            {
                "cluster_id": 0,
                "cluster_name": "Có nguy cơ rời bỏ",
                "customer_count": 1854,
                "pct_customers": 12.8,
                "avg_recency": 151.66,
                "avg_frequency": 1.40,
                "avg_monetary": 294602.0,
                "avg_tenure": 3.16,
                "top_genre": "Hành động",
                "combo_rate": 0.18,
                "updated_at": pd.Timestamp("2026-11-19"),
            },
            {
                "cluster_id": 1,
                "cluster_name": "VIP",
                "customer_count": 3013,
                "pct_customers": 20.8,
                "avg_recency": 16.84,
                "avg_frequency": 26.32,
                "avg_monetary": 6615897.1,
                "avg_tenure": 297.11,
                "top_genre": "Khoa học viễn tưởng",
                "combo_rate": 0.45,
                "updated_at": pd.Timestamp("2026-11-19"),
            },
            {
                "cluster_id": 2,
                "cluster_name": "Trung thành",
                "customer_count": 9622,
                "pct_customers": 66.4,
                "avg_recency": 115.43,
                "avg_frequency": 5.69,
                "avg_monetary": 1288242.25,
                "avg_tenure": 176.68,
                "top_genre": "Hài",
                "combo_rate": 0.25,
                "updated_at": pd.Timestamp("2026-11-19"),
            },
        ]
    )
    res = evaluate_rfm_rules(prof_k3)
    rule_ids = {d.rule_id for d in res}
    assert "RFM-K-MISMATCH" in rule_ids
    assert "RFM-CHURN-RISK" in rule_ids
    assert "RFM-VIP-RETENTION" in rule_ids

    mismatch = next(d for d in res if d.rule_id == "RFM-K-MISMATCH")
    assert mismatch.evidence[0]["actual_k"] == 3
    assert "Ngủ đông" in mismatch.evidence[0]["missing_business_clusters"]

    churn = next(d for d in res if d.rule_id == "RFM-CHURN-RISK")
    assert churn.severity == "HIGH"
    assert any("A/B" in lim for lim in churn.limitations)


def test_forecast_peak_demand_and_missing_data_guard():
    # Thiếu dữ liệu hoàn toàn -> FC-DATA-GAP
    gap = evaluate_forecast_rules(pd.DataFrame(), expected_cinema_keys=[1, 2], cinema_name_map={1: "CN01", 2: "CN02"})
    assert len(gap) == 2
    assert all(d.rule_id == "FC-DATA-GAP" for d in gap)

    # CN01 có đỉnh nhu cầu >= 80% capacity (400 * 0.8 = 320), CN02 thiếu dữ liệu
    fc_df = pd.DataFrame(
        [
            {
                "target_date": date(2026, 10, 3),
                "cinema_key": 1,
                "time_slot": "Tối",
                "model_name": "xgboost",
                "yhat": 360.0,
                "yhat_lower": 260.0,
                "yhat_upper": 460.0,
                "is_test": False,
                "created_at": pd.Timestamp("2026-11-25"),
            }
        ]
    )
    res = evaluate_forecast_rules(
        fc_df,
        cinema_capacity_map={1: 400, 2: 340},
        cinema_name_map={1: "CGV Landmark 81", 2: "CGV Vincom Đồng Khởi"},
        expected_cinema_keys=[1, 2],
    )
    by_ck = {d.cinema_key: d for d in res}
    assert by_ck[1].rule_id == "FC-PEAK-DEMAND"
    assert by_ck[1].severity == "HIGH"
    assert any("heuristic" in lim for lim in by_ck[1].limitations)
    assert by_ck[2].rule_id == "FC-DATA-GAP"


def test_review_cinema_isolation_and_min_sample():
    # CN01 có 12 review (đủ mẫu), CN02 có 4 review (<10, không sinh quyết định)
    rows = []
    for i in range(12):
        rows.append(
            {
                "review_id": f"cn1_{i}",
                "cinema_key": 1,
                "rating": 1.0 if i < 6 else 5.0,
                "published_date": date(2026, 10, 1),
                "sentiment_final": "NEGATIVE" if i < 6 else "POSITIVE",
                "topics": ["Vệ sinh"],
            }
        )
    for i in range(4):
        rows.append(
            {
                "review_id": f"cn2_{i}",
                "cinema_key": 2,
                "rating": 1.0,
                "published_date": date(2026, 10, 1),
                "sentiment_final": "NEGATIVE",
                "topics": ["Vệ sinh"],
            }
        )
    df = pd.DataFrame(rows)
    res = evaluate_review_rules(df, cinema_name_map={1: "CGV Landmark 81", 2: "CGV Vincom Đồng Khởi"})
    assert len(res) == 1
    assert res[0].cinema_key == 1
    assert res[0].rule_id == "REV-TOPIC-ALERT"
    assert res[0].severity == "HIGH"
    assert any("Ground Truth" in lim for lim in res[0].limitations)


def test_conflict_resolution_and_deduplication():
    d1 = Decision(
        decision_id="id1",
        rule_id="KPI-REV-DROP",
        cinema_key=1,
        time_scope="2026-09-24..2026-09-30",
        category="REVENUE_KPI",
        severity="HIGH",
        title="Same title",
        recommendation="Check",
        explanation={"what_happened": "x", "why_triggered": "y", "why_recommended": "z", "manager_checks": []},
        evidence=[{"v": 1}],
        source_model="kpi_dw",
        as_of_date="2026-09-30",
    )
    d1_dup = Decision(
        decision_id="id1b",
        rule_id="KPI-REV-DROP",
        cinema_key=1,
        time_scope="2026-09-24..2026-09-30",
        category="REVENUE_KPI",
        severity="CRITICAL",
        title="Same title",
        recommendation="Check",
        explanation={"what_happened": "x", "why_triggered": "y", "why_recommended": "z", "manager_checks": []},
        evidence=[{"v": 1}],
        source_model="kpi_dw",
        as_of_date="2026-09-30",
    )
    d_anom = Decision(
        decision_id="id2",
        rule_id="KPI-ANOM-HIGH",
        cinema_key=1,
        time_scope="as_of_2026-09-30",
        category="REVENUE_KPI",
        severity="HIGH",
        title="Anom high",
        recommendation="Audit",
        explanation={"what_happened": "x", "why_triggered": "y", "why_recommended": "z", "manager_checks": []},
        evidence=[{"v": 40}],
        source_model="kpi_dw",
        as_of_date="2026-09-30",
    )
    resolved = resolve_conflicts_and_deduplicate([d1, d1_dup, d_anom])
    assert len(resolved) == 2
    rev_dec = next(d for d in resolved if d.rule_id == "KPI-REV-DROP")
    assert rev_dec.severity == "CRITICAL"
    assert any("KPI-ANOM-HIGH" in lim for lim in rev_dec.limitations)


def test_deterministic_and_output_contract():
    kpi = _make_daily_kpi(prev_rev=100_000_000.0, curr_rev=70_000_000.0)
    r1 = [d.to_dict() for d in run_dss_from_dataframes(daily_kpi_df=kpi)]
    r2 = [d.to_dict() for d in run_dss_from_dataframes(daily_kpi_df=kpi)]
    assert r1 == r2
    assert len(r1) == 2
    rev_item = next(x for x in r1 if x["rule_id"] == "KPI-REV-DROP")
    assert rev_item["status"] == "PROPOSED"
    assert rev_item["severity"] in VALID_SEVERITIES
    assert rev_item["decision_id"].startswith("DSS-KPI-REV-DROP-")
    assert "customer_name" not in str(rev_item)
    assert "email" not in str(rev_item)

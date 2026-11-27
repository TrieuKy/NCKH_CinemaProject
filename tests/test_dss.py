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
    # 1. Thiếu dữ liệu hoàn toàn -> FC-DATA-GAP
    ref = date(2026, 11, 25)
    gap = evaluate_forecast_rules(
        pd.DataFrame(),
        expected_cinema_keys=[1, 2],
        cinema_name_map={1: "CN01", 2: "CN02"},
        reference_date=ref,
    )
    assert len(gap) == 2
    assert all(d.rule_id == "FC-DATA-GAP" for d in gap)

    # 2. Dữ liệu tháng 10/2026 dù created_at=2026-11-25 vẫn EXPIRED tại reference_date=2026-11-25
    expired_df = pd.DataFrame(
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
    exp_res = evaluate_forecast_rules(
        expired_df,
        cinema_name_map={1: "CGV Landmark 81", 2: "CGV Vincom Đồng Khởi"},
        expected_cinema_keys=[1, 2],
        reference_date=ref,
    )
    assert all(d.rule_id == "FC-DATA-GAP" for d in exp_res)
    assert not any(d.rule_id == "FC-PEAK-DEMAND" for d in exp_res)

    # 3. Dữ liệu tương lai đủ 14 ngày + có lịch chiếu xác nhận (400 ghế) -> FC-PEAK-DEMAND cho CN01, FC-DATA-GAP cho CN02
    future_rows = []
    sched_rows = []
    for i in range(1, 15):
        d = ref + timedelta(days=i)
        future_rows.append(
            {
                "target_date": d,
                "cinema_key": 1,
                "time_slot": "Tối",
                "model_name": "xgboost",
                "yhat": 360.0 if i == 3 else 150.0,
                "yhat_lower": 260.0,
                "yhat_upper": 460.0,
                "is_test": False,
                "created_at": pd.Timestamp("2026-11-25"),
            }
        )
        sched_rows.append({"show_date": d, "cinema_key": 1, "time_slot": "Tối", "capacity": 400})

    res = evaluate_forecast_rules(
        pd.DataFrame(future_rows),
        cinema_name_map={1: "CGV Landmark 81", 2: "CGV Vincom Đồng Khởi"},
        expected_cinema_keys=[1, 2],
        reference_date=ref,
        schedule_df=pd.DataFrame(sched_rows),
    )
    by_ck = {d.cinema_key: d for d in res}
    assert by_ck[1].rule_id == "FC-PEAK-DEMAND"
    assert by_ck[1].severity == "HIGH"
    assert by_ck[1].evidence[0]["projected_occupancy_ratio"] == 0.9
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


# ---------------------------------------------------------------------------
# Task 9.1 — Temporal Validity, Capacity Semantics, KPI & Data Completeness
# ---------------------------------------------------------------------------
def _make_14d_forecast(ref_date: date, cinema_key: int = 1, yhat: float = 360.0, slot: str = "Tối", created_at: str = "2026-11-25") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "target_date": ref_date + timedelta(days=i),
                "cinema_key": cinema_key,
                "time_slot": slot,
                "model_name": "xgboost",
                "yhat": yhat,
                "yhat_lower": max(0.0, yhat - 80.0),
                "yhat_upper": yhat + 80.0,
                "is_test": False,
                "created_at": pd.Timestamp(created_at),
            }
            for i in range(1, 15)
        ]
    )


def test_fix1_forecast_temporal_validity_expired_and_mixed_and_audit():
    ref = date(2026, 11, 25)

    # 1. Toàn bộ 2026-10-01..2026-10-14 với created_at mới (2026-11-25) -> vẫn expired
    old_fc = pd.DataFrame(
        [
            {
                "target_date": date(2026, 10, d),
                "cinema_key": 1,
                "time_slot": "Tối",
                "model_name": "xgboost",
                "yhat": 450.0,
                "is_test": False,
                "created_at": pd.Timestamp("2026-11-25"),
            }
            for d in range(1, 15)
        ]
    )
    sched_old = pd.DataFrame(
        [{"show_date": date(2026, 10, d), "cinema_key": 1, "time_slot": "Tối", "capacity": 400} for d in range(1, 15)]
    )
    res_op = evaluate_forecast_rules(old_fc, reference_date=ref, schedule_df=sched_old, expected_cinema_keys=[1])
    assert not any(d.rule_id == "FC-PEAK-DEMAND" for d in res_op)
    assert len(res_op) == 1
    assert res_op[0].rule_id == "FC-DATA-GAP"
    assert res_op[0].evidence[0]["reason"] == "all_forecasts_expired"

    # 2. Historical audit mode cho phép replay nhưng gắn nhãn rõ ràng và giảm cấp
    res_audit = evaluate_forecast_rules(
        old_fc, reference_date=ref, schedule_df=sched_old, expected_cinema_keys=[1], mode="historical_audit"
    )
    peak_audit = [d for d in res_audit if d.rule_id == "FC-PEAK-DEMAND"]
    assert len(peak_audit) == 1
    assert peak_audit[0].title.startswith("[LỊCH SỬ / AUDIT]")
    assert peak_audit[0].explanation["decision_mode"] == "HISTORICAL_AUDIT"
    assert peak_audit[0].severity == "MEDIUM"

    # 3. Hỗn hợp quá khứ + tương lai: chỉ phần tương lai được dùng
    mixed_fc = pd.concat(
        [
            old_fc.iloc[:5],  # quá khứ
            _make_14d_forecast(ref, cinema_key=1, yhat=360.0).iloc[:5],  # chỉ 5 ngày tương lai
        ],
        ignore_index=True,
    )
    sched_fut = pd.DataFrame(
        [{"show_date": ref + timedelta(days=i), "cinema_key": 1, "time_slot": "Tối", "capacity": 400} for i in range(1, 6)]
    )
    res_mixed = evaluate_forecast_rules(mixed_fc, reference_date=ref, schedule_df=sched_fut, expected_cinema_keys=[1])
    rule_ids = {d.rule_id for d in res_mixed}
    assert "FC-PEAK-DEMAND" in rule_ids
    assert "FC-DATA-GAP" in rule_ids  # thiếu 9/14 ngày horizon
    peak_m = next(d for d in res_mixed if d.rule_id == "FC-PEAK-DEMAND")
    assert peak_m.time_scope == f"{(ref + timedelta(days=1)).isoformat()}..{(ref + timedelta(days=5)).isoformat()}"


def test_fix2_forecast_capacity_semantics_and_demand_surge():
    ref = date(2026, 11, 25)
    fc = _make_14d_forecast(ref, cinema_key=1, yhat=360.0, slot="Tối")

    # 1. 360 vé / 400 ghế khả dụng (1 suất 400) -> 90% -> FC-PEAK-DEMAND
    sched_400 = pd.DataFrame(
        [{"show_date": ref + timedelta(days=i), "cinema_key": 1, "time_slot": "Tối", "capacity": 400} for i in range(1, 15)]
    )
    res_90 = evaluate_forecast_rules(fc, reference_date=ref, schedule_df=sched_400, expected_cinema_keys=[1])
    assert len(res_90) == 1
    assert res_90[0].rule_id == "FC-PEAK-DEMAND"
    assert res_90[0].evidence[0]["projected_occupancy_ratio"] == 0.9

    # 2. 360 vé / 800 ghế khả dụng (2 suất 400 cùng khung giờ) -> 45% -> không phát FC-PEAK-DEMAND
    sched_800 = pd.DataFrame(
        [
            {"show_date": ref + timedelta(days=i), "cinema_key": 1, "time_slot": "Tối", "capacity": 400}
            for i in range(1, 15)
            for _ in range(2)
        ]
    )
    res_45 = evaluate_forecast_rules(fc, reference_date=ref, schedule_df=sched_800, expected_cinema_keys=[1])
    assert res_45 == []

    # 3. Không có lịch chiếu -> không kết luận occupancy, không phát FC-PEAK-DEMAND
    res_nosched = evaluate_forecast_rules(
        fc, cinema_capacity_map={1: 400}, reference_date=ref, schedule_df=None, expected_cinema_keys=[1]
    )
    assert not any(d.rule_id == "FC-PEAK-DEMAND" for d in res_nosched)

    # 4. Ngày không mở suất Sáng -> không tạo áp lực công suất giả cho Sáng
    fc_morning = _make_14d_forecast(ref, cinema_key=1, yhat=380.0, slot="Sáng")
    res_morning = evaluate_forecast_rules(fc_morning, reference_date=ref, schedule_df=sched_400, expected_cinema_keys=[1])
    assert not any(d.rule_id == "FC-PEAK-DEMAND" for d in res_morning)

    # 5. Không có lịch chiếu nhưng có lịch sử hợp lệ -> chỉ phát FC-DEMAND-SURGE (Demand Surge Alert)
    hist_df = pd.DataFrame([{"cinema_key": 1, "time_slot": "Tối", "qty_clean": 200.0}])
    res_surge = evaluate_forecast_rules(
        fc, reference_date=ref, schedule_df=None, hist_slot_df=hist_df, expected_cinema_keys=[1]
    )
    assert len(res_surge) == 1
    assert res_surge[0].rule_id == "FC-DEMAND-SURGE"
    assert res_surge[0].severity == "MEDIUM"
    assert res_surge[0].evidence[0]["schedule_available"] is False
    assert "không mở thêm suất" in " ".join(res_surge[0].limitations).lower() or "mở thêm suất" in " ".join(res_surge[0].limitations).lower()


def test_fix3_kpi_window_completeness_and_data_quality():
    end_d = date(2026, 9, 30)
    full_df = _make_daily_kpi(prev_rev=100_000_000.0, curr_rev=80_000_000.0, end_date=end_d)

    # 1. 3 ngày vs 7 ngày -> không sinh KPI-REV-DROP giả, sinh KPI-DATA-GAP
    df_3v7 = pd.concat([full_df.iloc[:7], full_df.iloc[11:14]], ignore_index=True)
    res_3v7 = evaluate_kpi_rules(df_3v7)
    assert not any(d.rule_id == "KPI-REV-DROP" for d in res_3v7)
    assert any(d.rule_id == "KPI-DATA-GAP" for d in res_3v7)

    # 2. Hai kỳ thiếu ngày ở vị trí khác nhau (6 ngày vs 6 ngày, giữ nguyên end_d)
    df_6v6 = full_df.drop(index=[2, 10]).reset_index(drop=True)
    res_6v6 = evaluate_kpi_rules(df_6v6)
    assert not any(d.rule_id == "KPI-REV-DROP" for d in res_6v6)
    gap_6v6 = next(d for d in res_6v6 if d.rule_id == "KPI-DATA-GAP")
    assert gap_6v6.evidence[0]["days_current"] == 6
    assert gap_6v6.evidence[0]["days_previous"] == 6

    # 3. Trùng full_date
    df_dup = pd.concat([full_df, full_df.iloc[[13]]], ignore_index=True)
    res_dup = evaluate_kpi_rules(df_dup)
    assert not any(d.rule_id == "KPI-REV-DROP" for d in res_dup)
    gap_dup = next(d for d in res_dup if d.rule_id == "KPI-DATA-GAP")
    assert "duplicate_full_date" in gap_dup.evidence[0]["reasons"]

    # 4. NULL/NaN trong doanh thu sạch
    df_nan = full_df.copy()
    df_nan.loc[10, "ticket_revenue_clean"] = float("nan")
    res_nan = evaluate_kpi_rules(df_nan)
    assert not any(d.rule_id == "KPI-REV-DROP" for d in res_nan)
    gap_nan = next(d for d in res_nan if d.rule_id == "KPI-DATA-GAP")
    assert "null_or_nan_kpi" in gap_nan.evidence[0]["reasons"]


def test_fix4_data_freshness_downgrades_stale_actionable_decisions():
    # KPI cũ > 30 ngày -> giảm cấp CRITICAL -> HIGH, gắn nhãn HISTORICAL_ANALYSIS_ONLY
    old_kpi = _make_daily_kpi(prev_rev=100_000_000.0, curr_rev=70_000_000.0, end_date=date(2026, 9, 30))
    res = evaluate_kpi_rules(old_kpi, reference_date=date(2026, 11, 25))
    assert len(res) == 1
    assert res[0].severity == "HIGH"  # downgraded from CRITICAL
    assert res[0].explanation["decision_mode"] == "HISTORICAL_ANALYSIS_ONLY"
    assert res[0].evidence[0]["freshness"]["is_stale"] is True

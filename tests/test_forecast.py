"""
tests/test_forecast.py
----------------------
Unit test cho module core/forecast.py:
  - Kiểm tra các hàm tính toán chỉ số WAPE, RMSE, MAPE (bỏ ngày thực = 0).
  - Kiểm tra mô hình Baseline (cùng thứ tuần trước, fallback).
  - Kiểm tra logic phân chia dữ liệu train/test không xáo trộn.
  - HOÀN TOÀN KHÔNG CẦN KẾT NỐI DATABASE.
"""

from __future__ import annotations

from datetime import date, timedelta
import numpy as np
import pandas as pd
import pytest

from core.forecast import (
    calculate_wape,
    calculate_rmse,
    calculate_mape,
    evaluate_forecast,
    predict_baseline,
    split_train_test,
    get_holiday_sets,
    get_holidays_dataframe,
    TRAIN_END_DATE,
    TEST_START_DATE,
    TEST_END_DATE,
)


class TestMetrics:
    """Kiểm tra độ chính xác của các hàm đo lường sai số dự báo."""

    def test_wape_perfect_prediction(self):
        y_true = np.array([100.0, 200.0, 300.0])
        y_pred = np.array([100.0, 200.0, 300.0])
        assert calculate_wape(y_true, y_pred) == pytest.approx(0.0)

    def test_wape_known_values(self):
        y_true = np.array([100.0, 200.0])
        y_pred = np.array([120.0, 180.0])
        # |100 - 120| + |200 - 180| = 20 + 20 = 40
        # Denominator = 300
        # WAPE = 40 / 300 = 0.133333...
        assert calculate_wape(y_true, y_pred) == pytest.approx(40.0 / 300.0)

    def test_wape_zero_actual_sum(self):
        y_true = np.array([0.0, 0.0])
        y_pred = np.array([10.0, 20.0])
        assert calculate_wape(y_true, y_pred) == 0.0

    def test_rmse_perfect_prediction(self):
        y_true = np.array([50.0, 100.0, 150.0])
        y_pred = np.array([50.0, 100.0, 150.0])
        assert calculate_rmse(y_true, y_pred) == pytest.approx(0.0)

    def test_rmse_known_values(self):
        y_true = np.array([10.0, 20.0])
        y_pred = np.array([13.0, 16.0])
        # Errors: +3, -4 -> squares: 9, 16 -> mean: 12.5 -> sqrt(12.5)
        expected = np.sqrt(12.5)
        assert calculate_rmse(y_true, y_pred) == pytest.approx(expected)

    def test_rmse_empty(self):
        assert calculate_rmse(np.array([]), np.array([])) == 0.0

    def test_mape_perfect_prediction(self):
        y_true = np.array([100.0, 200.0])
        y_pred = np.array([100.0, 200.0])
        assert calculate_mape(y_true, y_pred) == pytest.approx(0.0)

    def test_mape_known_values(self):
        y_true = np.array([100.0, 200.0])
        y_pred = np.array([110.0, 180.0])
        # |10/100| = 0.10, |20/200| = 0.10 -> mean = 0.10
        assert calculate_mape(y_true, y_pred) == pytest.approx(0.10)

    def test_mape_ignores_zero_actuals(self):
        """Yêu cầu: Bỏ ngày thực = 0 để tránh chia cho 0."""
        y_true = np.array([0.0, 100.0, 0.0, 200.0])
        y_pred = np.array([50.0, 120.0, 30.0, 180.0])
        # Bỏ 2 điểm thực = 0, chỉ còn:
        # |100 - 120| / 100 = 0.20
        # |200 - 180| / 200 = 0.10
        # Mean = (0.20 + 0.10) / 2 = 0.15
        assert calculate_mape(y_true, y_pred) == pytest.approx(0.15)

    def test_mape_all_zero_actuals(self):
        y_true = np.array([0.0, 0.0])
        y_pred = np.array([10.0, 20.0])
        assert calculate_mape(y_true, y_pred) == 0.0

    def test_evaluate_forecast_keys(self):
        metrics = evaluate_forecast([100.0, 200.0], [110.0, 190.0])
        assert "wape" in metrics
        assert "rmse" in metrics
        assert "mape" in metrics
        assert metrics["wape"] > 0
        assert metrics["rmse"] > 0
        assert metrics["mape"] > 0


class TestBaselineModel:
    """Kiểm tra mô hình Baseline (cùng thứ tuần trước)."""

    def test_baseline_exact_lag7(self):
        """Kiểm tra dự báo chính xác lấy từ giá trị 7 ngày trước đó."""
        start_date = date(2026, 7, 1)
        records = []
        for d_offset in range(30):
            cur_d = start_date + timedelta(days=d_offset)
            # Giả sử ngày thứ Hai (offset % 7 == 0) bán 100 vé, các ngày khác bán 50 vé
            sold = 100.0 if d_offset % 7 == 0 else 50.0
            records.append({
                "target_date": pd.to_datetime(cur_d),
                "cinema_key": 1,
                "time_slot": "Tối",
                "tickets_sold": sold,
            })
        df = pd.DataFrame(records)

        # Cắt train (21 ngày đầu), test (9 ngày sau)
        train_df = df.iloc[:21].copy()
        test_df = df.iloc[21:].copy()

        pred_df = predict_baseline(train_df, test_df)

        assert len(pred_df) == len(test_df)
        assert "yhat" in pred_df.columns

        # Với mỗi dòng trong test, yhat phải bằng tickets_sold của 7 ngày trước đó
        for _, row in pred_df.iterrows():
            target_d = row["target_date"].date()
            prev_7 = target_d - timedelta(days=7)
            prev_actual = df[
                (df["cinema_key"] == 1) &
                (df["time_slot"] == "Tối") &
                (df["target_date"].dt.date == prev_7)
            ]["tickets_sold"].values[0]
            assert row["yhat"] == pytest.approx(prev_actual)

    def test_baseline_fallback_missing_lag7(self):
        """Nếu 7 ngày trước không có suất chiếu, fallback về trung bình tập train."""
        train_records = [
            {"target_date": pd.to_datetime(date(2026, 7, 1)), "cinema_key": 1, "time_slot": "Tối", "tickets_sold": 100.0},
            {"target_date": pd.to_datetime(date(2026, 7, 2)), "cinema_key": 1, "time_slot": "Tối", "tickets_sold": 200.0},
        ]
        train_df = pd.DataFrame(train_records)

        # Test date ngày 2026-08-01 (không có ngày 2026-07-25 trong dữ liệu)
        test_records = [
            {"target_date": pd.to_datetime(date(2026, 8, 1)), "cinema_key": 1, "time_slot": "Tối", "tickets_sold": 150.0},
        ]
        test_df = pd.DataFrame(test_records)

        pred_df = predict_baseline(train_df, test_df)
        # Trung bình train = (100 + 200) / 2 = 150.0
        assert pred_df["yhat"].iloc[0] == pytest.approx(150.0)

    def test_baseline_non_negative(self):
        """Dự báo số vé không được âm."""
        train_df = pd.DataFrame([
            {"target_date": pd.to_datetime(date(2026, 7, 1)), "cinema_key": 1, "time_slot": "Sáng", "tickets_sold": 0.0}
        ])
        test_df = pd.DataFrame([
            {"target_date": pd.to_datetime(date(2026, 7, 8)), "cinema_key": 1, "time_slot": "Sáng", "tickets_sold": 10.0}
        ])
        pred_df = predict_baseline(train_df, test_df)
        assert pred_df["yhat"].iloc[0] >= 0.0


class TestDataSplit:
    """Kiểm tra phân chia chuỗi thời gian không xáo trộn."""

    def test_split_chronological(self):
        dates = pd.date_range("2026-05-01", "2026-09-30", freq="D")
        records = [
            {"target_date": d, "cinema_key": 1, "time_slot": "Tối", "tickets_sold": 100.0}
            for d in dates
        ]
        df = pd.DataFrame(records)

        train_df, test_df = split_train_test(df)

        # Kiểm tra ngưỡng ngày
        assert train_df["target_date"].max() <= pd.to_datetime(TRAIN_END_DATE)
        assert test_df["target_date"].min() >= pd.to_datetime(TEST_START_DATE)
        assert test_df["target_date"].max() <= pd.to_datetime(TEST_END_DATE)

        # Không xáo trộn: ngày phải tăng dần đều
        assert train_df["target_date"].is_monotonic_increasing
        assert test_df["target_date"].is_monotonic_increasing

        # Không mất mát dữ liệu trong khoảng hợp lệ
        expected_train_count = (pd.to_datetime(TRAIN_END_DATE) - pd.to_datetime("2026-05-01")).days + 1
        expected_test_count = (pd.to_datetime(TEST_END_DATE) - pd.to_datetime(TEST_START_DATE)).days + 1
        assert len(train_df) == expected_train_count
        assert len(test_df) == expected_test_count


class TestHolidaysIntegration:
    """Kiểm tra danh sách ngày lễ chuẩn bị cho mô hình."""

    def test_holidays_dataframe_format(self):
        df_h = get_holidays_dataframe(2025, 2026)
        assert "holiday" in df_h.columns
        assert "ds" in df_h.columns
        assert len(df_h) > 0
        assert pd.api.types.is_datetime64_any_dtype(df_h["ds"])

    def test_holiday_sets_separation(self):
        official, peak = get_holiday_sets(2026, 2026)
        assert isinstance(official, set)
        assert isinstance(peak, set)
        assert len(official) > 0
        assert len(peak) > 0
        # Tết Dương lịch 2026 là lễ chính thức
        assert date(2026, 1, 1) in official
        # Giáng sinh 24/12/2026 là peak day
        assert date(2026, 12, 24) in peak


class TestScientificValidation:
    """
    Kiểm định khoa học (Scientific Validation) theo yêu cầu Task 7:
      - Train/Test separation & non-overlapping index.
      - Data leakage audit: one-step feature computation vs multi-step forecast horizon.
      - Prediction interval consistency: y_lower <= yhat <= y_upper và non-negativity.
      - Deterministic reproducibility: fixed random state.
      - Missing data and zero values handling.
    """

    def test_train_test_no_temporal_overlap(self):
        """Train và Test phải hoàn toàn tách biệt theo thời gian, không có ngày giao nhau."""
        dates = pd.date_range("2025-10-01", "2026-09-30", freq="D")
        df = pd.DataFrame({
            "target_date": dates,
            "cinema_key": 1,
            "time_slot": "Tối",
            "tickets_sold": np.random.uniform(50, 200, size=len(dates)),
        })
        train_df, test_df = split_train_test(df)
        train_dates = set(train_df["target_date"].dt.date)
        test_dates = set(test_df["target_date"].dt.date)
        assert len(train_dates.intersection(test_dates)) == 0
        assert max(train_dates) < min(test_dates)

    def test_prediction_interval_bounds_and_non_negativity(self):
        """Kiểm tra cận dưới và cận trên: 0 <= yhat_lower <= yhat <= yhat_upper."""
        from core.forecast import run_xgboost_pipeline
        dates_train = pd.date_range("2026-01-01", "2026-07-31", freq="D")
        dates_test = pd.date_range("2026-08-01", "2026-08-14", freq="D")
        records = []
        for d in dates_train:
            records.append({
                "target_date": d, "cinema_key": 1, "time_slot": "Tối",
                "tickets_sold": float(100 + 30 * np.sin(d.dayofweek))
            })
        for d in dates_test:
            records.append({
                "target_date": d, "cinema_key": 1, "time_slot": "Tối",
                "tickets_sold": float(100 + 30 * np.sin(d.dayofweek))
            })
        df = pd.DataFrame(records)
        train_df, test_df = split_train_test(df)
        test_pred_df, future_pred_df = run_xgboost_pipeline(train_df, test_df, future_horizon_days=7)

        for col in ["yhat", "yhat_lower", "yhat_upper"]:
            assert col in test_pred_df.columns
            assert col in future_pred_df.columns
            # Non-negativity constraint
            assert (test_pred_df[col] >= 0).all()
            assert (future_pred_df[col] >= 0).all()

        # Interval ordering constraint
        assert (test_pred_df["yhat_lower"] <= test_pred_df["yhat"] + 1e-6).all()
        assert (test_pred_df["yhat"] <= test_pred_df["yhat_upper"] + 1e-6).all()
        assert (future_pred_df["yhat_lower"] <= future_pred_df["yhat"] + 1e-6).all()
        assert (future_pred_df["yhat"] <= future_pred_df["yhat_upper"] + 1e-6).all()

    def test_xgboost_reproducibility(self):
        """Với random_state=42 cố định, 2 lần fit và dự báo phải cho kết quả giống hệt nhau."""
        from core.forecast import run_xgboost_pipeline
        dates = pd.date_range("2026-05-01", "2026-08-14", freq="D")
        records = []
        for d in dates:
            records.append({
                "target_date": d, "cinema_key": 1, "time_slot": "Chiều",
                "tickets_sold": float(50 + 20 * (d.dayofweek >= 5))
            })
        df = pd.DataFrame(records)
        train_df, test_df = split_train_test(df)
        t1, f1 = run_xgboost_pipeline(train_df, test_df, future_horizon_days=5)
        t2, f2 = run_xgboost_pipeline(train_df, test_df, future_horizon_days=5)
        np.testing.assert_allclose(t1["yhat"].values, t2["yhat"].values, rtol=1e-5)
        np.testing.assert_allclose(f1["yhat"].values, f2["yhat"].values, rtol=1e-5)

    def test_future_autoregressive_horizon_14(self):
        """Kiểm tra dự báo tương lai 14 ngày sinh đúng số dòng và ngày liên tiếp."""
        from core.forecast import run_xgboost_pipeline
        dates = pd.date_range("2026-06-01", "2026-08-10", freq="D")
        records = []
        for d in dates:
            records.append({
                "target_date": d, "cinema_key": 1, "time_slot": "Tối",
                "tickets_sold": float(80.0)
            })
        df = pd.DataFrame(records)
        train_df, test_df = split_train_test(df)
        _, future_pred_df = run_xgboost_pipeline(train_df, test_df, future_horizon_days=14)
        assert len(future_pred_df) == 14
        expected_start = test_df["target_date"].max() + timedelta(days=1)
        expected_end = test_df["target_date"].max() + timedelta(days=14)
        assert future_pred_df["target_date"].min() == expected_start
        assert future_pred_df["target_date"].max() == expected_end


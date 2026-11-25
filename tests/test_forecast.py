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


class TestTask71Validation:
    """
    Kiểm thử bổ sung cho Task 7.1 (Section E):
      1. Interval calibration không dùng nhãn Evaluation.
      2. Thay đổi dữ liệu sau origin không làm thay đổi dự báo tại origin.
      3. Recursive rollout đúng 14 ngày.
      4. Missing showtime và missing lag.
      5. Interval coverage, MIW, MAE, Bias computation.
      6. Tái lập khi cố định cấu hình.
      7. Backtest không làm thay đổi Fact/ETL hoặc bảng production.
      8. Idempotency của logic persistence (save_forecast_to_db).
    """

    @staticmethod
    def _make_synthetic_series(start="2026-05-01", end="2026-08-31", morning_weekend_only=False):
        dates = pd.date_range(start, end, freq="D")
        records = []
        for d in dates:
            records.append({
                "target_date": d,
                "cinema_key": 1,
                "time_slot": "Tối",
                "tickets_sold": float(150 + 40 * (d.dayofweek >= 5) + (d.day % 5)),
            })
            if not morning_weekend_only or d.dayofweek >= 5:
                records.append({
                    "target_date": d,
                    "cinema_key": 1,
                    "time_slot": "Sáng",
                    "tickets_sold": float(60 + 15 * (d.dayofweek == 6)),
                })
        return pd.DataFrame(records)

    def test_interval_calibration_ignores_evaluation_labels(self):
        """Khoảng dự báo (half-width) không được phụ thuộc vào nhãn thực tế của tập Test."""
        from core.forecast import run_xgboost_pipeline

        df = self._make_synthetic_series("2026-04-01", "2026-08-14")
        train_df, test_df = split_train_test(df, train_end=date(2026, 7, 31), test_start=date(2026, 8, 1), test_end=date(2026, 8, 14))

        test_df_perturbed = test_df.copy()
        test_df_perturbed["tickets_sold"] = test_df_perturbed["tickets_sold"] * 500.0 + 10000.0

        t1, _ = run_xgboost_pipeline(train_df, test_df, future_horizon_days=7)
        t2, _ = run_xgboost_pipeline(train_df, test_df_perturbed, future_horizon_days=7)

        hw1 = (t1["yhat_upper"] - t1["yhat"]).values
        hw2 = (t2["yhat_upper"] - t2["yhat"]).values
        np.testing.assert_allclose(hw1, hw2, rtol=1e-6)

    def test_future_perturbation_after_origin_does_not_change_forecast(self):
        """Thay đổi dữ liệu thực tế sau origin không làm thay đổi dự báo tại origin."""
        from core.forecast import predict_baseline_multistep, predict_xgboost_multistep

        df = self._make_synthetic_series("2026-05-01", "2026-08-20")
        origin = date(2026, 7, 31)

        df_perturbed = df.copy()
        mask = df_perturbed["target_date"] > pd.to_datetime(origin)
        df_perturbed.loc[mask, "tickets_sold"] = 999999.0

        for runner in (predict_baseline_multistep, predict_xgboost_multistep):
            p1 = runner(df, origin, horizon_days=14)
            p2 = runner(df_perturbed, origin, horizon_days=14)
            np.testing.assert_allclose(p1["yhat"].values, p2["yhat"].values, rtol=1e-6)
            np.testing.assert_allclose(p1["yhat_lower"].values, p2["yhat_lower"].values, rtol=1e-6)
            np.testing.assert_allclose(p1["yhat_upper"].values, p2["yhat_upper"].values, rtol=1e-6)

    def test_multistep_recursive_rollout_14_days(self):
        """Kiểm tra rollout đệ quy đúng 14 ngày liên tiếp và bước 8..14 dùng yhat trước đó."""
        from core.forecast import predict_baseline_multistep, predict_xgboost_multistep

        df = self._make_synthetic_series("2026-05-01", "2026-08-20")
        origin = date(2026, 7, 31)

        for runner in (predict_baseline_multistep, predict_xgboost_multistep):
            pred = runner(df, origin, horizon_days=14)
            assert pred["target_date"].nunique() == 14
            assert pred["target_date"].min() == pd.to_datetime(origin + timedelta(days=1))
            assert pred["target_date"].max() == pd.to_datetime(origin + timedelta(days=14))
            assert (pred["yhat"] >= 0).all()
            assert (pred["yhat_lower"] <= pred["yhat"] + 1e-6).all()
            assert (pred["yhat"] <= pred["yhat_upper"] + 1e-6).all()

        # Kiểm tra riêng baseline bước 8..14 lặp lại đúng giá trị bước 1..7 (lag-7 đệ quy)
        b_pred = predict_baseline_multistep(df, origin, horizon_days=14)
        toi = b_pred[b_pred["time_slot"] == "Tối"].sort_values("target_date")
        np.testing.assert_allclose(toi["yhat"].iloc[7:14].values, toi["yhat"].iloc[0:7].values, rtol=1e-6)

    def test_missing_showtime_and_missing_lag_handling(self):
        """Khung giờ Sáng chỉ có suất cuối tuần (thiếu suất ngày thường & thiếu lag) vẫn chạy an toàn."""
        from core.forecast import predict_baseline_multistep, predict_xgboost_multistep

        df = self._make_synthetic_series("2026-05-01", "2026-08-14", morning_weekend_only=True)
        origin = date(2026, 7, 31)

        for runner in (predict_baseline_multistep, predict_xgboost_multistep):
            pred = runner(df, origin, horizon_days=14)
            assert len(pred) == 14 * 2  # 2 time_slots
            assert not pred["yhat"].isna().any()
            assert (pred["yhat"] >= 0).all()
            assert (pred["yhat_lower"] >= 0).all()
            assert (pred["yhat_lower"] <= pred["yhat"] + 1e-6).all()
            assert (pred["yhat"] <= pred["yhat_upper"] + 1e-6).all()

    def test_interval_coverage_miw_mae_bias(self):
        """Kiểm tra các hàm tính Coverage, Mean Interval Width, MAE và Bias."""
        from core.forecast import (
            calculate_bias,
            calculate_mae,
            compute_interval_coverage,
            compute_mean_interval_width,
        )

        y_true = np.array([100.0, 200.0, 300.0, 400.0])
        y_pred = np.array([110.0, 190.0, 310.0, 350.0])
        y_lo = np.array([90.0, 180.0, 290.0, 360.0])   # điểm thứ 4 (400) nằm ngoài [360, 410]? No, 400 in [360, 410]!
        y_hi = np.array([120.0, 210.0, 320.0, 390.0])  # điểm thứ 4 (400 > 390) nằm ngoài

        assert compute_interval_coverage(y_true, y_lo, y_hi) == pytest.approx(0.75)
        assert compute_mean_interval_width(y_lo, y_hi) == pytest.approx(30.0)
        assert calculate_mae(y_true, y_pred) == pytest.approx((10 + 10 + 10 + 50) / 4.0)
        assert calculate_bias(y_true, y_pred) == pytest.approx((10 - 10 + 10 - 50) / 4.0)
        assert compute_interval_coverage([], [], []) == 0.0
        assert compute_mean_interval_width([], []) == 0.0

    def test_multistep_reproducibility_fixed_seed(self):
        """Cố định cấu hình (random_state=42), 2 lần chạy multistep cho kết quả giống hệt nhau."""
        from core.forecast import predict_xgboost_multistep

        df = self._make_synthetic_series("2026-05-01", "2026-07-31")
        origin = date(2026, 7, 31)
        p1 = predict_xgboost_multistep(df, origin, horizon_days=14)
        p2 = predict_xgboost_multistep(df, origin, horizon_days=14)
        np.testing.assert_allclose(p1["yhat"].values, p2["yhat"].values, rtol=1e-6)
        np.testing.assert_allclose(p1["yhat_lower"].values, p2["yhat_lower"].values, rtol=1e-6)
        np.testing.assert_allclose(p1["yhat_upper"].values, p2["yhat_upper"].values, rtol=1e-6)

    def test_backtest_script_does_not_mutate_fact_or_production(self):
        """Đảm bảo script backtest chỉ đọc dữ liệu, không gọi ghi DB hay sửa Fact/ETL."""
        from pathlib import Path

        backtest_src = Path("scripts/eval_forecast_backtest.py").read_text(encoding="utf-8")
        for forbidden in ("save_forecast_to_db", "run_forecast(", "INSERT ", "UPDATE ", "DELETE ", "ALTER "):
            assert forbidden not in backtest_src

    def test_save_forecast_to_db_idempotency_calls(self):
        """Kiểm tra save_forecast_to_db xóa phạm vi cũ trước khi ghi mới (idempotent)."""
        from unittest.mock import MagicMock
        from core.forecast import save_forecast_to_db

        executed_sql = []
        mock_conn = MagicMock()

        def capture_execute(stmt, params=None):
            executed_sql.append(str(stmt))

        mock_conn.execute.side_effect = capture_execute
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn
        mock_ctx.__exit__.return_value = False
        mock_engine = MagicMock()
        mock_engine.begin.return_value = mock_ctx

        metrics = [{"model_name": "xgboost", "metric_name": "wape", "metric_value": 0.11}]
        forecasts = [{
            "target_date": date(2026, 8, 1),
            "cinema_key": 1,
            "time_slot": "Tối",
            "model_name": "xgboost",
            "yhat": 100.0,
            "yhat_lower": 50.0,
            "yhat_upper": 150.0,
            "is_test": True,
        }]

        save_forecast_to_db(mock_engine, metrics, forecasts)
        save_forecast_to_db(mock_engine, metrics, forecasts)

        delete_metrics = [s for s in executed_sql if "DELETE FROM cinema_dw.model_metrics" in s]
        delete_forecasts = [s for s in executed_sql if "DELETE FROM cinema_dw.forecast_result" in s]
        insert_forecasts = [s for s in executed_sql if "INSERT INTO cinema_dw.forecast_result" in s]

        assert len(delete_metrics) == 2
        assert len(delete_forecasts) == 2
        assert len(insert_forecasts) == 2



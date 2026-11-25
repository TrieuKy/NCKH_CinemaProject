"""
scripts/eval_forecast_backtest.py
---------------------------------
Kiểm định khoa học mô hình dự báo vé xem phim (Task 7.1):
  1. Reproducible 14-Day Rolling-Origin Backtesting trên 4 cửa sổ:
       - 01/08/2026 – 14/08/2026 (origin: 2026-07-31)
       - 15/08/2026 – 28/08/2026 (origin: 2026-08-14)
       - 29/08/2026 – 11/09/2026 (origin: 2026-08-28)
       - 12/09/2026 – 25/09/2026 (origin: 2026-09-11)
  2. So sánh công bằng 3 mô hình (Lag-7/Baseline, Prophet, XGBoost) trên cùng
     tập điểm hợp lệ tại chế độ Fixed-Origin 14-Day Multi-Step, phân biệt rõ với
     chế độ Rolling One-Step và Future Production Forecast.
  3. Đánh giá Prediction Interval Coverage và Mean Interval Width (MIW) độc lập
     (hiệu chỉnh trên Calibration, đánh giá trên Evaluation).
  4. Kiểm tra tính bất biến trước nhiễu dữ liệu tương lai (Future Perturbation Test).
  5. Phân tích lịch suất chiếu khung giờ Sáng (có suất vs không mở suất).
  6. Chỉ đọc dữ liệu (read-only), tuyệt đối không ghi đè bảng production.

Sử dụng:
    python scripts/eval_forecast_backtest.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.forecast import (
    FUTURE_HORIZON_DAYS,
    compute_interval_coverage,
    compute_mean_interval_width,
    evaluate_forecast,
    load_forecast_data,
    predict_baseline,
    predict_baseline_multistep,
    predict_prophet_multistep,
    predict_xgboost_multistep,
    run_prophet_pipeline,
    run_xgboost_pipeline,
    split_train_test,
    _calibrate_baseline_residual_std,
)
from etl.db import get_engine


ROLLING_ORIGINS: List[Tuple[date, date, date]] = [
    (date(2026, 7, 31), date(2026, 8, 1), date(2026, 8, 14)),
    (date(2026, 8, 14), date(2026, 8, 15), date(2026, 8, 28)),
    (date(2026, 8, 28), date(2026, 8, 29), date(2026, 9, 11)),
    (date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 25)),
]


def _score_predictions(merged: pd.DataFrame) -> Dict[str, Any]:
    """Tính đầy đủ WAPE, RMSE, MAPE, MAE, Bias, Coverage, MIW trên tập điểm hợp lệ."""
    m = evaluate_forecast(merged["tickets_sold"], merged["yhat"])
    cov = compute_interval_coverage(
        merged["tickets_sold"], merged["yhat_lower"], merged["yhat_upper"]
    )
    miw = compute_mean_interval_width(merged["yhat_lower"], merged["yhat_upper"])
    return {
        "n_valid": int(len(merged)),
        "wape": m["wape"],
        "rmse": m["rmse"],
        "mape": m["mape"],
        "mae": m["mae"],
        "bias": m["bias"],
        "coverage": cov,
        "miw": miw,
    }


def run_14d_rolling_origin_backtest(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Thực hiện Fixed-Origin 14-Day Rolling-Origin Backtest trên 4 cửa sổ chuẩn.
    Trả về:
      - per_origin_df: bảng metrics theo từng (origin, model)
      - overall_df: bảng metrics tổng hợp toàn hệ thống theo từng model
      - all_eval_df: toàn bộ điểm dự báo đã khớp thực tế để phân tích thêm (theo time_slot...)
    """
    per_origin_rows: List[Dict[str, Any]] = []
    eval_frames: List[pd.DataFrame] = []

    model_runners = {
        "lag7_baseline": predict_baseline_multistep,
        "prophet": predict_prophet_multistep,
        "xgboost": predict_xgboost_multistep,
    }

    for origin_d, win_start, win_end in ROLLING_ORIGINS:
        origin_ts = pd.to_datetime(origin_d)
        start_ts = pd.to_datetime(win_start)
        end_ts = pd.to_datetime(win_end)

        # Cắt dữ liệu nghiêm ngặt tại forecast origin
        history_df = df[df["target_date"] <= origin_ts].copy()
        actual_win = df[(df["target_date"] >= start_ts) & (df["target_date"] <= end_ts)].copy()

        for model_name, runner in model_runners.items():
            pred_df = runner(history_df, origin_d, horizon_days=FUTURE_HORIZON_DAYS)
            merged = pd.merge(
                actual_win,
                pred_df,
                on=["target_date", "cinema_key", "time_slot"],
                how="inner",
            )
            scores = _score_predictions(merged)
            per_origin_rows.append({
                "origin": str(origin_d),
                "window": f"{win_start} .. {win_end}",
                "model": model_name,
                "n_predicted": int(len(pred_df)),
                "n_unscheduled": int(len(pred_df) - len(merged)),
                **scores,
            })
            merged["origin"] = str(origin_d)
            merged["model"] = model_name
            eval_frames.append(merged)

    per_origin_df = pd.DataFrame(per_origin_rows)
    all_eval_df = pd.concat(eval_frames, ignore_index=True)

    overall_rows: List[Dict[str, Any]] = []
    for model_name in ["lag7_baseline", "prophet", "xgboost"]:
        sub = all_eval_df[all_eval_df["model"] == model_name]
        scores = _score_predictions(sub)
        overall_rows.append({"model": model_name, **scores})
    overall_df = pd.DataFrame(overall_rows)

    return per_origin_df, overall_df, all_eval_df


def run_onestep_mode_audit(df: pd.DataFrame) -> pd.DataFrame:
    """
    Đánh giá chế độ Production / Rolling One-Step trên tập Test (2026-08-01..2026-09-30)
    với khoảng dự báo đã hiệu chỉnh độc lập trên Train/Calibration.
    """
    train_df, test_df = split_train_test(df)

    # Baseline (one-step lag-7 với cập nhật quan sát + calibrated std)
    b_test = predict_baseline(train_df, test_df)
    b_std = _calibrate_baseline_residual_std(train_df)
    b_test["yhat_lower"] = np.maximum(0.0, b_test["yhat"] - 1.96 * b_std)
    b_test["yhat_upper"] = b_test["yhat"] + 1.96 * b_std

    # Prophet (fit đến 2026-07-31, dự báo 61 ngày Test)
    p_test, _ = run_prophet_pipeline(train_df, test_df, FUTURE_HORIZON_DAYS)

    # XGBoost (one-step lag với cập nhật quan sát + calibrated std)
    x_test, _ = run_xgboost_pipeline(train_df, test_df, FUTURE_HORIZON_DAYS)

    rows = []
    for name, frame in [("baseline_onestep", b_test), ("prophet_fixed_61d", p_test), ("xgboost_onestep", x_test)]:
        scores = _score_predictions(frame)
        rows.append({"model": name, **scores})
    return pd.DataFrame(rows)


def verify_future_perturbation_invariance(df: pd.DataFrame, origin_d: date = date(2026, 7, 31)) -> pd.DataFrame:
    """
    Kiểm định rò rỉ tương lai:
    Cố ý thay đổi mạnh dữ liệu thực tế sau `origin_d` (nhân 1000x + cộng 9999)
    và kiểm tra dự báo 14 ngày của cả 3 mô hình có giữ nguyên tuyệt đối (max_diff == 0).
    """
    origin_ts = pd.to_datetime(origin_d)
    perturbed_df = df.copy()
    mask_future = perturbed_df["target_date"] > origin_ts
    perturbed_df.loc[mask_future, "tickets_sold"] = perturbed_df.loc[mask_future, "tickets_sold"] * 1000.0 + 9999.0

    results = []
    for name, runner in [
        ("lag7_baseline", predict_baseline_multistep),
        ("prophet", predict_prophet_multistep),
        ("xgboost", predict_xgboost_multistep),
    ]:
        p_orig = runner(df, origin_d, horizon_days=FUTURE_HORIZON_DAYS)
        p_pert = runner(perturbed_df, origin_d, horizon_days=FUTURE_HORIZON_DAYS)
        max_diff = float(np.max(np.abs(p_orig["yhat"].values - p_pert["yhat"].values)))
        results.append({
            "model": name,
            "origin": str(origin_d),
            "max_abs_diff_yhat": max_diff,
            "leakage_free": max_diff == 0.0,
        })
    return pd.DataFrame(results)


def main() -> None:
    engine = get_engine()
    df = load_forecast_data(engine)

    print("=" * 92)
    print("  TASK 7.1 — REPRODUCIBLE 14-DAY ROLLING-ORIGIN BACKTEST & INTERVAL AUDIT")
    print("=" * 92)

    # 1. Mode 1: Production One-Step / Test Split Audit
    print("\n[1] CHẾ ĐỘ PRODUCTION / ROLLING ONE-STEP (Test: 2026-08-01 .. 2026-09-30)")
    onestep_df = run_onestep_mode_audit(df)
    print(onestep_df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # 2. Mode 2: Fair Fixed-Origin 14-Day Multi-Step Backtest
    print("\n[2] CHẾ ĐỘ FIXED-ORIGIN 14-DAY MULTI-STEP (4 cửa sổ Rolling-Origin)")
    per_origin_df, overall_df, all_eval_df = run_14d_rolling_origin_backtest(df)
    print("\n--- Kết quả theo từng Origin ---")
    print(per_origin_df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    print("\n--- Tổng hợp toàn hệ thống (4 cửa sổ, cùng tập điểm hợp lệ) ---")
    print(overall_df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # 3. Per-slot breakdown (đặc biệt khung giờ Sáng)
    print("\n[3] PHÂN TÍCH THEO KHUNG GIỜ (Sáng vs Chiều vs Tối) — Fixed-Origin 14-Day")
    slot_rows = []
    for (model_name, slot), sub in all_eval_df.groupby(["model", "time_slot"]):
        scores = _score_predictions(sub)
        slot_rows.append({"model": model_name, "time_slot": slot, **scores})
    print(pd.DataFrame(slot_rows).to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # 4. Future perturbation invariance check
    print("\n[4] KIỂM ĐỊNH BẤT BIẾN DỮ LIỆU TƯƠNG LAI (Perturb tickets_sold > 2026-07-31)")
    pert_df = verify_future_perturbation_invariance(df, date(2026, 7, 31))
    print(pert_df.to_string(index=False))
    print("=" * 92)


if __name__ == "__main__":
    main()

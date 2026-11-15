"""
scripts/run_forecast.py
-----------------------
Script chạy dự báo vé xem phim (Task T2).
Đọc dữ liệu từ fact_showtime_summary JOIN dim_time, huấn luyện các mô hình
(Baseline, Prophet, XGBoost), đánh giá chất lượng (WAPE, RMSE, MAPE),
dự báo 14 ngày tương lai và ghi vào model_metrics + forecast_result.

Hỗ trợ chạy nhiều lần không nhân đôi dữ liệu (Idempotent).

Sử dụng:
    python scripts/run_forecast.py
    python scripts/run_forecast.py --cinema-key 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Thêm ROOT vào sys.path để import etl và core
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.forecast import run_forecast
from etl.db import get_engine, logger


def main() -> None:
    parser = argparse.ArgumentParser(description="Chạy dự báo vé xem phim (T2)")
    parser.add_argument(
        "--cinema-key",
        type=int,
        default=None,
        help="Lọc theo mã rạp cụ thể (mặc định: toàn bộ rạp)",
    )
    args = parser.parse_args()

    engine = get_engine()

    try:
        results = run_forecast(engine, cinema_key=args.cinema_key)
    except Exception as exc:
        logger.error("Dự báo thất bại: %s", exc, exc_info=True)
        sys.exit(1)

    # In kết quả định dạng bảng
    print("\n" + "=" * 78)
    print("                KẾT QUẢ ĐÁNH GIÁ MÔ HÌNH DỰ BÁO VÉ (TASK T2)")
    print("=" * 78)
    summary_df = results["summary_table"]
    print(summary_df.to_string(index=False))
    print("-" * 78)

    m = results["metrics"]
    wape_b = m["baseline"]["wape"] * 100
    wape_p = m["prophet"]["wape"] * 100
    wape_x = m["xgboost"]["wape"] * 100

    rmse_b = m["baseline"]["rmse"]
    rmse_p = m["prophet"]["rmse"]
    rmse_x = m["xgboost"]["rmse"]

    mape_b = m["baseline"]["mape"] * 100
    mape_p = m["prophet"]["mape"] * 100
    mape_x = m["xgboost"]["mape"] * 100

    # Đánh giá chi tiết so với Baseline
    p_better = results["is_prophet_better"]
    x_better = results["is_xgboost_better"]

    print("\nKẾT LUẬN SO SÁNH VỚI BASELINE:")
    if p_better:
        print(f"  • Prophet CÓ TỐT HƠN Baseline:")
        print(f"      - WAPE: {wape_p:.2f}% vs {wape_b:.2f}% (giảm {wape_b - wape_p:.2f}%)")
        print(f"      - RMSE: {rmse_p:.2f} vs {rmse_b:.2f} vé")
        print(f"      - MAPE: {mape_p:.2f}% vs {mape_b:.2f}%")
    else:
        print(f"  • Prophet KHÔNG TỐT HƠN Baseline:")
        print(f"      - WAPE: {wape_p:.2f}% vs {wape_b:.2f}%")
        print(f"      - RMSE: {rmse_p:.2f} vs {rmse_b:.2f}")
        print(f"      - MAPE: {mape_p:.2f}% vs {mape_b:.2f}%")

    if x_better:
        print(f"  • XGBoost CÓ TỐT HƠN Baseline:")
        print(f"      - WAPE: {wape_x:.2f}% vs {wape_b:.2f}% (giảm {wape_b - wape_x:.2f}%)")
        print(f"      - RMSE: {rmse_x:.2f} vs {rmse_b:.2f} vé")
        print(f"      - MAPE: {mape_x:.2f}% vs {mape_b:.2f}%")
    else:
        print(f"  • XGBoost KHÔNG TỐT HƠN Baseline:")
        print(f"      - WAPE: {wape_x:.2f}% vs {wape_b:.2f}%")
        print(f"      - RMSE: {rmse_x:.2f} vs {rmse_b:.2f}")
        print(f"      - MAPE: {mape_x:.2f}% vs {mape_b:.2f}%")

    print(f"\n  🏆 Mô hình tốt nhất: {results['best_model'].upper()}")
    print(f"  📅 Đã ghi nhận {results['future_forecast_count']} bản ghi dự báo 14 ngày tương lai (is_test=FALSE).")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()

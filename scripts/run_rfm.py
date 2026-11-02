"""
scripts/run_rfm.py
------------------
Script chạy quy trình phân khúc khách hàng RFM + K-Means:
1. Tải dữ liệu giao dịch sạch (loại bỏ is_anomaly = 1, is_guest = FALSE).
2. Tính toán các chỉ số RFM (Recency, Frequency, Monetary).
3. Đánh giá lựa chọn số cụm k (k = 3..6) theo Silhouette Score và Inertia.
4. Gán nhãn cụm theo thứ hạng tâm cụm (VIP, Trung thành, Có nguy cơ rời bỏ, Mới, Ngủ đông).
5. Xây dựng bảng hồ sơ cụm (Profile) kèm thể loại xem nhiều nhất và tỷ lệ mua combo.
6. Lưu kết quả vào DW (cinema_dw.rfm_result, cinema_dw.rfm_cluster_profile, cinema_dw.model_metrics).
Chạy độc lập bằng: python -m scripts.run_rfm
"""

import sys
from etl.db import get_engine, logger
from core.rfm import (
    load_rfm_source_data,
    compute_rfm_metrics,
    evaluate_kmeans_candidates,
    cluster_rfm_customers,
    build_cluster_profiles,
    save_rfm_pipeline_results,
)


def run_rfm_pipeline():
    logger.info("=== BẮT ĐẦU PIPELINE RFM + K-MEANS ===")
    engine = get_engine()

    # 1. Tải dữ liệu
    df_tickets, df_fnb, ref_date = load_rfm_source_data(engine)

    # 2. Tính RFM
    df_rfm = compute_rfm_metrics(df_tickets, df_fnb, reference_date=ref_date)
    num_customers = len(df_rfm)
    print(f"\n==========================================")
    print(f"  SỐ KHÁCH HÀNG THÀNH VIÊN: {num_customers}")
    print(f"  NGÀY MỐC DỮ LIỆU: {ref_date}")
    print(f"==========================================")

    # 3. Đánh giá k = 3..6 theo Silhouette & Inertia
    best_k, metrics, X_scaled = evaluate_kmeans_candidates(df_rfm, k_range=range(3, 7))

    # 4. Phân cụm và đặt tên bằng luật ngữ nghĩa
    df_rfm_clustered, label_map = cluster_rfm_customers(df_rfm, best_k, X_scaled)

    # 5. Xây dựng hồ sơ cụm
    df_profile = build_cluster_profiles(df_rfm_clustered, df_tickets, df_fnb)

    print("\n==========================================")
    print(f"  BẢNG HỒ SƠ CỤM KHÁCH HÀNG (k={best_k})")
    print("==========================================")
    # Định dạng hiển thị tiền tệ và tỷ lệ %
    df_disp = df_profile.copy()
    df_disp["avg_monetary"] = df_disp["avg_monetary"].apply(lambda v: f"{v:,.0f} đ")
    df_disp["pct_customers"] = df_disp["pct_customers"].apply(lambda v: f"{v:.1f}%")
    df_disp["combo_rate"] = df_disp["combo_rate"].apply(lambda v: f"{v * 100:.1f}%")
    print(df_disp.to_string(index=False))
    print("==========================================\n")

    # 6. Lưu vào cơ sở dữ liệu
    save_rfm_pipeline_results(df_rfm_clustered, df_profile, metrics, best_k, engine)
    logger.info("=== HOÀN TẤT PIPELINE RFM + K-MEANS ===")


if __name__ == "__main__":
    try:
        run_rfm_pipeline()
        sys.exit(0)
    except Exception as exc:
        logger.exception("Lỗi khi chạy RFM pipeline: %s", exc)
        sys.exit(1)

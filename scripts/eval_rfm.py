"""
scripts/eval_rfm.py
-------------------
Đánh giá phân cụm K-Means so với nhãn tiềm ẩn (Ground Truth) trong data/latent_labels.csv:
1. Tính toán các chỉ số:
   - Adjusted Rand Index (ARI)
   - Purity
   - Bảng nhầm lẫn (Confusion Matrix / Crosstab giữa latent_segment và cluster_name)
2. Thử nghiệm với k = 4, 5, 6.
3. In các phân vị p10, p50, p90 của R, F, M, Tenure.
4. Kiểm tra F trung bình của hội viên (~8/năm).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from etl.db import get_engine, logger
from core.rfm import (
    load_rfm_source_data,
    compute_rfm_metrics,
    assign_cluster_names,
)


def calculate_purity(y_true: pd.Series, y_pred: pd.Series) -> float:
    """
    Tính độ tinh khiết (Clustering Purity) của các cụm so với nhãn gốc:
        Purity(Omega, C) = (1 / N) * sum_{k} max_{j} |omega_k cap c_j|
    Trong đó y_pred bắt buộc phải là `cluster_id` duy nhất do K-Means sinh ra,
    tuyệt đối không dùng `cluster_name` để tránh hiện tượng sáp nhập các cụm trùng tên.
    """
    contingency = pd.crosstab(y_pred, y_true)
    return float(np.sum(contingency.max(axis=1)) / len(y_true))


def evaluate_rfm():
    logger.info("=== BẮT ĐẦU ĐÁNH GIÁ MÔ HÌNH RFM SO VỚI GROUND TRUTH ===")
    latent_path = ROOT / "data" / "latent_labels.csv"
    if not latent_path.exists():
        print(f"Lỗi: Không tìm thấy file nhãn tiềm ẩn tại {latent_path}")
        sys.exit(1)

    df_latent = pd.read_csv(latent_path)
    print(f"Đã nạp {len(df_latent):,} nhãn tiềm ẩn từ {latent_path.name}")

    engine = get_engine()
    df_tickets, df_fnb, ref_date = load_rfm_source_data(engine)
    df_rfm = compute_rfm_metrics(df_tickets, df_fnb, reference_date=ref_date)

    merged = pd.merge(df_rfm, df_latent, on="customer_id", how="inner")
    n_cust = len(merged)
    print(f"\n==================================================")
    print(f"  SỐ HỘI VIÊN ĐỐI SOÁT: {n_cust:,}")
    print(f"  TẦN SUẤT (F) TRUNG BÌNH TOÀN BỘ HỘI VIÊN: {merged['frequency'].mean():.2f} / năm")
    print(f"==================================================")

    # 1. Phân vị p10, p50, p90 của R, F, M, Tenure
    print("\n--- PHÂN VỊ (p10, p50, p90) TOÀN BỘ HỘI VIÊN ---")
    metrics_cols = ["recency_days", "frequency", "monetary", "tenure"]
    p_df = pd.DataFrame({
        "p10": merged[metrics_cols].quantile(0.10),
        "p50 (median)": merged[metrics_cols].quantile(0.50),
        "p90": merged[metrics_cols].quantile(0.90),
    })
    print(p_df.to_string())

    print("\n--- PHÂN VỊ THEO PHÂN KHÚC TIỀM ẨN (GROUND TRUTH) ---")
    seg_summary = merged.groupby("latent_segment").agg(
        so_khach=("customer_id", "count"),
        F_mean=("frequency", "mean"),
        F_p50=("frequency", "median"),
        R_mean=("recency_days", "mean"),
        R_p50=("recency_days", "median"),
        Tenure_mean=("tenure", "mean"),
        M_mean=("monetary", lambda s: f"{s.mean():,.0f} đ"),
    )
    print(seg_summary.to_string())

    # 2. Chuẩn bị ma trận đặc trưng chuẩn hóa
    features = merged[["recency_days", "frequency", "monetary", "tenure"]].copy()
    X_log = np.log1p(features.values)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_log)

    print("\n==================================================")
    print("  ĐÁNH GIÁ CỤM K-MEANS VỚI k = 3, 4, 5, 6")
    print("==================================================")

    for k in [3, 4, 5, 6]:
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        cluster_ids = km.fit_predict(X_scaled)

        df_eval = merged.copy()
        df_eval["cluster_id"] = cluster_ids

        # Gán nhãn cụm bằng quy tắc
        centroids = df_eval.groupby("cluster_id")[["recency_days", "frequency", "monetary", "tenure"]].mean()
        label_map = assign_cluster_names(centroids)
        df_eval["cluster_name"] = df_eval["cluster_id"].map(label_map)

        ari = adjusted_rand_score(df_eval["latent_segment"], df_eval["cluster_id"])
        # Sửa: Purity tính trên cluster_id duy nhất do K-Means sinh ra, không tính trên cluster_name
        purity = calculate_purity(df_eval["latent_segment"], df_eval["cluster_id"])

        print(f"\n>>> [k = {k}] ARI = {ari:.4f} | Overall Purity (theo cluster_id) = {purity:.4f}")
        print("Tâm cụm đặc trưng (Centroids):")
        cent_display = centroids.copy()
        cent_display["name"] = cent_display.index.map(label_map)
        cent_display["monetary"] = cent_display["monetary"].apply(lambda v: f"{v:,.0f} đ")
        cent_display["count"] = df_eval.groupby("cluster_id")["customer_id"].count()
        print(cent_display[["name", "count", "recency_days", "frequency", "tenure", "monetary"]].to_string())

        print(f"\nBảng nhầm lẫn (Crosstab: Hàng=Latent Segment, Cột=KMeans Cluster Name [cluster_id]) [k={k}]:")
        ct = pd.crosstab(df_eval["latent_segment"], df_eval["cluster_name"], margins=True)
        print(ct.to_string())

        # Chuẩn hóa các chỉ số đánh giá cho phân khúc VIP
        if "VIP" in df_eval["cluster_name"].values and "VIP" in df_eval["latent_segment"].values:
            vip_cids = [cid for cid, name in label_map.items() if name == "VIP"]
            vip_latent = (df_eval["latent_segment"] == "VIP")
            vip_cluster = df_eval["cluster_id"].isin(vip_cids)
            intersection = int((vip_latent & vip_cluster).sum())
            total_latent_vip = int(vip_latent.sum())
            total_cluster_vip = int(vip_cluster.sum())

            recall_vip = intersection / total_latent_vip if total_latent_vip else 0.0
            purity_vip = intersection / total_cluster_vip if total_cluster_vip else 0.0

            print(f"\n  [Chuẩn hóa chỉ số phân khúc VIP]:")
            print(f"  - Recall lớp VIP tiềm ẩn (Latent Class Recall): {intersection}/{total_latent_vip} = {recall_vip:.4f} ({recall_vip*100:.2f}%)")
            print(f"  - Purity của cụm VIP (Cluster Purity / Precision): {intersection}/{total_cluster_vip} = {purity_vip:.4f} ({purity_vip*100:.2f}%)")

        print("-" * 60)

    # 3. Đánh giá độ ổn định phân hoạch (Stability & Reproducibility)
    print("\n==================================================")
    print("  ĐÁNH GIÁ ĐỘ ỔN ĐỊNH PHÂN HOẠCH (STABILITY & REPRODUCIBILITY)")
    print("==================================================")
    seeds = [42, 10, 100, 2024, 999]
    print(f"Danh sách random seeds kiểm tra: {seeds}")
    for k in [3, 4, 5, 6]:
        runs = {}
        for s in seeds:
            km = KMeans(n_clusters=k, random_state=s, n_init=10)
            runs[s] = km.fit_predict(X_scaled)

        # Reproducibility check: cùng cấu hình, cùng seed cho cùng kết quả (ARI = 1.0)
        km_rep = KMeans(n_clusters=k, random_state=42, n_init=10)
        rep_labels = km_rep.fit_predict(X_scaled)
        rep_ari = adjusted_rand_score(runs[42], rep_labels)

        # Stability check: Pairwise ARI giữa các seed khác nhau
        pairwise_aris = []
        for i in range(len(seeds)):
            for j in range(i + 1, len(seeds)):
                pairwise_aris.append(adjusted_rand_score(runs[seeds[i]], runs[seeds[j]]))

        print(
            f"k = {k}: Reproducibility (ARI cùng seed) = {rep_ari:.4f} | "
            f"Stability (Mean Pairwise ARI) = {np.mean(pairwise_aris):.4f} "
            f"(min={np.min(pairwise_aris):.4f}, max={np.max(pairwise_aris):.4f}, std={np.std(pairwise_aris):.4f})"
        )

    logger.info("=== HOÀN TẤT ĐÁNH GIÁ MÔ HÌNH RFM ===")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    evaluate_rfm()


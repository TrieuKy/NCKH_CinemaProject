"""
core/rfm.py
-----------
Phân khúc khách hàng thành viên dựa trên RFM và thuật toán K-Means:
1. Lọc khách hàng thành viên (is_guest = FALSE), bỏ dòng bất thường (is_anomaly = 1).
2. Tính toán:
   - Recency (R): Số ngày từ lần mua cuối cùng đến ngày sau cùng của dữ liệu.
   - Frequency (F): Số giao dịch (txn_id) khác nhau.
   - Monetary (M): Tổng net_amount (vé) + line_amount (bắp nước).
   - Tenure: Số ngày từ giao dịch đầu tiên đến giao dịch cuối cùng của khách hàng.
3. Tiền xử lý:
   - np.log1p + StandardScaler trên [recency_days, frequency, monetary, tenure].
4. Phân cụm K-Means:
   - Đánh giá k = 3..6 theo Silhouette Score và Inertia.
5. Gán tên cụm bằng quy tắc kết hợp thứ hạng và ngưỡng tuyệt đối:
   - Ngủ đông khi R > 180;
   - Có nguy cơ rời bỏ khi R từ 60 đến 180;
   - Mới khi tenure <= 60;
   - Còn lại chia VIP / Trung thành / Thỉnh thoảng theo F và M.
6. Hồ sơ cụm (Cluster Profile):
   - Số lượng khách, tỷ lệ %, R/F/M/Tenure trung bình.
   - Thể loại xem nhiều nhất (top_genre).
   - Tỷ lệ mua combo (combo_rate).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger("etl")


def load_rfm_source_data(engine: Engine) -> Tuple[pd.DataFrame, pd.DataFrame, date]:
    """
    Tải dữ liệu sạch từ cinema_dw phục vụ tính RFM:
    - Bỏ các dòng có is_anomaly = 1.
    - Chỉ lấy khách hàng thành viên (is_guest = FALSE).
    """
    query_tickets = """
        SELECT 
            t.customer_key,
            c.customer_id,
            t.txn_id,
            t.net_amount,
            d.full_date,
            m.genre,
            t.is_anomaly
        FROM cinema_dw.fact_ticket t
        JOIN cinema_dw.dim_date d ON t.date_key = d.date_key
        JOIN cinema_dw.dim_movie m ON t.movie_key = m.movie_key
        JOIN cinema_dw.dim_customer c ON t.customer_key = c.customer_key
        WHERE c.is_guest = FALSE AND t.is_anomaly = 0
    """

    query_fnb = """
        SELECT 
            f.customer_key,
            c.customer_id,
            f.txn_id,
            f.line_amount,
            f.qty,
            p.is_combo,
            d.full_date,
            f.is_anomaly
        FROM cinema_dw.fact_fnb f
        JOIN cinema_dw.dim_date d ON f.date_key = d.date_key
        JOIN cinema_dw.dim_product p ON f.product_key = p.product_key
        JOIN cinema_dw.dim_customer c ON f.customer_key = c.customer_key
        WHERE c.is_guest = FALSE AND f.is_anomaly = 0
    """

    with engine.connect() as conn:
        df_tickets = pd.read_sql(text(query_tickets), conn)
        df_fnb = pd.read_sql(text(query_fnb), conn)

    # Ngày sau cùng của dữ liệu
    max_ticket_date = pd.to_datetime(df_tickets["full_date"]).max() if not df_tickets.empty else pd.to_datetime("2026-09-30")
    max_fnb_date = pd.to_datetime(df_fnb["full_date"]).max() if not df_fnb.empty else max_ticket_date
    ref_date = max(max_ticket_date, max_fnb_date).date()

    logger.info("Đã tải %d dòng vé và %d dòng F&B sạch. Ngày mốc dữ liệu: %s", len(df_tickets), len(df_fnb), ref_date)
    return df_tickets, df_fnb, ref_date


def compute_rfm_metrics(
    df_tickets: pd.DataFrame,
    df_fnb: Optional[pd.DataFrame] = None,
    reference_date: Optional[date | datetime | str] = None,
) -> pd.DataFrame:
    """
    Tính R, F, M, Tenure cho từng khách hàng:
    - Bỏ dòng is_anomaly = 1.
    - Lọc is_guest = FALSE nếu có cột is_guest.
    - R: số ngày từ lần mua cuối đến ngày sau cùng của dữ liệu.
    - F: số txn_id khác nhau.
    - M: net_amount vé + line_amount bắp nước.
    - Tenure: số ngày từ giao dịch đầu tiên đến giao dịch cuối cùng.
    """
    if df_tickets.empty:
        return pd.DataFrame(columns=["customer_key", "customer_id", "recency_days", "frequency", "monetary", "tenure"])

    # Lọc bỏ bất thường
    t_clean = df_tickets.copy()
    if "is_anomaly" in t_clean.columns:
        t_clean = t_clean[t_clean["is_anomaly"] == 0]
    if "is_guest" in t_clean.columns:
        t_clean = t_clean[t_clean["is_guest"] == False]  # noqa: E712

    f_clean = pd.DataFrame()
    if df_fnb is not None and not df_fnb.empty:
        f_clean = df_fnb.copy()
        if "is_anomaly" in f_clean.columns:
            f_clean = f_clean[f_clean["is_anomaly"] == 0]
        if "is_guest" in f_clean.columns:
            f_clean = f_clean[f_clean["is_guest"] == False]  # noqa: E712

    t_clean["full_date"] = pd.to_datetime(t_clean["full_date"])
    if not f_clean.empty and "full_date" in f_clean.columns:
        f_clean["full_date"] = pd.to_datetime(f_clean["full_date"])

    # Xác định ngày sau cùng của dữ liệu
    if reference_date is None:
        t_max = t_clean["full_date"].max() if not t_clean.empty else pd.to_datetime("now")
        f_max = f_clean["full_date"].max() if not f_clean.empty and "full_date" in f_clean.columns else t_max
        ref_dt = max(t_max, f_max)
    else:
        ref_dt = pd.to_datetime(reference_date)

    # Tính theo vé: ngày đầu, ngày cuối, giao dịch, số tiền
    ticket_group = t_clean.groupby("customer_key").agg(
        customer_id=("customer_id", "first") if "customer_id" in t_clean.columns else ("customer_key", lambda x: f"CUS{x.iloc[0]}"),
        first_ticket_date=("full_date", "min"),
        last_ticket_date=("full_date", "max"),
        ticket_txns=("txn_id", lambda s: set(s.dropna().unique())),
        ticket_net_amount=("net_amount", "sum"),
    ).reset_index()

    # Tính theo F&B
    if not f_clean.empty:
        fnb_group = f_clean.groupby("customer_key").agg(
            customer_id=("customer_id", "first") if "customer_id" in f_clean.columns else ("customer_key", lambda x: f"CUS{x.iloc[0]}"),
            first_fnb_date=("full_date", "min") if "full_date" in f_clean.columns else ("customer_key", lambda _: pd.NaT),
            last_fnb_date=("full_date", "max") if "full_date" in f_clean.columns else ("customer_key", lambda _: pd.NaT),
            fnb_txns=("txn_id", lambda s: set(s.dropna().unique())),
            fnb_line_amount=("line_amount", "sum"),
        ).reset_index()
        merged = pd.merge(ticket_group, fnb_group, on="customer_key", how="outer", suffixes=("_t", "_f"))
        merged["customer_id"] = merged["customer_id_t"].combine_first(merged["customer_id_f"])
        merged.drop(columns=["customer_id_t", "customer_id_f"], inplace=True)
    else:
        merged = ticket_group.copy()
        merged["first_fnb_date"] = pd.NaT
        merged["last_fnb_date"] = pd.NaT
        merged["fnb_txns"] = [set() for _ in range(len(merged))]
        merged["fnb_line_amount"] = 0.0

    merged["ticket_txns"] = merged["ticket_txns"].apply(lambda x: x if isinstance(x, set) else set())
    merged["fnb_txns"] = merged["fnb_txns"].apply(lambda x: x if isinstance(x, set) else set())
    merged["ticket_net_amount"] = merged["ticket_net_amount"].fillna(0.0)
    merged["fnb_line_amount"] = merged["fnb_line_amount"].fillna(0.0)

    # Ngày đầu và ngày cuối
    merged["first_purchase_date"] = merged[["first_ticket_date", "first_fnb_date"]].min(axis=1)
    merged["last_purchase_date"] = merged[["last_ticket_date", "last_fnb_date"]].max(axis=1)

    # 1. R: Số ngày từ lần mua cuối cùng đến mốc ref_dt
    merged["recency_days"] = (ref_dt - merged["last_purchase_date"]).dt.days.fillna(0).astype(int)

    # 2. F: Số txn_id khác nhau
    merged["all_txns"] = merged.apply(lambda row: row["ticket_txns"] | row["fnb_txns"], axis=1)
    merged["frequency"] = merged["all_txns"].apply(len).astype(int)

    # 3. M: net_amount vé + line_amount bắp nước
    merged["monetary"] = (merged["ticket_net_amount"] + merged["fnb_line_amount"]).round(2)

    # 4. Tenure: Số ngày từ giao dịch đầu tiên đến giao dịch cuối cùng
    merged["tenure"] = (merged["last_purchase_date"] - merged["first_purchase_date"]).dt.days.fillna(0).astype(int)

    if "customer_id" not in merged.columns or merged["customer_id"].isnull().all():
        merged["customer_id"] = merged["customer_key"].apply(lambda k: f"CUS_{k}")
    else:
        merged["customer_id"] = merged["customer_id"].fillna(merged["customer_key"].apply(lambda k: f"CUS_{k}"))

    # Điểm số R, F, M (1..5)
    try:
        merged["r_score"] = pd.qcut(merged["recency_days"].rank(method="first"), q=5, labels=[5, 4, 3, 2, 1]).astype(int)
        merged["f_score"] = pd.qcut(merged["frequency"].rank(method="first"), q=5, labels=[1, 2, 3, 4, 5]).astype(int)
        merged["m_score"] = pd.qcut(merged["monetary"].rank(method="first"), q=5, labels=[1, 2, 3, 4, 5]).astype(int)
    except Exception:
        merged["r_score"] = 3
        merged["f_score"] = 3
        merged["m_score"] = 3

    merged["rfm_score"] = merged["r_score"].astype(str) + merged["f_score"].astype(str) + merged["m_score"].astype(str)

    result_cols = [
        "customer_key", "customer_id", "recency_days", "frequency", "monetary",
        "tenure", "r_score", "f_score", "m_score", "rfm_score"
    ]
    return merged[result_cols].sort_values("customer_key").reset_index(drop=True)


def evaluate_kmeans_candidates(
    df_rfm: pd.DataFrame,
    k_range: range = range(3, 7),
    random_state: int = 42,
) -> Tuple[int, Dict[int, Dict[str, float]], np.ndarray]:
    """
    Tiền xử lý log1p + StandardScaler trên [recency_days, frequency, monetary, tenure]
    và đánh giá k theo Silhouette Score & Inertia.
    """
    if len(df_rfm) < max(k_range):
        raise ValueError(f"Số lượng khách hàng ({len(df_rfm)}) nhỏ hơn k tối đa ({max(k_range)}).")

    features = df_rfm[["recency_days", "frequency", "monetary", "tenure"]].copy()
    X_log = np.log1p(features.values)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_log)

    metrics: Dict[int, Dict[str, float]] = {}
    best_k = min(k_range)
    best_silhouette = -1.0

    print("\n--- ĐÁNH GIÁ CÁC GIÁ TRỊ K (K-MEANS) ---")
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=random_state, n_init=10)
        labels = km.fit_predict(X_scaled)
        sil = float(silhouette_score(X_scaled, labels))
        inertia = float(km.inertia_)

        metrics[k] = {"inertia": inertia, "silhouette": sil}
        print(f"k={k}: Inertia = {inertia:10.2f} | Silhouette Score = {sil:.4f}")

        if sil > best_silhouette:
            best_silhouette = sil
            best_k = k

    print(f"--> k tối ưu được chọn: k={best_k} (Silhouette = {best_silhouette:.4f})")
    return best_k, metrics, X_scaled


def assign_cluster_names(centroids_df: pd.DataFrame) -> Dict[int, str]:
    """
    Gán tên cụm bằng quy tắc kết hợp thứ hạng và ngưỡng tuyệt đối:
    - Ngủ đông khi R > 180;
    - Có nguy cơ rời bỏ khi R từ 60 đến 180;
    - Mới khi tenure <= 60;
    - Còn lại chia VIP / Trung thành / Thỉnh thoảng theo F và M.
    """
    labels: Dict[int, str] = {}
    remaining = list(centroids_df.index)

    # 1. Ngủ đông: R > 180
    dormant_candidates = [cid for cid in remaining if centroids_df.loc[cid, "recency_days"] > 180]
    if dormant_candidates:
        best_dormant = max(dormant_candidates, key=lambda c: centroids_df.loc[c, "recency_days"])
        labels[best_dormant] = "Ngủ đông"
        remaining.remove(best_dormant)

    # 2. Có nguy cơ rời bỏ: 60 <= R <= 180
    at_risk_candidates = [cid for cid in remaining if 60 <= centroids_df.loc[cid, "recency_days"] <= 180]
    if at_risk_candidates:
        best_at_risk = max(at_risk_candidates, key=lambda c: centroids_df.loc[c, "recency_days"])
        labels[best_at_risk] = "Có nguy cơ rời bỏ"
        remaining.remove(best_at_risk)

    # 3. Mới: tenure <= 60
    new_candidates = [cid for cid in remaining if centroids_df.loc[cid, "tenure"] <= 60]
    if new_candidates:
        best_new = min(new_candidates, key=lambda c: centroids_df.loc[c, "tenure"])
        labels[best_new] = "Mới"
        remaining.remove(best_new)

    # 4. Các cụm còn lại: chia VIP / Trung thành / Thỉnh thoảng theo thứ hạng F và M
    if remaining:
        ranked = sorted(
            remaining,
            key=lambda c: (centroids_df.loc[c, "frequency"], centroids_df.loc[c, "monetary"]),
            reverse=True,
        )
        active_names = ["VIP", "Trung thành", "Thỉnh thoảng"]
        for idx, cid in enumerate(ranked):
            if idx < len(active_names):
                labels[cid] = active_names[idx]
            else:
                labels[cid] = "Thỉnh thoảng"

    return labels


def cluster_rfm_customers(
    df_rfm: pd.DataFrame,
    k: int,
    X_scaled: np.ndarray,
    random_state: int = 42,
) -> Tuple[pd.DataFrame, Dict[int, str]]:
    """
    Chạy K-Means với k đã chọn và gán nhãn theo quy tắc kết hợp thứ hạng và ngưỡng tuyệt đối.
    """
    km = KMeans(n_clusters=k, random_state=random_state, n_init=10)
    cluster_ids = km.fit_predict(X_scaled)

    df_result = df_rfm.copy()
    df_result["cluster_id"] = cluster_ids

    # Tính tâm cụm trên R, F, M, tenure gốc
    centroids = df_result.groupby("cluster_id")[["recency_days", "frequency", "monetary", "tenure"]].mean()
    label_map = assign_cluster_names(centroids)

    df_result["cluster_name"] = df_result["cluster_id"].map(label_map)
    return df_result, label_map


def build_cluster_profiles(
    df_rfm_clustered: pd.DataFrame,
    df_tickets: Optional[pd.DataFrame] = None,
    df_fnb: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Xây dựng hồ sơ chân dung cụm:
    - cluster_id, cluster_name
    - customer_count, pct_customers
    - avg_recency, avg_frequency, avg_monetary, avg_tenure
    - top_genre: Thể loại phim xem nhiều nhất của cụm
    - combo_rate: Tỷ lệ mua combo của cụm (số giao dịch mua combo / tổng giao dịch vé)
    """
    total_cust = len(df_rfm_clustered)

    profile_df = df_rfm_clustered.groupby(["cluster_id", "cluster_name"]).agg(
        customer_count=("customer_key", "count"),
        avg_recency=("recency_days", "mean"),
        avg_frequency=("frequency", "mean"),
        avg_monetary=("monetary", "mean"),
        avg_tenure=("tenure", "mean"),
    ).reset_index()

    profile_df["pct_customers"] = ((profile_df["customer_count"] / total_cust) * 100.0).round(2)
    profile_df["avg_recency"] = profile_df["avg_recency"].round(2)
    profile_df["avg_frequency"] = profile_df["avg_frequency"].round(2)
    profile_df["avg_monetary"] = profile_df["avg_monetary"].round(2)
    profile_df["avg_tenure"] = profile_df["avg_tenure"].round(2)

    # Xác định top_genre
    top_genres: Dict[int, str] = {}
    total_ticket_txns_by_cluster: Dict[int, int] = {}
    if df_tickets is not None and not df_tickets.empty and "genre" in df_tickets.columns:
        t_clean = df_tickets[df_tickets["is_anomaly"] == 0] if "is_anomaly" in df_tickets.columns else df_tickets
        t_merged = pd.merge(t_clean, df_rfm_clustered[["customer_key", "cluster_id"]], on="customer_key")

        for cid, group in t_merged.groupby("cluster_id"):
            genre_mode = group["genre"].mode()
            top_genres[cid] = str(genre_mode.iloc[0]) if not genre_mode.empty else "N/A"
            total_ticket_txns_by_cluster[cid] = group["txn_id"].nunique()

    # Tính tỷ lệ mua combo (combo_rate)
    combo_rates: Dict[int, float] = {}
    if df_fnb is not None and not df_fnb.empty and "is_combo" in df_fnb.columns:
        f_clean = df_fnb[df_fnb["is_anomaly"] == 0] if "is_anomaly" in df_fnb.columns else df_fnb
        f_merged = pd.merge(f_clean, df_rfm_clustered[["customer_key", "cluster_id"]], on="customer_key")

        for cid, group in f_merged.groupby("cluster_id"):
            combo_txns = group[group["is_combo"] == True]["txn_id"].nunique()  # noqa: E712
            ticket_txns = total_ticket_txns_by_cluster.get(cid, 0)
            if ticket_txns > 0:
                rate = combo_txns / ticket_txns
            elif group["qty"].sum() > 0 if "qty" in group.columns else False:
                rate = group[group["is_combo"] == True]["qty"].sum() / group["qty"].sum()  # noqa: E712
            else:
                rate = combo_txns / max(1, group["txn_id"].nunique())
            combo_rates[cid] = round(rate, 4)

    profile_df["top_genre"] = profile_df["cluster_id"].map(lambda cid: top_genres.get(cid, "Hành động"))
    profile_df["combo_rate"] = profile_df["cluster_id"].map(lambda cid: combo_rates.get(cid, 0.0))

    cols = [
        "cluster_id", "cluster_name", "customer_count", "pct_customers",
        "avg_recency", "avg_frequency", "avg_monetary", "avg_tenure", "top_genre", "combo_rate"
    ]
    return profile_df[cols].sort_values("cluster_id").reset_index(drop=True)


def save_rfm_pipeline_results(
    df_rfm: pd.DataFrame,
    df_profile: pd.DataFrame,
    metrics: Dict[int, Dict[str, float]],
    best_k: int,
    engine: Engine,
) -> None:
    """
    Lưu kết quả RFM vào cơ sở dữ liệu:
    - cinema_dw.rfm_result (kèm tenure_days)
    - cinema_dw.rfm_cluster_profile (kèm avg_tenure)
    - cinema_dw.model_metrics (ghi nhận inertia & silhouette)
    Đảm bảo Idempotent: chạy nhiều lần không nhân đôi bản ghi.
    """
    with engine.begin() as conn:
        # 1. Ghi rfm_result
        conn.execute(text("DELETE FROM cinema_dw.rfm_result"))
        records_to_insert = df_rfm.copy()
        if "tenure" in records_to_insert.columns:
            records_to_insert["tenure_days"] = records_to_insert["tenure"]
        else:
            records_to_insert["tenure_days"] = 0

        rfm_records = records_to_insert[[
            "customer_key", "customer_id", "recency_days", "frequency",
            "monetary", "tenure_days", "r_score", "f_score", "m_score", "rfm_score",
            "cluster_id", "cluster_name"
        ]].to_dict(orient="records")

        insert_rfm_sql = text("""
            INSERT INTO cinema_dw.rfm_result (
                customer_key, customer_id, recency_days, frequency,
                monetary, tenure_days, r_score, f_score, m_score, rfm_score,
                cluster_id, cluster_name
            ) VALUES (
                :customer_key, :customer_id, :recency_days, :frequency,
                :monetary, :tenure_days, :r_score, :f_score, :m_score, :rfm_score,
                :cluster_id, :cluster_name
            )
        """)
        conn.execute(insert_rfm_sql, rfm_records)

        # 2. Ghi rfm_cluster_profile
        conn.execute(text("DELETE FROM cinema_dw.rfm_cluster_profile"))
        profile_records = df_profile.to_dict(orient="records")
        insert_profile_sql = text("""
            INSERT INTO cinema_dw.rfm_cluster_profile (
                cluster_id, cluster_name, customer_count, pct_customers,
                avg_recency, avg_frequency, avg_monetary, avg_tenure, top_genre, combo_rate
            ) VALUES (
                :cluster_id, :cluster_name, :customer_count, :pct_customers,
                :avg_recency, :avg_frequency, :avg_monetary, :avg_tenure, :top_genre, :combo_rate
            )
        """)
        conn.execute(insert_profile_sql, profile_records)

        # 3. Ghi model_metrics cho lần đánh giá K-Means này
        conn.execute(text("DELETE FROM cinema_dw.model_metrics WHERE model_name = 'rfm_kmeans'"))
        metrics_records = []
        for k_val, val in metrics.items():
            metrics_records.append({
                "model_name": "rfm_kmeans",
                "model_version": "v1.0",
                "metric_name": f"silhouette_k{k_val}",
                "metric_value": round(val["silhouette"], 4),
                "params": f'{{"k": {k_val}, "is_best": {str(k_val == best_k).lower()}}}',
            })
            metrics_records.append({
                "model_name": "rfm_kmeans",
                "model_version": "v1.0",
                "metric_name": f"inertia_k{k_val}",
                "metric_value": round(val["inertia"], 4),
                "params": f'{{"k": {k_val}}}',
            })

        insert_metrics_sql = text("""
            INSERT INTO cinema_dw.model_metrics (
                model_name, model_version, metric_name, metric_value, params
            ) VALUES (
                :model_name, :model_version, :metric_name, :metric_value, CAST(:params AS JSONB)
            )
        """)
        conn.execute(insert_metrics_sql, metrics_records)

    logger.info(
        "Đã lưu thành công %d khách hàng vào rfm_result, %d cụm vào rfm_cluster_profile, và metrics vào model_metrics.",
        len(df_rfm), len(df_profile)
    )

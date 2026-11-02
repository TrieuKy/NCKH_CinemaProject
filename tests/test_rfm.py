"""
tests/test_rfm.py
-----------------
Unit tests cho module RFM + K-Means (core/rfm.py).
Hoàn toàn sử dụng DataFrame giả (mock/synthetic in-memory), KHÔNG kết nối cơ sở dữ liệu.
"""

from datetime import date, datetime
import numpy as np
import pandas as pd
import pytest

from core.rfm import (
    compute_rfm_metrics,
    evaluate_kmeans_candidates,
    assign_cluster_names,
    cluster_rfm_customers,
    build_cluster_profiles,
)


@pytest.fixture
def dummy_tickets_and_fnb():
    """Tạo bộ dữ liệu giả lập cho vé và F&B với các trường hợp biên."""
    ref_date = date(2026, 10, 1)

    # 4 khách:
    # 1: VIP (mua gần, nhiều lần, tiền lớn)
    # 2: Ngủ đông (mua từ lâu, ít lần, tiền ít)
    # 3: Khách vãng lai (GUEST -> phải bị lọc)
    # 4: Giao dịch bất thường (is_anomaly = 1 -> phải bị loại)
    tickets = pd.DataFrame([
        # Khách 1 (VIP)
        {
            "customer_key": 1, "customer_id": "CUS001", "txn_id": "TXN_01",
            "net_amount": 100_000, "full_date": date(2026, 9, 30), "genre": "Hành động",
            "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 1, "customer_id": "CUS001", "txn_id": "TXN_02",
            "net_amount": 150_000, "full_date": date(2026, 9, 28), "genre": "Hành động",
            "is_anomaly": 0, "is_guest": False
        },
        # Khách 2 (Ngủ đông)
        {
            "customer_key": 2, "customer_id": "CUS002", "txn_id": "TXN_03",
            "net_amount": 80_000, "full_date": date(2026, 8, 1), "genre": "Kinh dị",
            "is_anomaly": 0, "is_guest": False
        },
        # Khách 3 (Guest -> bị lọc)
        {
            "customer_key": 3, "customer_id": "GUEST", "txn_id": "TXN_04",
            "net_amount": 500_000, "full_date": date(2026, 9, 30), "genre": "Hài",
            "is_anomaly": 0, "is_guest": True
        },
        # Khách 4 (Có 1 txn sạch và 1 txn bất thường)
        {
            "customer_key": 4, "customer_id": "CUS004", "txn_id": "TXN_05",
            "net_amount": 90_000, "full_date": date(2026, 9, 15), "genre": "Tình cảm",
            "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 4, "customer_id": "CUS004", "txn_id": "TXN_ANOM",
            "net_amount": 99_000_000, "full_date": date(2026, 9, 30), "genre": "Hành động",
            "is_anomaly": 1, "is_guest": False  # Phải bị bỏ qua
        },
    ])

    fnb = pd.DataFrame([
        # Khách 1 mua combo ở TXN_01 và snack ở TXN_02
        {
            "customer_key": 1, "customer_id": "CUS001", "txn_id": "TXN_01",
            "line_amount": 60_000, "qty": 1, "is_combo": True,
            "full_date": date(2026, 9, 30), "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 1, "customer_id": "CUS001", "txn_id": "TXN_02",
            "line_amount": 40_000, "qty": 1, "is_combo": False,
            "full_date": date(2026, 9, 28), "is_anomaly": 0, "is_guest": False
        },
        # Khách 4 mua F&B bất thường
        {
            "customer_key": 4, "customer_id": "CUS004", "txn_id": "TXN_05",
            "line_amount": 30_000, "qty": 1, "is_combo": False,
            "full_date": date(2026, 9, 15), "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 4, "customer_id": "CUS004", "txn_id": "TXN_ANOM_FNB",
            "line_amount": 10_000_000, "qty": 100, "is_combo": True,
            "full_date": date(2026, 9, 30), "is_anomaly": 1, "is_guest": False  # Bỏ qua
        },
    ])

    return tickets, fnb, ref_date


def test_compute_rfm_filters_guests_and_anomalies(dummy_tickets_and_fnb):
    """Kiểm tra chỉ lấy is_guest = FALSE và loại bỏ dòng is_anomaly = 1."""
    tickets, fnb, ref_date = dummy_tickets_and_fnb
    rfm = compute_rfm_metrics(tickets, fnb, reference_date=ref_date)

    # Khách 3 (is_guest=True) không được có trong rfm
    assert 3 not in rfm["customer_key"].values
    assert "GUEST" not in rfm["customer_id"].values

    # Các khách hợp lệ còn lại: 1, 2, 4
    assert set(rfm["customer_key"].values) == {1, 2, 4}

    # Kiểm tra khách 4 không bị tính khoản bất thường 99_000_000 vé và 10_000_000 fnb
    cus4 = rfm[rfm["customer_key"] == 4].iloc[0]
    assert cus4["monetary"] == 90_000 + 30_000  # 120_000
    assert cus4["frequency"] == 1  # Chỉ tính TXN_05, bỏ TXN_ANOM và TXN_ANOM_FNB
    assert cus4["recency_days"] == (ref_date - date(2026, 9, 15)).days


def test_compute_rfm_exact_calculations(dummy_tickets_and_fnb):
    """Kiểm tra tính R, F, M chính xác theo định nghĩa đề bài."""
    tickets, fnb, ref_date = dummy_tickets_and_fnb
    rfm = compute_rfm_metrics(tickets, fnb, reference_date=ref_date)

    cus1 = rfm[rfm["customer_key"] == 1].iloc[0]
    # R: 2026-10-01 - 2026-09-30 = 1 ngày
    assert cus1["recency_days"] == 1
    # F: TXN_01 và TXN_02 = 2 giao dịch khác nhau
    assert cus1["frequency"] == 2
    # M: Vé (100k + 150k) + F&B (60k + 40k) = 350,000
    assert cus1["monetary"] == 350_000

    cus2 = rfm[rfm["customer_key"] == 2].iloc[0]
    # R: 2026-10-01 - 2026-08-01 = 61 ngày
    assert cus2["recency_days"] == 61
    assert cus2["frequency"] == 1
    assert cus2["monetary"] == 80_000


@pytest.fixture
def synthetic_rfm_clusters():
    """Tạo tập RFM giả lập 60 khách với 4 cụm phân tách rõ rệt."""
    np.random.seed(42)
    clusters = []

    # Cụm 1: VIP (R thấp, F cao, M cao)
    for i in range(15):
        clusters.append({
            "customer_key": 100 + i, "customer_id": f"CUS_{100+i}",
            "recency_days": np.random.randint(1, 4),
            "frequency": np.random.randint(60, 80),
            "monetary": np.random.uniform(15_000_000, 20_000_000),
        })

    # Cụm 2: Trung thành (R thấp, F trung bình-cao, M khá)
    for i in range(15):
        clusters.append({
            "customer_key": 200 + i, "customer_id": f"CUS_{200+i}",
            "recency_days": np.random.randint(1, 5),
            "frequency": np.random.randint(45, 55),
            "monetary": np.random.uniform(11_000_000, 14_000_000),
        })

    # Cụm 3: Có nguy cơ rời bỏ (R cao, F khá, M khá)
    for i in range(15):
        clusters.append({
            "customer_key": 300 + i, "customer_id": f"CUS_{300+i}",
            "recency_days": np.random.randint(20, 40),
            "frequency": np.random.randint(45, 60),
            "monetary": np.random.uniform(11_000_000, 15_000_000),
        })

    # Cụm 4: Ngủ đông (R cao, F thấp, M thấp)
    for i in range(15):
        clusters.append({
            "customer_key": 400 + i, "customer_id": f"CUS_{400+i}",
            "recency_days": np.random.randint(30, 60),
            "frequency": np.random.randint(10, 25),
            "monetary": np.random.uniform(3_000_000, 6_000_000),
        })

    return pd.DataFrame(clusters)


def test_evaluate_kmeans_candidates(synthetic_rfm_clusters):
    """Kiểm tra đánh giá k = 3..6 và chọn k theo silhouette score."""
    best_k, metrics, X_scaled = evaluate_kmeans_candidates(synthetic_rfm_clusters, k_range=range(3, 7))

    assert 3 <= best_k <= 6
    assert len(metrics) == 4
    for k in range(3, 7):
        assert k in metrics
        assert "inertia" in metrics[k]
        assert "silhouette" in metrics[k]
        assert metrics[k]["inertia"] > 0
        assert -1.0 <= metrics[k]["silhouette"] <= 1.0

    # Best k phải có silhouette lớn nhất
    assert metrics[best_k]["silhouette"] == max(m["silhouette"] for m in metrics.values())
    assert X_scaled.shape == (len(synthetic_rfm_clusters), 3)


def test_assign_cluster_names_logic():
    """Kiểm tra luật xếp hạng tâm cụm theo ngũ phân chuẩn nghiệp vụ."""
    # Giả lập tâm cụm k=4
    centroids_k4 = pd.DataFrame([
        {"recency_days": 2.0, "frequency": 70.0, "monetary": 18_000_000.0},  # VIP
        {"recency_days": 2.5, "frequency": 50.0, "monetary": 12_000_000.0},  # Trung thành
        {"recency_days": 25.0, "frequency": 50.0, "monetary": 13_000_000.0}, # Có nguy cơ rời bỏ
        {"recency_days": 40.0, "frequency": 15.0, "monetary": 4_000_000.0},  # Ngủ đông
    ])
    labels_k4 = assign_cluster_names(centroids_k4)
    assert labels_k4[0] == "VIP"
    assert labels_k4[1] == "Trung thành"
    assert labels_k4[2] == "Có nguy cơ rời bỏ"
    assert labels_k4[3] == "Ngủ đông"

    # Giả lập tâm cụm k=5
    centroids_k5 = pd.DataFrame([
        {"recency_days": 2.0, "frequency": 70.0, "monetary": 18_000_000.0},  # VIP
        {"recency_days": 2.0, "frequency": 55.0, "monetary": 13_000_000.0},  # Trung thành
        {"recency_days": 2.0, "frequency": 30.0, "monetary": 8_000_000.0},   # Mới
        {"recency_days": 25.0, "frequency": 50.0, "monetary": 12_000_000.0}, # Có nguy cơ rời bỏ
        {"recency_days": 40.0, "frequency": 15.0, "monetary": 4_000_000.0},  # Ngủ đông
    ])
    labels_k5 = assign_cluster_names(centroids_k5)
    assert labels_k5[0] == "VIP"
    assert labels_k5[1] == "Trung thành"
    assert labels_k5[2] == "Mới"
    assert labels_k5[3] == "Có nguy cơ rời bỏ"
    assert labels_k5[4] == "Ngủ đông"


def test_build_cluster_profiles(synthetic_rfm_clusters):
    """Kiểm tra xây dựng bảng hồ sơ cụm có đủ cột, top_genre và combo_rate."""
    best_k, metrics, X_scaled = evaluate_kmeans_candidates(synthetic_rfm_clusters, k_range=range(3, 5))
    df_clustered, label_map = cluster_rfm_customers(synthetic_rfm_clusters, best_k, X_scaled)

    # Giả lập lịch sử vé & F&B tương ứng
    mock_tickets = []
    mock_fnb = []
    for _, row in df_clustered.iterrows():
        ckey = row["customer_key"]
        # VIP thích Hành động, các nhóm khác thích Hài
        genre = "Hành động" if row["cluster_name"] == "VIP" else "Hài"
        mock_tickets.append({
            "customer_key": ckey, "txn_id": f"T_{ckey}_1", "genre": genre, "is_anomaly": 0
        })
        # VIP mua combo, nhóm khác mua snack lẻ
        mock_fnb.append({
            "customer_key": ckey, "txn_id": f"T_{ckey}_1", "qty": 1,
            "is_combo": (row["cluster_name"] == "VIP"), "is_anomaly": 0
        })

    df_t = pd.DataFrame(mock_tickets)
    df_f = pd.DataFrame(mock_fnb)

    profile = build_cluster_profiles(df_clustered, df_t, df_f)

    # Kiểm tra các cột bắt buộc
    expected_cols = [
        "cluster_id", "cluster_name", "customer_count", "pct_customers",
        "avg_recency", "avg_frequency", "avg_monetary", "top_genre", "combo_rate"
    ]
    assert list(profile.columns) == expected_cols
    assert len(profile) == best_k
    assert profile["customer_count"].sum() == len(synthetic_rfm_clusters)
    assert pytest.approx(profile["pct_customers"].sum(), 0.1) == 100.0

    # Kiểm tra top_genre của VIP là Hành động
    vip_prof = profile[profile["cluster_name"] == "VIP"].iloc[0]
    assert vip_prof["top_genre"] == "Hành động"
    assert vip_prof["combo_rate"] > 0.0


def test_empty_dataframe_handling():
    """Kiểm tra xử lý an toàn khi đầu vào trống."""
    empty_df = pd.DataFrame(columns=["customer_key", "full_date", "txn_id", "net_amount", "is_anomaly"])
    rfm = compute_rfm_metrics(empty_df)
    assert rfm.empty
    assert "recency_days" in rfm.columns
    assert "frequency" in rfm.columns
    assert "monetary" in rfm.columns

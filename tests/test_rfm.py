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
            "net_amount": 80_000, "full_date": date(2026, 1, 1), "genre": "Kinh dị",
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
            "is_anomaly": 1, "is_guest": False
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
            "full_date": date(2026, 9, 30), "is_anomaly": 1, "is_guest": False
        },
    ])

    return tickets, fnb, ref_date


def test_compute_rfm_filters_guests_and_anomalies(dummy_tickets_and_fnb):
    """Kiểm tra chỉ lấy is_guest = FALSE và loại bỏ dòng is_anomaly = 1."""
    tickets, fnb, ref_date = dummy_tickets_and_fnb
    rfm = compute_rfm_metrics(tickets, fnb, reference_date=ref_date)

    assert 3 not in rfm["customer_key"].values
    assert "GUEST" not in rfm["customer_id"].values
    assert set(rfm["customer_key"].values) == {1, 2, 4}

    cus4 = rfm[rfm["customer_key"] == 4].iloc[0]
    assert cus4["monetary"] == 90_000 + 30_000
    assert cus4["frequency"] == 1
    assert cus4["recency_days"] == (ref_date - date(2026, 9, 15)).days


def test_compute_rfm_exact_calculations(dummy_tickets_and_fnb):
    """Kiểm tra tính R, F, M, Tenure chính xác theo định nghĩa đề bài."""
    tickets, fnb, ref_date = dummy_tickets_and_fnb
    rfm = compute_rfm_metrics(tickets, fnb, reference_date=ref_date)

    cus1 = rfm[rfm["customer_key"] == 1].iloc[0]
    assert cus1["recency_days"] == 1
    assert cus1["frequency"] == 2
    assert cus1["monetary"] == 350_000
    # Tenure: 2026-09-30 - 2026-09-28 = 2 ngày
    assert cus1["tenure"] == 2

    cus2 = rfm[rfm["customer_key"] == 2].iloc[0]
    # R: 2026-10-01 - 2026-01-01 = 273 ngày (> 180)
    assert cus2["recency_days"] == 273
    assert cus2["frequency"] == 1
    assert cus2["monetary"] == 80_000
    assert cus2["tenure"] == 0


@pytest.fixture
def synthetic_rfm_clusters():
    """Tạo tập RFM giả lập 60 khách với 4 cụm phân tách rõ rệt."""
    np.random.seed(42)
    clusters = []

    # Cụm 1: VIP (R thấp, F cao, M cao, tenure cao)
    for i in range(15):
        clusters.append({
            "customer_key": 100 + i, "customer_id": f"CUS_{100+i}",
            "recency_days": np.random.randint(1, 4),
            "frequency": np.random.randint(60, 80),
            "monetary": np.random.uniform(15_000_000, 20_000_000),
            "tenure": np.random.randint(250, 365),
        })

    # Cụm 2: Trung thành (R thấp, F trung bình-cao, M khá, tenure cao)
    for i in range(15):
        clusters.append({
            "customer_key": 200 + i, "customer_id": f"CUS_{200+i}",
            "recency_days": np.random.randint(1, 5),
            "frequency": np.random.randint(45, 55),
            "monetary": np.random.uniform(11_000_000, 14_000_000),
            "tenure": np.random.randint(200, 350),
        })

    # Cụm 3: Có nguy cơ rời bỏ (R 60-180, F khá, M khá, tenure trung bình)
    for i in range(15):
        clusters.append({
            "customer_key": 300 + i, "customer_id": f"CUS_{300+i}",
            "recency_days": np.random.randint(70, 140),
            "frequency": np.random.randint(45, 60),
            "monetary": np.random.uniform(11_000_000, 15_000_000),
            "tenure": np.random.randint(150, 250),
        })

    # Cụm 4: Ngủ đông (R > 180, F thấp, M thấp, tenure thấp)
    for i in range(15):
        clusters.append({
            "customer_key": 400 + i, "customer_id": f"CUS_{400+i}",
            "recency_days": np.random.randint(190, 250),
            "frequency": np.random.randint(10, 25),
            "monetary": np.random.uniform(3_000_000, 6_000_000),
            "tenure": np.random.randint(30, 100),
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

    assert metrics[best_k]["silhouette"] == max(m["silhouette"] for m in metrics.values())
    assert X_scaled.shape == (len(synthetic_rfm_clusters), 4)


def test_assign_cluster_names_logic():
    """Kiểm tra quy tắc kết hợp thứ hạng và ngưỡng tuyệt đối."""
    # 4 cụm: R>180 (Ngủ đông), R 60-180 (Có nguy cơ rời bỏ), R<60 chia VIP & Trung thành
    centroids_k4 = pd.DataFrame([
        {"recency_days": 2.0, "frequency": 70.0, "monetary": 18_000_000.0, "tenure": 320.0},  # VIP
        {"recency_days": 3.0, "frequency": 50.0, "monetary": 12_000_000.0, "tenure": 300.0},  # Trung thành
        {"recency_days": 95.0, "frequency": 45.0, "monetary": 11_000_000.0, "tenure": 200.0}, # Có nguy cơ rời bỏ (60-180)
        {"recency_days": 210.0, "frequency": 15.0, "monetary": 4_000_000.0, "tenure": 50.0},  # Ngủ đông (>180)
    ])
    labels_k4 = assign_cluster_names(centroids_k4)
    assert labels_k4[0] == "VIP"
    assert labels_k4[1] == "Trung thành"
    assert labels_k4[2] == "Có nguy cơ rời bỏ"
    assert labels_k4[3] == "Ngủ đông"

    # 6 cụm đầy đủ: thêm Mới (tenure <= 60, R thấp), Thỉnh thoảng
    centroids_k6 = pd.DataFrame([
        {"recency_days": 2.0, "frequency": 30.0, "monetary": 18_000_000.0, "tenure": 320.0},  # VIP
        {"recency_days": 3.0, "frequency": 10.0, "monetary": 10_000_000.0, "tenure": 300.0},  # Trung thành
        {"recency_days": 4.0, "frequency": 3.0, "monetary": 2_500_000.0, "tenure": 250.0},   # Thỉnh thoảng
        {"recency_days": 8.0, "frequency": 4.0, "monetary": 3_000_000.0, "tenure": 40.0},    # Mới (tenure <= 60)
        {"recency_days": 100.0, "frequency": 8.0, "monetary": 7_000_000.0, "tenure": 200.0},  # Có nguy cơ rời bỏ (60-180)
        {"recency_days": 220.0, "frequency": 5.0, "monetary": 4_000_000.0, "tenure": 60.0},   # Ngủ đông (>180)
    ])
    labels_k6 = assign_cluster_names(centroids_k6)
    assert labels_k6[0] == "VIP"
    assert labels_k6[1] == "Trung thành"
    assert labels_k6[2] == "Thỉnh thoảng"
    assert labels_k6[3] == "Mới"
    assert labels_k6[4] == "Có nguy cơ rời bỏ"
    assert labels_k6[5] == "Ngủ đông"


def test_build_cluster_profiles(synthetic_rfm_clusters):
    """Kiểm tra xây dựng bảng hồ sơ cụm có đủ cột, top_genre, combo_rate và avg_tenure."""
    best_k, metrics, X_scaled = evaluate_kmeans_candidates(synthetic_rfm_clusters, k_range=range(3, 5))
    df_clustered, label_map = cluster_rfm_customers(synthetic_rfm_clusters, best_k, X_scaled)

    mock_tickets = []
    mock_fnb = []
    for _, row in df_clustered.iterrows():
        ckey = row["customer_key"]
        genre = "Hành động" if row["cluster_name"] == "VIP" else "Hài"
        mock_tickets.append({
            "customer_key": ckey, "txn_id": f"T_{ckey}_1", "genre": genre, "is_anomaly": 0
        })
        mock_fnb.append({
            "customer_key": ckey, "txn_id": f"T_{ckey}_1", "qty": 1,
            "is_combo": (row["cluster_name"] == "VIP"), "is_anomaly": 0
        })

    df_t = pd.DataFrame(mock_tickets)
    df_f = pd.DataFrame(mock_fnb)

    profile = build_cluster_profiles(df_clustered, df_t, df_f)

    expected_cols = [
        "cluster_id", "cluster_name", "customer_count", "pct_customers",
        "avg_recency", "avg_frequency", "avg_monetary", "avg_tenure", "top_genre", "combo_rate"
    ]
    assert list(profile.columns) == expected_cols
    assert len(profile) == best_k
    assert profile["customer_count"].sum() == len(synthetic_rfm_clusters)
    assert pytest.approx(profile["pct_customers"].sum(), 0.1) == 100.0

    vip_prof = profile[profile["cluster_name"] == "VIP"].iloc[0]
    assert vip_prof["top_genre"] == "Hành động"
    assert vip_prof["combo_rate"] > 0.0
    assert vip_prof["avg_tenure"] > 0


def test_empty_dataframe_handling():
    """Kiểm tra xử lý an toàn khi đầu vào trống."""
    empty_df = pd.DataFrame(columns=["customer_key", "full_date", "txn_id", "net_amount", "is_anomaly"])
    rfm = compute_rfm_metrics(empty_df)
    assert rfm.empty
    assert "recency_days" in rfm.columns
    assert "frequency" in rfm.columns
    assert "monetary" in rfm.columns
    assert "tenure" in rfm.columns


# ==============================================================================
# BỔ SUNG CÁC TEST KIỂM ĐỊNH KHOA HỌC (TASK 6 - VALIDATION)
# ==============================================================================

def test_frequency_single_txn_multiple_tickets():
    """
    Kiểm định: Frequency đếm số txn_id duy nhất, không bị nhân đôi theo số vé (qty).
    Ví dụ: Một giao dịch mua 4 vé (qty=4) chỉ tính frequency = 1.
    """
    tickets = pd.DataFrame([
        {
            "customer_key": 10, "customer_id": "CUS010", "txn_id": "TXN_PARTY_4",
            "qty": 4, "net_amount": 400_000, "full_date": date(2026, 8, 1),
            "is_anomaly": 0, "is_guest": False
        }
    ])
    rfm = compute_rfm_metrics(tickets, reference_date=date(2026, 9, 30))
    assert len(rfm) == 1
    row = rfm.iloc[0]
    assert row["frequency"] == 1, "Frequency phải là 1 (theo txn_id), không được là 4 (theo số vé)"
    assert row["monetary"] == 400_000


def test_monetary_single_txn_multiple_fnb_lines():
    """
    Kiểm định: Monetary cộng đúng doanh thu vé và nhiều dòng F&B, không nhân đôi khi JOIN.
    Ví dụ: 1 giao dịch vé 100k + 3 dòng F&B (45k, 39k, 99k) -> Monetary = 283k, F = 1.
    """
    tickets = pd.DataFrame([
        {
            "customer_key": 20, "customer_id": "CUS020", "txn_id": "TXN_MULTI_FNB",
            "net_amount": 100_000, "full_date": date(2026, 9, 1),
            "is_anomaly": 0, "is_guest": False
        }
    ])
    fnb = pd.DataFrame([
        {
            "customer_key": 20, "customer_id": "CUS020", "txn_id": "TXN_MULTI_FNB",
            "line_amount": 45_000, "full_date": date(2026, 9, 1),
            "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 20, "customer_id": "CUS020", "txn_id": "TXN_MULTI_FNB",
            "line_amount": 39_000, "full_date": date(2026, 9, 1),
            "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 20, "customer_id": "CUS020", "txn_id": "TXN_MULTI_FNB",
            "line_amount": 99_000, "full_date": date(2026, 9, 1),
            "is_anomaly": 0, "is_guest": False
        },
    ])
    rfm = compute_rfm_metrics(tickets, fnb, reference_date=date(2026, 9, 30))
    assert len(rfm) == 1
    row = rfm.iloc[0]
    expected_monetary = 100_000 + 45_000 + 39_000 + 99_000
    assert row["monetary"] == expected_monetary, f"Monetary phải là {expected_monetary}, thực tế: {row['monetary']}"
    assert row["frequency"] == 1, "Cùng txn_id giữa vé và 3 dòng F&B chỉ được tính frequency = 1"


def test_guest_customers_strictly_excluded():
    """Kiểm định: Khách GUEST (vãng lai) bị loại hoàn toàn khỏi phân tích RFM cá nhân."""
    tickets = pd.DataFrame([
        {
            "customer_key": 1, "customer_id": "GUEST", "txn_id": "TXN_G1",
            "net_amount": 200_000, "full_date": date(2026, 9, 1),
            "is_anomaly": 0, "is_guest": True
        },
        {
            "customer_key": 2, "customer_id": "CUS002", "txn_id": "TXN_M1",
            "net_amount": 100_000, "full_date": date(2026, 9, 1),
            "is_anomaly": 0, "is_guest": False
        }
    ])
    rfm = compute_rfm_metrics(tickets, reference_date=date(2026, 9, 30))
    assert len(rfm) == 1
    assert "GUEST" not in rfm["customer_id"].values
    assert rfm.iloc[0]["customer_id"] == "CUS002"


def test_anomaly_transactions_excluded_cleanly():
    """Kiểm định: Các giao dịch có is_anomaly = 1 bị loại bỏ ở cả vé và F&B."""
    tickets = pd.DataFrame([
        {
            "customer_key": 30, "customer_id": "CUS030", "txn_id": "TXN_OK",
            "net_amount": 80_000, "full_date": date(2026, 9, 10),
            "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 30, "customer_id": "CUS030", "txn_id": "TXN_ANOM",
            "net_amount": 999_999_999, "full_date": date(2026, 9, 20),
            "is_anomaly": 1, "is_guest": False
        }
    ])
    fnb = pd.DataFrame([
        {
            "customer_key": 30, "customer_id": "CUS030", "txn_id": "TXN_OK",
            "line_amount": 50_000, "full_date": date(2026, 9, 10),
            "is_anomaly": 0, "is_guest": False
        },
        {
            "customer_key": 30, "customer_id": "CUS030", "txn_id": "TXN_ANOM",
            "line_amount": 500_000_000, "full_date": date(2026, 9, 20),
            "is_anomaly": 1, "is_guest": False
        }
    ])
    rfm = compute_rfm_metrics(tickets, fnb, reference_date=date(2026, 9, 30))
    assert len(rfm) == 1
    row = rfm.iloc[0]
    assert row["frequency"] == 1
    assert row["monetary"] == 80_000 + 50_000
    assert row["recency_days"] == 20  # 2026-09-30 - 2026-09-10 = 20 (bỏ qua ngày 20)


def test_customer_without_fnb_handled_cleanly():
    """Kiểm định: Khách hàng không mua F&B có monetary = net_amount vé và không bị NaN."""
    tickets = pd.DataFrame([
        {
            "customer_key": 40, "customer_id": "CUS040", "txn_id": "TXN_NO_FNB",
            "net_amount": 120_000, "full_date": date(2026, 9, 15),
            "is_anomaly": 0, "is_guest": False
        }
    ])
    # Không truyền df_fnb
    rfm_no_fnb_arg = compute_rfm_metrics(tickets, df_fnb=None, reference_date=date(2026, 9, 30))
    assert len(rfm_no_fnb_arg) == 1
    assert rfm_no_fnb_arg.iloc[0]["monetary"] == 120_000
    assert not rfm_no_fnb_arg.isna().any().any()

    # Truyền df_fnb rỗng
    rfm_empty_fnb = compute_rfm_metrics(tickets, df_fnb=pd.DataFrame(), reference_date=date(2026, 9, 30))
    assert len(rfm_empty_fnb) == 1
    assert rfm_empty_fnb.iloc[0]["monetary"] == 120_000
    assert not rfm_empty_fnb.isna().any().any()


def test_single_transaction_customer_tenure_and_features():
    """Kiểm định: Khách hàng chỉ có 1 giao dịch có tenure = 0 và không gây lỗi log1p/scaling."""
    tickets = pd.DataFrame([
        {
            "customer_key": 50, "customer_id": "CUS050", "txn_id": "TXN_SINGLE",
            "net_amount": 95_000, "full_date": date(2026, 9, 25),
            "is_anomaly": 0, "is_guest": False
        }
    ])
    rfm = compute_rfm_metrics(tickets, reference_date=date(2026, 9, 30))
    assert len(rfm) == 1
    row = rfm.iloc[0]
    assert row["frequency"] == 1
    assert row["tenure"] == 0
    assert row["recency_days"] == 5

    # Kiểm tra log1p và scaling không sinh NaN / Inf
    features = rfm[["recency_days", "frequency", "monetary", "tenure"]].values
    X_log = np.log1p(features)
    assert not np.isnan(X_log).any()
    assert not np.isinf(X_log).any()
    assert X_log[0, 3] == 0.0  # log1p(0) == 0


def test_no_latent_labels_leakage_into_training():
    """Kiểm định: Không rò rỉ nhãn tiềm ẩn (latent_segment) vào không gian huấn luyện K-Means."""
    df_rfm = pd.DataFrame([
        {"customer_key": i, "customer_id": f"CUS_{i}", "recency_days": 10 + i * 5, "frequency": 2 + i * 2, "monetary": 200_000 + i * 100_000, "tenure": 10 + i * 20, "latent_segment": f"SEG_{i%3}"}
        for i in range(15)
    ])
    # evaluate_kmeans_candidates chỉ được phép trích xuất đúng 4 đặc trưng hành vi
    best_k, metrics, X_scaled = evaluate_kmeans_candidates(df_rfm, k_range=range(3, 4))
    assert X_scaled.shape[1] == 4, "Không gian đặc trưng chỉ được gồm đúng 4 cột: R, F, M, Tenure"


def test_kmeans_reproducibility_fixed_seed(synthetic_rfm_clusters):
    """Kiểm định: Tính tái lập 100% của K-Means khi cố định random_state."""
    _, _, X_scaled_1 = evaluate_kmeans_candidates(synthetic_rfm_clusters, k_range=range(3, 4), random_state=42)
    _, _, X_scaled_2 = evaluate_kmeans_candidates(synthetic_rfm_clusters, k_range=range(3, 4), random_state=42)
    np.testing.assert_array_almost_equal(X_scaled_1, X_scaled_2)

    df_clustered_1, _ = cluster_rfm_customers(synthetic_rfm_clusters, 4, X_scaled_1, random_state=42)
    df_clustered_2, _ = cluster_rfm_customers(synthetic_rfm_clusters, 4, X_scaled_2, random_state=42)
    pd.testing.assert_series_equal(df_clustered_1["cluster_id"], df_clustered_2["cluster_id"])


def test_insufficient_data_raises_value_error():
    """Kiểm định: Xử lý an toàn khi số lượng khách hàng nhỏ hơn k tối đa."""
    small_df = pd.DataFrame([
        {"customer_key": 1, "customer_id": "CUS1", "recency_days": 10, "frequency": 2, "monetary": 200_000, "tenure": 20},
        {"customer_key": 2, "customer_id": "CUS2", "recency_days": 20, "frequency": 1, "monetary": 100_000, "tenure": 0},
    ])
    with pytest.raises(ValueError, match="nhỏ hơn k tối đa"):
        evaluate_kmeans_candidates(small_df, k_range=range(3, 7))


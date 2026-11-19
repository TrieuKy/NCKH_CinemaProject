# Giai đoạn 2 — Hướng dẫn làm việc (sinh viên / BA–DA)

Mục tiêu giai đoạn này: có **kho dữ liệu đa chiều** và **dữ liệu thô có kiểm soát**, không viết App.

Bạn đang hoàn thành **Bước 1 + Bước 2**. Bước ETL (nạp CSV vào PostgreSQL) làm tiếp sau.

---

## Việc đã có trong repo

| File | Việc của file |
|------|----------------|
| `docs/assumptions.md` | Luật sinh dữ liệu — mang đi bảo vệ đồ án (Hợp đồng dữ liệu) |
| `docs/kpi_dictionary.md` | Từ điển KPI chuẩn hóa (công thức, grain, raw vs clean, SPP/SPT) |
| `docs/data_dictionary.md` | Từ điển dữ liệu Star Schema, Staging và Summary tables |
| `docs/data_lineage.md` | Sơ đồ dòng chảy dữ liệu toàn hệ thống |
| `scripts/generate_mock_data.py` | Pandas/Numpy sinh CSV + nhãn `is_anomaly` |
| `data/staging_ticket.csv` | Vé thô (1 dòng = 1 giao dịch vé, biểu diễn số vé qua `qty`) |
| `data/staging_fnb.csv` | Bắp nước thô, chung `txn_id` |
| `sql/01_schema.sql` | Star schema PostgreSQL |

---

## Bước 1 — Chốt giả định & sinh mock (đã cài)

1. Đọc `docs/assumptions.md` một lượt. Đây là việc **BA**: viết luật trước khi có số.
2. Cài Python 3.10+ rồi:

```bash
pip install -r requirements.txt
python scripts/generate_mock_data.py
```

3. Script tự kiểm tra: 3 rạp CGV (Landmark 81, Vincom Đồng Khởi, Pearl Plaza), 12 tháng (2025-10-01 → 2026-09-30), khách vãng lai ~70%, anomaly 1–2%, F&B không mồ côi, không vượt capacity trừ `qty_spike`.
4. Mở CSV bằng Excel hoặc Python, lọc `is_anomaly = 1` để thấy 3 loại lỗi.

### Gợi ý báo cáo (BA)

- Grain là gì? Phân biệt số dòng giao dịch (`COUNT(*)`) và tổng số vé bán ra (`SUM(qty)`).
- Vì sao `GUEST` chỉ là **một** khách? (tránh RFM giả)
- Vì sao cố ý gieo 1–2% lỗi? (huấn luyện cảnh báo, use case “Xem và xác nhận cảnh báo bất thường”)

---

## Bước 2 — Star schema trên PostgreSQL (đã cài DDL)

1. Cài PostgreSQL 16 (Windows installer). Tạo database:

```sql
CREATE DATABASE cinema_dw;
```

2. Chạy schema:

```bash
psql -U postgres -d cinema_dw -f sql/01_schema.sql
```

Hoặc trong pgAdmin: Query Tool → mở file → Execute.

3. Kiểm tra bảng:

```sql
SET search_path TO cinema_dw;
\dt
```

Phải thấy 9 dimension + 2 fact.

### Gợi ý báo cáo (DA)

Vẽ ngôi sao:

- Tâm: `fact_ticket`, `fact_fnb`
- Cánh: date, time, movie, cinema, room, customer, product, pricing, channel
- `txn_id` không phải bảng dim — gọi là **degenerate dimension** (thuật ngữ Kimball, hay gặp khi xin việc DA)

---

## Stack nên nhớ cho vị trí BA / DA

| Công nghệ | Dùng để | Ra phỏng vấn nói gì |
|-----------|---------|---------------------|
| Python + pandas + numpy | Profiling, mock, ETL nhỏ | “Tôi từng dựng pipeline từ file POS” |
| PostgreSQL | Kho + SQL phân tích | JOIN fact–dim, cửa sổ thời gian |
| Star schema | Mô hình Kimball | Grain, surrogate key, slowly changing (chưa làm type 2) |
| CSV staging | Tầng raw | Tách extract khỏi warehouse |
| App (giai đoạn sau) | Dashboard quản lý rạp | Yêu cầu user = use case UML |

Chưa cần Spark/Airflow ở đồ án này. Khi mở rộng webview: cùng API đọc PostgreSQL.

---

## Bước tiếp theo (chưa làm trong lượt này)

1. `sql/02_seed_dims.sql` — nạp dim từ CSV distinct.
2. Script ETL Python: staging → fact (lookup key).
3. View KPI: occupancy, doanh thu theo khung giờ, tỷ lệ combo, tỷ lệ anomaly.

Làm xong bước đó thì mới train mô hình trên dữ liệu **đã sạch + còn nhãn**.

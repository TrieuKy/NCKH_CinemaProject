# Giai đoạn 2 — Hướng dẫn làm việc (sinh viên / BA–DA)

> [!NOTE] TÀI LIỆU LỊCH SỬ DỰ ÁN (HISTORICAL GUIDELINES)
> Tài liệu này được lập tại thời điểm khởi tạo **Giai đoạn 2 (Bước 1 & 2)** nhằm hướng dẫn sinh viên, BA và DA thiết lập giả định dữ liệu thô và dựng Star Schema DDL ban đầu.
>
> **Hiện trạng thực tế của dự án (Task 5 / Task 5.1):**
> Toàn bộ các nội dung tại mục *"Bước tiếp theo (chưa làm trong lượt này)"* ở cuối tài liệu này (`sql/02_seed_dims.sql`, ETL nạp Fact `sql/03_load_facts.sql`, View KPI phân tích `sql/03_views.sql`, Đối soát QC 17 tests `etl/reconcile.py`, Tóm lược Summary `sql/05_app_tables.sql`, và Nền tảng ứng dụng Streamlit `app/main.py`) **đều đã được lập trình, kiểm thử và nghiệm thu hoàn tất**.
>
> Toàn bộ nội dung hướng dẫn nghiệp vụ, giải thích lý thuyết Kimball, định vị vai trò BA/DA và kỹ năng phỏng vấn dưới đây được **bảo lưu nguyên vẹn** nhằm phục vụ viết báo cáo Nghiên cứu Khoa học và bảo vệ đồ án.

---

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

## Bước tiếp theo (Kế hoạch ban đầu & Hiện trạng đã hoàn thành)

> [!NOTE] CẬP NHẬT TRẠNG THÁI HIỆN TẠI (TASK 5):
> Toàn bộ 3 hạng mục dự kiến ban đầu dưới đây đã được triển khai hoàn chỉnh trong codebase:
> 1. `sql/02_seed_dims.sql` (kết hợp `etl/seed_dims.py`): Đã nạp 9 Dimension an toàn với `ON CONFLICT DO NOTHING`.
> 2. `sql/03_load_facts.sql` (kết hợp `etl/load_facts.py`): Đã nạp `fact_ticket` và `fact_fnb` với 8 dimension lookup (Set-based, Atomic transaction).
> 3. `sql/03_views.sql`: Đã xây dựng 4 views phân tích (`vw_daily_kpi`, `vw_showtime_occupancy`, `vw_fnb_attach`, `vw_anomaly_showtime`).
>
> Ngoài ra, hệ thống đã mở rộng hoàn thành:
> - Tầng đối soát chất lượng độc lập 17 bài test QC (`etl/reconcile.py`).
> - Tầng tóm lược hiệu năng cao `fact_showtime_summary` và `fact_daily_cinema_summary` (`etl/aggregate_summary.py`).
> - Pipeline phân khúc khách hàng RFM K-Means (`core/rfm.py`) và đánh giá Ground Truth (`scripts/eval_rfm.py`).
> - Pipeline dự báo chuỗi thời gian Prophet/XGBoost (`core/forecast.py`).
> - Nền tảng ứng dụng Streamlit UI-1 (`app/main.py`): xác thực bcrypt, phân quyền, filter bar dùng lại và thư viện linh kiện.

1. `sql/02_seed_dims.sql` — nạp dim từ CSV distinct.
2. Script ETL Python: staging → fact (lookup key).
3. View KPI: occupancy, doanh thu theo khung giờ, tỷ lệ combo, tỷ lệ anomaly.

Làm xong bước đó thì mới train mô hình trên dữ liệu **đã sạch + còn nhãn**.

# TỪ ĐIỂN DỮ LIỆU KHO DỮ LIỆU RẠP CHIẾU (DATA DICTIONARY)

**Dự án:** Hệ thống phân tích hành vi khách hàng và hỗ trợ quyết định vận hành rạp phim  
**Mã tài liệu:** `NCKH-CINEMA-DOC-DWH-01`  
**Phiên bản:** 2.0 (Chuẩn hóa Task 5)  
**Schema quản lý:** `staging`, `cinema_dw`  

---

## 1. Kiến trúc Tổng thể & Phân lớp Dữ liệu

Kho dữ liệu được thiết kế theo mô hình **Kimball Star Schema (Lược đồ Ngôi sao)** với hai bảng Fact trung tâm (`fact_ticket`, `fact_fnb`) và 9 bảng Dimension bao quanh, hỗ trợ bởi tầng Staging trung gian và tầng Tóm lược (Summary/Data Mart) phục vụ ứng dụng:

```
[CSV Files: data/*.csv] 
       ↓ (etl/load_staging.py)
[Staging Schema: staging.ticket, staging.fnb]
       ↓ (sql/02_seed_dims.sql, sql/03_load_facts.sql)
[Star Schema: cinema_dw.dim_*, cinema_dw.fact_*]
       ↓ (etl/aggregate_summary.py, sql/03_views.sql)
[Summary & Views: fact_showtime_summary, fact_daily_cinema_summary, vw_*]
       ↓ (core/rfm.py, core/forecast.py, app/)
[Analytics, ML & Streamlit Dashboard]
```

---

## 2. Bảng Tầng Staging (Schema: `staging`)

### 2.1. Bảng `staging.ticket`
- **Mục đích:** Lưu trữ dữ liệu thô bán vé được trích xuất từ file CSV (`data/staging_ticket.csv`) trước khi biến đổi và phân rã vào Star Schema. Ghi đè mỗi chu kỳ ETL (Truncate & Insert).
- **Grain:** Một dòng = Một giao dịch vé cho một suất chiếu (chứa từ 1 đến 4 vé biểu diễn bởi `qty`).
- **Primary / Business Key:** `txn_id` (Khóa nghiệp vụ duy nhất của giao dịch vé).
- **Xử lý bất thường:** Giữ nguyên các trường lỗi `is_anomaly = 1` và `anomaly_type` (`price_zero`, `revenue_mismatch`, `qty_spike`).

| Tên cột | Kiểu dữ liệu | NULL | Đơn vị / Định dạng | Mô tả nghiệp vụ |
|:---|:---|:---:|:---|:---|
| `txn_id` | TEXT | NO | `TXN00000001` | Mã giao dịch vé duy nhất |
| `show_date` | TEXT | NO | `YYYY-MM-DD` | Ngày chiếu phim |
| `show_time` | TEXT | NO | `HH:MI:SS` | Giờ bắt đầu suất chiếu |
| `cinema_id` | TEXT | NO | `CN01`, `CN02`, `CN03` | Mã định danh rạp chiếu |
| `cinema_name`| TEXT | NO | Chuỗi | Tên rạp (Landmark 81, Vincom Đồng Khởi, Pearl Plaza) |
| `district` | TEXT | NO | Chuỗi | Quận (Bình Thạnh, Quận 1) |
| `city` | TEXT | NO | Chuỗi | Thành phố (TP. Hồ Chí Minh) |
| `room_id` | TEXT | NO | `R101` … `R302` | Mã phòng chiếu |
| `room_name` | TEXT | NO | Chuỗi | Tên phòng chiếu |
| `room_type` | TEXT | NO | `2D`, `3D`, `IMAX` | Định dạng phòng chiếu |
| `capacity` | INTEGER | NO | Ghế | Sức chứa tối đa của phòng |
| `movie_id` | TEXT | NO | `MV01` … `MV24` | Mã phim |
| `movie_title`| TEXT | NO | Chuỗi | Tên phim |
| `genre` | TEXT | NO | Chuỗi | Thể loại phim chính |
| `duration_min`| INTEGER | NO | Phút | Thời lượng phim |
| `rating` | TEXT | NO | `P`, `T13`, `T16`, `T18` | Phân loại độ tuổi khán giả |
| `release_date`| TEXT | NO | `YYYY-MM-DD` | Ngày phát hành phim |
| `language` | TEXT | NO | Chuỗi | Ngôn ngữ phim |
| `country` | TEXT | NO | Chuỗi | Quốc gia sản xuất |
| `customer_id`| TEXT | NO | `GUEST` hoặc `CUS000001`… | Mã khách hàng (GUEST cho khách vãng lai) |
| `customer_type`| TEXT | NO | `MEMBER`, `WALK_IN` | Phân loại khách hàng |
| `gender` | TEXT | YES | Chuỗi | Giới tính (chỉ có ở MEMBER) |
| `age_group` | TEXT | YES | Chuỗi | Nhóm tuổi (chỉ có ở MEMBER) |
| `membership_tier`| TEXT | YES | `Standard`, `Silver`, `Gold` | Hạng hội viên |
| `register_date`| TEXT | YES | `YYYY-MM-DD` | Ngày gia nhập hội viên |
| `channel_code`| TEXT | NO | `POS`, `APP`, `WEB` | Kênh giao dịch |
| `ticket_format`| TEXT | NO | `2D`, `3D`, `IMAX` | Định dạng vé |
| `day_type` | TEXT | NO | `weekday`, `weekend`, `holiday` | Loại ngày tính giá |
| `time_slot` | TEXT | NO | `Sáng`, `Chiều`, `Tối`, `Đêm` | Khung giờ chiếu |
| `list_price` | NUMERIC(12,0)| NO | VNĐ / Vé | Giá niêm yết của 1 vé đơn lẻ |
| `discount_rate`| NUMERIC(5,4)| NO | Tỷ lệ ($0.0 \rightarrow 0.35$) | Tỷ lệ giảm giá áp dụng |
| `discount_amount`| NUMERIC(12,0)| NO | VNĐ | Tổng tiền giảm giá của cả giao dịch ($= \text{disc} \times \text{qty}$) |
| `paid_amount`| NUMERIC(12,0)| NO | VNĐ | Tổng tiền thực trả của cả giao dịch ($= \text{paid} \times \text{qty}$) |
| `qty` | SMALLINT | NO | Số vé | Số lượng vé của giao dịch ($1 \le \text{qty} \le 4$, ngoại lệ spike) |
| `is_holiday` | SMALLINT | NO | 0 hoặc 1 | Cờ ngày lễ |
| `holiday_name`| TEXT | YES | Chuỗi | Tên ngày lễ |
| `is_anomaly` | SMALLINT | NO | 0 hoặc 1 | Cờ đánh dấu dòng bất thường |
| `anomaly_type`| TEXT | YES | Chuỗi | Loại lỗi (`price_zero`, `revenue_mismatch`, `qty_spike`) |

---

### 2.2. Bảng `staging.fnb`
- **Mục đích:** Lưu trữ dữ liệu thô mua bắp nước từ file CSV (`data/staging_fnb.csv`).
- **Grain:** Một dòng = Một dòng sản phẩm F&B trong một giao dịch.
- **Primary / Business Key:** `fnb_line_id` (Khóa dòng sản phẩm F&B duy nhất).
- **Khóa liên kết:** `txn_id` liên kết về giao dịch vé tương ứng trong `staging.ticket`.

| Tên cột | Kiểu dữ liệu | NULL | Đơn vị / Định dạng | Mô tả nghiệp vụ |
|:---|:---|:---:|:---|:---|
| `txn_id` | TEXT | NO | `TXN00000001` | Mã giao dịch vé đã mua kèm |
| `fnb_line_id` | TEXT | NO | `FNB00000001` | Mã dòng sản phẩm F&B duy nhất |
| `sale_date` | TEXT | NO | `YYYY-MM-DD` | Ngày bán hàng |
| `sale_time` | TEXT | NO | `HH:MI:SS` | Giờ bán hàng |
| `cinema_id` | TEXT | NO | `CN01`, `CN02`, `CN03` | Mã rạp |
| `customer_id` | TEXT | NO | `GUEST` hoặc `CUS000001`… | Mã khách hàng đồng bộ từ vé |
| `customer_type`| TEXT | NO | `MEMBER`, `WALK_IN` | Loại khách hàng |
| `channel_code` | TEXT | NO | `POS`, `APP`, `WEB` | Kênh bán hàng |
| `product_id` | TEXT | NO | `P01` … `P08` | Mã sản phẩm |
| `product_name` | TEXT | NO | Chuỗi | Tên sản phẩm |
| `product_category`| TEXT | NO | `snack`, `drink`, `combo` | Nhóm sản phẩm |
| `is_combo` | SMALLINT | NO | 0 hoặc 1 | Cờ sản phẩm là gói combo |
| `qty` | SMALLINT | NO | Sản phẩm | Số lượng sản phẩm mua trong dòng |
| `unit_price` | NUMERIC(12,0)| NO | VNĐ / Sản phẩm | Đơn giá sản phẩm |
| `line_amount` | NUMERIC(12,0)| NO | VNĐ | Thành tiền ($= \text{qty} \times \text{unit\_price}$) |
| `is_anomaly` | SMALLINT | NO | 0 hoặc 1 | Cờ dòng bất thường |
| `anomaly_type` | TEXT | YES | Chuỗi | Loại lỗi F&B (`price_zero`, `revenue_mismatch`) |

---

## 3. Bảng Tầng Dimension (Schema: `cinema_dw`)

### 3.1. `dim_date` (Chiều Ngày)
- **Grain:** Một dòng = Một ngày dương lịch.
- **Primary Key:** `date_key` (Số nguyên YYYYMMDD, ví dụ: `20251001`).
- **Phạm vi dữ liệu:** Khởi tạo từ `2025-10-01` đến `2026-09-30` (12 tháng dữ liệu giao dịch), mở rộng tự động đến `2027-12-31` bằng `etl/extend_dim_date.py` để phục vụ dự báo tương lai.
- **Cột quan trọng:** `full_date`, `is_weekend`, `is_holiday`, `is_peak_day`, `holiday_name`.

### 3.2. `dim_time` (Chiều Giờ)
- **Grain:** Một dòng = Một phút trong ngày (1.440 dòng: `00:00` đến `23:59`).
- **Primary Key:** `time_key` (Số nguyên HHMM, ví dụ: `1930` cho `19:30`).
- **Cột quan trọng:** `time_slot` (`Sáng`, `Chiều`, `Tối`, `Đêm`), `is_prime_time` (`TRUE` nếu từ 17:00 đến 21:29).

### 3.3. `dim_cinema` (Chiều Rạp)
- **Grain:** Một dòng = Một rạp chiếu phim (3 rạp).
- **Surrogate Key:** `cinema_key` (IDENTITY PK).
- **Natural Key:** `cinema_id` (UNIQUE: `CN01` - Landmark 81, `CN02` - Vincom Đồng Khởi, `CN03` - Pearl Plaza).
- **Cột quan trọng:** `cinema_segment` (`premium_destination`, `urban_cbd`, `family_mall`).

### 3.4. `dim_room` (Chiều Phòng chiếu)
- **Grain:** Một dòng = Một phòng chiếu thuộc một rạp (8 phòng).
- **Surrogate Key:** `room_key` (IDENTITY PK).
- **Natural Key:** `room_id` (UNIQUE: `R101` … `R302`).
- **Foreign Key:** `cinema_key` REFERENCES `dim_cinema(cinema_key)`.
- **Cột quan trọng:** `room_type` (`2D`, `3D`, `IMAX`), `capacity` (Sức chứa tối đa, dùng làm mẫu số tính occupancy và kiểm soát `qty_spike`).

### 3.5. `dim_movie` (Chiều Phim)
- **Grain:** Một dòng = Một tựa phim (24 tựa phim).
- **Surrogate Key:** `movie_key` (IDENTITY PK).
- **Natural Key:** `movie_id` (UNIQUE: `MV01` … `MV24`).
- **Cột quan trọng:** `title`, `genre`, `duration_min`, `rating` (`P`, `T13`, `T16`, `T18`), `release_date`.

### 3.6. `dim_customer` (Chiều Khách hàng)
- **Grain:** Một dòng = Một khách hàng định danh, kèm duy nhất 1 dòng đại diện cho khách vãng lai.
- **Surrogate Key:** `customer_key` (IDENTITY PK).
- **Natural Key:** `customer_id` (UNIQUE: `GUEST` hoặc `CUS000001` … `CUS015000`).
- **Quy tắc GUEST:** Đúng 1 dòng có `customer_id = 'GUEST'`, `is_guest = TRUE`, `customer_type = 'WALK_IN'`, các trường `membership_tier`, `gender`, `age_group` mang giá trị NULL.
- **Phân khúc tiềm ẩn:** Không lưu cột `latent_segment` trong bảng này nhằm giữ tính khách quan cho đánh giá mô hình phân cụm K-Means.

### 3.7. `dim_product` (Chiều Sản phẩm F&B)
- **Grain:** Một dòng = Một sản phẩm ăn uống (8 sản phẩm: P01 … P08).
- **Surrogate Key:** `product_key` (IDENTITY PK).
- **Natural Key:** `product_id` (UNIQUE).
- **Cột quan trọng:** `category` (`snack`, `drink`, `combo`), `is_combo` (BOOLEAN), `list_price`.

### 3.8. `dim_pricing` (Chiều Bảng giá & Chiết khấu)
- **Grain:** Một dòng = Một chính sách giá vé theo tổ hợp: Định dạng vé + Loại ngày + Khung giờ + Loại khách + Mức giảm giá.
- **Surrogate Key:** `pricing_key` (IDENTITY PK).
- **Cột quan trọng:** `ticket_format`, `day_type`, `time_slot`, `customer_type`, `list_price`, `discount_rate`.

### 3.9. `dim_channel` (Chiều Kênh bán hàng)
- **Grain:** Một dòng = Một kênh giao dịch (3 kênh: `POS`, `APP`, `WEB`).
- **Surrogate Key:** `channel_key` (IDENTITY PK).
- **Natural Key:** `channel_code` (UNIQUE).
- **Cột quan trọng:** `channel_group` (`Offline`, `Online`).

---

## 4. Bảng Tầng Fact (Schema: `cinema_dw`)

### 4.1. Bảng `fact_ticket`
- **Mục đích:** Lưu trữ chi tiết toàn bộ các giao dịch bán vé xem phim.
- **Grain:** **Một dòng = Một giao dịch bán vé cho một suất chiếu. Giao dịch có thể chứa từ 1 đến 4 vé (hoặc vượt công suất do lỗi spike), được biểu diễn bởi cột `qty`.**
- **Surrogate Key:** `ticket_sk` (BIGINT GENERATED ALWAYS AS IDENTITY PK).
- **Degenerate Dimension:** `txn_id` (UNIQUE, dùng làm khóa nối sang `fact_fnb` và hệ thống bán hàng).
- **Quan hệ khóa ngoại:**
  - `date_key` → `dim_date(date_key)`
  - `time_key` → `dim_time(time_key)`
  - `movie_key` → `dim_movie(movie_key)`
  - `cinema_key` → `dim_cinema(cinema_key)`
  - `room_key` → `dim_room(room_key)`
  - `customer_key` → `dim_customer(customer_key)`
  - `pricing_key` → `dim_pricing(pricing_key)`
  - `channel_key` → `dim_channel(channel_key)`

| Tên cột | Kiểu dữ liệu | NULL | Đơn vị | Ý nghĩa & Quy tắc |
|:---|:---|:---:|:---|:---|
| `ticket_sk` | BIGINT | NO | ID | Khóa thay thế (Surrogate Key) tự sinh |
| `date_key` | INTEGER | NO | YYYYMMDD | Khóa ngày chiếu |
| `time_key` | INTEGER | NO | HHMM | Khóa giờ chiếu |
| `movie_key` | INTEGER | NO | FK | Khóa phim |
| `cinema_key` | INTEGER | NO | FK | Khóa rạp |
| `room_key` | INTEGER | NO | FK | Khóa phòng chiếu |
| `customer_key` | INTEGER | NO | FK | Khóa khách hàng (map về dòng GUEST nếu WALK_IN) |
| `pricing_key` | INTEGER | NO | FK | Khóa chính sách giá áp dụng |
| `channel_key` | INTEGER | NO | FK | Khóa kênh bán |
| `txn_id` | VARCHAR(20) | NO | TXN* | Mã giao dịch vé duy nhất (UNIQUE) |
| `qty` | SMALLINT | NO | Vé | Số lượng vé của giao dịch ($1 \le \text{qty} \le 4$, ngoại lệ spike) |
| `list_price` | NUMERIC(12,0)| NO | VNĐ / Vé | Đơn giá niêm yết của 1 vé |
| `discount_amount`| NUMERIC(12,0)| NO | VNĐ | Tổng tiền giảm giá của giao dịch ($= \text{disc} \times \text{qty}$) |
| `net_amount` | NUMERIC(12,0)| NO | VNĐ | Tổng tiền thực thu của giao dịch ($= \text{paid} \times \text{qty}$) |
| `is_anomaly` | SMALLINT | NO | 0 hoặc 1 | Cờ bất thường (1: bất thường, 0: sạch) |
| `anomaly_type` | VARCHAR(40) | YES | Chuỗi | Chi tiết loại lỗi (`price_zero`, `revenue_mismatch`, `qty_spike`) |
| `loaded_at` | TIMESTAMP | NO | Giờ hệ thống | Thời điểm nạp dữ liệu |

---

### 4.2. Bảng `fact_fnb`
- **Mục đích:** Lưu trữ chi tiết các dòng sản phẩm bắp nước được bán kèm trong giao dịch.
- **Grain:** **Một dòng = Một dòng sản phẩm F&B thuộc một giao dịch.**
- **Surrogate Key:** `fnb_sk` (BIGINT GENERATED ALWAYS AS IDENTITY PK).
- **Business Key:** `fnb_line_id` (VARCHAR(20) UNIQUE, định danh dòng F&B).
- **Degenerate Dimension:** `txn_id` (Mã giao dịch nối với `fact_ticket.txn_id`).
- **Quan hệ khóa ngoại:**
  - `date_key` → `dim_date(date_key)`
  - `time_key` → `dim_time(time_key)`
  - `cinema_key` → `dim_cinema(cinema_key)`
  - `customer_key` → `dim_customer(customer_key)`
  - `product_key` → `dim_product(product_key)`
  - `channel_key` → `dim_channel(channel_key)`

| Tên cột | Kiểu dữ liệu | NULL | Đơn vị | Ý nghĩa & Quy tắc |
|:---|:---|:---:|:---|:---|
| `fnb_sk` | BIGINT | NO | ID | Khóa thay thế tự sinh |
| `fnb_line_id` | VARCHAR(20) | NO | FNB* | Mã định danh dòng sản phẩm F&B (UNIQUE) |
| `date_key` | INTEGER | NO | YYYYMMDD | Khóa ngày bán |
| `time_key` | INTEGER | NO | HHMM | Khóa giờ bán |
| `cinema_key` | INTEGER | NO | FK | Khóa rạp |
| `customer_key` | INTEGER | NO | FK | Khóa khách hàng đồng bộ từ giao dịch vé |
| `product_key` | INTEGER | NO | FK | Khóa sản phẩm F&B |
| `channel_key` | INTEGER | NO | FK | Khóa kênh bán hàng |
| `txn_id` | VARCHAR(20) | NO | TXN* | Mã giao dịch vé liên kết (1 giao dịch vé có 0..N dòng F&B) |
| `qty` | SMALLINT | NO | Sản phẩm | Số lượng sản phẩm của dòng ($1 \le \text{qty} \le 2$) |
| `unit_price` | NUMERIC(12,0)| NO | VNĐ / SP | Đơn giá sản phẩm |
| `line_amount` | NUMERIC(12,0)| NO | VNĐ | Tổng tiền dòng sản phẩm ($= \text{qty} \times \text{unit\_price}$) |
| `is_anomaly` | SMALLINT | NO | 0 hoặc 1 | Cờ bất thường (1: bất thường, 0: sạch) |
| `anomaly_type` | VARCHAR(40) | YES | Chuỗi | Chi tiết loại lỗi F&B (`price_zero`, `revenue_mismatch`) |
| `loaded_at` | TIMESTAMP | NO | Giờ hệ thống | Thời điểm nạp dữ liệu |

---

## 5. Bảng Tầng Tóm Lược & Vận Hành Ứng Dụng (Summary Tables)

### 5.1. Bảng `fact_showtime_summary`
- **Mục đích:** Bảng tổng hợp số liệu trước theo từng suất chiếu, phục vụ tối ưu hóa truy vấn cho Streamlit Dashboard, phân tích lấp đầy và cung cấp dữ liệu huấn luyện cho mô hình Dự báo (`core/forecast.py`).
- **Grain:** Một dòng = Một suất chiếu (xác định bởi `date_key`, `time_key`, `room_key`).
- **Primary Key:** `summary_id` (UUID PK).
- **Ràng buộc duy nhất:** `UNIQUE (date_key, time_key, room_key)`.
- **Nguồn tổng hợp:** `fact_ticket`.

| Tên cột | Kiểu dữ liệu | NULL | Đơn vị | Công thức / Ý nghĩa |
|:---|:---|:---:|:---|:---|
| `summary_id` | UUID | NO | UUID | Khóa chính tự sinh |
| `date_key` | INTEGER | NO | FK | Ngày chiếu |
| `time_key` | INTEGER | NO | FK | Giờ chiếu |
| `cinema_key` | INTEGER | NO | FK | Rạp |
| `room_key` | INTEGER | NO | FK | Phòng chiếu |
| `movie_key` | INTEGER | NO | FK | Phim chiếu |
| `tickets_sold` | INTEGER | NO | Vé | Tổng vé bán ra (Raw): `SUM(qty)` |
| `ticket_revenue`| NUMERIC(18,0)| NO | VNĐ | Tổng doanh thu vé (Raw): `SUM(net_amount)` |
| `tickets_sold_clean`| INTEGER | YES | Vé | Tổng vé bán ra (Clean): `SUM(CASE WHEN is_anomaly=0 THEN qty ELSE 0 END)`. Nguồn chính cho Forecast. |
| `ticket_revenue_clean`| NUMERIC(18,0)| YES | VNĐ | Doanh thu vé sạch: `SUM(CASE WHEN is_anomaly=0 THEN net_amount ELSE 0 END)` |
| `source` | VARCHAR(20) | NO | Enum | `MOCK` (tổng hợp tự động) hoặc `MANUAL` (nhân viên nhập tay) |
| `entered_by` | UUID | YES | FK | Mã nhân viên nhập nếu là MANUAL |
| `is_locked` | BOOLEAN | NO | Cờ | Đã khóa chốt sổ hay chưa |

---

### 5.2. Bảng `fact_daily_cinema_summary`
- **Mục đích:** Tổng hợp chỉ số giao dịch vé và bắp nước theo từng ngày và từng cụm rạp, phục vụ báo cáo doanh thu ngày và tính toán Attach Rate cấp ngày.
- **Grain:** Một dòng = Một ngày × Một rạp.
- **Primary Key:** `summary_id` (UUID PK).
- **Ràng buộc duy nhất:** `UNIQUE (date_key, cinema_key)`.
- **Ràng buộc logic:** `CHECK (fnb_txns <= ticket_txns)` (Số giao dịch mua F&B không được vượt quá số giao dịch vé).

| Tên cột | Kiểu dữ liệu | NULL | Đơn vị | Công thức / Ý nghĩa |
|:---|:---|:---:|:---|:---|
| `summary_id` | UUID | NO | UUID | Khóa chính |
| `date_key` | INTEGER | NO | FK | Ngày giao dịch |
| `cinema_key` | INTEGER | NO | FK | Rạp |
| `ticket_txns` | INTEGER | NO | Giao dịch | Số giao dịch vé: `COUNT(DISTINCT txn_id)` từ `fact_ticket` |
| `fnb_txns` | INTEGER | NO | Giao dịch | Số giao dịch F&B: `COUNT(DISTINCT txn_id)` từ `fact_fnb` |
| `fnb_revenue` | NUMERIC(18,0)| NO | VNĐ | Tổng doanh thu F&B: `SUM(line_amount)` |
| `source` | VARCHAR(20) | NO | Enum | `MOCK` hoặc `MANUAL` |
| `is_locked` | BOOLEAN | NO | Cờ | Đã chốt sổ hay chưa |

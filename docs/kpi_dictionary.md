# TỪ ĐIỂN CHỈ SỐ DOANH THU & HIỆU QUẢ VẬN HÀNH (KPI DICTIONARY)

**Dự án:** Hệ thống phân tích hành vi khách hàng và hỗ trợ quyết định vận hành rạp phim  
**Mã tài liệu:** `NCKH-CINEMA-DOC-KPI-01`  
**Phiên bản:** 2.0 (Chuẩn hóa Task 5)  
**Trạng thái:** Chính thức nghiệm thu  

---

## 1. Giới thiệu & Nguyên tắc chung

Tài liệu này định nghĩa chuẩn mực toàn bộ các chỉ số đo lường hiệu quả hoạt động (Key Performance Indicators - KPIs) được cài đặt và vận hành trong hệ thống Data Warehouse (`cinema_dw`), các Views (`03_views.sql`), tầng tóm lược (`fact_showtime_summary`, `fact_daily_cinema_summary`), mô hình RFM (`core/rfm.py`) và mô hình dự báo nhu cầu (`core/forecast.py`).

### 1.1. Nguyên tắc Raw vs Clean
Nhằm phục vụ cả hai mục đích: (1) Phân tích vận hành chính xác và (2) Phát hiện / cảnh báo gian lận bất thường, hệ thống tách biệt hai luồng số liệu:
- **Raw (Dữ liệu thô):** Bao gồm toàn bộ các dòng giao dịch phát sinh từ nguồn (kể cả các bản ghi có cờ `is_anomaly = 1`).
- **Clean (Dữ liệu sạch):** Chỉ tính trên các bản ghi hợp lệ (`is_anomaly = 0`). Mọi KPI phục vụ báo cáo quản trị, phân khúc khách hàng (RFM) và huấn luyện dự báo (Forecasting) **bắt buộc** dùng dữ liệu Clean.

### 1.2. Nguyên tắc Grain & Tránh Double Counting
- `fact_ticket`: Một dòng = Một giao dịch vé (`txn_id` là duy nhất). Số vé thực tế là `qty` (1–4 vé).
- `fact_fnb`: Một dòng = Một dòng sản phẩm F&B trong giao dịch (`fnb_line_id` duy nhất).
- **Cấm JOIN trực tiếp 1-N giữa `fact_ticket` và `fact_fnb`** trong câu lệnh tính tổng doanh thu/số lượng để tránh nhân bản số dòng (Cartesian fan-out). Phải aggregate riêng từng bảng theo grain mong muốn trước khi ghép (`JOIN`).

---

## 2. Danh mục Chỉ số Chi tiết

### 2.1. Giao dịch vé & Số vé bán (Ticket Transactions & Tickets Sold)

#### KPI 01: Số giao dịch vé (Ticket Transactions)
- **Tên kỹ thuật:** `ticket_txn_count` / `ticket_txns` / `total_txns`
- **Mục đích nghiệp vụ:** Đo lường lưu lượng khách hàng thực hiện thanh toán vé tại quầy (POS) hoặc qua ứng dụng (APP/WEB).
- **Grain:** Ngày × Rạp (`fact_daily_cinema_summary`), Ngày × Rạp × Khung giờ (`vw_fnb_attach`), Suất chiếu (`vw_anomaly_showtime`).
- **Công thức SQL:**
  ```sql
  COUNT(DISTINCT txn_id)
  -- Hoặc COUNT(*) trên fact_ticket do txn_id đã có UNIQUE constraint
  ```
- **Đơn vị đo:** Giao dịch (Transactions).
- **Nguồn dữ liệu:** `cinema_dw.fact_ticket (txn_id)`.
- **Quy tắc Raw/Clean:**
  - Raw: `COUNT(DISTINCT txn_id)`
  - Clean: `COUNT(DISTINCT CASE WHEN is_anomaly = 0 THEN txn_id END)`
- **Xử lý mẫu số = 0:** Không áp dụng (chỉ số đếm).
- **Hạn chế & Lưu ý:** Không được nhầm lẫn số giao dịch với tổng số vé bán ra (`Tickets Sold`). Một giao dịch gia đình/nhóm có thể mua từ 2 đến 4 vé.

---

#### KPI 02: Tổng số vé bán ra (Tickets Sold)
- **Tên kỹ thuật:** `ticket_qty_raw`, `ticket_qty_clean`, `tickets_sold`, `tickets_sold_clean`
- **Mục đích nghiệp vụ:** Đo lường tổng số lượng người xem / ghế xem phim thực tế được bán ra.
- **Grain:** Ngày × Rạp (`vw_daily_kpi`), Suất chiếu (`fact_showtime_summary`, `vw_showtime_occupancy`).
- **Công thức SQL:**
  ```sql
  -- Raw:
  SUM(qty) AS ticket_qty_raw
  -- Clean:
  SUM(CASE WHEN is_anomaly = 0 THEN qty ELSE 0 END) AS ticket_qty_clean
  ```
- **Đơn vị đo:** Vé (Tickets / Seats).
- **Nguồn dữ liệu:** `cinema_dw.fact_ticket (qty)`.
- **Quy tắc Raw/Clean:** Bắt buộc phân tách cờ `is_anomaly = 0` để loại bỏ các trường hợp `qty_spike` (ghế ảo vượt công suất phòng).
- **Hạn chế & Lưu ý:** `COUNT(*)` đếm số dòng (tức số giao dịch), tuyệt đối không dùng `COUNT(*)` để tính số vé bán ra.

---

### 2.2. Doanh thu vé & Giá vé trung bình (Ticket Revenue & ATP)

#### KPI 03: Doanh thu vé (Ticket Revenue)
- **Tên kỹ thuật:** `ticket_revenue_raw`, `ticket_revenue_clean`, `ticket_revenue`
- **Mục đích nghiệp vụ:** Đo lường tổng số tiền thực thu sau khi đã trừ toàn bộ chiết khấu/khuyến mãi từ hoạt động bán vé xem phim.
- **Grain:** Ngày × Rạp (`vw_daily_kpi`), Suất chiếu (`fact_showtime_summary`, `vw_anomaly_showtime`).
- **Công thức SQL:**
  ```sql
  -- Raw:
  SUM(net_amount) AS ticket_revenue_raw
  -- Clean:
  SUM(CASE WHEN is_anomaly = 0 THEN net_amount ELSE 0 END) AS ticket_revenue_clean
  ```
- **Đơn vị đo:** VNĐ (Việt Nam Đồng).
- **Nguồn dữ liệu:** `cinema_dw.fact_ticket (net_amount)`.
- **Quan hệ công thức dòng:**
  $$\text{net\_amount} = (\text{list\_price} - \text{per\_ticket\_discount}) \times \text{qty} = \text{per\_ticket\_paid} \times \text{qty}$$
- **Hạn chế & Lưu ý:** `net_amount` phản ánh số tiền khách trả, không phải `list_price`. Với dòng bất thường `price_zero`, `net_amount = 0`.

---

#### KPI 04: Giá vé trung bình (Average Ticket Price - ATP)
- **Tên kỹ thuật:** `atp_clean`
- **Mục đích nghiệp vụ:** Đánh giá mức giá thực thu bình quân trên mỗi vé bán ra sau khuyến mãi, hỗ trợ định giá và phân tích độ co giãn nhu cầu.
- **Grain:** Ngày × Rạp (`vw_daily_kpi`).
- **Công thức SQL:**
  ```sql
  CASE 
      WHEN t.ticket_qty_clean > 0 THEN t.ticket_revenue_clean / t.ticket_qty_clean 
      ELSE 0 
  END AS atp_clean
  ```
- **Đơn vị đo:** VNĐ / Vé (VNĐ/ticket).
- **Nguồn dữ liệu:** `cinema_dw.fact_ticket`.
- **Quy tắc Raw/Clean:** Chỉ tính trên tập dữ liệu sạch (`ticket_revenue_clean` và `ticket_qty_clean`).
- **Xử lý mẫu số = 0:** Bắt buộc bọc điều kiện `CASE WHEN ticket_qty_clean > 0 ... ELSE 0 END`.
- **Hạn chế & Lưu ý:** Mẫu số là **tổng số vé sạch (`ticket_qty_clean`)**, KHÔNG PHẢI tổng số giao dịch. Chia cho số giao dịch sẽ cho ra giá trị giỏ vé bình quân (Ticket Basket Size), không phải ATP.

---

### 2.3. Tỷ lệ lấp đầy phòng chiếu (Showtime Occupancy)

#### KPI 05: Tỷ lệ lấp đầy suất chiếu sạch (Occupancy Clean)
- **Tên kỹ thuật:** `occupancy_clean`
- **Mục đích nghiệp vụ:** Đo lường mức độ khai thác công suất ghế của từng suất chiếu thực tế, làm căn cứ xếp lịch chiếu và phân nhóm suất chiếu (Thấp điểm, Bình thường, Tốt).
- **Grain:** Suất chiếu = `date_key` × `time_key` × `cinema_key` × `room_key` × `movie_key`.
- **Công thức SQL:**
  ```sql
  (s.qty_clean::NUMERIC / dr.capacity) AS occupancy_clean
  ```
  *(Đề xuất cải tiến an toàn: `s.qty_clean::NUMERIC / NULLIF(dr.capacity, 0)`)*.
- **Đơn vị đo:** Tỷ lệ phần trăm / Thập phân ($0.0 \rightarrow 1.0$).
- **Nguồn dữ liệu:** `fact_ticket (qty)` tổng hợp, kết hợp `dim_room (capacity)`.
- **Quy tắc Raw/Clean:**
  - `occupancy_clean`: Sử dụng `qty_clean` (`is_anomaly = 0`). Giá trị luôn trong khoảng $[0.0, 1.0]$.
  - `occupancy_raw`: Sử dụng `qty_raw`. Có thể vượt $1.0$ ($>100\%$) do lỗi `qty_spike`.
- **Phân loại nhu cầu (`demand_group`):**
  - Thấp điểm: $\text{occupancy\_clean} < 0.30$ ($<30\%$)
  - Bình thường: $0.30 \le \text{occupancy\_clean} < 0.70$
  - Tốt / Giờ vàng: $\text{occupancy\_clean} \ge 0.70$ ($\ge 70\%$)
- **Hạn chế & Lưu ý:** Grain tính toán bắt buộc phải ở cấp **Suất chiếu (Showtime)**. Tuyệt đối không cộng gộp trực tiếp `occupancy` của nhiều suất bằng hàm `AVG()` mà không gia quyền theo sức chứa phòng (`dr.capacity`).

---

### 2.4. Doanh thu & Chỉ số gắn kèm Bắp nước (F&B Metrics)

#### KPI 06: Doanh thu F&B (F&B Revenue)
- **Tên kỹ thuật:** `fnb_revenue_raw`, `fnb_revenue_clean`, `fnb_revenue`
- **Mục đích nghiệp vụ:** Đo lường tổng doanh thu từ dịch vụ ăn uống kèm theo (bắp, nước, snack, combo).
- **Grain:** Ngày × Rạp (`vw_daily_kpi`, `fact_daily_cinema_summary`), Ngày × Rạp × Khung giờ (`vw_fnb_attach`).
- **Công thức SQL:**
  ```sql
  -- Raw:
  SUM(line_amount) AS fnb_revenue_raw
  -- Clean:
  SUM(CASE WHEN is_anomaly = 0 THEN line_amount ELSE 0 END) AS fnb_revenue_clean
  ```
- **Đơn vị đo:** VNĐ.
- **Nguồn dữ liệu:** `cinema_dw.fact_fnb (line_amount)`.
- **Quan hệ công thức dòng:** $\text{line\_amount} = \text{qty} \times \text{unit\_price}$.

---

#### KPI 07: Tỷ lệ gắn kèm F&B (F&B Attach Rate)
- **Tên kỹ thuật:** `attach_rate`
- **Mục đích nghiệp vụ:** Đánh giá hiệu quả bán chéo (cross-selling) F&B trên các khách hàng mua vé xem phim.
- **Grain:** Ngày × Rạp × Khung giờ (`vw_fnb_attach`).
- **Công thức SQL:**
  ```sql
  CASE 
      WHEN t.ticket_txn_count > 0 THEN COALESCE(f.fnb_txn_count, 0)::NUMERIC / t.ticket_txn_count 
      ELSE 0 
  END AS attach_rate
  ```
  Trong đó:
  - `ticket_txn_count`: `COUNT(DISTINCT txn_id)` từ `fact_ticket`.
  - `fnb_txn_count`: `COUNT(DISTINCT txn_id)` từ `fact_fnb`.
- **Đơn vị đo:** Tỷ lệ phần trăm ($0.0 \rightarrow 1.0$).
- **Xử lý mẫu số = 0:** Kiểm tra `t.ticket_txn_count > 0`.
- **Hạn chế & Lưu ý:** Tỷ lệ này đo lường trên cấp **Giao dịch (Transaction)**. Vì một giao dịch mua F&B có cùng `txn_id` với giao dịch vé, chỉ số thể hiện tỷ lệ % giao dịch vé có mua kèm F&B.

---

#### KPI 08: Tỷ lệ gắn kèm Combo (Combo Attach Rate)
- **Tên kỹ thuật:** `combo_attach_rate`
- **Mục đích nghiệp vụ:** Đo lường tỷ lệ các giao dịch mua vé có lựa chọn sản phẩm gói combo (bắp + nước), là nhóm hàng mang lại biên lợi nhuận cao.
- **Grain:** Ngày × Rạp × Khung giờ (`vw_fnb_attach`).
- **Công thức SQL:**
  ```sql
  CASE 
      WHEN t.ticket_txn_count > 0 THEN COALESCE(f.combo_txn_count, 0)::NUMERIC / t.ticket_txn_count 
      ELSE 0 
  END AS combo_attach_rate
  ```
  Trong đó: `combo_txn_count = COUNT(DISTINCT CASE WHEN p.is_combo THEN f.txn_id END)` với `p` là `dim_product`.
- **Đơn vị đo:** Tỷ lệ phần trăm ($0.0 \rightarrow 1.0$).
- **Xử lý mẫu số = 0:** Kiểm tra `t.ticket_txn_count > 0`.

---

#### KPI 09: Mức chi tiêu F&B trên mỗi giao dịch vé (spp_clean — F&B Spend Per Ticket Transaction)
- **Tên kỹ thuật:** `spp_clean` (trong view `vw_fnb_attach`)
- **Mục đích nghiệp vụ:** Đo lường mức doanh thu F&B sạch bình quân thu được trên mỗi giao dịch mua vé xem phim.
- **Grain:** Ngày × Rạp × Khung giờ (`vw_fnb_attach`).
- **Công thức SQL triển khai thực tế:**
  ```sql
  CASE 
      WHEN t.ticket_txn_count > 0 THEN COALESCE(f.fnb_revenue_clean, 0)::NUMERIC / t.ticket_txn_count 
      ELSE 0 
  END AS spp_clean
  ```
- **Đơn vị đo:** VNĐ / Giao dịch vé (VNĐ/ticket transaction).
- **Nguồn dữ liệu:** `fact_fnb (line_amount)`, `fact_ticket (txn_id)`.
- **> [!WARNING] CẢNH BÁO NGUY CƠ HIỂU NHẦM NGHIỆP VỤ (SPP vs SPT):**
  - Trong thuật ngữ điện ảnh chuẩn quốc tế, **SPP (Spend Per Patron)** có mẫu số là **tổng số khách / số vé bán ra (`SUM(qty)`)**.
  - Tuy nhiên, trong mã nguồn `sql/03_views.sql` (L130, chú thích L141), mẫu số được sử dụng là **`ticket_txn_count` (tổng số giao dịch vé)** với lý do: *"Khách vãng lai GUEST chiếm 70% không định danh cá nhân, hệ thống chọn số giao dịch vé làm mẫu số chuẩn"*.
  - Do đó, về bản chất nghiệp vụ, chỉ số `spp_clean` hiện hành là **Spend Per Ticket Transaction (SPT)** chứ **KHÔNG PHẢI Spend Per Patron (SPP)**. Với quy mô nhóm khách 1–4 vé/giao dịch, giá trị `spp_clean` sẽ cao hơn chi tiêu trên từng đầu người thực tế.
  - Khi viết báo cáo NCKH hoặc tài liệu quản trị, bắt buộc ghi chú rõ định nghĩa này.

---

### 2.5. Chỉ số Phát hiện Bất thường & Kiểm soát Chất lượng (Anomaly Metrics)

#### KPI 10: Tỷ lệ giao dịch bất thường theo suất (Anomaly Transaction Rate)
- **Tên kỹ thuật:** `anom_txn_rate_pct`
- **Mục đích nghiệp vụ:** Xác định mức độ nhiễm bẩn dữ liệu hoặc gian lận trên từng suất chiếu để kích hoạt quy trình kiểm soát (QC Alert).
- **Grain:** Suất chiếu (`vw_anomaly_showtime`).
- **Công thức SQL:**
  ```sql
  ROUND((s.anom_txns::NUMERIC / NULLIF(s.total_txns, 0)) * 100, 2) AS anom_txn_rate_pct
  ```
- **Đơn vị đo:** Tỷ lệ phần trăm (%).
- **Xử lý mẫu số = 0:** Sử dụng `NULLIF(s.total_txns, 0)`.
- **Cờ cảnh báo:** `is_anomaly_showtime = (has_anomaly = 1)`.

---

## 3. Tổng hợp Bảng Đối chiếu Chỉ số

| Mã KPI | Tên chỉ số | Tên cột SQL | Grain | Tử số | Mẫu số | Bảo vệ mẫu số = 0 | Raw vs Clean |
|:---|:---|:---|:---|:---|:---|:---:|:---:|
| **KPI-01** | Ticket Transactions | `ticket_txn_count` | Ngày × Rạp × Giờ | `COUNT(DISTINCT txn_id)` | N/A | Không | Tách cờ |
| **KPI-02** | Tickets Sold | `ticket_qty_clean` | Ngày × Rạp / Suất | `SUM(qty)` | N/A | Không | `is_anomaly = 0` |
| **KPI-03** | Ticket Revenue | `ticket_revenue_clean` | Ngày × Rạp / Suất | `SUM(net_amount)` | N/A | Không | `is_anomaly = 0` |
| **KPI-04** | Average Ticket Price | `atp_clean` | Ngày × Rạp | `ticket_revenue_clean` | `ticket_qty_clean` | `CASE > 0` | Dữ liệu sạch |
| **KPI-05** | Showtime Occupancy | `occupancy_clean` | Suất chiếu | `SUM(qty_clean)` | `dr.capacity` | Cần `NULLIF` | Cả Raw & Clean |
| **KPI-06** | F&B Revenue | `fnb_revenue_clean` | Ngày × Rạp × Giờ | `SUM(line_amount)` | N/A | Không | `is_anomaly = 0` |
| **KPI-07** | F&B Attach Rate | `attach_rate` | Ngày × Rạp × Giờ | `fnb_txn_count` | `ticket_txn_count` | `CASE > 0` | Dữ liệu sạch |
| **KPI-08** | Combo Attach Rate | `combo_attach_rate` | Ngày × Rạp × Giờ | `combo_txn_count` | `ticket_txn_count` | `CASE > 0` | Dữ liệu sạch |
| **KPI-09** | F&B Spend / Txn | `spp_clean` | Ngày × Rạp × Giờ | `fnb_revenue_clean` | `ticket_txn_count` | `CASE > 0` | Dữ liệu sạch |
| **KPI-10** | Anomaly Rate | `anom_txn_rate_pct` | Suất chiếu | `anom_txns` | `total_txns` | `NULLIF` | Phân tích lỗi |

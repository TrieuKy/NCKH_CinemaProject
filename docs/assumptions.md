# Giả định sinh dữ liệu (Mock Data) — Giai đoạn 2 (Chuẩn hóa Task 5)

Tài liệu này là **hợp đồng dữ liệu (Data Contract)** giữa BA/DA, Data Architect và Kỹ thuật. Mọi quy luật dưới đây được cài đặt và vận hành trực tiếp trong mã nguồn `scripts/generate_mock_data.py`, `sql/01_schema.sql`, `sql/02_seed_dims.sql`, `sql/03_load_facts.sql`. Khi báo cáo NCKH, dẫn file này làm nguồn sự thật chính thức.

> [!NOTE] CẬP NHẬT PHIÊN BẢN (TASK 5):
> - **Khoảng thời gian giao dịch thực tế:** **2025-10-01 → 2026-09-30 (12 tháng tròn)**, theo đúng cấu hình `START` và `END` trong `scripts/generate_mock_data.py`. (Tài liệu sơ khởi từng ghi nhận kỳ vọng thiết kế 18 tháng từ 2025-04-01, nay được chuẩn hóa theo mã nguồn thực thi).
> - **Chiều ngày mở rộng (`dim_date`):** Bảng `dim_date` được khởi tạo ban đầu cho 12 tháng (`2025-10-01 → 2026-09-30`), sau đó được mở rộng tự động đến `2027-12-31` bằng script `etl/extend_dim_date.py` nhằm phục vụ mô hình Dự báo nhu cầu tương lai.
> - **Khối lượng dữ liệu quan sát thực tế (với SEED=42, VOLUME_SCALE=1.0):**
>   - Giao dịch vé: **458.804 dòng** (`staging.ticket` và `fact_ticket`).
>   - Tổng số vé thực tế (`SUM(qty)`): **852.878 vé**.
>   - Dòng F&B thực tế: **355.417 dòng** (`staging.fnb` và `fact_fnb`).
>   - *(Số liệu thiết kế ước lượng ban đầu ở khung 18 tháng là ~1.236.204 vé, ~535.211 dòng F&B)*.
> - **Hạt nhân (Grain) vé chính thức:** **Một dòng `fact_ticket` tương ứng một giao dịch bán vé cho một suất chiếu. Giao dịch có thể chứa nhiều vé, được biểu diễn bởi `qty` ($1 \le \text{qty} \le 4$).**
> - **Hạt nhân (Grain) F&B:** **Một dòng `fact_fnb` tương ứng một dòng sản phẩm F&B thuộc một giao dịch.**
> - **Khóa nối vé ↔ bắp nước:** **`txn_id`** (Degenerate Dimension trên cả hai bảng fact).

Seed ngẫu nhiên: **`42`** (chạy lại script ra cùng kết quả).

---

## 1. Phạm vi vận hành (3 rạp CGV)

| cinema_id | Tên rạp | Quận | Đặc điểm phục vụ BA |
|-----------|---------|------|---------------------|
| CN01 | CGV Landmark 81 | Bình Thạnh | Rạp đích đến, IMAX, suất tối / cuối tuần rất đông |
| CN02 | CGV Vincom Đồng Khởi | Quận 1 | Trung tâm, khách vãng lai + dân văn phòng, POS chiếm tỷ lệ cao |
| CN03 | CGV Pearl Plaza | Bình Thạnh | Mall gia đình, cuối tuần / lễ cao hơn ngày thường |

Mỗi rạp có 2–3 phòng. **Số vé bán của một suất không vượt `capacity`**, trừ nhóm giao dịch được gán nhãn bất thường `qty_spike`.

| room_id | cinema_id | Tên phòng | room_type | capacity |
|---------|-----------|-----------|-----------|----------|
| R101 | CN01 | Phòng 1 | 2D | 120 |
| R102 | CN01 | Phòng 2 | 3D | 100 |
| R103 | CN01 | Phòng IMAX | IMAX | 180 |
| R201 | CN02 | Phòng 1 | 2D | 140 |
| R202 | CN02 | Phòng 2 | 3D | 110 |
| R203 | CN02 | Phòng 3 | 2D | 90 |
| R301 | CN03 | Phòng 1 | 2D | 130 |
| R302 | CN03 | Phòng 2 | 3D | 95 |

Hệ số lấp đầy theo rạp: Landmark 81 ×1.08, Vincom Đồng Khởi ×1.02, Pearl Plaza ×0.95.

---

## 2. Kênh bán & khách hàng

### 2.1. Kênh (`dim_channel`)

| channel_code | Tên | Nhóm |
|--------------|-----|------|
| POS | Quầy / máy POS tại rạp | Offline |
| APP | Ứng dụng điện thoại | Online |
| WEB | Website đặt vé | Online |

- Khách **WALK_IN**: ~80% POS, 15% APP, 5% WEB.
- Khách **MEMBER**: ~25% POS, 50% APP, 25% WEB.

### 2.2. Khách hàng (`dim_customer`)

- **Khách vãng lai chiếm đa số: 70% giao dịch vé.**
- Mọi khách vãng lai map về **một mã chuẩn**: `customer_id = 'GUEST'`, `customer_type = 'WALK_IN'`.
  - Lý do DA: không bịa RFM cho người không định danh; KPI “tỷ lệ thành viên” tính đúng.
- Thành viên: `CUS000001` … `CUS015000` (15.000 hội viên), `customer_type = 'MEMBER'`.
  - Hạng: Standard, Silver, Gold phân bổ theo phân khúc tiềm ẩn.
  - Ngày đăng ký (`register_date`): khách Mới lấy ngày giao dịch đầu tiên; các nhóm khác đặt trước ngày bắt đầu dữ liệu.

### 2.3. Phân khúc tiềm ẩn của hội viên (Latent Segments) — Đánh giá Ground Truth (T1b)

Để mô phỏng chân thực hành vi hội viên và đối chuẩn mô hình phân cụm K-Means, mỗi hội viên được gán một phân khúc tiềm ẩn (lưu tại `data/latent_labels.csv`, **không** đưa vào DWH).

> [!IMPORTANT] NGUYÊN TẮC SỬ DỤNG GROUND TRUTH TRONG MÔ HÌNH HÓA (ML GOVERNANCE):
> - File `data/latent_labels.csv` chỉ được sử dụng làm **Ground Truth** độc lập trong script đánh giá `scripts/eval_rfm.py` (tính các chỉ số Adjusted Rand Index - ARI, Purity, Confusion Matrix).
> - **Tuyệt đối không đưa các nhãn tiềm ẩn này vào không gian đặc trưng huấn luyện (Feature Space) của thuật toán phân cụm K-Means** trong `core/rfm.py` nhằm đảm bảo tính khách quan và khoa học của đề tài nghiên cứu. Mô hình K-Means chỉ học thuần túy từ 4 đặc trưng hành vi thực tế: Recency, Frequency, Monetary, và Tenure.

| Phân khúc tiềm ẩn | Tỷ lệ (%) | Tần suất/năm TB | Cửa sổ hoạt động | Thể loại ưa thích | Phòng ưa thích | Xác suất mua Combo |
| :--- | :---: | :---: | :--- | :--- | :---: | :---: |
| **VIP** | 8% | 30 | Cả năm (365 ngày) | 1–2 thể loại (60% lượt) | Ưa IMAX / 4DX | 55% |
| **Trung thành** | 20% | 10 | Cả năm (365 ngày) | 1–2 thể loại (60% lượt) | Tiêu chuẩn | 35% |
| **Thỉnh thoảng** | 40% | 3 | Cả năm (365 ngày) | 1–2 thể loại (60% lượt) | Tiêu chuẩn | 20% |
| **Có nguy cơ rời bỏ** | 15% | 8 | Ngừng mua 60–150 ngày trước ngày cuối | 1–2 thể loại (60% lượt) | Tiêu chuẩn | 30% |
| **Ngủ đông** | 10% | 5 | Ngừng mua > 180 ngày trước ngày cuối | 1–2 thể loại (60% lượt) | Tiêu chuẩn | 20% |
| **Mới** | 7% | 4 | Gia nhập trong 60 ngày cuối | 1–2 thể loại (60% lượt) | Tiêu chuẩn | 25% |

- Tần suất trung bình toàn bộ hội viên: $\approx 7.6 \approx 8$ lượt/năm.
- Tỷ lệ giao dịch hội viên chiếm $\approx 30\%$ tổng số giao dịch (GUEST chiếm 70%).

---

## 3. Lịch chiếu & hành vi lấp đầy phòng

Suất chiếu mẫu: `09:30`, `11:15`, `14:00`, `16:30`, `19:00`, `21:15`.

- Ngày thường: 3 suất (14:00, 19:00, 21:15); thứ Sáu 4 suất; IMAX 4 suất.
- Cuối tuần / lễ: 5 suất, thêm suất sáng.

### 3.1. Khung giờ (`time_slot`)

| Khung | Giờ | Ý nghĩa vận hành |
|-------|-----|------------------|
| Sáng | 06:00–11:59 | Vắng hơn, giá thấp hơn (suất sớm) |
| Chiều | 12:00–16:59 | Trung bình |
| Tối | 17:00–21:29 | Giờ vàng |
| Đêm | 21:30–23:59 | Trung bình khá, phụ thuộc phim |

### 3.2. Occupancy trước khi nhân hệ số phim / rạp

Occupancy sau cùng cắt trong `[0.05, 0.98]`, rồi `sold = min(capacity, round(occupancy * capacity * VOLUME_SCALE))`.

| Điều kiện | Sáng | Chiều | Tối | Đêm |
|-----------|------|-------|-----|-----|
| Thứ 2–5 | 0.12 | 0.32 | 0.58 | 0.40 |
| Thứ 6 | 0.16 | 0.40 | 0.72 | 0.55 |
| Thứ 7–CN | 0.38 | 0.68 | 0.88 | 0.70 |
| Ngày lễ | 0.50 | 0.80 | 0.94 | 0.78 |

Nhiễu ngẫu nhiên: ±8 điểm phần trăm.

### 3.3. Phim mới đông hơn phim cũ

`days_since_release = show_date - release_date` (chỉ chiếu phim đã phát hành).

| Tuổi phim | Hệ số occupancy |
|-----------|-----------------|
| 0–13 ngày | ×1.35 |
| 14–41 ngày | ×1.10 |
| 42–69 ngày | ×0.85 |
| ≥ 70 ngày | ×0.60 |

Catalog ~24 tựa, xen kẽ Việt / Hollywood. Phim IMAX ưu tiên phòng IMAX tại Landmark 81.

---

## 4. Bảng giá & khuyến mãi (`dim_pricing`)

| ticket_format | Ngày thường | Cuối tuần | Ngày lễ |
|---------------|-------------|-----------|---------|
| 2D | 75_000 | 95_000 | 105_000 |
| 3D | 95_000 | 115_000 | 125_000 |
| IMAX | 140_000 | 165_000 | 180_000 |

Giảm giá (cộng dồn, trần 35%):

- Suất sáng: −20%.
- MEMBER Standard: −5%; Silver: −8%; Gold: −12%.
- WALK_IN: 0% (trừ suất sáng).

Công thức tính toán giao dịch vé:

- `list_price`: Đơn giá niêm yết của một vé đơn lẻ.
- `per_ticket_discount = round(list_price * discount_rate)`: Số tiền giảm trên 1 vé.
- `per_ticket_paid = list_price - per_ticket_discount`: Số tiền thực trả trên 1 vé.
- `discount_amount = per_ticket_discount * qty`: Tổng giảm giá của cả giao dịch.
- `paid_amount` (staging) = `net_amount` (DWH `fact_ticket`) = `per_ticket_paid * qty`: Tổng tiền thực thanh toán của cả giao dịch.
- `qty`: Số vé mua trong giao dịch ($1 \le \text{qty} \le 4$).

---

## 5. Vé theo giao dịch (Transaction Grain & Party Size)

- **Grain chuẩn của `fact_ticket`:** **Một dòng = Một giao dịch bán vé cho một suất chiếu.** Giao dịch chứa từ 1 đến 4 vé (đại diện bởi `qty`).
- Khóa định danh: Duy nhất `txn_id` dạng `TXN00000001` (cột `txn_id` có ràng buộc UNIQUE trên `fact_ticket`, không sinh mã `ticket_id` theo từng ghế lẻ).
- Phân phối nhóm khách (`party = qty`):
  - 1 vé: 45%
  - 2 vé: 35%
  - 3 vé: 15%
  - 4 vé: 5%
- `COUNT(*)` trên `fact_ticket`: Đếm số lượng giao dịch vé (`ticket_txns`).
- `SUM(qty)` trên `fact_ticket`: Đếm tổng số lượng vé bán ra (`tickets_sold`).

---

## 6. Bắp nước (`staging_fnb.csv`)

| Điều kiện | Xác suất mua F&B |
|-----------|------------------|
| Suất sáng | 22% |
| Suất chiều | 38% |
| Suất tối / đêm | 52% |
| Cộng thêm cuối tuần hoặc lễ | +10 điểm % (trần 70%) |

Nếu mua: 1 dòng 55%, 2 dòng 35%, 3 dòng 10%.

| product_id | Tên | Nhóm | Giá |
|------------|-----|------|-----|
| P01 | Bắp rang nhỏ | snack | 45_000 |
| P02 | Bắp rang vừa | snack | 65_000 |
| P03 | Bắp rang lớn | snack | 79_000 |
| P04 | Nước ngọt | drink | 39_000 |
| P05 | Nước ngọt lớn | drink | 49_000 |
| P06 | Combo 1 (bắp vừa + nước) | combo | 99_000 |
| P07 | Combo 2 (bắp lớn + 2 nước) | combo | 145_000 |
| P08 | Nachos phô mai | snack | 69_000 |

`line_amount = qty * unit_price`. Mọi `txn_id` F&B phải có trên file vé.

---

## 7. Ngày lễ (cờ vận hành)

- 2025: Giỗ Tổ 07/04, 30/04–01/05, 02/09, 20/10, 24–25/12.
- 2026: 01/01, Tết 16/02–22/02, Giỗ Tổ 16/04, 30/04–01/05, 02/09.

---

## 8. Nhãn bất thường (`is_anomaly`)

Tỷ lệ mục tiêu: **1.0% – 2.0%** số dòng vé và số dòng F&B.

| anomaly_type | Cách phá luật |
|--------------|----------------|
| `price_zero` | `paid_amount` / giá F&B = 0 |
| `revenue_mismatch` | tiền ≠ giá × SL − giảm giá |
| `qty_spike` | thêm ghế vượt `capacity`; chỉ dòng vượt sức chứa mang nhãn 1 |

Dòng bình thường: `is_anomaly = 0`, `anomaly_type` trống.

---

## 9. File staging

- `data/staging_ticket.csv`
- `data/staging_fnb.csv`

UTF-8, dấu phẩy, header. Staging **denormalize** để bài ETL tách Dimension / Fact.

---

## 10. Ánh xạ Staging → Star Schema

| Nguồn staging | Đích warehouse |
|---------------|----------------|
| `show_date` / `sale_date` | `dim_date` |
| `show_time` / `sale_time` | `dim_time` |
| `movie_*` | `dim_movie` |
| `cinema_*` | `dim_cinema` |
| `room_*`, `capacity` | `dim_room` |
| `customer_*` | `dim_customer` (`GUEST` = 1 dòng) |
| `product_*` | `dim_product` |
| format + day_type + time_slot + giá | `dim_pricing` |
| `channel_code` | `dim_channel` |
| mỗi giao dịch vé / mỗi dòng SP F&B | `fact_ticket` / `fact_fnb` |
| `txn_id` | degenerate dimension trên cả hai fact |

> [!WARNING] NGUYÊN TẮC JOIN FACT_TICKET VÀ FACT_FNB (TRÁNH DOUBLE COUNTING):
> - Một giao dịch vé (`fact_ticket.txn_id`) có thể có $0, 1$ hoặc nhiều dòng F&B (`fact_fnb`).
> - **Tuyệt đối không JOIN trực tiếp `fact_ticket` với `fact_fnb`** trong các truy vấn phân tích tổng hợp số lượng vé, doanh thu vé hoặc occupancy vì sẽ gây nhân bản số dòng (Cartesian fan-out) và làm sai lệch số liệu (Double Counting).
> - Khi cần tính các chỉ số kết hợp (như Attach Rate, F&B Spend per Ticket Transaction), bắt buộc phải tổng hợp (aggregate) riêng từng bảng theo cùng grain (ví dụ: ngày × rạp × khung giờ) trước khi thực hiện phép JOIN.

---

## 11. Việc cố ý chưa làm ở bước sinh dữ liệu

- Chưa load PostgreSQL (ETL bước sau).
- Chưa RFM / clustering / forecast.
- Chưa user App.
- Không mô phỏng hoàn tiền / suất hủy.

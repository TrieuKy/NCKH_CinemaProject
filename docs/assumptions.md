# Giả định sinh dữ liệu (Mock Data) — Giai đoạn 2

Tài liệu này là **hợp đồng dữ liệu** giữa BA/DA và kỹ thuật. Mọi luật dưới đây được cài trong `scripts/generate_mock_data.py`. Khi báo cáo NCKH, dẫn file này thay vì mô tả miệng.

Khoảng thời gian: **2025-04-01 → 2026-09-30** (18 tháng, nằm trong khung 12–24 tháng).
Khối lượng mock: occupancy thực tế nhân `VOLUME_SCALE = 1.0` trong script để có khối lượng dữ liệu thật. Kỳ vọng: ~1.236.204 dòng vé, ~535.211 dòng F&B, ~16.925 suất chiếu. Occupancy trung bình ~59%, với khoảng 174 suất có occupancy_raw > 100% do lỗi qty_spike. Tỷ lệ tương đối giữa sáng/tối, ngày thường/cuối tuần vẫn giữ nguyên.
Hạt nhân (grain) vé: **1 dòng = 1 ghế / 1 vé**.
Hạt nhân F&B: **1 dòng = 1 sản phẩm trong 1 giao dịch**.
Khóa nối vé ↔ bắp nước: **`txn_id`**.

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
- Thành viên: `CUS000001` … `CUS002500`, `customer_type = 'MEMBER'`.
  - Hạng: Standard 60%, Silver 30%, Gold 10%.

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

Công thức đúng:

```
discount_amount = round(list_price * discount_rate)
paid_amount     = list_price - discount_amount
qty             = 1
```

---

## 5. Vé theo giao dịch

- Mỗi `txn_id` nhóm 1–4 ghế cùng suất (45% / 35% / 15% / 5%).
- `txn_id` dạng `TXN00000001`; `ticket_id` dạng `TKT00000001`.
- Ghế: `A1`, `A2`, … không trùng trong cùng suất.

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
| mỗi ghế / mỗi dòng SP | `fact_ticket` / `fact_fnb` |
| `txn_id` | degenerate dimension trên cả hai fact |

---

## 11. Việc cố ý chưa làm ở bước sinh dữ liệu

- Chưa load PostgreSQL (ETL bước sau).
- Chưa RFM / clustering / forecast.
- Chưa user App.
- Không mô phỏng hoàn tiền / suất hủy.

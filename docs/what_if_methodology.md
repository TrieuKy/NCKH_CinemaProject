# PHƯƠNG PHÁP LUẬN MÔ PHỎNG KỊCH BẢN WHAT-IF (UC07)

**Tài liệu:** Task 11 — What-If Scenario Simulation for Cinema Operations  
**Module:** `core/what_if.py` | **UI:** `app/pages/6_What_If_Analysis.py`

---

## 1. Mục tiêu & Nguyên tắc khoa học

Hệ thống What-If hỗ trợ người quản lý rạp (vai trò `MANAGER`) thực hiện **phân tích giả định có điều kiện (Conditional Arithmetic Simulation)** dựa trên dữ liệu lịch sử thực tế trong PostgreSQL (`cinema_dw`).

### Phân tách khái niệm bắt buộc
1. **Historical Baseline (`HISTORICAL_BASELINE`):** Số liệu thực tế đã ghi nhận trong quá khứ (giao dịch kết thúc ngày 30/09/2026).
2. **Hypothetical Scenario (`HYPOTHETICAL_SCENARIO`):** Kết quả tính toán số học dưới các giả định do người dùng cấu hình.
3. **Operational Forecast:** Chỉ dùng khi còn hiệu lực. Kết quả forecast hiện có trong DB đã hết hạn (`EXPIRED` tại 27/11/2026) nên **tuyệt đối không sử dụng** trong What-If.

### Giới hạn khoa học
- Không ước lượng **Price Elasticity of Demand** (độ co giãn cầu theo giá).
- Không tuyên bố **quan hệ nhân quả** (ví dụ: không kết luận tăng giá vé sẽ làm doanh thu tăng tương ứng trong thực tế).
- Không tính **lợi nhuận / biên lợi nhuận (margin)** vì schema hiện tại chưa có dữ liệu chi phí (giá vốn phim, giá vốn F&B, chi phí vận hành).
- Không tự động đề xuất mở thêm suất chiếu vì `showtime_schedule` không đầy đủ cho lịch chiếu tương lai.

---

## 2. Baseline Data Contract & Grain

Baseline được truy vấn **chỉ đọc (Read-Only)** từ 3 view độc lập theo đúng grain, tuyệt đối không `JOIN` 1-N giữa `fact_ticket` và `fact_fnb`:

| Thành phần | Nguồn dữ liệu | Grain | Phân loại Raw/Clean |
|:---|:---|:---|:---|
| `ticket_revenue_clean`, `ticket_qty_clean`, `fnb_revenue_clean`, `atp_clean` | `cinema_dw.vw_daily_kpi` | Ngày × Rạp | **Clean (`is_anomaly = 0`)** |
| `available_showtime_capacity`, `showtime_count`, `weighted_occupancy` | `cinema_dw.vw_showtime_occupancy` + `dim_cinema` | Suất chiếu | **Clean (`qty_clean / capacity`)** |
| `ticket_txn_count`, `fnb_txn_count`, `attach_rate` | `cinema_dw.vw_fnb_attach` | Ngày × Rạp × Khung giờ | **Raw (toàn bộ giao dịch)** |
| `avg_fnb_revenue_per_fnb_txn`, `spp_clean` | Kết hợp `fnb_revenue_clean` & `ticket_txn_count` | Ngày × Rạp | **Hybrid** |

---

## 3. Công thức các kịch bản

### Scenario A — Thay đổi Giá vé bình quân (`TICKET_PRICE`)
- `Scenario ATP = Baseline ATP × (1 + price_change_pct)`
- `Scenario Ticket Revenue = Baseline Ticket Qty Clean × Scenario ATP`
- `Revenue Delta = Scenario Ticket Revenue - Baseline Ticket Revenue Clean`
- **Giả định bắt buộc:** *"Giả định giữ nguyên lượng vé bán; chưa mô hình hóa phản ứng nhu cầu của khách hàng đối với thay đổi giá."*

### Scenario B — Thay đổi Lượng vé & Sức chứa (`TICKET_VOLUME`)
- `Scenario Ticket Qty = round_half_up(Baseline Ticket Qty Clean × (1 + qty_change_pct))`
- `Scenario Ticket Revenue = Scenario Ticket Qty × Baseline ATP`
- `Scenario Occupancy = Scenario Ticket Qty / Observed Showtime Capacity`
- **Capacity Safeguards:** Nếu `Scenario Ticket Qty > Observed Showtime Capacity`, đánh dấu `CAPACITY_EXCEEDED`, tách phần `unmet_demand_tickets` và `feasible_ticket_revenue_clean` thay vì âm thầm biến nhu cầu vượt ngưỡng thành doanh thu đã bán. Nếu thiếu `capacity`, không công bố `occupancy`.

### Scenario C — Thay đổi Tỷ lệ mua kèm F&B (`FNB_ATTACH`)
- `Baseline Attach Rate = fnb_txn_count / ticket_txn_count` (Raw)
- `Baseline Avg F&B Rev / F&B Txn = fnb_revenue_clean / fnb_txn_count` (Hybrid)
- `Scenario F&B Txns = ticket_txn_count × scenario_attach_rate`
- `Scenario F&B Revenue = Scenario F&B Txns × Baseline Avg F&B Rev / F&B Txn`
- **Lưu ý:** `spp_clean` là **Spend Per Ticket Transaction**, không phải Spend Per Patron.

---

## 4. Quy tắc So sánh & Bảo mật
- Chỉ so sánh các kịch bản có cùng `baseline_fingerprint` (cùng rạp, cùng khoảng ngày, cùng số liệu nền). Đổi rạp hoặc ngày sẽ tự động vô hiệu danh sách kịch bản cũ.
- Không cộng dồn tác động A + B + C như thể chúng độc lập về mặt kinh tế.
- Chỉ `MANAGER` được truy cập UC07; `STAFF` bị chặn ở đầu trang và không thấy menu.
- Đăng xuất hoặc đổi người dùng sẽ xóa toàn bộ `whatif_*` trong `st.session_state`.
- CSV xuất ra không chứa thông tin tài khoản người dùng.

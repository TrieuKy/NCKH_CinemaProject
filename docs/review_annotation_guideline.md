# Hướng dẫn Gán nhãn Dữ liệu Đánh giá Khách hàng (Human Ground Truth)

Tài liệu này quy định chuẩn gán nhãn thủ công cho tối đa **165 đánh giá có văn bản** từ Google Maps (hiện tại thuộc rạp **CN01 — CGV Vincom Center Landmark 81**) nhằm đánh giá khoa học module `reviews/nlp.py`.

> **Nguyên tắc mù (Blind Annotation)**: Người gán nhãn **không được xem số sao (`rating`)** và **không được xem kết quả của mô hình NLP** khi gán nhãn. Nhãn phải phản ánh đúng nội dung văn bản `review_text`.

---

## 1. Cấu trúc file gán nhãn

File mẫu: `data/review_ground_truth_template.csv` (mã hóa UTF-8 with BOM).

| Cột | Mô tả | Giá trị hợp lệ |
| :--- | :--- | :--- |
| `review_id` | Mã định danh đánh giá | Giữ nguyên |
| `review_text` | Nội dung văn bản đánh giá | Giữ nguyên |
| `human_sentiment` | Cảm xúc tổng thể của văn bản | `POSITIVE` \| `NEUTRAL` \| `NEGATIVE` |
| `human_topics` | Một hoặc nhiều chủ đề (phân tách bằng `;`) | Xem mục 3 |
| `annotator_id` | Mã người gán nhãn (ví dụ `ANN_01`, `ANN_02`) | Chuỗi định danh |
| `annotation_status` | Trạng thái hoàn thành | `PENDING` \| `DONE` |

---

## 2. Quy tắc gán nhãn Cảm xúc (`human_sentiment`)

Chỉ chọn **1 nhãn duy nhất** cho toàn bộ `review_text`:

| Nhãn | Định nghĩa | Ví dụ minh họa |
| :--- | :--- | :--- |
| **`POSITIVE`** | Thể hiện sự hài lòng, khen ngợi trải nghiệm, chất lượng, nhân viên, không gian. | *"Rạp đẹp, âm thanh đã tai"*, *"Không quá đông nên rất thoải mái"*, *"Ok, tuyệt"* |
| **`NEGATIVE`** | Thể hiện sự không hài lòng, phàn nàn, tức giận, chê bai dịch vụ/cơ sở vật chất. Lưu ý **phủ định** và **mỉa mai**. | *"Cố úp sale không hài lòng"*, *"Nhân viên ko tốt"*, *"Rạp tuyệt vời quá, chờ 45 phút mới được vào"* |
| **`NEUTRAL`** | Trung tính, mô tả sự kiện khách quan không rõ khen/chê, hoặc khen-chê cân bằng ngang nhau không nghiêng hẳn về phía nào. | *"Hôm nay đi xem phim suất 10h30"*, *"Màn hình to nhưng ghế hơi chật"* |

### Lưu ý các trường hợp tiếng Việt khó
- **Phủ định**: `"không hài lòng"`, `"ko tốt"`, `"chẳng đẹp"` → **`NEGATIVE`**; `"không tệ"` → **`POSITIVE`** hoặc **`NEUTRAL`** tùy ngữ cảnh.
- **Đa ý trái chiều**: Nếu lời chê/khen chiếm ưu thế rõ rệt về mức độ nghiêm trọng, chọn nhãn theo ý chủ đạo; chỉ chọn `NEUTRAL` khi hai vế cân bằng.
- **Tiếng Anh / Ngôn ngữ khác**: Đánh giá theo đúng nghĩa văn bản (ví dụ `"Overrated"` → `NEGATIVE`, `"It was fun"` → `POSITIVE`).

---

## 3. Quy tắc gán nhãn Chủ đề (`human_topics`)

Cho phép **nhiều chủ đề trong một review**, phân tách bằng dấu chấm phẩy `;` (không khoảng trắng thừa quanh `;` hoặc tự động trim khi đọc), ví dụ: `Phòng chiếu & Âm thanh;Thái độ phục vụ`.

| Tên chủ đề (chính xác) | Phạm vi nội dung |
| :--- | :--- |
| **`Hàng chờ & Bắp nước`** | Xếp hàng, quầy bắp nước, combo, đồ ăn/nước uống, thời gian chờ order/lấy bắp, thu ngân quầy bar. |
| **`Giá cả & Vé`** | Giá vé, phụ thu, khuyến mãi, voucher, U22, thẻ thành viên, cảm nhận đắt/rẻ/xứng đáng giá tiền. |
| **`Phòng chiếu & Âm thanh`** | Màn hình, máy chiếu, độ sáng/mờ, âm thanh, loa, ghế ngồi, điều hòa/nhiệt độ phòng, IMAX/Gold Class/Starium. |
| **`Thái độ phục vụ`** | Nhân viên soát vé, thu ngân, bảo vệ, quản lý, thái độ niềm nở/cộc cằn, hỗ trợ khách, ép mua combo/upsell. |
| **`Vệ sinh`** | Độ sạch/dơ của rạp, rác trên sàn/ghế, mùi hôi/ẩm mốc, nhà vệ sinh (toilet/WC), côn trùng. |
| **`Khác`** | **Chỉ dùng** khi văn bản không đề cập bất kỳ chủ đề nào trong 5 nhóm trên (ví dụ: gửi xe, vị trí rạp, quảng cáo trước phim, nhận xét chung chung `"Tuyệt"`). Không ghép `Khác` chung với 5 chủ đề kia. |

---

## 4. Quy trình 2 Annotator độc lập & Giải quyết bất đồng

1. **Gán nhãn độc lập**: Hai người nghiên cứu (`ANN_01` và `ANN_02`) gán nhãn **độc lập** trên cùng tập 165 review (không trao đổi trong lúc gán).
2. **Đo độ đồng thuận (Inter-Annotator Agreement) & Xuất danh sách bất đồng**:
   - Sau khi cả hai hoàn tất (`annotation_status = DONE` với `annotator_id` khác nhau), chạy:
     `python -m scripts.eval_reviews_nlp --input <file_ann1.csv> --annotator2 <file_ann2.csv> --export-disagreements data/review_annotation_disagreements.csv`
   - Script tính **Cohen's Kappa ($\kappa$)** cho `human_sentiment` và từng chủ đề (chỉ tính khi đủ mẫu và phương sai khác 0; không bịa số khi không xác định).
   - Ngưỡng tham chiếu khoa học: $\kappa \ge 0.75$ (đồng thuận tốt).
3. **Giải quyết bất đồng (Adjudication) & Gold Standard**:
   - File `data/review_annotation_disagreements.csv` tự động đánh dấu `AGREED` cho các dòng đồng thuận và `PENDING_ADJUDICATION` cho các dòng bất đồng (để trống `final_sentiment`, `final_topics` — không tự chọn nhãn của `ANN_01` hay mô hình).
   - Các dòng `PENDING_ADJUDICATION` được người thứ ba (Senior Researcher / Adjudicator) chốt `final_sentiment`, `final_topics` và đổi `adjudication_status` thành `ADJUDICATED` để tạo tập **Gold Standard** cuối cùng trước khi công bố F1 chính thức.

# TÀI LIỆU DÒNG CHẢY DỮ LIỆU TOÀN HỆ THỐNG (DATA LINEAGE)

**Dự án:** Hệ thống phân tích hành vi khách hàng và hỗ trợ quyết định vận hành rạp phim  
**Mã tài liệu:** `NCKH-CINEMA-DOC-LINEAGE-01`  
**Phiên bản:** 2.0 (Chuẩn hóa Task 5)  
**Trạng thái:** Hoàn thiện  

---

## 1. Sơ đồ Dòng chảy Dữ liệu Tổng thể (End-to-End Data Lineage)

Dưới đây là sơ đồ chi tiết biểu diễn sự dịch chuyển, biến đổi và tiêu thụ dữ liệu qua các tầng: từ tạo nguồn Mock Data, qua Staging, Data Warehouse (Star Schema), Tầng Tóm lược (Summary), Tầng Views (Data Mart), đến các mô hình Phân tích / Học máy (RFM, Forecasting) và Ứng dụng Streamlit Dashboard:

```mermaid
flowchart TD
    subgraph S1["1. TẦNG SINH NGUỒN (DATA GENERATION)"]
        GEN["scripts/generate_mock_data.py\n• Seed: 42 | Range: 2025-10-01 → 2026-09-30 (12M)\n• 3 Cinemas, 8 Rooms, 15k Members, 70% Walk-in\n• Gieo lỗi Anomaly 1.5%"]
        CSV_T["data/staging_ticket.csv\n(458,804 dòng, 852,878 vé)"]
        CSV_F["data/staging_fnb.csv\n(355,417 dòng F&B)"]
        CSV_L["data/latent_labels.csv\n(15,000 nhãn Ground Truth)"]
        GEN --> CSV_T
        GEN --> CSV_F
        GEN --> CSV_L
    end

    subgraph S2["2. TẦNG TRÍCH XUẤT & STAGING (ELT STAGING)"]
        L_STG["etl/load_staging.py\nTRUNCATE & COPY / INSERT"]
        STG_T["staging.ticket\n(Mirror staging_ticket.csv)"]
        STG_F["staging.fnb\n(Mirror staging_fnb.csv)"]
        CSV_T --> L_STG --> STG_T
        CSV_F --> L_STG --> STG_F
    end

    subgraph S3["3. TẦNG STAR SCHEMA (DIMS & FACTS)"]
        SEED_D["sql/02_seed_dims.sql\netl/seed_dims.py\nON CONFLICT DO NOTHING"]
        D_DATE["dim_date (365 ngày)"]
        EXT_D["etl/extend_dim_date.py\n(Mở rộng đến 2027-12-31)"]
        D_OTHER["dim_time, dim_cinema, dim_room\ndim_movie, dim_customer, dim_product\ndim_pricing, dim_channel"]
        
        STG_T --> SEED_D
        STG_F --> SEED_D
        SEED_D --> D_DATE
        SEED_D --> D_OTHER
        D_DATE --> EXT_D

        L_FACT["sql/03_load_facts.sql\netl/load_facts.py\nSet-based JOIN 8 dims (Atomic)"]
        FACT_T["cinema_dw.fact_ticket\n• Grain: 1 dòng = 1 giao dịch vé\n• txn_id UNIQUE | qty: 1..4\n• net_amount = per_ticket_paid * qty"]
        FACT_F["cinema_dw.fact_fnb\n• Grain: 1 dòng = 1 dòng F&B\n• fnb_line_id UNIQUE | txn_id\n• line_amount = qty * unit_price"]

        STG_T --> L_FACT
        STG_F --> L_FACT
        D_DATE -.-> L_FACT
        D_OTHER -.-> L_FACT
        L_FACT --> FACT_T
        L_FACT --> FACT_F
    end

    subgraph S4["4. TẦNG ĐỐI SOÁT & TÓM LƯỢC (QC & SUMMARY)"]
        QC_REC["etl/reconcile.py\n17 bài test đối soát (5 nhóm):\n• Đối soát số lượng (3 checks)\n• Đối soát tài chính (2 checks)\n• Toàn vẹn & 2 chiều Stg-Fact (7 checks)\n• Kiểm soát thất thoát do JOIN (2 checks)\n• Khóa ngoại NULL & Capacity (3 checks)"]
        FACT_T -.-> QC_REC
        FACT_F -.-> QC_REC
        STG_T -.-> QC_REC
        STG_F -.-> QC_REC
        LOG_ETL["cinema_dw.etl_log\n(Audit trail theo run_id)"]
        QC_REC --> LOG_ETL

        AGG_SUM["etl/aggregate_summary.py\n(sql/05_app_tables.sql)"]
        SUM_SHOW["fact_showtime_summary\n• Grain: Suất chiếu\n• tickets_sold_clean, ticket_revenue_clean"]
        SUM_DAY["fact_daily_cinema_summary\n• Grain: Ngày x Rạp\n• ticket_txns, fnb_txns, fnb_revenue"]
        
        FACT_T --> AGG_SUM --> SUM_SHOW
        FACT_T --> AGG_SUM --> SUM_DAY
        FACT_F --> AGG_SUM --> SUM_DAY
    end

    subgraph S5["5. TẦNG VIEWS (DATA MARTS - sql/03_views.sql)"]
        VW_KPI["vw_daily_kpi\n(ATP, Clean Ticket/F&B Revenue)"]
        VW_OCC["vw_showtime_occupancy\n(Occupancy Clean vs Raw, Demand Group)"]
        VW_ATT["vw_fnb_attach\n(Attach Rate Raw, SPP Hybrid, Combo Rate)"]
        VW_ANM["vw_anomaly_showtime\n(Phát hiện suất chiếu lỗi/vượt capacity)"]

        FACT_T --> VW_KPI
        FACT_F --> VW_KPI
        FACT_T --> VW_OCC
        FACT_T --> VW_ATT
        FACT_F --> VW_ATT
        FACT_T --> VW_ANM
    end

    subgraph S6["6. TẦNG PHÂN TÍCH & TIÊU THỤ (ANALYTICS, ML & APP)"]
        MOD_RFM["core/rfm.py (scripts/run_rfm.py)\n• Filter: is_guest=FALSE AND is_anomaly=0\n• K-Means (R, F, M, Tenure)\n• Table: rfm_result"]
        EVAL_RFM["scripts/eval_rfm.py\n• So khớp với data/latent_labels.csv\n• ARI, Purity, Confusion Matrix"]
        
        MOD_FC["core/forecast.py (scripts/run_forecast.py)\n• Input: fact_showtime_summary.tickets_sold_clean\n• Baseline, Prophet, XGBoost\n• Table: forecast_result"]
        
        APP_SHELL["app/main.py (Streamlit UI Foundation)\n• Xác thực bcrypt (cinema_dw.app_user)\n• Phân quyền STAFF/MANAGER, Bộ lọc chuẩn\n• Thư viện linh kiện UI (Đã xác nhận trong mã)"]

        APP_DASH["Dashboard Nghiệp vụ (UC01–UC09)\n• Tổng quan KPI, RFM, F&B, Dự báo, Cảnh báo QC\n• Dự kiến tích hợp giao diện ở Task 9–10"]

        FACT_T --> MOD_RFM
        FACT_F --> MOD_RFM
        MOD_RFM --> EVAL_RFM
        CSV_L -.->|Chỉ dùng đánh giá| EVAL_RFM
        
        SUM_SHOW --> MOD_FC
        D_DATE -.-> MOD_FC

        APP_SHELL --- APP_DASH
        VW_KPI -.->|Dự kiến Task 9-10| APP_DASH
        VW_OCC -.->|Dự kiến Task 9-10| APP_DASH
        VW_ATT -.->|Dự kiến Task 9-10| APP_DASH
        VW_ANM -.->|Dự kiến Task 9-10| APP_DASH
        MOD_RFM -.->|Dự kiến Task 9-10| APP_DASH
        MOD_FC -.->|Dự kiến Task 9-10| APP_DASH
    end
```

---

## 2. Chi tiết Biến đổi & Chuyển giao qua từng Công đoạn

### Giai đoạn 1: Sinh nguồn (Mock Generation)
- **Công cụ:** `scripts/generate_mock_data.py`.
- **Cơ chế:** Dựa trên các bảng phân phối xác suất thực tế (khung giờ, thể loại phim, độ tuổi phim, ngày lễ/cuối tuần).
- **Đặc trưng:**
  - Nhóm khách theo đoàn: $1 \le \text{party} \le 4$.
  - Lưu trữ dưới dạng giao dịch vé: `qty = party`, `net_amount = per_ticket_paid * party`, `discount_amount = per_ticket_discount * party`.
  - Gieo nhãn bất thường $1.5\%$ với 3 dạng: `price_zero`, `revenue_mismatch`, `qty_spike`.
  - Sinh file nhãn tiềm ẩn `data/latent_labels.csv` riêng biệt, không đưa vào luồng ETL nạp kho.

### Giai đoạn 2: Nạp Staging (ELT Extract & Load)
- **Công cụ:** `etl/load_staging.py` (hoặc `sql/01b_etl_support.sql`).
- **Bảng đích:** `staging.ticket`, `staging.fnb`.
- **Cơ chế:** TRUNCATE và tải nguyên vẹn dữ liệu từ file CSV, giữ định dạng văn bản thô để đảm bảo tốc độ nạp cao nhất.

### Giai đoạn 3: Nạp Star Schema (SQL Set-Based Transformation)
- **Công cụ:** `sql/02_seed_dims.sql` và `sql/03_load_facts.sql` (bọc bởi `etl/load_facts.py`).
- **Cơ chế:**
  - `dim_date`: Khởi tạo bằng `generate_series` toàn bộ chu kỳ 12 tháng (`2025-10-01` đến `2026-09-30`), sau đó được mở rộng đến `2027-12-31` bằng `etl/extend_dim_date.py`.
  - Các dimension khác (`dim_movie`, `dim_customer`, `dim_pricing`...): Trích xuất `DISTINCT` kèm ép kiểu dữ liệu từ staging bảng `ON CONFLICT DO NOTHING`.
  - `fact_ticket`: JOIN 8 dimension (`dim_date`, `dim_time`, `dim_movie`, `dim_cinema`, `dim_room`, `dim_customer`, `dim_pricing`, `dim_channel`) để tra surrogate key.
  - `fact_fnb`: JOIN 6 dimension. Ràng buộc `WHERE EXISTS` đảm bảo `txn_id` phải tồn tại trong giao dịch vé (loại bỏ triệt để F&B mồ côi).

### Giai đoạn 4: Đối soát & Tổng hợp tóm lược (Reconciliation & Summarization)
- **Đối soát chất lượng dữ liệu (`etl/reconcile.py`):** Thực thi độc lập 17 bài kiểm tra kiểm định chất lượng (QC Checks) chia thành 5 nhóm chuyên biệt, ghi toàn bộ lịch sử và trạng thái `OK`/`FAIL` vào `cinema_dw.etl_log` theo `run_id`:
  1. *Đối soát số lượng (3 checks):* Số dòng vé (`reconcile_ticket_rows`), tổng vé quy đổi `SUM(qty)` (`reconcile_ticket_qty`), số dòng F&B (`reconcile_fnb_rows`).
  2. *Đối soát tài chính (2 checks):* Tổng tiền vé (`reconcile_ticket_amount`: `net_amount` vs `paid_amount`), tổng tiền F&B (`reconcile_fnb_amount`: `line_amount`).
  3. *QC Toàn vẹn & Đối soát hai chiều Staging – Fact (7 checks):* F&B mồ côi trong staging (`qc_orphan_fnb_stg`), F&B mồ côi trong Fact (`qc_orphan_fnb_fact`), đối soát hai chiều giao dịch vé giữa Staging và Fact (`qc_stg_ticket_missing_in_fact`, `qc_fact_ticket_missing_in_stg`), đối soát hai chiều dòng F&B giữa Staging và Fact (`qc_stg_fnb_missing_in_fact`, `qc_fact_fnb_missing_in_stg`), kiểm tra lệch tiền chi tiết từng dòng vé (`qc_ticket_amount_mismatch`).
  4. *QC Kiểm soát thất thoát do JOIN (Inner Join Loss) (2 checks):* Giao dịch vé staging không khớp bất kỳ dimension nào (`qc_stg_ticket_unmatched_dims`), dòng F&B staging không khớp bất kỳ dimension nào (`qc_stg_fnb_unmatched_dims`).
  5. *QC Khóa ngoại & Logic nghiệp vụ (3 checks):* Suất chiếu bán vượt capacity mà `is_anomaly = 0` (`qc_capacity_breach_normal`), khóa ngoại NULL trong `fact_ticket` (`qc_fact_null_fk_ticket`), khóa ngoại NULL trong `fact_fnb` (`qc_fact_null_fk_fnb`).
  *(Lưu ý chuẩn hóa: Không tuyên bố có các kiểm tra chưa được triển khai; chỉ số phân tích tỷ lệ bất thường thuộc view nghiệp vụ `vw_anomaly_showtime`, không nằm trong script reconcile).*
- **Tổng hợp:** `etl/aggregate_summary.py` nhóm số liệu từ fact sang:
  - `fact_showtime_summary`: Cấp suất chiếu (`date_key`, `time_key`, `room_key`).
  - `fact_daily_cinema_summary`: Cấp ngày × rạp (`date_key`, `cinema_key`).

### Giai đoạn 5: Tiêu thụ phân tích & Mô hình hóa (Consumption)
- **Mô hình RFM (`core/rfm.py`):** Lọc dữ liệu khách hàng sạch (`is_guest = FALSE` và `is_anomaly = 0`), tính toán 4 chỉ số $R, F, M, \text{Tenure}$ và phân cụm K-Means. Kết quả được lưu tại bảng `cinema_dw.rfm_result`.
- **Đánh giá Ground Truth (`scripts/eval_rfm.py`):** Đọc nhãn tiềm ẩn từ `data/latent_labels.csv` để đo độ tương đồng ARI và Purity so với cụm K-Means. Nhãn tiềm ẩn **không tham gia** quá trình huấn luyện.
- **Mô hình Dự báo (`core/forecast.py`):** Đọc chuỗi thời gian `tickets_sold_clean` từ `fact_showtime_summary` gộp theo ngày × rạp × khung giờ. Huấn luyện Baseline, Prophet, XGBoost và lưu kết quả dự báo 14 ngày tương lai vào bảng `cinema_dw.forecast_result`.
- **Ứng dụng giao diện người dùng (`app/main.py`) & Phân định phạm vi triển khai:**
  - *Luồng dữ liệu đã xác nhận trong mã (Đã hoàn thiện ở UI-1):* Khởi tạo nền tảng giao diện Streamlit, cơ chế xác thực bảo mật người dùng qua bảng `cinema_dw.app_user` (mã hóa mật khẩu bcrypt), quản trị phiên làm việc (session state), cây điều hướng sidebar phân quyền động theo vai trò (STAFF / MANAGER), thanh bộ lọc đa tiêu chuẩn dùng lại (`render_standard_filters`), hệ thống giao diện mẫu (Theme, Format, UI Components) và trang Thư viện linh kiện (`pages/0_Thu_vien_thanh_phan.py`).
  - *Module backend đã có nhưng chưa kiểm chứng tích hợp giao diện:* Các Data Mart Views (`vw_daily_kpi`, `vw_showtime_occupancy`, `vw_fnb_attach`, `vw_anomaly_showtime`), các bảng summary (`fact_showtime_summary`, `fact_daily_cinema_summary`), bảng phân khúc `rfm_result` và bảng dự báo `forecast_result` đã hoàn thành và kiểm thử ở tầng cơ sở dữ liệu / ETL.
  - *Chức năng dự kiến phát triển ở Task 9–10:* Đấu nối các nguồn dữ liệu trên vào các trang Dashboard tương tác phân tích chuyên sâu (UC01 Tổng quan KPI, UC02 Phân khúc RFM, UC03 Bắp nước & Giỏ hàng, UC04 Dự báo vé, UC05 Cảnh báo bất thường, UC06 Đề xuất vận hành, UC07 Mô phỏng What-If, UC09 Nhật ký ETL). Không xem các kế hoạch tích hợp giao diện này là chức năng đã hoàn thiện trong mã nguồn hiện tại.

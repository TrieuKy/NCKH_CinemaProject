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
        QC_REC["etl/reconcile.py\n13 bài test đối soát:\nRow counts, SUM(qty), Amounts,\nOrphan check, FK integrity"]
        FACT_T -.-> QC_REC
        FACT_F -.-> QC_REC
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
        VW_ATT["vw_fnb_attach\n(Attach Rate, SPP/SPT Clean, Combo Rate)"]
        VW_ANM["vw_anomaly_showtime\n(Phát hiện suất chiếu lỗi/vượt capacity)"]

        FACT_T --> VW_KPI
        FACT_F --> VW_KPI
        FACT_T --> VW_OCC
        FACT_T --> VW_ATT
        FACT_F --> VW_ATT
        FACT_T --> VW_ANM
    end

    subgraph S6["6. TẦNG PHÂN TÍCH TIÂU THỤ (ANALYTICS, ML & APP)"]
        MOD_RFM["core/rfm.py (scripts/run_rfm.py)\n• Filter: is_guest=FALSE AND is_anomaly=0\n• K-Means (R, F, M, Tenure)\n• Table: rfm_result"]
        EVAL_RFM["scripts/eval_rfm.py\n• So khớp với data/latent_labels.csv\n• ARI, Purity, Confusion Matrix"]
        
        MOD_FC["core/forecast.py (scripts/run_forecast.py)\n• Input: fact_showtime_summary.tickets_sold_clean\n• Baseline, Prophet, XGBoost\n• Table: forecast_result"]
        
        APP_UI["app/main.py (Streamlit Dashboard)\n• Tổng quan KPI, Doanh thu, Lấp đầy\n• Phân khúc khách hàng & Gợi ý vé/F&B\n• Cảnh báo bất thường QC"]

        FACT_T --> MOD_RFM
        FACT_F --> MOD_RFM
        MOD_RFM --> EVAL_RFM
        CSV_L -.->|Chỉ dùng đánh giá| EVAL_RFM
        
        SUM_SHOW --> MOD_FC
        D_DATE -.-> MOD_FC

        VW_KPI --> APP_UI
        VW_OCC --> APP_UI
        VW_ATT --> APP_UI
        VW_ANM --> APP_UI
        MOD_RFM --> APP_UI
        MOD_FC --> APP_UI
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
- **Đối soát:** `etl/reconcile.py` chạy 13 truy vấn kiểm tra chéo (số dòng, tổng `qty`, tổng tiền, khóa ngoại, tỷ lệ bất thường), ghi nhật ký kiểm định vào `cinema_dw.etl_log`.
- **Tổng hợp:** `etl/aggregate_summary.py` nhóm số liệu từ fact sang:
  - `fact_showtime_summary`: Cấp suất chiếu (`date_key`, `time_key`, `room_key`).
  - `fact_daily_cinema_summary`: Cấp ngày × rạp (`date_key`, `cinema_key`).

### Giai đoạn 5: Tiêu thụ phân tích & Mô hình hóa (Consumption)
- **Mô hình RFM (`core/rfm.py`):** Lọc dữ liệu khách hàng sạch (`is_guest = FALSE` và `is_anomaly = 0`), tính toán 4 chỉ số $R, F, M, \text{Tenure}$ và phân cụm K-Means. Kết quả được lưu tại bảng `cinema_dw.rfm_result`.
- **Đánh giá Ground Truth (`scripts/eval_rfm.py`):** Đọc nhãn tiềm ẩn từ `data/latent_labels.csv` để đo độ tương đồng ARI và Purity so với cụm K-Means. Nhãn tiềm ẩn **không tham gia** quá trình huấn luyện.
- **Mô hình Dự báo (`core/forecast.py`):** Đọc chuỗi thời gian `tickets_sold_clean` từ `fact_showtime_summary` gộp theo ngày × rạp × khung giờ. Huấn luyện Baseline, Prophet, XGBoost và lưu kết quả dự báo 14 ngày tương lai vào bảng `cinema_dw.forecast_result`.
- **Giao diện người dùng (`app/main.py`):** Đọc trực tiếp các views và bảng kết quả phân tích để hiển thị dashboard tương tác.

-- =============================================================================
-- sql/03_views.sql
-- Tạo các view phục vụ phân tích (Data Mart layer) trong schema cinema_dw.
-- Mọi view đều cung cấp số liệu raw (tất cả dòng) và clean (is_anomaly = 0).
-- Chỉ số KPIs chính (occupancy, ATP, attach rate) chỉ tính trên dữ liệu clean.
-- =============================================================================

SET search_path TO cinema_dw, public;

-- =============================================================================
-- 1. vw_daily_kpi (theo ngày × rạp)
-- =============================================================================
CREATE OR REPLACE VIEW cinema_dw.vw_daily_kpi AS
WITH ticket_agg AS (
    SELECT 
        date_key, cinema_key,
        SUM(net_amount) AS ticket_revenue_raw,
        SUM(CASE WHEN is_anomaly = 0 THEN net_amount ELSE 0 END) AS ticket_revenue_clean,
        SUM(qty) AS ticket_qty_raw,
        SUM(CASE WHEN is_anomaly = 0 THEN qty ELSE 0 END) AS ticket_qty_clean
    FROM cinema_dw.fact_ticket
    GROUP BY date_key, cinema_key
),
fnb_agg AS (
    SELECT 
        date_key, cinema_key,
        SUM(line_amount) AS fnb_revenue_raw,
        SUM(CASE WHEN is_anomaly = 0 THEN line_amount ELSE 0 END) AS fnb_revenue_clean
    FROM cinema_dw.fact_fnb
    GROUP BY date_key, cinema_key
)
SELECT 
    t.date_key, t.cinema_key,
    d.full_date,
    c.cinema_name,
    t.ticket_revenue_raw,
    t.ticket_revenue_clean,
    t.ticket_qty_raw,
    t.ticket_qty_clean,
    CASE 
        WHEN t.ticket_qty_clean > 0 THEN t.ticket_revenue_clean / t.ticket_qty_clean 
        ELSE 0 
    END AS atp_clean,
    COALESCE(f.fnb_revenue_raw, 0) AS fnb_revenue_raw,
    COALESCE(f.fnb_revenue_clean, 0) AS fnb_revenue_clean
FROM ticket_agg t
JOIN cinema_dw.dim_date d ON t.date_key = d.date_key
JOIN cinema_dw.dim_cinema c ON t.cinema_key = c.cinema_key
LEFT JOIN fnb_agg f ON t.date_key = f.date_key AND t.cinema_key = f.cinema_key;

COMMENT ON VIEW cinema_dw.vw_daily_kpi IS 'KPI tổng hợp theo ngày và rạp.';
COMMENT ON COLUMN cinema_dw.vw_daily_kpi.atp_clean IS 'Average Ticket Price (ATP) = ticket_revenue_clean / ticket_qty_clean.';


-- =============================================================================
-- 2. vw_showtime_occupancy (theo suất chiếu)
-- Suất chiếu định danh bằng: date_key, time_key, room_key, movie_key
-- =============================================================================
CREATE OR REPLACE VIEW cinema_dw.vw_showtime_occupancy AS
WITH showtime_agg AS (
    SELECT 
        t.date_key, t.time_key, t.cinema_key, t.room_key, t.movie_key,
        SUM(t.qty) AS qty_raw,
        SUM(CASE WHEN t.is_anomaly = 0 THEN t.qty ELSE 0 END) AS qty_clean
    FROM cinema_dw.fact_ticket t
    GROUP BY t.date_key, t.time_key, t.cinema_key, t.room_key, t.movie_key
)
SELECT 
    s.date_key, s.time_key, s.room_key, s.movie_key,
    dc.cinema_name,
    dr.room_name,
    dr.room_type,
    dm.title AS movie_title,
    dt.time_of_day AS show_time,
    dr.capacity,
    s.qty_clean,
    s.qty_raw,
    (s.qty_clean::NUMERIC / dr.capacity) AS occupancy_clean,
    (s.qty_raw::NUMERIC / dr.capacity) AS occupancy_raw,
    (s.qty_clean::NUMERIC / dr.capacity) < 0.30 AS is_low_demand,
    CASE 
        WHEN (s.qty_clean::NUMERIC / dr.capacity) < 0.30 THEN 'Thấp điểm'
        WHEN (s.qty_clean::NUMERIC / dr.capacity) >= 0.70 THEN 'Tốt'
        ELSE 'Bình thường'
    END AS demand_group
FROM showtime_agg s
JOIN cinema_dw.dim_cinema dc ON s.cinema_key = dc.cinema_key
JOIN cinema_dw.dim_room dr ON s.room_key = dr.room_key
JOIN cinema_dw.dim_movie dm ON s.movie_key = dm.movie_key
JOIN cinema_dw.dim_time dt ON s.time_key = dt.time_key;

COMMENT ON VIEW cinema_dw.vw_showtime_occupancy IS 'Phân tích tỷ lệ lấp đầy (occupancy) theo từng suất chiếu.';
COMMENT ON COLUMN cinema_dw.vw_showtime_occupancy.occupancy_clean IS 'Tỷ lệ lấp đầy = qty_clean / capacity. Chỉ dùng dữ liệu sạch.';
COMMENT ON COLUMN cinema_dw.vw_showtime_occupancy.occupancy_raw IS 'Tỷ lệ lấp đầy thô (có thể > 1 nếu có lỗi qty_spike).';


-- =============================================================================
-- 3. vw_fnb_attach (theo ngày × rạp × khung giờ)
-- =============================================================================
CREATE OR REPLACE VIEW cinema_dw.vw_fnb_attach AS
WITH ticket_txns AS (
    -- Số lượng giao dịch vé theo ngày, rạp, giờ (raw để làm mẫu số tổng)
    SELECT date_key, cinema_key, time_key, COUNT(DISTINCT txn_id) AS ticket_txn_count
    FROM cinema_dw.fact_ticket
    GROUP BY date_key, cinema_key, time_key
),
fnb_txns AS (
    -- Các chỉ số F&B theo ngày, rạp, giờ
    SELECT 
        f.date_key, f.cinema_key, f.time_key,
        COUNT(DISTINCT f.txn_id) AS fnb_txn_count,
        SUM(CASE WHEN f.is_anomaly = 0 THEN f.line_amount ELSE 0 END) AS fnb_revenue_clean,
        COUNT(DISTINCT CASE WHEN p.is_combo THEN f.txn_id END) AS combo_txn_count
    FROM cinema_dw.fact_fnb f
    JOIN cinema_dw.dim_product p ON f.product_key = p.product_key
    GROUP BY f.date_key, f.cinema_key, f.time_key
)
SELECT 
    t.date_key, t.cinema_key, t.time_key,
    d.full_date,
    c.cinema_name,
    tm.time_of_day AS txn_time,
    t.ticket_txn_count,
    COALESCE(f.fnb_txn_count, 0) AS fnb_txn_count,
    COALESCE(f.combo_txn_count, 0) AS combo_txn_count,
    COALESCE(f.fnb_revenue_clean, 0) AS fnb_revenue_clean,
    
    CASE WHEN t.ticket_txn_count > 0 THEN COALESCE(f.fnb_txn_count, 0)::NUMERIC / t.ticket_txn_count ELSE 0 END AS attach_rate,
    
    CASE WHEN t.ticket_txn_count > 0 THEN COALESCE(f.fnb_revenue_clean, 0)::NUMERIC / t.ticket_txn_count ELSE 0 END AS spp_clean,
    
    CASE WHEN t.ticket_txn_count > 0 THEN COALESCE(f.combo_txn_count, 0)::NUMERIC / t.ticket_txn_count ELSE 0 END AS combo_attach_rate
FROM ticket_txns t
JOIN cinema_dw.dim_date d ON t.date_key = d.date_key
JOIN cinema_dw.dim_cinema c ON t.cinema_key = c.cinema_key
JOIN cinema_dw.dim_time tm ON t.time_key = tm.time_key
LEFT JOIN fnb_txns f ON t.date_key = f.date_key AND t.cinema_key = f.cinema_key AND t.time_key = f.time_key;

COMMENT ON VIEW cinema_dw.vw_fnb_attach IS 'Phân tích Attach Rate và SPP của F&B.';
COMMENT ON COLUMN cinema_dw.vw_fnb_attach.attach_rate IS 'Tỷ lệ giao dịch F&B trên tổng giao dịch vé (fnb_txn_count / ticket_txn_count).';
COMMENT ON COLUMN cinema_dw.vw_fnb_attach.spp_clean IS 'Spend Per Patron/Transaction = fnb_revenue_clean / ticket_txn_count (do GUEST không định danh, dùng số giao dịch vé làm mẫu số).';
COMMENT ON COLUMN cinema_dw.vw_fnb_attach.combo_attach_rate IS 'Tỷ lệ giao dịch vé có mua combo.';


-- =============================================================================
-- 4. vw_anomaly_rate (theo loại bất thường × rạp)
-- =============================================================================
CREATE OR REPLACE VIEW cinema_dw.vw_anomaly_rate AS
WITH ticket_anom AS (
    SELECT 
        'Ticket' AS source_type, 
        ft.cinema_key,
        COALESCE(ft.anomaly_type, 'normal') AS anomaly_type,
        COUNT(*) AS row_count, 
        SUM(ft.net_amount) AS total_amount,
        SUM(COUNT(*)) OVER (PARTITION BY ft.cinema_key) AS cinema_total_rows,
        SUM(SUM(ft.net_amount)) OVER (PARTITION BY ft.cinema_key) AS cinema_total_amount
    FROM cinema_dw.fact_ticket ft
    GROUP BY ft.cinema_key, COALESCE(ft.anomaly_type, 'normal')
),
fnb_anom AS (
    SELECT 
        'F&B' AS source_type, 
        ff.cinema_key,
        COALESCE(ff.anomaly_type, 'normal') AS anomaly_type,
        COUNT(*) AS row_count, 
        SUM(ff.line_amount) AS total_amount,
        SUM(COUNT(*)) OVER (PARTITION BY ff.cinema_key) AS cinema_total_rows,
        SUM(SUM(ff.line_amount)) OVER (PARTITION BY ff.cinema_key) AS cinema_total_amount
    FROM cinema_dw.fact_fnb ff
    GROUP BY ff.cinema_key, COALESCE(ff.anomaly_type, 'normal')
),
combined AS (
    SELECT * FROM ticket_anom
    UNION ALL
    SELECT * FROM fnb_anom
)
SELECT 
    c.source_type,
    dc.cinema_name,
    c.anomaly_type,
    c.row_count,
    c.total_amount,
    ROUND((c.row_count::NUMERIC / c.cinema_total_rows) * 100, 2) AS pct_rows,
    ROUND((c.total_amount::NUMERIC / c.cinema_total_amount) * 100, 2) AS pct_amount
FROM combined c
JOIN cinema_dw.dim_cinema dc ON c.cinema_key = dc.cinema_key;

COMMENT ON VIEW cinema_dw.vw_anomaly_rate IS 'Thống kê tỷ lệ các dòng dữ liệu bất thường và bình thường.';

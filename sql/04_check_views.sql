-- =============================================================================
-- sql/04_check_views.sql
-- Kiểm tra logic và kết quả của các views trong cinema_dw.
-- =============================================================================

SET search_path TO cinema_dw, public;

-- (a) Tổng doanh thu thô của vw_daily_kpi = tổng net_amount của fact_ticket, in hiệu số
SELECT 
    'Check (a)' AS check_name,
    (SELECT SUM(ticket_revenue_raw) FROM vw_daily_kpi) AS kpi_revenue,
    (SELECT SUM(net_amount) FROM fact_ticket) AS fact_revenue,
    (SELECT SUM(ticket_revenue_raw) FROM vw_daily_kpi) - (SELECT SUM(net_amount) FROM fact_ticket) AS diff,
    CASE 
        WHEN (SELECT SUM(ticket_revenue_raw) FROM vw_daily_kpi) = (SELECT SUM(net_amount) FROM fact_ticket) THEN 'PASS'
        ELSE 'FAIL'
    END AS status;

-- (b) occupancy_clean <= 1 ở mọi suất
SELECT 
    'Check (b)' AS check_name,
    COUNT(*) AS count_breach,
    CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status
FROM vw_showtime_occupancy
WHERE occupancy_clean > 1.0;

-- (c) attach_rate toàn kỳ (trung bình tổng quan)
SELECT 
    'Check (c)' AS check_name,
    SUM(fnb_txn_count) AS total_fnb_txns,
    SUM(ticket_txn_count) AS total_ticket_txns,
    CASE WHEN SUM(ticket_txn_count) > 0 THEN 
        ROUND((SUM(fnb_txn_count)::NUMERIC / SUM(ticket_txn_count)) * 100, 2) 
    ELSE 0 END AS overall_attach_rate_pct
FROM vw_fnb_attach;

-- (d) 10 suất có occupancy_raw lớn nhất, xem có phải đều do lỗi không (thêm anomaly_type ưu thế)
WITH showtime_anom AS (
    SELECT 
        date_key, time_key, room_key, movie_key,
        MODE() WITHIN GROUP (ORDER BY anomaly_type) AS primary_anomaly
    FROM fact_ticket
    GROUP BY date_key, time_key, room_key, movie_key
)
SELECT 
    'Check (d)' AS check_name,
    v.cinema_name,
    v.movie_title,
    v.show_time,
    v.capacity,
    v.qty_raw,
    v.occupancy_raw,
    v.demand_group,
    a.primary_anomaly
FROM vw_showtime_occupancy v
LEFT JOIN showtime_anom a 
    ON v.date_key = a.date_key AND v.time_key = a.time_key 
    AND v.room_key = a.room_key AND v.movie_key = a.movie_key
ORDER BY v.occupancy_raw DESC
LIMIT 10;

-- (e) Truy vấn phân bố demand_group (Thấp điểm/Bình thường/Tốt) theo số suất và theo khung giờ
SELECT 
    'Check (e)' AS check_name,
    show_time,
    demand_group,
    COUNT(*) AS count_showtimes
FROM vw_showtime_occupancy
GROUP BY show_time, demand_group
ORDER BY show_time, demand_group;

-- (f) Tổng quan vw_anomaly_showtime: tỷ lệ suất có bất thường và vượt sức chứa
SELECT 
    'Check (f)' AS check_name,
    COUNT(*) AS total_showtimes,
    SUM(CASE WHEN is_anomaly_showtime THEN 1 ELSE 0 END) AS anomaly_showtimes,
    ROUND((SUM(CASE WHEN is_anomaly_showtime THEN 1 ELSE 0 END)::NUMERIC / COUNT(*)) * 100, 2) AS anomaly_showtime_rate_pct,
    COUNT(CASE WHEN occupancy_raw > 1.0 THEN 1 END) AS over_capacity_showtimes
FROM vw_anomaly_showtime;

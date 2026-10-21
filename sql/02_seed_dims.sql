-- =============================================================================
-- sql/02_seed_dims.sql
-- Nạp 9 dimension từ dữ liệu staging (INSERT ... ON CONFLICT DO NOTHING).
--
-- Idempotent: chạy lại nhiều lần không sinh dòng trùng (nhờ ON CONFLICT DO NOTHING
-- kết hợp với UNIQUE constraint trên natural key của từng dim).
--
-- Thứ tự phải giữ:
--   1. dim_cinema  (không phụ thuộc)
--   2. dim_room    (FK → dim_cinema)
--   3. dim_movie, dim_customer, dim_product, dim_pricing, dim_channel, dim_date, dim_time
--      (không phụ thuộc nhau, có thể chạy song song)
-- =============================================================================

SET search_path TO cinema_dw, staging, public;

-- =============================================================================
-- 1. dim_date — generate_series toàn bộ 12 tháng + cờ lễ từ staging
-- =============================================================================
-- Lý do dùng generate_series thay vì DISTINCT từ staging:
--   Đảm bảo phủ đủ mọi ngày kể cả ngày không có vé (rạp đóng cửa).
--   holiday_name/is_holiday lấy từ staging để khớp với dữ liệu thật.
INSERT INTO cinema_dw.dim_date (
    date_key, full_date, day_of_month, month_no, month_name,
    quarter_no, year_no, week_of_year, day_of_week, day_name,
    is_weekend, is_holiday, is_peak_day, holiday_name
)
WITH holidays AS (
    -- Tổng hợp ngày lễ từ staging (DISTINCT phòng khi cùng ngày nhiều phòng chiếu)
    SELECT DISTINCT
        show_date::DATE                 AS hdate,
        NULLIF(holiday_name, '')        AS holiday_name
    FROM staging.ticket
    WHERE is_holiday = 1
)
SELECT
    TO_CHAR(d, 'YYYYMMDD')::INTEGER,            -- 20251001
    d::DATE,
    EXTRACT(DAY     FROM d)::SMALLINT,
    EXTRACT(MONTH   FROM d)::SMALLINT,
    TRIM(TO_CHAR(d, 'Month')),                   -- 'October', …
    EXTRACT(QUARTER FROM d)::SMALLINT,
    EXTRACT(YEAR    FROM d)::SMALLINT,
    EXTRACT(WEEK    FROM d)::SMALLINT,
    EXTRACT(ISODOW  FROM d)::SMALLINT,           -- ISO: 1=Thứ 2 … 7=Chủ nhật
    TRIM(TO_CHAR(d, 'Day')),                     -- 'Monday', …
    EXTRACT(ISODOW  FROM d) >= 6,               -- TRUE nếu Thứ 7 hoặc CN
    (h.hdate IS NOT NULL),                       -- is_holiday
    (
        (EXTRACT(MONTH FROM d) = 10 AND EXTRACT(DAY FROM d) = 20)
        OR (EXTRACT(MONTH FROM d) = 12 AND EXTRACT(DAY FROM d) IN (24, 25))
    )                                            AS is_peak_day,
    h.holiday_name
FROM generate_series(
    '2025-10-01'::DATE,
    '2026-09-30'::DATE,
    '1 day'::INTERVAL
) AS g(d)
LEFT JOIN holidays h ON h.hdate = d::DATE
ON CONFLICT (date_key) DO NOTHING;

-- =============================================================================
-- 2. dim_time — 1.440 dòng (00:00 – 23:59), generate_series
-- =============================================================================
-- time_key = hour*100 + minute (ví dụ: 19:30 → 1930)
-- time_slot / is_prime_time theo định nghĩa assumptions.md §3.1
INSERT INTO cinema_dw.dim_time (
    time_key, time_of_day, hour_no, minute_no, time_slot, is_prime_time
)
SELECT
    h * 100 + m                                  AS time_key,
    MAKE_TIME(h, m, 0)                           AS time_of_day,
    h::SMALLINT                                  AS hour_no,
    m::SMALLINT                                  AS minute_no,
    CASE
        WHEN h < 12                              THEN 'Sáng'
        WHEN h < 17                              THEN 'Chiều'
        WHEN h < 21 OR (h = 21 AND m < 30)      THEN 'Tối'
        ELSE                                          'Đêm'
    END                                          AS time_slot,
    -- Prime time: 17:00–21:29 (assumptions.md §3.1)
    (h >= 17 AND (h < 21 OR (h = 21 AND m < 30))) AS is_prime_time
FROM generate_series(0, 23) AS h,
     generate_series(0, 59) AS m
ON CONFLICT (time_key) DO NOTHING;

-- =============================================================================
-- 3. dim_cinema — 3 rạp từ staging
-- =============================================================================
-- cinema_segment không có trong CSV → hardcode theo assumptions.md §1
INSERT INTO cinema_dw.dim_cinema (
    cinema_id, cinema_name, district, city, region, cinema_segment
)
SELECT DISTINCT ON (cinema_id)
    cinema_id,
    cinema_name,
    district,
    city,
    'TP. Hồ Chí Minh'                           AS region,
    CASE cinema_id
        WHEN 'CN01' THEN 'premium_destination'
        WHEN 'CN02' THEN 'urban_cbd'
        WHEN 'CN03' THEN 'family_mall'
    END                                          AS cinema_segment
FROM staging.ticket
ORDER BY cinema_id
ON CONFLICT (cinema_id) DO NOTHING;

-- =============================================================================
-- 4. dim_room — 8 phòng, FK → dim_cinema (phải chạy SAU dim_cinema)
-- =============================================================================
INSERT INTO cinema_dw.dim_room (
    room_id, cinema_key, room_name, room_type, capacity
)
SELECT DISTINCT ON (t.room_id)
    t.room_id,
    c.cinema_key,
    t.room_name,
    t.room_type,
    t.capacity
FROM staging.ticket t
JOIN cinema_dw.dim_cinema c ON c.cinema_id = t.cinema_id
ORDER BY t.room_id
ON CONFLICT (room_id) DO NOTHING;

-- =============================================================================
-- 5. dim_movie — 24 phim từ staging (có language, country sau [G] fix)
-- =============================================================================
INSERT INTO cinema_dw.dim_movie (
    movie_id, title, genre, language, duration_min, rating, release_date, country
)
SELECT DISTINCT ON (movie_id)
    movie_id,
    movie_title,
    genre,
    language,
    duration_min::INTEGER,
    rating,
    release_date::DATE,
    country
FROM staging.ticket
ORDER BY movie_id
ON CONFLICT (movie_id) DO NOTHING;

-- =============================================================================
-- 6. dim_customer — 1 dòng GUEST tường minh + 2.500 thành viên từ staging
-- =============================================================================

-- 6a. Chèn GUEST trước (khóa cố định, is_guest = TRUE)
INSERT INTO cinema_dw.dim_customer (
    customer_id, customer_type, is_guest,
    gender, age_group, membership_tier, city, register_date
)
VALUES (
    'GUEST', 'WALK_IN', TRUE,
    NULL, NULL, NULL, NULL, NULL   -- không có thông tin cá nhân
)
ON CONFLICT (customer_id) DO NOTHING;

-- 6b. Thành viên DISTINCT (loại bỏ GUEST, city/register_date = NULL: chưa có nguồn)
INSERT INTO cinema_dw.dim_customer (
    customer_id, customer_type, is_guest,
    gender, age_group, membership_tier, city, register_date
)
SELECT DISTINCT ON (customer_id)
    customer_id,
    customer_type,
    FALSE                           AS is_guest,
    NULLIF(gender, '')              AS gender,
    NULLIF(age_group, '')           AS age_group,
    NULLIF(membership_tier, '')     AS membership_tier,
    NULL                            AS city,            -- chưa có nguồn (xem data_dictionary.md)
    NULL                            AS register_date    -- chưa có nguồn
FROM staging.ticket
WHERE customer_id <> 'GUEST'
ORDER BY customer_id
ON CONFLICT (customer_id) DO NOTHING;

-- =============================================================================
-- 7. dim_product — 8 sản phẩm từ staging.fnb
-- =============================================================================
-- list_price = unit_price của sản phẩm bình thường (lấy giá nhỏ nhất để tránh anomaly price_zero)
INSERT INTO cinema_dw.dim_product (
    product_id, product_name, category, is_combo, list_price
)
SELECT DISTINCT ON (product_id)
    product_id,
    product_name,
    product_category                AS category,
    (is_combo = 1)                  AS is_combo,  -- SMALLINT 0/1 → BOOLEAN
    unit_price                      AS list_price
FROM staging.fnb
WHERE is_anomaly = 0               -- tránh lấy giá 0 từ anomaly price_zero
ORDER BY product_id, unit_price DESC
ON CONFLICT (product_id) DO NOTHING;

-- =============================================================================
-- 8. dim_pricing — chính sách giá từ DISTINCT (ticket_format, day_type,
--                  time_slot, customer_type, discount_rate) trong staging
-- =============================================================================
-- pricing_id = ticket_format|day_type|time_slot|customer_type|discount_rate (4 chữ số thập phân)
-- Ví dụ: IMAX|holiday|Chiều|WALK_IN|0.0000
--
-- discount_name theo quy tắc [D]:
--   Sáng + MEMBER → 'Suất sớm + Hội viên'
--   Sáng + WALK_IN → 'Suất sớm'
--   khác + MEMBER  → 'Hội viên'
--   còn lại → NULL
--
-- Chỉ lấy dòng is_anomaly = 0 để tránh giá bất thường ảnh hưởng dim_pricing.
INSERT INTO cinema_dw.dim_pricing (
    pricing_id, ticket_format, day_type, time_slot,
    customer_type, list_price, discount_rate, discount_name,
    effective_from, effective_to
)
SELECT DISTINCT ON (pricing_id)
    ticket_format || '|' || day_type || '|' || time_slot
        || '|' || customer_type
        || '|' || TO_CHAR(discount_rate, 'FM0.0000') AS pricing_id,
    ticket_format,
    day_type,
    time_slot,
    customer_type,
    list_price,
    discount_rate,
    CASE
        WHEN time_slot = 'Sáng' AND customer_type = 'MEMBER'  THEN 'Suất sớm + Hội viên'
        WHEN time_slot = 'Sáng'                                THEN 'Suất sớm'
        WHEN customer_type = 'MEMBER'                          THEN 'Hội viên'
        ELSE NULL
    END                                                         AS discount_name,
    '2025-10-01'::DATE                                          AS effective_from,
    NULL                                                        AS effective_to
FROM staging.ticket
WHERE is_anomaly = 0
ORDER BY pricing_id
ON CONFLICT (pricing_id) DO NOTHING;

-- =============================================================================
-- 9. dim_channel — POS / APP / WEB
-- =============================================================================
-- channel_name và channel_group hardcode theo assumptions.md §2.1 (không có trong CSV)
INSERT INTO cinema_dw.dim_channel (
    channel_code, channel_name, channel_group
)
SELECT DISTINCT
    channel_code,
    CASE channel_code
        WHEN 'POS' THEN 'Quầy / máy POS tại rạp'
        WHEN 'APP' THEN 'Ứng dụng điện thoại'
        WHEN 'WEB' THEN 'Website đặt vé'
        ELSE channel_code
    END AS channel_name,
    CASE channel_code
        WHEN 'POS' THEN 'Offline'
        ELSE            'Online'
    END AS channel_group
FROM staging.ticket
ON CONFLICT (channel_code) DO NOTHING;

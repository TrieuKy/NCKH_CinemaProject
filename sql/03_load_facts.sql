-- =============================================================================
-- sql/03_load_facts.sql
-- Nạp fact_ticket và fact_fnb từ staging bằng SQL set-based (JOIN tra khóa dim).
--
-- Chiến lược idempotent: TRUNCATE ... RESTART IDENTITY CASCADE trước mỗi lần nạp.
--
-- Lý do chọn TRUNCATE thay UPSERT:
--   - fact_ticket và fact_fnb không có natural key duy nhất nào có thể làm
--     ON CONFLICT target ngoài ticket_id / fnb_line_id.
--   - TRUNCATE + INSERT nhanh hơn UPSERT ~3–5× trên tập dữ liệu lớn.
--   - CASCADE đảm bảo reset surrogate key sequence (ticket_sk, fnb_sk).
--   - Staging đã là nguồn duy nhất → TRUNCATE an toàn trong pipeline batch.
-- =============================================================================

SET search_path TO cinema_dw, staging, public;

-- ---------------------------------------------------------------------------
-- Xóa và reset sequence surrogate key
-- ---------------------------------------------------------------------------
TRUNCATE cinema_dw.fact_ticket RESTART IDENTITY CASCADE;
TRUNCATE cinema_dw.fact_fnb    RESTART IDENTITY CASCADE;

-- ---------------------------------------------------------------------------
-- fact_ticket: JOIN 8 dim để tra surrogate key
-- ---------------------------------------------------------------------------
-- Tra pricing_key: dùng ROUND(x::numeric, 4) ở cả hai phía để tránh sai số float.
-- net_amount = paid_amount từ staging (giữ nguyên, kể cả revenue_mismatch).
-- is_anomaly và anomaly_type giữ nguyên từ staging (không loại dòng anomaly).
INSERT INTO cinema_dw.fact_ticket (
    date_key, time_key, movie_key, cinema_key, room_key,
    customer_key, pricing_key, channel_key,
    txn_id, ticket_id, seat_code,
    qty, list_price, discount_amount, net_amount,
    is_anomaly, anomaly_type
)
SELECT
    -- Tra date_key từ show_date (TEXT → DATE → INTEGER YYYYMMDD)
    TO_CHAR(t.show_date::DATE, 'YYYYMMDD')::INTEGER         AS date_key,

    -- Tra time_key từ show_time (HHMM)
    EXTRACT(HOUR   FROM t.show_time::TIME)::INTEGER * 100
    + EXTRACT(MINUTE FROM t.show_time::TIME)::INTEGER        AS time_key,

    dm.movie_key,
    dc.cinema_key,
    dr.room_key,
    cu.customer_key,

    -- Tra pricing_key: so sánh discount_rate với ROUND để tránh lỗi float
    dp.pricing_key,

    ch.channel_key,

    t.txn_id,
    t.ticket_id,
    t.seat_code,
    t.qty,
    t.list_price,
    t.discount_amount,
    t.paid_amount                                            AS net_amount,
    t.is_anomaly,
    NULLIF(t.anomaly_type, '')                               AS anomaly_type

FROM staging.ticket t

-- JOIN dim_date (date_key tra nhanh vì là integer)
JOIN cinema_dw.dim_date dd
    ON dd.date_key = TO_CHAR(t.show_date::DATE, 'YYYYMMDD')::INTEGER

-- JOIN dim_time
JOIN cinema_dw.dim_time dt
    ON dt.time_key = (
        EXTRACT(HOUR   FROM t.show_time::TIME)::INTEGER * 100
        + EXTRACT(MINUTE FROM t.show_time::TIME)::INTEGER
    )

-- JOIN dim_movie
JOIN cinema_dw.dim_movie dm ON dm.movie_id = t.movie_id

-- JOIN dim_cinema
JOIN cinema_dw.dim_cinema dc ON dc.cinema_id = t.cinema_id

-- JOIN dim_room
JOIN cinema_dw.dim_room dr ON dr.room_id = t.room_id

-- JOIN dim_customer
JOIN cinema_dw.dim_customer cu ON cu.customer_id = t.customer_id

-- JOIN dim_pricing: 5 thành phần + ROUND cả hai phía
JOIN cinema_dw.dim_pricing dp
    ON  dp.ticket_format  = t.ticket_format
    AND dp.day_type       = t.day_type
    AND dp.time_slot      = t.time_slot
    AND dp.customer_type  = t.customer_type
    AND ROUND(dp.discount_rate::NUMERIC, 4) = ROUND(t.discount_rate::NUMERIC, 4)

-- JOIN dim_channel
JOIN cinema_dw.dim_channel ch ON ch.channel_code = t.channel_code
;

-- ---------------------------------------------------------------------------
-- fact_fnb: JOIN 6 dim (không có movie_key, room_key, pricing_key)
-- ---------------------------------------------------------------------------
-- txn_id trong fnb phải có trong fact_ticket (tránh F&B mồ côi).
-- Lý do không dùng FK cứng (ghi trong schema): linh hoạt hơn khi load batch.
INSERT INTO cinema_dw.fact_fnb (
    date_key, time_key, cinema_key, customer_key, product_key, channel_key,
    txn_id, fnb_line_id,
    qty, unit_price, line_amount,
    is_anomaly, anomaly_type
)
SELECT
    TO_CHAR(f.sale_date::DATE, 'YYYYMMDD')::INTEGER         AS date_key,

    EXTRACT(HOUR   FROM f.sale_time::TIME)::INTEGER * 100
    + EXTRACT(MINUTE FROM f.sale_time::TIME)::INTEGER        AS time_key,

    dc.cinema_key,
    cu.customer_key,
    dp.product_key,
    ch.channel_key,

    f.txn_id,
    f.fnb_line_id,
    f.qty,
    f.unit_price,
    f.line_amount,
    f.is_anomaly,
    NULLIF(f.anomaly_type, '')                               AS anomaly_type

FROM staging.fnb f

JOIN cinema_dw.dim_date dd
    ON dd.date_key = TO_CHAR(f.sale_date::DATE, 'YYYYMMDD')::INTEGER

JOIN cinema_dw.dim_time dt
    ON dt.time_key = (
        EXTRACT(HOUR   FROM f.sale_time::TIME)::INTEGER * 100
        + EXTRACT(MINUTE FROM f.sale_time::TIME)::INTEGER
    )

JOIN cinema_dw.dim_cinema dc ON dc.cinema_id = f.cinema_id

JOIN cinema_dw.dim_customer cu ON cu.customer_id = f.customer_id

JOIN cinema_dw.dim_product dp ON dp.product_id = f.product_id

JOIN cinema_dw.dim_channel ch ON ch.channel_code = f.channel_code
;

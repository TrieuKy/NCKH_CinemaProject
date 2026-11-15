-- =============================================================================
-- sql/01b_etl_support.sql
-- Tạo schema staging và bảng etl_log để ghi nhật ký mỗi lần chạy ETL.
--
-- Lý do tách file riêng (không gộp vào 01_schema.sql):
--   01_schema.sql = DDL kho dữ liệu (đã ổn định).
--   01b_etl_support.sql = hạ tầng vận hành ETL (có thể mở rộng độc lập).
-- =============================================================================

SET search_path TO cinema_dw, public;

-- ---------------------------------------------------------------------------
-- Schema staging: chứa bảng raw CSV trước khi transform
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS staging;

-- ---------------------------------------------------------------------------
-- Bảng staging.ticket — mirror toàn bộ staging_ticket.csv
-- Ghi đè mỗi lần chạy ETL (TRUNCATE trước INSERT).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.ticket (
    txn_id           TEXT,
    show_date        TEXT,        -- giữ dạng text để load nhanh, cast khi seed dim
    show_time        TEXT,
    cinema_id        TEXT,
    cinema_name      TEXT,
    district         TEXT,
    city             TEXT,
    room_id          TEXT,
    room_name        TEXT,
    room_type        TEXT,
    capacity         INTEGER,
    movie_id         TEXT,
    movie_title      TEXT,
    genre            TEXT,
    duration_min     INTEGER,
    rating           TEXT,
    release_date     TEXT,
    language         TEXT,
    country          TEXT,
    customer_id      TEXT,
    customer_type    TEXT,
    gender           TEXT,
    age_group        TEXT,
    membership_tier  TEXT,
    register_date    TEXT,
    channel_code     TEXT,
    ticket_format    TEXT,
    day_type         TEXT,
    time_slot        TEXT,
    list_price       NUMERIC(12,0),
    discount_rate    NUMERIC(5,4),
    discount_amount  NUMERIC(12,0),
    paid_amount      NUMERIC(12,0),
    qty              SMALLINT,
    is_holiday       SMALLINT,
    holiday_name     TEXT,
    is_anomaly       SMALLINT,
    anomaly_type     TEXT
);

COMMENT ON TABLE staging.ticket IS 'Mirror staging_ticket.csv. Ghi đè mỗi lần chạy ETL.';

-- ---------------------------------------------------------------------------
-- Bảng staging.fnb — mirror toàn bộ staging_fnb.csv
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.fnb (
    txn_id           TEXT,
    fnb_line_id      TEXT,
    sale_date        TEXT,
    sale_time        TEXT,
    cinema_id        TEXT,
    customer_id      TEXT,
    customer_type    TEXT,
    channel_code     TEXT,
    product_id       TEXT,
    product_name     TEXT,
    product_category TEXT,
    is_combo         SMALLINT,
    qty              SMALLINT,
    unit_price       NUMERIC(12,0),
    line_amount      NUMERIC(12,0),
    is_anomaly       SMALLINT,
    anomaly_type     TEXT
);

COMMENT ON TABLE staging.fnb IS 'Mirror staging_fnb.csv. Ghi đè mỗi lần chạy ETL.';

-- ---------------------------------------------------------------------------
-- Bảng cinema_dw.etl_log — nhật ký mỗi lần chạy ETL
-- Mỗi bước (staging, seed_dim, load_fact, reconcile) ghi 1 dòng.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cinema_dw.etl_log (
    log_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       UUID        NOT NULL,          -- UUID chung cho cả 1 lần chạy run_etl.py
    run_at       TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    step         VARCHAR(60) NOT NULL,          -- staging / seed_dim / load_fact_ticket / ...
    src_rows     BIGINT,                        -- số dòng đọc từ nguồn
    dwh_rows     BIGINT,                        -- số dòng đã nạp vào DWH
    src_amount   NUMERIC(18,0),                 -- tổng tiền nguồn (chỉ bước reconcile)
    dwh_amount   NUMERIC(18,0),                 -- tổng tiền DWH  (chỉ bước reconcile)
    status       VARCHAR(10) NOT NULL           -- OK / FAIL / WARN
                 CHECK (status IN ('OK', 'FAIL', 'WARN')),
    message      TEXT                           -- mô tả lỗi hoặc cảnh báo
);

COMMENT ON TABLE cinema_dw.etl_log IS
    'Nhật ký ETL. Mỗi lần chạy run_etl.py tạo 1 run_id UUID dùng chung cho tất cả bước.';
COMMENT ON COLUMN cinema_dw.etl_log.run_id    IS 'UUID sinh khi bắt đầu run_etl.py.';
COMMENT ON COLUMN cinema_dw.etl_log.step      IS 'staging_ticket | staging_fnb | seed_dims | load_fact_ticket | load_fact_fnb | reconcile';
COMMENT ON COLUMN cinema_dw.etl_log.src_rows  IS 'Số dòng đọc từ CSV hoặc staging.';
COMMENT ON COLUMN cinema_dw.etl_log.dwh_rows  IS 'Số dòng thực sự nạp vào bảng dim/fact.';
COMMENT ON COLUMN cinema_dw.etl_log.src_amount IS 'Tổng tiền tính từ staging (reconcile).';
COMMENT ON COLUMN cinema_dw.etl_log.dwh_amount IS 'Tổng tiền trong fact (reconcile).';

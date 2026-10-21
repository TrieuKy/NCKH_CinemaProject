-- =============================================================================
-- CINEMA DW — Star Schema (PostgreSQL)
-- Giai đoạn 2 | Kimball: Dimension quanh Fact
-- Schema: cinema_dw
--
-- Hạt nhân:
--   fact_ticket : 1 hàng = 1 ghế / 1 vé
--   fact_fnb    : 1 hàng = 1 dòng sản phẩm trong 1 giao dịch
-- Cả hai fact đều có txn_id để nối vé ↔ bắp nước.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS cinema_dw;
SET search_path TO cinema_dw, public;

-- -----------------------------------------------------------------------------
-- DIMENSIONS
-- -----------------------------------------------------------------------------

-- Lịch: một dòng / ngày. date_key dạng YYYYMMDD để JOIN không cần CAST date.
CREATE TABLE dim_date (
    date_key        INTEGER PRIMARY KEY,          -- 20250401
    full_date       DATE        NOT NULL UNIQUE,
    day_of_month    SMALLINT    NOT NULL,
    month_no        SMALLINT    NOT NULL,
    month_name      VARCHAR(20) NOT NULL,
    quarter_no      SMALLINT    NOT NULL,
    year_no         SMALLINT    NOT NULL,
    week_of_year    SMALLINT    NOT NULL,
    day_of_week     SMALLINT    NOT NULL,         -- 1=Thứ 2 … 7=Chủ nhật (ISO)
    day_name        VARCHAR(20) NOT NULL,
    is_weekend      BOOLEAN     NOT NULL,
    is_holiday      BOOLEAN     NOT NULL,
    holiday_name    VARCHAR(80)
);

COMMENT ON TABLE dim_date IS 'Chiều thời gian theo ngày — KPI theo tuần/tháng/lễ.';

-- Giờ trong ngày: 1440 dòng (00:00–23:59). time_key = hour*100 + minute (1930 = 19:30).
CREATE TABLE dim_time (
    time_key        INTEGER PRIMARY KEY,          -- 0–2359 theo HHMM
    time_of_day     TIME        NOT NULL UNIQUE,
    hour_no         SMALLINT    NOT NULL,
    minute_no       SMALLINT    NOT NULL,
    time_slot       VARCHAR(20) NOT NULL,         -- Sáng / Chiều / Tối / Đêm
    is_prime_time   BOOLEAN     NOT NULL          -- 17:00–21:29
);

COMMENT ON TABLE dim_time IS 'Chiều giờ — tách khỏi ngày để phân tích khung giờ vàng.';

CREATE TABLE dim_movie (
    movie_key       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    movie_id        VARCHAR(20)  NOT NULL UNIQUE,
    title           VARCHAR(200) NOT NULL,
    genre           VARCHAR(50)  NOT NULL,
    language        VARCHAR(30)  NOT NULL,
    duration_min    INTEGER      NOT NULL,
    rating          VARCHAR(10)  NOT NULL,        -- P, T13, T16, T18
    release_date    DATE         NOT NULL,
    country         VARCHAR(50)  NOT NULL
);

COMMENT ON TABLE dim_movie IS 'Chiều phim. Cờ "phim mới" tính lúc ETL: show_date - release_date.';

CREATE TABLE dim_cinema (
    cinema_key      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cinema_id       VARCHAR(20)  NOT NULL UNIQUE,
    cinema_name     VARCHAR(120) NOT NULL,
    district        VARCHAR(80)  NOT NULL,
    city            VARCHAR(80)  NOT NULL,
    region          VARCHAR(80)  NOT NULL DEFAULT 'TP. Hồ Chí Minh',
    cinema_segment  VARCHAR(40)  NOT NULL         -- premium_destination / urban_cbd / family_mall
);

COMMENT ON TABLE dim_cinema IS '3 rạp CGV: Landmark 81, Vincom Đồng Khởi, Pearl Plaza.';

CREATE TABLE dim_room (
    room_key        INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id         VARCHAR(20)  NOT NULL UNIQUE,
    cinema_key      INTEGER      NOT NULL REFERENCES dim_cinema (cinema_key),
    room_name       VARCHAR(80)  NOT NULL,
    room_type       VARCHAR(20)  NOT NULL,        -- 2D, 3D, IMAX
    capacity        INTEGER      NOT NULL CHECK (capacity > 0)
);

COMMENT ON TABLE dim_room IS 'Phòng chiếu. Capacity dùng đối soát occupancy và anomaly qty_spike.';

-- Quy ước khách vãng lai: đúng 1 dòng customer_id = 'GUEST', is_guest = TRUE.
-- Thành viên: CUS000001…  |  Không sinh guest giả theo từng giao dịch.
CREATE TABLE dim_customer (
    customer_key      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id       VARCHAR(20)  NOT NULL UNIQUE,
    customer_type     VARCHAR(20)  NOT NULL CHECK (customer_type IN ('MEMBER', 'WALK_IN')),
    is_guest          BOOLEAN      NOT NULL,
    gender            VARCHAR(10),
    age_group         VARCHAR(20),
    membership_tier   VARCHAR(20),               -- Standard / Silver / Gold / NULL với GUEST
    city              VARCHAR(80),
    register_date     DATE
);

COMMENT ON TABLE dim_customer IS 'GUEST = khách không định danh. Dùng cho tỷ lệ thành viên, không dùng cho RFM cá nhân.';

CREATE TABLE dim_product (
    product_key       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id        VARCHAR(20)  NOT NULL UNIQUE,
    product_name      VARCHAR(120) NOT NULL,
    category          VARCHAR(40)  NOT NULL,     -- snack / drink / combo
    is_combo          BOOLEAN      NOT NULL,
    list_price        NUMERIC(12, 0) NOT NULL
);

COMMENT ON TABLE dim_product IS 'Menu bắp nước. Hạt F&B join về đây.';

-- Bảng giá: một dòng = một chính sách giá (format + loại ngày + khung giờ + đối tượng).
CREATE TABLE dim_pricing (
    pricing_key       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pricing_id        VARCHAR(40)  NOT NULL UNIQUE,
    ticket_format     VARCHAR(20)  NOT NULL,     -- 2D / 3D / IMAX
    day_type          VARCHAR(20)  NOT NULL,     -- weekday / weekend / holiday
    time_slot         VARCHAR(20)  NOT NULL,     -- Sáng / Chiều / Tối / Đêm
    customer_type     VARCHAR(20)  NOT NULL,     -- MEMBER / WALK_IN / ALL
    list_price        NUMERIC(12, 0) NOT NULL,
    discount_rate     NUMERIC(5, 4) NOT NULL DEFAULT 0,
    discount_name     VARCHAR(80),
    effective_from    DATE         NOT NULL,
    effective_to      DATE
);

COMMENT ON TABLE dim_pricing IS 'Giá và mức giảm — phục vụ what-if giá ở giai đoạn sau.';

CREATE TABLE dim_channel (
    channel_key       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel_code      VARCHAR(20)  NOT NULL UNIQUE,
    channel_name      VARCHAR(80)  NOT NULL,
    channel_group     VARCHAR(40)  NOT NULL      -- Offline / Online
);

COMMENT ON TABLE dim_channel IS 'POS / APP / WEB — kênh App là hướng mở rộng webview.';

-- -----------------------------------------------------------------------------
-- FACTS
-- -----------------------------------------------------------------------------

CREATE TABLE fact_ticket (
    ticket_sk         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date_key          INTEGER      NOT NULL REFERENCES dim_date (date_key),
    time_key          INTEGER      NOT NULL REFERENCES dim_time (time_key),
    movie_key         INTEGER      NOT NULL REFERENCES dim_movie (movie_key),
    cinema_key        INTEGER      NOT NULL REFERENCES dim_cinema (cinema_key),
    room_key          INTEGER      NOT NULL REFERENCES dim_room (room_key),
    customer_key      INTEGER      NOT NULL REFERENCES dim_customer (customer_key),
    pricing_key       INTEGER      NOT NULL REFERENCES dim_pricing (pricing_key),
    channel_key       INTEGER      NOT NULL REFERENCES dim_channel (channel_key),
    txn_id            VARCHAR(20)  NOT NULL,     -- degenerate: nối fact_fnb
    ticket_id         VARCHAR(20)  NOT NULL UNIQUE,
    seat_code         VARCHAR(10)  NOT NULL,
    qty               SMALLINT     NOT NULL DEFAULT 1 CHECK (qty >= 0),
    list_price        NUMERIC(12, 0) NOT NULL,
    discount_amount   NUMERIC(12, 0) NOT NULL,
    net_amount        NUMERIC(12, 0) NOT NULL,   -- paid_amount trên staging
    is_anomaly        SMALLINT     NOT NULL DEFAULT 0 CHECK (is_anomaly IN (0, 1)),
    anomaly_type      VARCHAR(40),
    loaded_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE fact_ticket IS 'Fact vé — grain 1 ghế. txn_id bắt buộc để đối soát F&B và POS.';
COMMENT ON COLUMN fact_ticket.txn_id IS 'Mã giao dịch POS/APP/WEB, dùng JOIN fact_fnb.';

CREATE INDEX idx_fact_ticket_txn    ON fact_ticket (txn_id);
CREATE INDEX idx_fact_ticket_date   ON fact_ticket (date_key);
CREATE INDEX idx_fact_ticket_movie  ON fact_ticket (movie_key);
CREATE INDEX idx_fact_ticket_cinema ON fact_ticket (cinema_key);
CREATE INDEX idx_fact_ticket_cust   ON fact_ticket (customer_key);
CREATE INDEX idx_fact_ticket_anom   ON fact_ticket (is_anomaly);

CREATE TABLE fact_fnb (
    fnb_sk            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date_key          INTEGER      NOT NULL REFERENCES dim_date (date_key),
    time_key          INTEGER      NOT NULL REFERENCES dim_time (time_key),
    cinema_key        INTEGER      NOT NULL REFERENCES dim_cinema (cinema_key),
    customer_key      INTEGER      NOT NULL REFERENCES dim_customer (customer_key),
    product_key       INTEGER      NOT NULL REFERENCES dim_product (product_key),
    channel_key       INTEGER      NOT NULL REFERENCES dim_channel (channel_key),
    txn_id            VARCHAR(20)  NOT NULL,     -- cùng txn_id với fact_ticket
    fnb_line_id       VARCHAR(20)  NOT NULL UNIQUE,
    qty               SMALLINT     NOT NULL CHECK (qty >= 0),
    unit_price        NUMERIC(12, 0) NOT NULL,
    line_amount       NUMERIC(12, 0) NOT NULL,
    is_anomaly        SMALLINT     NOT NULL DEFAULT 0 CHECK (is_anomaly IN (0, 1)),
    anomaly_type      VARCHAR(40),
    loaded_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE fact_fnb IS 'Fact bắp nước — grain 1 dòng SP / giao dịch. txn_id bắt buộc.';
COMMENT ON COLUMN fact_fnb.txn_id IS 'Khóa nghiệp vụ nối với fact_ticket (không phải FK cứng: một txn có thể chưa có F&B).';

CREATE INDEX idx_fact_fnb_txn     ON fact_fnb (txn_id);
CREATE INDEX idx_fact_fnb_date    ON fact_fnb (date_key);
CREATE INDEX idx_fact_fnb_product ON fact_fnb (product_key);
CREATE INDEX idx_fact_fnb_cinema  ON fact_fnb (cinema_key);
CREATE INDEX idx_fact_fnb_anom    ON fact_fnb (is_anomaly);

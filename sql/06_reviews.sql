-- =============================================================================
-- sql/06_reviews.sql
-- Mô-đun phân tích đánh giá Google Maps — PostgreSQL
-- Schema: cinema_dw
--
-- Bảng:
--   review_import_log  : nhật ký mỗi lần nhập / cào
--   review_source      : Place ID của từng rạp (1 dòng/rạp)
--   review_topic_dict  : từ điển chủ đề (có thể sửa)
--   review_raw         : đánh giá thô (1 dòng/đánh giá)
--   review_nlp         : kết quả NLP (1-1 với review_raw)
--
-- Hàm:
--   fn_review_summary  : tổng hợp theo rạp, kỳ, chủ đề, cảm xúc
--
-- Idempotent: chạy lại nhiều lần không sinh lỗi (CREATE … IF NOT EXISTS,
--   CREATE OR REPLACE FUNCTION, INSERT … ON CONFLICT DO NOTHING).
-- =============================================================================

SET search_path TO cinema_dw, public;

-- =============================================================================
-- 1. review_import_log — nhật ký mỗi lần nhập/cào dữ liệu
-- =============================================================================
CREATE TABLE IF NOT EXISTS cinema_dw.review_import_log (
    import_id       BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cinema_key      INTEGER     REFERENCES cinema_dw.dim_cinema(cinema_key),  -- NULL = toàn bộ / migration
    source          VARCHAR(40) NOT NULL,          -- 'APIFY_CRAWLER' | 'SQLITE_MIGRATION' | 'MANUAL'
    actor_name      VARCHAR(120),                  -- tên Apify Actor, NULL nếu không phải Apify
    apify_run_id    VARCHAR(80),                   -- run ID trả về từ Apify
    dataset_id      VARCHAR(80),                   -- dataset ID Apify
    raw_file_path   TEXT,                          -- đường dẫn file nếu nhập từ file
    rows_total      INTEGER NOT NULL DEFAULT 0,
    rows_new        INTEGER NOT NULL DEFAULT 0,
    rows_dup        INTEGER NOT NULL DEFAULT 0,    -- trùng review_id, bỏ qua
    rows_skipped    INTEGER NOT NULL DEFAULT 0,    -- thiếu ngày / thiếu cả sao lẫn chữ
    message         TEXT
);

COMMENT ON TABLE cinema_dw.review_import_log IS
    'Nhật ký mỗi lần cào hoặc nhập đánh giá. Một dòng = một lần chạy.';

-- =============================================================================
-- 2. review_source — Place ID Google Maps của từng rạp
-- =============================================================================
CREATE TABLE IF NOT EXISTS cinema_dw.review_source (
    source_id           SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cinema_key          INTEGER  NOT NULL UNIQUE
                                 REFERENCES cinema_dw.dim_cinema(cinema_key),
    google_place_id     VARCHAR(100),              -- NULL khi chưa có Place ID
    maps_url            TEXT,
    is_active           BOOLEAN  NOT NULL DEFAULT FALSE,
    note                TEXT,
    -- Khi is_active=TRUE, Place ID bắt buộc phải tồn tại
    CONSTRAINT ck_review_source_active_needs_place_id
        CHECK (is_active = FALSE OR google_place_id IS NOT NULL)
);

COMMENT ON TABLE cinema_dw.review_source IS
    'Một dòng / rạp. is_active=TRUE mới được crawler chọn. '
    'CN02, CN03 để NULL và is_active=FALSE cho đến khi có Place ID.';

-- Seed: chỉ Landmark 81 (CN01) có Place ID và bật active
-- CN02, CN03: google_place_id=NULL, is_active=FALSE
INSERT INTO cinema_dw.review_source (cinema_key, google_place_id, maps_url, is_active, note)
SELECT
    c.cinema_key,
    CASE c.cinema_id
        WHEN 'CN01' THEN 'ChIJn5Uzw-8pdTERyxTk1eLXCjc'
        ELSE NULL
    END                         AS google_place_id,
    CASE c.cinema_id
        WHEN 'CN01' THEN 'https://www.google.com/maps/search/CGV+Vincom+Center+Landmark+81'
        ELSE NULL
    END                         AS maps_url,
    (c.cinema_id = 'CN01')      AS is_active,
    c.cinema_name               AS note
FROM cinema_dw.dim_cinema c
ON CONFLICT (cinema_key) DO NOTHING;

-- =============================================================================
-- 3. review_topic_dict — từ điển chủ đề (có thể sửa từ SQL hoặc UI)
-- =============================================================================
CREATE TABLE IF NOT EXISTS cinema_dw.review_topic_dict (
    topic_id    SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    topic_name  VARCHAR(80) NOT NULL UNIQUE,
    keywords    TEXT[]      NOT NULL DEFAULT '{}',
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE cinema_dw.review_topic_dict IS
    'Từ điển chủ đề. Sửa keywords → chạy lại reviews/analyze.py để phân tích lại. '
    'Chủ đề "Khác" là fallback khi không khớp bất kỳ chủ đề nào.';

-- Seed 5 chủ đề vận hành + 1 chủ đề fallback
-- Bỏ từ quá chung: "rạp", "vé", "nước", "tối", "tiền"
INSERT INTO cinema_dw.review_topic_dict (topic_name, keywords) VALUES
    ('Hàng chờ & Bắp nước',
     ARRAY['bắp','popcorn','xếp hàng','quầy','combo','order','thanh toán',
            'đồ ăn','nước ngọt','phô mai','caramel','bỏng ngô','chờ đợi',
            'máy tính tiền','thu ngân','xếp hàng lâu','quầy bắp','hàng đợi','lấy bắp']),
    ('Giá cả & Vé',
     ARRAY['giá','đắt','mắc','khuyến mãi','voucher','đắt đỏ','phụ thu',
            'giá cả','chi phí','giảm giá','u22','ghế vip','vé phim','tốn kém',
            'chát','bảng giá','giá vé']),
    ('Phòng chiếu & Âm thanh',
     ARRAY['màn hình','loa','âm thanh','ghế','lạnh','máy lạnh',
            'mờ','phòng chiếu','máy chiếu','âm lượng','nhiệt độ','rét','rung',
            'imax','gold class','starium','cúp điện','hình ảnh','chói','nhức mắt',
            'điều hoà','ghế ngồi','mùi máy lạnh']),
    ('Thái độ phục vụ',
     ARRAY['nhân viên','thái độ','bảo vệ','soát vé','phục vụ','quản lý','cọc cằn',
            'khó chịu','nhiệt tình','hỗ trợ','lịch sự','thân thiện','chậm chạp',
            'coi thường','quát','mặt mày','tư vấn','chăm sóc khách hàng']),
    ('Vệ sinh',
     ARRAY['dơ','bẩn','mùi','rác','hôi','dơ dáy','toilet','nhà vệ sinh','wc',
            'sàn','dính','bốc mùi','ẩm mốc','giấy lau','giấy vệ sinh','lau dọn',
            'rác rưởi','vết bẩn','dơ bẩn','hôi hám'])
ON CONFLICT (topic_name) DO NOTHING;

-- Fallback "Khác": không có keywords; pipeline gán khi không khớp chủ đề nào
INSERT INTO cinema_dw.review_topic_dict (topic_name, keywords, is_active)
VALUES ('Khác', '{}', TRUE)
ON CONFLICT (topic_name) DO NOTHING;

-- =============================================================================
-- 4. review_raw — đánh giá thô (1 dòng / đánh giá)
-- =============================================================================
CREATE TABLE IF NOT EXISTS cinema_dw.review_raw (
    raw_id          BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id       TEXT    NOT NULL UNIQUE,
    import_id       BIGINT  REFERENCES cinema_dw.review_import_log(import_id),
    cinema_key      INTEGER NOT NULL REFERENCES cinema_dw.dim_cinema(cinema_key),
    rating          NUMERIC(2,1) CHECK (rating BETWEEN 1.0 AND 5.0),  -- NULL nếu thiếu
    review_text     TEXT,
    -- generated column: TRUE khi review_text không NULL và không rỗng
    has_text        BOOLEAN NOT NULL GENERATED ALWAYS AS
                        (review_text IS NOT NULL AND review_text <> '') STORED,
    lang            VARCHAR(20),                   -- 'vi' | 'unknown' | giá trị từ Apify
    published_date  DATE    NOT NULL,              -- chuẩn hóa về DATE; bỏ dòng nếu thiếu
    likes_count     INTEGER NOT NULL DEFAULT 0,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_processed    BOOLEAN NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE cinema_dw.review_raw IS
    'Đánh giá thô sau khi cào về. Grain: 1 dòng = 1 đánh giá. '
    'Không lưu tên / ảnh / URL hồ sơ người đánh giá. '
    'Dòng bị bỏ nếu: thiếu published_date, HOẶC thiếu cả rating lẫn review_text.';
COMMENT ON COLUMN cinema_dw.review_raw.has_text IS
    'Generated column — TRUE khi review_text không NULL và không rỗng.';
COMMENT ON COLUMN cinema_dw.review_raw.lang IS
    'Ưu tiên trường ngôn ngữ từ Apify. Fallback: ký tự tiếng Việt có dấu → vi, ngược lại → unknown.';

CREATE INDEX IF NOT EXISTS idx_review_raw_cinema
    ON cinema_dw.review_raw (cinema_key);
CREATE INDEX IF NOT EXISTS idx_review_raw_date
    ON cinema_dw.review_raw (published_date);
CREATE INDEX IF NOT EXISTS idx_review_raw_unprocessed
    ON cinema_dw.review_raw (is_processed)
    WHERE NOT is_processed;

-- =============================================================================
-- 5. review_nlp — kết quả NLP (1-1 với review_raw)
-- =============================================================================
CREATE TABLE IF NOT EXISTS cinema_dw.review_nlp (
    nlp_id              BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id           TEXT    NOT NULL UNIQUE
                                REFERENCES cinema_dw.review_raw(review_id),
    -- Chiều 1: cảm xúc từ số sao (độc lập với văn bản)
    sentiment_rating    VARCHAR(10)
        CHECK (sentiment_rating IN ('POSITIVE','NEUTRAL','NEGATIVE')),
    -- Chiều 2: cảm xúc từ văn bản (NULL khi has_text=FALSE)
    sentiment_text      VARCHAR(10)
        CHECK (sentiment_text IN ('POSITIVE','NEUTRAL','NEGATIVE')),
    -- Chiều 3: tổng hợp — không bao giờ NULL
    sentiment_final     VARCHAR(10) NOT NULL
        CHECK (sentiment_final IN ('POSITIVE','NEUTRAL','NEGATIVE')),
    sentiment_score     NUMERIC(4,2)
        CHECK (sentiment_score BETWEEN -1.00 AND 1.00),
    topics              TEXT[]  NOT NULL DEFAULT '{}',
    -- Tăng khi thay đổi từ điển hoặc thuật toán → dùng để biết dòng nào cần phân tích lại
    model_version       VARCHAR(40) NOT NULL DEFAULT 'v1',
    analyzed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE cinema_dw.review_nlp IS
    'Kết quả NLP. Khi từ điển thay đổi: tăng model_version rồi chạy lại reviews/analyze.py '
    'để hệ thống phân tích lại các dòng có model_version cũ hơn.';
COMMENT ON COLUMN cinema_dw.review_nlp.sentiment_final IS
    '60% rating + 40% text nếu có cả hai; nếu chỉ có rating → dùng rating; '
    'nếu chỉ có text → dùng text. Không bao giờ NULL.';

CREATE INDEX IF NOT EXISTS idx_review_nlp_sentiment
    ON cinema_dw.review_nlp (sentiment_final);
CREATE INDEX IF NOT EXISTS idx_review_nlp_topics
    ON cinema_dw.review_nlp USING GIN (topics);
CREATE INDEX IF NOT EXISTS idx_review_nlp_model_ver
    ON cinema_dw.review_nlp (model_version);

-- =============================================================================
-- 6. fn_review_summary — tổng hợp theo nhiều bộ lọc
-- =============================================================================
-- Tham số:
--   p_cinema_keys  INTEGER[] : danh sách cinema_key lọc; NULL = tất cả rạp
--   p_from         DATE      : ngày bắt đầu (bao gồm); NULL = không giới hạn dưới
--   p_to           DATE      : ngày kết thúc (bao gồm); NULL = không giới hạn trên
--   p_grain        TEXT      : 'day' | 'week' | 'month' | 'year'
--                              Giá trị sai → RAISE EXCEPTION với thông báo rõ ràng
--   p_sentiments   TEXT[]    : lọc sentiment_final; NULL = tất cả
--   p_topics       TEXT[]    : lọc theo chủ đề (đánh giá có ÍT NHẤT 1 chủ đề trong danh sách)
--                              NULL = tất cả
-- Kết quả:
--   period_start   DATE      : ngày đầu kỳ (DATE_TRUNC cast về DATE)
--   n              INTEGER   : số đánh giá trong kỳ (sau bộ lọc)
--   du_du_lieu     BOOLEAN   : n >= 10; chỉ hiển thị đề xuất khi TRUE
--   avg_rating     NUMERIC   : trung bình số sao (NULL nếu không dòng nào có rating)
--   pct_positive   NUMERIC   : % POSITIVE
--   pct_neutral    NUMERIC   : % NEUTRAL
--   pct_negative   NUMERIC   : % NEGATIVE
--   n_has_text     INTEGER   : số đánh giá có văn bản
-- =============================================================================
CREATE OR REPLACE FUNCTION cinema_dw.fn_review_summary(
    p_cinema_keys  INTEGER[] DEFAULT NULL,
    p_from         DATE      DEFAULT NULL,
    p_to           DATE      DEFAULT NULL,
    p_grain        TEXT      DEFAULT 'month',
    p_sentiments   TEXT[]    DEFAULT NULL,
    p_topics       TEXT[]    DEFAULT NULL
)
RETURNS TABLE (
    period_start   DATE,
    n              INTEGER,
    du_du_lieu     BOOLEAN,
    avg_rating     NUMERIC(4,2),
    pct_pos_reviews     NUMERIC(5,2),
    pct_neutral_reviews NUMERIC(5,2),
    pct_neg_reviews     NUMERIC(5,2),
    n_has_text          INTEGER
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    -- Kiểm tra p_grain hợp lệ; sai → báo lỗi ngay, không im lặng
    IF p_grain NOT IN ('day', 'week', 'month', 'year') THEN
        RAISE EXCEPTION
            'p_grain không hợp lệ: "%". Chỉ chấp nhận: day, week, month, year.',
            p_grain
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    SELECT
        DATE_TRUNC(p_grain, rr.published_date)::DATE        AS period_start,
        COUNT(*)::INTEGER                                    AS n,
        (COUNT(*) >= 10)                                     AS du_du_lieu,
        ROUND(AVG(rr.rating)::NUMERIC, 2)                   AS avg_rating,
        ROUND(
            100.0 * COUNT(*) FILTER (WHERE nl.sentiment_final = 'POSITIVE')
            / NULLIF(COUNT(*), 0), 2
        )                                                    AS pct_pos_reviews,
        ROUND(
            100.0 * COUNT(*) FILTER (WHERE nl.sentiment_final = 'NEUTRAL')
            / NULLIF(COUNT(*), 0), 2
        )                                                    AS pct_neutral_reviews,
        ROUND(
            100.0 * COUNT(*) FILTER (WHERE nl.sentiment_final = 'NEGATIVE')
            / NULLIF(COUNT(*), 0), 2
        )                                                    AS pct_neg_reviews,
        COUNT(*) FILTER (WHERE rr.has_text)::INTEGER        AS n_has_text
    FROM cinema_dw.review_raw   rr
    JOIN cinema_dw.review_nlp   nl USING (review_id)
    WHERE
        -- Lọc rạp: NULL = tất cả
        (p_cinema_keys IS NULL OR rr.cinema_key = ANY(p_cinema_keys))
        -- Lọc ngày
        AND (p_from IS NULL OR rr.published_date >= p_from)
        AND (p_to   IS NULL OR rr.published_date <= p_to)
        -- Lọc cảm xúc: NULL = tất cả
        AND (p_sentiments IS NULL OR nl.sentiment_final = ANY(p_sentiments))
        -- Lọc chủ đề: giao khác rỗng (đánh giá có ÍT NHẤT 1 chủ đề trong danh sách)
        AND (p_topics IS NULL OR nl.topics && p_topics)
    GROUP BY DATE_TRUNC(p_grain, rr.published_date)
    ORDER BY 1;
END;
$$;

COMMENT ON FUNCTION cinema_dw.fn_review_summary IS
    'Tổng hợp đánh giá theo kỳ với bộ lọc đầy đủ. '
    'p_grain: day|week|month|year — sai thì raise exception. '
    'NULL = bỏ qua bộ lọc đó. du_du_lieu=TRUE khi n>=10.';

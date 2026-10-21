-- =============================================================================
-- sql/05_app_tables.sql
-- Tạo các bảng hỗ trợ Data Entry App cho nhân viên (B5b).
-- Đảm bảo tính toàn vẹn (Constraints, UNIQUE, CHECK).
-- =============================================================================

SET search_path TO cinema_dw, public;

-- 1. Bảng app_user (Quản lý tài khoản đăng nhập)
CREATE TABLE IF NOT EXISTS cinema_dw.app_user (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL, -- Chỉ lưu hash bcrypt
    role VARCHAR(20) NOT NULL CHECK (role IN ('STAFF', 'MANAGER')),
    cinema_key INTEGER,                  -- NULL nếu là MANAGER tổng
    is_active BOOLEAN DEFAULT TRUE,
    CONSTRAINT fk_app_user_cinema FOREIGN KEY (cinema_key) REFERENCES cinema_dw.dim_cinema(cinema_key)
);

-- 2. Bảng showtime_schedule (Lịch chiếu dự kiến)
CREATE TABLE IF NOT EXISTS cinema_dw.showtime_schedule (
    schedule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    show_date DATE NOT NULL,
    show_time TIME NOT NULL,
    cinema_key INTEGER NOT NULL,
    room_key INTEGER NOT NULL,
    movie_key INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_showtime_schedule UNIQUE (show_date, show_time, room_key),
    CONSTRAINT fk_schedule_cinema FOREIGN KEY (cinema_key) REFERENCES cinema_dw.dim_cinema(cinema_key),
    CONSTRAINT fk_schedule_room FOREIGN KEY (room_key) REFERENCES cinema_dw.dim_room(room_key),
    CONSTRAINT fk_schedule_movie FOREIGN KEY (movie_key) REFERENCES cinema_dw.dim_movie(movie_key)
);

-- 3. Bảng fact_showtime_summary (Tổng kết từng suất chiếu)
CREATE TABLE IF NOT EXISTS cinema_dw.fact_showtime_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_key INTEGER NOT NULL,
    time_key INTEGER NOT NULL,
    cinema_key INTEGER NOT NULL,
    room_key INTEGER NOT NULL,
    movie_key INTEGER NOT NULL,
    tickets_sold INTEGER NOT NULL CHECK (tickets_sold >= 0),
    ticket_revenue NUMERIC(18,0) NOT NULL CHECK (ticket_revenue >= 0),
    tickets_sold_clean INTEGER,          -- Chỉ có ở MOCK, MANUAL sẽ NULL
    ticket_revenue_clean NUMERIC(18,0),  -- Chỉ có ở MOCK, MANUAL sẽ NULL
    source VARCHAR(20) NOT NULL CHECK (source IN ('MOCK', 'MANUAL')),
    entered_by UUID,
    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_locked BOOLEAN DEFAULT FALSE,
    CONSTRAINT uk_fact_showtime_summary UNIQUE (date_key, time_key, room_key),
    CONSTRAINT fk_summ_show_date FOREIGN KEY (date_key) REFERENCES cinema_dw.dim_date(date_key),
    CONSTRAINT fk_summ_show_time FOREIGN KEY (time_key) REFERENCES cinema_dw.dim_time(time_key),
    CONSTRAINT fk_summ_show_cinema FOREIGN KEY (cinema_key) REFERENCES cinema_dw.dim_cinema(cinema_key),
    CONSTRAINT fk_summ_show_room FOREIGN KEY (room_key) REFERENCES cinema_dw.dim_room(room_key),
    CONSTRAINT fk_summ_show_movie FOREIGN KEY (movie_key) REFERENCES cinema_dw.dim_movie(movie_key),
    CONSTRAINT fk_summ_show_user FOREIGN KEY (entered_by) REFERENCES cinema_dw.app_user(user_id)
);

-- 4. Bảng fact_daily_cinema_summary (Tổng kết bắp nước theo ngày)
CREATE TABLE IF NOT EXISTS cinema_dw.fact_daily_cinema_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_key INTEGER NOT NULL,
    cinema_key INTEGER NOT NULL,
    ticket_txns INTEGER NOT NULL CHECK (ticket_txns >= 0),
    fnb_txns INTEGER NOT NULL CHECK (fnb_txns >= 0),
    fnb_revenue NUMERIC(18,0) NOT NULL CHECK (fnb_revenue >= 0),
    source VARCHAR(20) NOT NULL CHECK (source IN ('MOCK', 'MANUAL')),
    entered_by UUID,
    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_locked BOOLEAN DEFAULT FALSE,
    CONSTRAINT uk_fact_daily_cinema_summary UNIQUE (date_key, cinema_key),
    CONSTRAINT ck_daily_txns CHECK (fnb_txns <= ticket_txns),
    CONSTRAINT fk_summ_daily_date FOREIGN KEY (date_key) REFERENCES cinema_dw.dim_date(date_key),
    CONSTRAINT fk_summ_daily_cinema FOREIGN KEY (cinema_key) REFERENCES cinema_dw.dim_cinema(cinema_key),
    CONSTRAINT fk_summ_daily_user FOREIGN KEY (entered_by) REFERENCES cinema_dw.app_user(user_id)
);

-- 5. Bảng import_log (Nhật ký upload/nhập dữ liệu thủ công)
CREATE TABLE IF NOT EXISTS cinema_dw.import_log (
    import_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    imported_by UUID,
    source VARCHAR(50) NOT NULL,
    file_name VARCHAR(255),
    rows_total INTEGER NOT NULL DEFAULT 0,
    rows_ok INTEGER NOT NULL DEFAULT 0,
    rows_error INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    CONSTRAINT fk_import_log_user FOREIGN KEY (imported_by) REFERENCES cinema_dw.app_user(user_id)
);

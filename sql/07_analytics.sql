-- =============================================================================
-- sql/07_analytics.sql
-- Schema: cinema_dw
-- Analytics, ML models, Anomaly Alerts, Recommendations & What-if Simulation
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS cinema_dw;
SET search_path TO cinema_dw, public;

-- 1. rfm_result: Kết quả phân khúc RFM theo từng khách hàng thành viên
CREATE TABLE IF NOT EXISTS cinema_dw.rfm_result (
    customer_key      INTEGER PRIMARY KEY REFERENCES cinema_dw.dim_customer(customer_key),
    customer_id       VARCHAR(20) NOT NULL,
    recency_days      INTEGER NOT NULL,
    frequency         INTEGER NOT NULL,
    monetary          NUMERIC(15, 2) NOT NULL,
    tenure_days       INTEGER DEFAULT 0,
    r_score           SMALLINT,
    f_score           SMALLINT,
    m_score           SMALLINT,
    rfm_score         VARCHAR(10),
    cluster_id        INTEGER NOT NULL,
    cluster_name      VARCHAR(50) NOT NULL,
    calculated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rfm_result_cluster ON cinema_dw.rfm_result(cluster_id);
CREATE INDEX IF NOT EXISTS idx_rfm_result_cust_id ON cinema_dw.rfm_result(customer_id);

COMMENT ON TABLE cinema_dw.rfm_result IS 'Kết quả phân cụm RFM khách hàng thành viên (is_guest = FALSE).';

-- 2. rfm_cluster_profile: Hồ sơ chân dung từng cụm khách hàng
CREATE TABLE IF NOT EXISTS cinema_dw.rfm_cluster_profile (
    cluster_id        INTEGER PRIMARY KEY,
    cluster_name      VARCHAR(50) NOT NULL,
    customer_count    INTEGER NOT NULL,
    pct_customers     NUMERIC(6, 2) NOT NULL,
    avg_recency       NUMERIC(10, 2) NOT NULL,
    avg_frequency     NUMERIC(10, 2) NOT NULL,
    avg_monetary      NUMERIC(15, 2) NOT NULL,
    avg_tenure        NUMERIC(10, 2) DEFAULT 0,
    top_genre         VARCHAR(100),
    combo_rate        NUMERIC(6, 4),
    updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE cinema_dw.rfm_cluster_profile IS 'Hồ sơ cụm khách hàng: RFM trung bình, thể loại xem nhiều nhất và tỷ lệ combo.';

-- 3. model_metrics: Chỉ số đánh giá các mô hình ML (Silhouette, Inertia, RMSE, MAPE...)
CREATE TABLE IF NOT EXISTS cinema_dw.model_metrics (
    metric_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_name        VARCHAR(50) NOT NULL,
    model_version     VARCHAR(50) DEFAULT 'v1.0',
    metric_name       VARCHAR(50) NOT NULL,
    metric_value      NUMERIC(14, 4) NOT NULL,
    params            JSONB,
    evaluated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_model_metrics_name ON cinema_dw.model_metrics(model_name);

COMMENT ON TABLE cinema_dw.model_metrics IS 'Lịch sử đánh giá chất lượng mô hình phân tích và dự báo.';

-- 4. forecast_result: Dự báo doanh thu/vé theo chuỗi thời gian (Prophet)
CREATE TABLE IF NOT EXISTS cinema_dw.forecast_result (
    forecast_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_date       DATE NOT NULL,
    cinema_key        INTEGER REFERENCES cinema_dw.dim_cinema(cinema_key),
    metric_type       VARCHAR(50) NOT NULL DEFAULT 'revenue',
    yhat              NUMERIC(15, 2) NOT NULL,
    yhat_lower        NUMERIC(15, 2),
    yhat_upper        NUMERIC(15, 2),
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_target_date ON cinema_dw.forecast_result(target_date);

COMMENT ON TABLE cinema_dw.forecast_result IS 'Kết quả dự báo chuỗi thời gian Prophet.';

-- 5. anomaly_alerts: Cảnh báo bất thường từ quy tắc hoặc mô hình Isolation Forest
CREATE TABLE IF NOT EXISTS cinema_dw.anomaly_alerts (
    alert_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_date        DATE NOT NULL,
    alert_type        VARCHAR(50) NOT NULL,
    severity          VARCHAR(20) NOT NULL DEFAULT 'MEDIUM' CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    description       TEXT,
    entity_type       VARCHAR(50),
    entity_id         VARCHAR(50),
    status            VARCHAR(20) NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW', 'CONFIRMED', 'DISMISSED')),
    details           JSONB,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_anomaly_alerts_status ON cinema_dw.anomaly_alerts(status);
CREATE INDEX IF NOT EXISTS idx_anomaly_alerts_date ON cinema_dw.anomaly_alerts(alert_date);

COMMENT ON TABLE cinema_dw.anomaly_alerts IS 'Cảnh báo bất thường suất chiếu, F&B với trạng thái NEW/CONFIRMED/DISMISSED.';

-- 6. recommendation: Đề xuất hành động kinh doanh và bằng chứng hỗ trợ
CREATE TABLE IF NOT EXISTS cinema_dw.recommendation (
    rec_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rec_type          VARCHAR(50) NOT NULL,
    target_entity     VARCHAR(50),
    target_id         VARCHAR(50),
    title             VARCHAR(255) NOT NULL,
    action_plan       TEXT,
    priority          VARCHAR(20) NOT NULL DEFAULT 'MEDIUM' CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH')),
    evidence          JSONB NOT NULL,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE cinema_dw.recommendation IS 'Gợi ý tối ưu vận hành kèm bằng chứng phân tích (evidence JSONB).';

-- 7. whatif_run: Lưu các kịch bản mô phỏng giá / khuyến mãi
CREATE TABLE IF NOT EXISTS cinema_dw.whatif_run (
    run_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario_name     VARCHAR(100) NOT NULL,
    params            JSONB NOT NULL,
    result            JSONB NOT NULL,
    run_by            VARCHAR(50),
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE cinema_dw.whatif_run IS 'Lịch sử chạy mô phỏng what-if với tham số params và kết quả result dạng JSONB.';


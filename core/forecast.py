"""
core/forecast.py
-----------------
Dự báo số lượng vé xem phim theo ngày, rạp và khung giờ (time_slot).

Bao gồm:
  1. Trích xuất và tiền xử lý dữ liệu từ cinema_dw.fact_showtime_summary JOIN dim_time.
  2. Phân chia chuỗi thời gian không xáo trộn:
       - Train: <= 2026-07-31
       - Test:  2026-08-01 .. 2026-09-30
  3. Ba mô hình dự báo:
       - Baseline: Cùng thứ tuần trước (lag_7)
       - Prophet: Từng chuỗi (cinema_key x time_slot), tắt yearly_seasonality,
                  ngày lễ lấy từ core.holidays (is_holiday + is_peak_day)
       - XGBoost: lag_7, lag_14, trung bình trượt 7 ngày, thứ trong tuần,
                  is_holiday, is_peak_day, rạp, time_slot
  4. Đánh giá chất lượng mô hình: WAPE, RMSE, MAPE (bỏ giá trị thực = 0).
  5. Dự báo 14 ngày tương lai (is_test = False) kèm khoảng tin cậy [y_lower, y_upper].
  6. Lưu trữ kết quả idempotent vào model_metrics và forecast_result.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from core.holidays import get_official_holidays, get_peak_days

logger = logging.getLogger("etl")

# Ngưỡng phân chia dữ liệu
TRAIN_END_DATE = date(2026, 7, 31)
TEST_START_DATE = date(2026, 8, 1)
TEST_END_DATE = date(2026, 9, 30)
FUTURE_HORIZON_DAYS = 14


# ──────────────────────────────────────────────────────────────────────
# 1. Các chỉ số đánh giá (Metrics)
# ──────────────────────────────────────────────────────────────────────
def calculate_wape(y_true: np.ndarray | pd.Series, y_pred: np.ndarray | pd.Series) -> float:
    """
    Weighted Absolute Percentage Error (WAPE):
        WAPE = sum(|y_true - y_pred|) / sum(y_true)
    """
    y_t = np.asarray(y_true, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    denom = np.sum(y_t)
    if denom == 0:
        return 0.0
    return float(np.sum(np.abs(y_t - y_p)) / denom)


def calculate_rmse(y_true: np.ndarray | pd.Series, y_pred: np.ndarray | pd.Series) -> float:
    """
    Root Mean Squared Error (RMSE):
        RMSE = sqrt(mean((y_true - y_pred)^2))
    """
    y_t = np.asarray(y_true, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    if len(y_t) == 0:
        return 0.0
    return float(np.sqrt(np.mean((y_t - y_p) ** 2)))


def calculate_mape(y_true: np.ndarray | pd.Series, y_pred: np.ndarray | pd.Series) -> float:
    """
    Mean Absolute Percentage Error (MAPE), bỏ các điểm y_true == 0:
        MAPE = mean(|(y_true - y_pred) / y_true|) cho y_true != 0
    """
    y_t = np.asarray(y_true, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    nonzero_mask = y_t != 0
    if not np.any(nonzero_mask):
        return 0.0
    return float(np.mean(np.abs((y_t[nonzero_mask] - y_p[nonzero_mask]) / y_t[nonzero_mask])))


def evaluate_forecast(y_true: np.ndarray | pd.Series, y_pred: np.ndarray | pd.Series) -> Dict[str, float]:
    """Tính toàn bộ 3 chỉ số WAPE, RMSE, MAPE."""
    return {
        "wape": calculate_wape(y_true, y_pred),
        "rmse": calculate_rmse(y_true, y_pred),
        "mape": calculate_mape(y_true, y_pred),
    }


# ──────────────────────────────────────────────────────────────────────
# 2. Trích xuất và phân chia dữ liệu
# ──────────────────────────────────────────────────────────────────────
def load_forecast_data(engine: Engine, cinema_key: Optional[int] = None) -> pd.DataFrame:
    """
    Đọc dữ liệu từ fact_showtime_summary (tickets_sold_clean) JOIN dim_time
    để lấy time_slot, gộp theo ngày x rạp x time_slot.
    Ngày không có suất thì bỏ (không tự điền 0).
    """
    sql = """
    SELECT
        d.full_date AS target_date,
        f.cinema_key,
        t.time_slot,
        SUM(f.tickets_sold_clean) AS tickets_sold
    FROM cinema_dw.fact_showtime_summary f
    JOIN cinema_dw.dim_time t ON f.time_key = t.time_key
    JOIN cinema_dw.dim_date d ON f.date_key = d.date_key
    WHERE (:cinema_key IS NULL OR f.cinema_key = :cinema_key)
    GROUP BY d.full_date, f.cinema_key, t.time_slot
    ORDER BY d.full_date, f.cinema_key, t.time_slot
    """
    with engine.connect() as conn:
        df = pd.read_sql(
            text(sql),
            conn,
            params={"cinema_key": cinema_key},
            parse_dates=["target_date"],
        )
    df["target_date"] = pd.to_datetime(df["target_date"])
    df["tickets_sold"] = df["tickets_sold"].astype(float)
    return df


def split_train_test(
    df: pd.DataFrame,
    train_end: date = TRAIN_END_DATE,
    test_start: date = TEST_START_DATE,
    test_end: date = TEST_END_DATE,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Phân chia chuỗi thời gian không xáo trộn:
      - Train: <= train_end (2026-07-31)
      - Test:  test_start (2026-08-01) .. test_end (2026-09-30)
    """
    train_end_ts = pd.to_datetime(train_end)
    test_start_ts = pd.to_datetime(test_start)
    test_end_ts = pd.to_datetime(test_end)

    train_df = df[df["target_date"] <= train_end_ts].copy()
    test_df = df[(df["target_date"] >= test_start_ts) & (df["target_date"] <= test_end_ts)].copy()

    # Sắp xếp theo ngày tăng dần
    train_df = train_df.sort_values(["target_date", "cinema_key", "time_slot"]).reset_index(drop=True)
    test_df = test_df.sort_values(["target_date", "cinema_key", "time_slot"]).reset_index(drop=True)

    return train_df, test_df


# ──────────────────────────────────────────────────────────────────────
# 3. Lịch ngày lễ từ core.holidays
# ──────────────────────────────────────────────────────────────────────
def get_holidays_dataframe(start_year: int = 2025, end_year: int = 2027) -> pd.DataFrame:
    """Tạo DataFrame ngày lễ (gồm is_holiday và is_peak_day) định dạng cho Prophet."""
    records = []
    for y in range(start_year, end_year + 1):
        for d, name in get_official_holidays(y).items():
            records.append({"holiday": f"Lễ: {name}", "ds": pd.to_datetime(d)})
        for d, name in get_peak_days(y).items():
            records.append({"holiday": f"Cao điểm: {name}", "ds": pd.to_datetime(d)})
    h_df = pd.DataFrame(records).drop_duplicates(subset=["holiday", "ds"]).reset_index(drop=True)
    return h_df


def get_holiday_sets(start_year: int = 2025, end_year: int = 2027) -> Tuple[set[date], set[date]]:
    """Trả về 2 set ngày: (official_holidays, peak_days)."""
    official = set()
    peak = set()
    for y in range(start_year, end_year + 1):
        official.update(get_official_holidays(y).keys())
        peak.update(get_peak_days(y).keys())
    return official, peak


# ──────────────────────────────────────────────────────────────────────
# 4. Mô hình Baseline: Cùng thứ tuần trước (lag_7)
# ──────────────────────────────────────────────────────────────────────
def predict_baseline(train_df: pd.DataFrame, test_df: pd.DataFrame) -> pd.DataFrame:
    """
    Dự báo cùng thứ tuần trước:
    y_pred(t, c, s) = y(t - 7 ngày, c, s).
    Nếu t - 7 không có suất, fallback về trung bình của (c, s) trong tập train.
    """
    # Xây dựng bảng tra cứu lịch sử từ cả train và test
    full_df = pd.concat([train_df, test_df], ignore_index=True)
    history_lookup = {}
    for _, row in full_df.iterrows():
        d = row["target_date"].date() if hasattr(row["target_date"], "date") else row["target_date"]
        history_lookup[(row["cinema_key"], row["time_slot"], d)] = float(row["tickets_sold"])

    train_means = train_df.groupby(["cinema_key", "time_slot"])["tickets_sold"].mean().to_dict()

    yhat = []
    for _, row in test_df.iterrows():
        target_d = row["target_date"].date() if hasattr(row["target_date"], "date") else row["target_date"]
        prev_7_d = target_d - timedelta(days=7)
        val = history_lookup.get((row["cinema_key"], row["time_slot"], prev_7_d))
        if val is None:
            val = train_means.get((row["cinema_key"], row["time_slot"]), 0.0)
        yhat.append(max(0.0, float(val)))

    result = test_df.copy()
    result["yhat"] = np.array(yhat, dtype=float)
    return result


# ──────────────────────────────────────────────────────────────────────
# 5. Mô hình Prophet: Từng chuỗi (cinema_key x time_slot)
# ──────────────────────────────────────────────────────────────────────
def run_prophet_pipeline(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    future_horizon_days: int = FUTURE_HORIZON_DAYS,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Chạy Prophet từng chuỗi (cinema_key x time_slot):
      - Tắt yearly_seasonality (yearly_seasonality=False) vì chỉ có 12 tháng dữ liệu.
      - Ngày lễ lấy từ core.holidays (is_holiday và is_peak_day).
      - Trả về (test_pred_df, future_pred_df).
    """
    # Import cục bộ để tránh overhead nếu chỉ gọi baseline/metrics
    from prophet import Prophet

    min_year = train_df["target_date"].dt.year.min()
    max_year = test_df["target_date"].dt.year.max() + 1
    holidays_df = get_holidays_dataframe(min_year, max_year)

    test_preds_list = []
    future_preds_list = []

    last_data_date = test_df["target_date"].max()
    future_dates = pd.date_range(
        last_data_date + timedelta(days=1),
        periods=future_horizon_days,
        freq="D",
    )

    series_groups = train_df.groupby(["cinema_key", "time_slot"])

    for (c_key, slot), group in series_groups:
        prophet_train = pd.DataFrame({
            "ds": group["target_date"],
            "y": group["tickets_sold"],
        })

        model = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=True,
            daily_seasonality=False,
            holidays=holidays_df,
        )
        # Tắt bớt log cmdstanpy
        import logging as _logging
        _logging.getLogger("cmdstanpy").setLevel(_logging.WARNING)

        model.fit(prophet_train)

        # 1. Dự báo tập test cho chuỗi này
        slot_test = test_df[(test_df["cinema_key"] == c_key) & (test_df["time_slot"] == slot)].copy()
        if len(slot_test) > 0:
            test_ds = pd.DataFrame({"ds": slot_test["target_date"]})
            forecast_test = model.predict(test_ds)
            slot_test["yhat"] = np.clip(forecast_test["yhat"].values, 0, None)
            slot_test["yhat_lower"] = np.clip(forecast_test["yhat_lower"].values, 0, None)
            slot_test["yhat_upper"] = np.clip(forecast_test["yhat_upper"].values, 0, None)
            test_preds_list.append(slot_test)

        # 2. Dự báo 14 ngày tương lai cho chuỗi này
        future_ds = pd.DataFrame({"ds": future_dates})
        forecast_future = model.predict(future_ds)
        future_slot_df = pd.DataFrame({
            "target_date": future_dates,
            "cinema_key": c_key,
            "time_slot": slot,
            "yhat": np.clip(forecast_future["yhat"].values, 0, None),
            "yhat_lower": np.clip(forecast_future["yhat_lower"].values, 0, None),
            "yhat_upper": np.clip(forecast_future["yhat_upper"].values, 0, None),
        })
        future_preds_list.append(future_slot_df)

    test_pred_df = pd.concat(test_preds_list, ignore_index=True)
    future_pred_df = pd.concat(future_preds_list, ignore_index=True)

    return test_pred_df, future_pred_df


# ──────────────────────────────────────────────────────────────────────
# 6. Mô hình XGBoost
# ──────────────────────────────────────────────────────────────────────
def create_xgboost_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sinh các đặc trưng cho XGBoost:
      - lag_7: số vé 7 ngày trước (theo cùng rạp x time_slot)
      - lag_14: số vé 14 ngày trước (theo cùng rạp x time_slot)
      - rolling_mean_7: trung bình trượt 7 ngày trước ngày mục tiêu
      - thứ: thứ trong tuần (day_of_week: 0..6)
      - is_holiday: ngày lễ chính thức (0/1) từ core.holidays
      - is_peak_day: ngày cao điểm (0/1) từ core.holidays
      - rạp: cinema_key (category)
      - time_slot: time_slot (category)
    """
    min_year = df["target_date"].dt.year.min()
    max_year = df["target_date"].dt.year.max() + 1
    official_holidays, peak_days = get_holiday_sets(min_year, max_year)

    all_series = []
    for (ck, slot), group in df.groupby(["cinema_key", "time_slot"]):
        group = group.sort_values("target_date").set_index("target_date")
        full_idx = pd.date_range(group.index.min(), group.index.max(), freq="D")
        reindexed = group.reindex(full_idx)
        reindexed["cinema_key"] = ck
        reindexed["time_slot"] = slot

        # Đặc trưng lag và rolling trượt trên lịch ngày
        reindexed["lag_7"] = reindexed["tickets_sold"].shift(7)
        reindexed["lag_14"] = reindexed["tickets_sold"].shift(14)
        reindexed["rolling_mean_7"] = reindexed["tickets_sold"].shift(1).rolling(7, min_periods=1).mean()

        # Giữ lại các ngày thực tế có suất chiếu ("ngày không có suất thì bỏ")
        valid = reindexed[reindexed["tickets_sold"].notna()].copy()
        valid["target_date"] = valid.index
        all_series.append(valid)

    feat_df = pd.concat(all_series, ignore_index=True)
    feat_df["day_of_week"] = feat_df["target_date"].dt.dayofweek
    feat_df["is_holiday"] = feat_df["target_date"].dt.date.map(lambda d: 1 if d in official_holidays else 0)
    feat_df["is_peak_day"] = feat_df["target_date"].dt.date.map(lambda d: 1 if d in peak_days else 0)
    feat_df["cinema_key"] = feat_df["cinema_key"].astype("category")
    feat_df["time_slot"] = feat_df["time_slot"].astype("category")

    return feat_df


def run_xgboost_pipeline(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    future_horizon_days: int = FUTURE_HORIZON_DAYS,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Chạy pipeline XGBoost:
      1. Tạo đặc trưng (lag_7, lag_14, rolling_mean_7, thứ, is_holiday, is_peak_day, rạp, time_slot).
      2. Huấn luyện mô hình XGBRegressor.
      3. Dự báo trên tập kiểm tra.
      4. Dự báo cuốn chiếu (autoregressive) 14 ngày tương lai.
      5. Ước lượng cận dưới (y_lower) và cận trên (y_upper) theo độ lệch chuẩn phần dư 95%.
    """
    import xgboost as xgb

    full_df = pd.concat([train_df, test_df], ignore_index=True)
    featured_df = create_xgboost_features(full_df)

    feature_cols = [
        "lag_7", "lag_14", "rolling_mean_7",
        "day_of_week", "is_holiday", "is_peak_day",
        "cinema_key", "time_slot",
    ]

    train_data = featured_df[featured_df["target_date"] <= pd.to_datetime(TRAIN_END_DATE)].copy()
    test_data = featured_df[
        (featured_df["target_date"] >= pd.to_datetime(TEST_START_DATE)) &
        (featured_df["target_date"] <= pd.to_datetime(TEST_END_DATE))
    ].copy()

    model = xgb.XGBRegressor(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.08,
        enable_categorical=True,
        random_state=42,
    )
    model.fit(train_data[feature_cols], train_data["tickets_sold"])

    # 1. Dự báo tập test
    test_preds = model.predict(test_data[feature_cols])
    test_pred_df = test_data.copy()
    test_pred_df["yhat"] = np.clip(test_preds, 0, None)

    # Ước lượng residual standard deviation để tạo khoảng tin cậy 95%
    residuals = test_pred_df["tickets_sold"].values - test_pred_df["yhat"].values
    residual_std = float(np.std(residuals)) if len(residuals) > 0 else 10.0
    z_score = 1.96

    test_pred_df["yhat_lower"] = np.maximum(0.0, test_pred_df["yhat"] - z_score * residual_std)
    test_pred_df["yhat_upper"] = test_pred_df["yhat"] + z_score * residual_std

    # 2. Dự báo 14 ngày tương lai (cuốn chiếu ngày x rạp x time_slot)
    last_data_date = test_df["target_date"].max()
    min_year = train_df["target_date"].dt.year.min()
    max_year = last_data_date.year + 1
    official_holidays, peak_days = get_holiday_sets(min_year, max_year)

    # Lịch sử vé theo (cinema_key, time_slot, date) để cập nhật động
    history: Dict[Tuple[int, str, date], float] = {}
    for _, r in full_df.iterrows():
        d = r["target_date"].date() if hasattr(r["target_date"], "date") else r["target_date"]
        history[(r["cinema_key"], r["time_slot"], d)] = float(r["tickets_sold"])

    # Danh sách các tổ hợp (cinema_key, time_slot)
    cinema_slots = train_df[["cinema_key", "time_slot"]].drop_duplicates().values.tolist()

    future_rows = []
    cinema_categories = train_data["cinema_key"].cat.categories
    slot_categories = train_data["time_slot"].cat.categories

    for step in range(1, future_horizon_days + 1):
        cur_date = (last_data_date + timedelta(days=step)).date()
        cur_dt = pd.to_datetime(cur_date)
        dow = cur_dt.dayofweek
        is_hol = 1 if cur_date in official_holidays else 0
        is_pk = 1 if cur_date in peak_days else 0

        for ck, slot in cinema_slots:
            # Tính lag_7 và lag_14
            d_lag7 = cur_date - timedelta(days=7)
            d_lag14 = cur_date - timedelta(days=14)
            lag7_val = history.get((ck, slot, d_lag7), np.nan)
            lag14_val = history.get((ck, slot, d_lag14), np.nan)

            # Tính rolling_mean_7 trên 7 ngày trước đó
            past_vals = [history.get((ck, slot, cur_date - timedelta(days=k))) for k in range(1, 8)]
            past_vals_clean = [v for v in past_vals if v is not None and not np.isnan(v)]
            roll7_val = float(np.mean(past_vals_clean)) if past_vals_clean else lag7_val

            row_dict = {
                "lag_7": lag7_val,
                "lag_14": lag14_val,
                "rolling_mean_7": roll7_val,
                "day_of_week": dow,
                "is_holiday": is_hol,
                "is_peak_day": is_pk,
                "cinema_key": ck,
                "time_slot": slot,
            }
            single_df = pd.DataFrame([row_dict])
            single_df["cinema_key"] = pd.Categorical(single_df["cinema_key"], categories=cinema_categories)
            single_df["time_slot"] = pd.Categorical(single_df["time_slot"], categories=slot_categories)

            pred_val = float(np.clip(model.predict(single_df[feature_cols])[0], 0, None))
            # Cập nhật vào lịch sử để các bước tiếp theo dùng lại làm lag
            history[(ck, slot, cur_date)] = pred_val

            future_rows.append({
                "target_date": cur_dt,
                "cinema_key": ck,
                "time_slot": slot,
                "yhat": pred_val,
                "yhat_lower": max(0.0, pred_val - z_score * residual_std),
                "yhat_upper": pred_val + z_score * residual_std,
            })

    future_pred_df = pd.DataFrame(future_rows)
    return test_pred_df, future_pred_df


# ──────────────────────────────────────────────────────────────────────
# 7. Ghi cơ sở dữ liệu (Idempotent)
# ──────────────────────────────────────────────────────────────────────
def ensure_forecast_tables(engine: Engine) -> None:
    """Đảm bảo bảng forecast_result có các cột cần thiết."""
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE cinema_dw.forecast_result 
            ADD COLUMN IF NOT EXISTS model_name VARCHAR(50),
            ADD COLUMN IF NOT EXISTS time_slot VARCHAR(50),
            ADD COLUMN IF NOT EXISTS is_test BOOLEAN DEFAULT TRUE;
        """))


def save_forecast_to_db(
    engine: Engine,
    metrics_records: List[Dict[str, Any]],
    forecast_records: List[Dict[str, Any]],
    cinema_key: Optional[int] = None,
) -> None:
    """
    Ghi dữ liệu vào cinema_dw.model_metrics và cinema_dw.forecast_result.
    Đảm bảo tính Idempotent ("chạy 2 lần không nhân đôi"):
    Xóa kết quả cũ theo phạm vi trước khi ghi mới.
    """
    ensure_forecast_tables(engine)

    with engine.begin() as conn:
        # 1. Xóa dữ liệu cũ trong model_metrics
        if cinema_key is None:
            conn.execute(text("""
                DELETE FROM cinema_dw.model_metrics 
                WHERE model_name IN ('baseline', 'prophet', 'xgboost')
                   OR model_name LIKE 'forecast_%';
            """))
        else:
            conn.execute(
                text("""
                    DELETE FROM cinema_dw.model_metrics 
                    WHERE (model_name IN ('baseline', 'prophet', 'xgboost')
                           OR model_name LIKE 'forecast_%')
                      AND (params->>'cinema_key')::int = :cinema_key;
                """),
                {"cinema_key": cinema_key},
            )

        # 2. Xóa dữ liệu cũ trong forecast_result
        if cinema_key is None:
            conn.execute(text("""
                DELETE FROM cinema_dw.forecast_result 
                WHERE metric_type = 'tickets';
            """))
        else:
            conn.execute(
                text("""
                    DELETE FROM cinema_dw.forecast_result 
                    WHERE metric_type = 'tickets' AND cinema_key = :cinema_key;
                """),
                {"cinema_key": cinema_key},
            )

        # 3. Ghi mới vào model_metrics
        if metrics_records:
            sql_metric = text("""
                INSERT INTO cinema_dw.model_metrics (
                    model_name, model_version, metric_name, metric_value, params, evaluated_at
                ) VALUES (
                    :model_name, :model_version, :metric_name, :metric_value, :params, :evaluated_at
                );
            """)
            for m in metrics_records:
                conn.execute(sql_metric, {
                    "model_name": m["model_name"],
                    "model_version": m.get("model_version", "v1.0"),
                    "metric_name": m["metric_name"],
                    "metric_value": round(float(m["metric_value"]), 4),
                    "params": json.dumps(m.get("params", {})),
                    "evaluated_at": m.get("evaluated_at", datetime.now()),
                })

        # 4. Ghi mới vào forecast_result
        if forecast_records:
            sql_forecast = text("""
                INSERT INTO cinema_dw.forecast_result (
                    target_date, cinema_key, time_slot, model_name,
                    metric_type, yhat, yhat_lower, yhat_upper, is_test, created_at
                ) VALUES (
                    :target_date, :cinema_key, :time_slot, :model_name,
                    :metric_type, :yhat, :yhat_lower, :yhat_upper, :is_test, :created_at
                );
            """)
            for f in forecast_records:
                conn.execute(sql_forecast, {
                    "target_date": f["target_date"].date() if hasattr(f["target_date"], "date") else f["target_date"],
                    "cinema_key": int(f["cinema_key"]),
                    "time_slot": str(f["time_slot"]),
                    "model_name": str(f["model_name"]),
                    "metric_type": "tickets",
                    "yhat": round(float(f["yhat"]), 2),
                    "yhat_lower": round(float(f["yhat_lower"]), 2) if f.get("yhat_lower") is not None else None,
                    "yhat_upper": round(float(f["yhat_upper"]), 2) if f.get("yhat_upper") is not None else None,
                    "is_test": bool(f["is_test"]),
                    "created_at": f.get("created_at", datetime.now()),
                })


# ──────────────────────────────────────────────────────────────────────
# 8. Hàm điều phối chính (run_forecast)
# ──────────────────────────────────────────────────────────────────────
def run_forecast(engine: Engine, cinema_key: Optional[int] = None) -> Dict[str, Any]:
    """
    Hàm thực thi toàn bộ pipeline dự báo vé (gọi được từ CLI hoặc giao diện):
      - Tải dữ liệu từ DW theo rạp (hoặc toàn bộ rạp).
      - Chia train (<= 2026-07-31) và test (2026-08-01..2026-09-30).
      - Chạy 3 mô hình: Baseline, Prophet, XGBoost.
      - Tính WAPE, RMSE, MAPE cho từng mô hình trên tập kiểm tra.
      - Xác định mô hình tốt nhất (WAPE thấp nhất).
      - Dự báo 14 ngày tương lai (is_test = False) cho mô hình tốt nhất và Prophet.
      - Lưu kết quả vào model_metrics và forecast_result (chạy 2 lần không nhân đôi).
      - Trả về dict tóm tắt kết quả.
    """
    logger.info("Bắt đầu quy trình dự báo vé (cinema_key=%s)...", cinema_key)

    # 1. Tải và phân chia dữ liệu
    df = load_forecast_data(engine, cinema_key=cinema_key)
    if len(df) == 0:
        raise ValueError(f"Không tìm thấy dữ liệu cho cinema_key={cinema_key}")

    train_df, test_df = split_train_test(df)
    logger.info(
        "Tập dữ liệu: Tổng=%d dòng | Train=%d dòng (<= %s) | Test=%d dòng (%s .. %s)",
        len(df), len(train_df), TRAIN_END_DATE, len(test_df), TEST_START_DATE, TEST_END_DATE,
    )

    now = datetime.now()
    metrics_records: List[Dict[str, Any]] = []
    forecast_records: List[Dict[str, Any]] = []

    # 2. Chạy Baseline
    logger.info("Đang chạy mô hình Baseline (cùng thứ tuần trước)...")
    baseline_test = predict_baseline(train_df, test_df)
    m_baseline = evaluate_forecast(baseline_test["tickets_sold"], baseline_test["yhat"])
    logger.info("Baseline: WAPE=%.4f | RMSE=%.2f | MAPE=%.4f", m_baseline["wape"], m_baseline["rmse"], m_baseline["mape"])

    # Tính khoảng tin cậy cho baseline trên tập test
    b_res = baseline_test["tickets_sold"].values - baseline_test["yhat"].values
    b_std = float(np.std(b_res)) if len(b_res) > 0 else 15.0
    baseline_test["yhat_lower"] = np.maximum(0.0, baseline_test["yhat"] - 1.96 * b_std)
    baseline_test["yhat_upper"] = baseline_test["yhat"] + 1.96 * b_std

    for _, row in baseline_test.iterrows():
        forecast_records.append({
            "target_date": row["target_date"],
            "cinema_key": row["cinema_key"],
            "time_slot": row["time_slot"],
            "model_name": "baseline",
            "yhat": row["yhat"],
            "yhat_lower": row["yhat_lower"],
            "yhat_upper": row["yhat_upper"],
            "is_test": True,
            "created_at": now,
        })

    # 3. Chạy Prophet
    logger.info("Đang chạy mô hình Prophet từng chuỗi...")
    prophet_test, prophet_future = run_prophet_pipeline(train_df, test_df, FUTURE_HORIZON_DAYS)
    m_prophet = evaluate_forecast(prophet_test["tickets_sold"], prophet_test["yhat"])
    logger.info("Prophet:  WAPE=%.4f | RMSE=%.2f | MAPE=%.4f", m_prophet["wape"], m_prophet["rmse"], m_prophet["mape"])

    for _, row in prophet_test.iterrows():
        forecast_records.append({
            "target_date": row["target_date"],
            "cinema_key": row["cinema_key"],
            "time_slot": row["time_slot"],
            "model_name": "prophet",
            "yhat": row["yhat"],
            "yhat_lower": row["yhat_lower"],
            "yhat_upper": row["yhat_upper"],
            "is_test": True,
            "created_at": now,
        })

    # 4. Chạy XGBoost
    logger.info("Đang chạy mô hình XGBoost...")
    xgb_test, xgb_future = run_xgboost_pipeline(train_df, test_df, FUTURE_HORIZON_DAYS)
    m_xgb = evaluate_forecast(xgb_test["tickets_sold"], xgb_test["yhat"])
    logger.info("XGBoost:  WAPE=%.4f | RMSE=%.2f | MAPE=%.4f", m_xgb["wape"], m_xgb["rmse"], m_xgb["mape"])

    for _, row in xgb_test.iterrows():
        forecast_records.append({
            "target_date": row["target_date"],
            "cinema_key": row["cinema_key"],
            "time_slot": row["time_slot"],
            "model_name": "xgboost",
            "yhat": row["yhat"],
            "yhat_lower": row["yhat_lower"],
            "yhat_upper": row["yhat_upper"],
            "is_test": True,
            "created_at": now,
        })

    # 5. So sánh và tìm mô hình tốt nhất (tiêu chí WAPE thấp nhất)
    all_metrics = {
        "baseline": m_baseline,
        "prophet": m_prophet,
        "xgboost": m_xgb,
    }
    best_model = min(all_metrics.keys(), key=lambda k: all_metrics[k]["wape"])
    logger.info("Mô hình tốt nhất được chọn: %s (WAPE=%.4f)", best_model, all_metrics[best_model]["wape"])

    # 6. Ghi dự báo tương lai 14 ngày (is_test = False) cho mô hình tốt nhất
    future_df = xgb_future if best_model == "xgboost" else prophet_future
    for _, row in future_df.iterrows():
        forecast_records.append({
            "target_date": row["target_date"],
            "cinema_key": row["cinema_key"],
            "time_slot": row["time_slot"],
            "model_name": best_model,
            "yhat": row["yhat"],
            "yhat_lower": row["yhat_lower"],
            "yhat_upper": row["yhat_upper"],
            "is_test": False,
            "created_at": now,
        })

    # 7. Đóng gói records cho model_metrics
    for model_name, m_dict in all_metrics.items():
        is_best = (model_name == best_model)
        for metric_name, val in m_dict.items():
            metrics_records.append({
                "model_name": model_name,
                "model_version": "v1.0",
                "metric_name": metric_name,
                "metric_value": val,
                "params": {"cinema_key": cinema_key, "is_best": is_best},
                "evaluated_at": now,
            })

    # 8. Lưu vào database idempotent
    save_forecast_to_db(engine, metrics_records, forecast_records, cinema_key=cinema_key)
    logger.info("Đã lưu %d dòng metrics và %d dòng forecast vào DB thành công.", len(metrics_records), len(forecast_records))

    # 9. Tạo bảng tóm tắt
    summary_df = pd.DataFrame([
        {
            "Mô hình": "Baseline (Cùng thứ tuần trước)",
            "WAPE": f"{m_baseline['wape'] * 100:.2f}%",
            "RMSE": f"{m_baseline['rmse']:.2f}",
            "MAPE": f"{m_baseline['mape'] * 100:.2f}%",
            "Tốt hơn Baseline": "-",
        },
        {
            "Mô hình": "Prophet (Từng chuỗi)",
            "WAPE": f"{m_prophet['wape'] * 100:.2f}%",
            "RMSE": f"{m_prophet['rmse']:.2f}",
            "MAPE": f"{m_prophet['mape'] * 100:.2f}%",
            "Tốt hơn Baseline": "CÓ" if m_prophet["wape"] < m_baseline["wape"] else "KHÔNG",
        },
        {
            "Mô hình": "XGBoost (Đặc trưng trượt + lễ)",
            "WAPE": f"{m_xgb['wape'] * 100:.2f}%",
            "RMSE": f"{m_xgb['rmse']:.2f}",
            "MAPE": f"{m_xgb['mape'] * 100:.2f}%",
            "Tốt hơn Baseline": "CÓ" if m_xgb["wape"] < m_baseline["wape"] else "KHÔNG",
        },
    ])

    return {
        "best_model": best_model,
        "metrics": all_metrics,
        "is_prophet_better": bool(m_prophet["wape"] < m_baseline["wape"]),
        "is_xgboost_better": bool(m_xgb["wape"] < m_baseline["wape"]),
        "summary_table": summary_df,
        "test_forecast_count": len(test_df) * 3,
        "future_forecast_count": len(future_df),
    }

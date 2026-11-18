"""
etl/extend_dim_date.py
----------------------
Mở rộng chiều ngày (dim_date) từ 2025-10-01 đến 2027-12-31.
Đồng bộ các cờ ngày lễ (is_holiday), ngày cao điểm (is_peak_day) theo quy tắc nghiệp vụ.
Ghi nhận audit log đầy đủ vào cinema_dw.etl_log theo run_id.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine
from etl.db import get_engine, logger
from core.holidays import get_holidays_range, get_peak_days_range


def extend_dim_date(run_id: Optional[str] = None, engine: Optional[Engine] = None) -> bool:
    if engine is None:
        engine = get_engine()
    if run_id is None:
        run_id = str(uuid.uuid4())
    logger.info("=== EXTEND DIM_DATE TO 2027-12-31 | run_id: %s ===", run_id)
    
    start_date = date(2025, 10, 1)
    end_date = date(2027, 12, 31)
    
    all_holidays = get_holidays_range(2025, 2027)
    all_peak_days = get_peak_days_range(2025, 2027)
    
    inserted_count = 0
    try:
        with engine.begin() as conn:
            d = start_date
            while d <= end_date:
                date_key = int(d.strftime("%Y%m%d"))
                full_date = d
                day_no = d.day
                month_no = d.month
                month_name = d.strftime("%B")
                quarter_no = (d.month - 1) // 3 + 1
                year_no = d.year
                week_no = d.isocalendar()[1]
                day_of_week = d.isoweekday()
                day_name = d.strftime("%A")
                is_weekend = True if day_of_week >= 6 else False
                holiday_name = all_holidays.get(d)
                is_holiday = True if holiday_name else False
                is_peak_day = True if d in all_peak_days else False
                
                sql = """
                    INSERT INTO cinema_dw.dim_date (
                        date_key, full_date, day_of_month, month_no, month_name, 
                        quarter_no, year_no, week_of_year, day_of_week, day_name, 
                        is_weekend, is_holiday, is_peak_day, holiday_name
                    ) VALUES (
                        :date_key, :full_date, :day_no, :month_no, :month_name,
                        :quarter_no, :year_no, :week_no, :day_of_week, :day_name,
                        :is_weekend, :is_holiday, :is_peak_day, :holiday_name
                    )
                    ON CONFLICT (date_key) DO UPDATE SET
                        is_holiday = EXCLUDED.is_holiday,
                        is_peak_day = EXCLUDED.is_peak_day,
                        holiday_name = EXCLUDED.holiday_name;
                """
                conn.execute(text(sql), {
                    "date_key": date_key, "full_date": full_date, "day_no": day_no,
                    "month_no": month_no, "month_name": month_name, "quarter_no": quarter_no,
                    "year_no": year_no, "week_no": week_no, "day_of_week": day_of_week,
                    "day_name": day_name, "is_weekend": is_weekend, "is_holiday": is_holiday,
                    "is_peak_day": is_peak_day, "holiday_name": holiday_name
                })
                inserted_count += 1
                d += timedelta(days=1)

            # Ghi nhận etl_log thành công
            conn.execute(
                text("""
                    INSERT INTO cinema_dw.etl_log 
                        (run_id, step, src_rows, dwh_rows, status, message)
                    VALUES 
                        (:run_id, 'extend_dim_date', :total, :total, 'OK', 'Mở rộng dim_date đến 2027-12-31 thành công')
                """),
                {"run_id": run_id, "total": inserted_count},
            )

        logger.info("=== EXTEND DIM_DATE HOÀN TẤT (%d ngày) ===", inserted_count)
        return True

    except Exception as exc:
        logger.error("Lỗi khi mở rộng dim_date: %s (Đã tự động rollback transaction)", exc)
        try:
            with engine.begin() as conn_fail:
                conn_fail.execute(
                    text("""
                        INSERT INTO cinema_dw.etl_log 
                            (run_id, step, status, message)
                        VALUES 
                            (:run_id, 'extend_dim_date', 'FAIL', :msg)
                    """),
                    {"run_id": run_id, "msg": str(exc)[:500]},
                )
        except Exception as log_err:
            logger.warning("Không thể ghi log thất bại cho extend_dim_date: %s", log_err)
        return False


if __name__ == "__main__":
    ok = extend_dim_date()
    sys.exit(0 if ok else 1)

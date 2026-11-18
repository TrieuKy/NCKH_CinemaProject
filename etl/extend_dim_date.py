import sys
import uuid
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text
from etl.db import get_engine, logger
from core.holidays import get_holidays_range, get_peak_days_range

def extend_dim_date(run_id: Optional[str] = None):
    engine = get_engine()
    if run_id is None:
        run_id = str(uuid.uuid4())
    logger.info("=== EXTEND DIM_DATE TO 2027-12-31 | run_id: %s ===", run_id)
    
    # Generate all dates from 2025-10-01 to 2027-12-31
    start_date = date(2025, 10, 1)
    end_date = date(2027, 12, 31)
    
    all_holidays = get_holidays_range(2025, 2027)
    all_peak_days = get_peak_days_range(2025, 2027)
    
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
            d += timedelta(days=1)
            
    logger.info("=== EXTEND DIM_DATE HOÀN TẤT ===")

if __name__ == "__main__":
    extend_dim_date()

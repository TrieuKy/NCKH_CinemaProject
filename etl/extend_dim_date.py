import sys
from datetime import date, timedelta
from sqlalchemy import text
from etl.db import get_engine, logger
from core.holidays import get_holidays_range

def extend_dim_date():
    engine = get_engine()
    logger.info("=== EXTEND DIM_DATE TO 2030 ===")
    
    # Generate all dates from 2025-04-01 to 2030-12-31
    start_date = date(2025, 4, 1)
    end_date = date(2030, 12, 31)
    
    all_holidays = get_holidays_range(2025, 2030)
    
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
            
            sql = """
                INSERT INTO cinema_dw.dim_date (
                    date_key, full_date, day_of_month, month_no, month_name, 
                    quarter_no, year_no, week_of_year, day_of_week, day_name, 
                    is_weekend, is_holiday, holiday_name
                ) VALUES (
                    :date_key, :full_date, :day_no, :month_no, :month_name,
                    :quarter_no, :year_no, :week_no, :day_of_week, :day_name,
                    :is_weekend, :is_holiday, :holiday_name
                )
                ON CONFLICT (date_key) DO UPDATE SET
                    is_holiday = EXCLUDED.is_holiday,
                    holiday_name = EXCLUDED.holiday_name;
            """
            conn.execute(text(sql), {
                "date_key": date_key, "full_date": full_date, "day_no": day_no,
                "month_no": month_no, "month_name": month_name, "quarter_no": quarter_no,
                "year_no": year_no, "week_no": week_no, "day_of_week": day_of_week,
                "day_name": day_name, "is_weekend": is_weekend, "is_holiday": is_holiday,
                "holiday_name": holiday_name
            })
            d += timedelta(days=1)
            
    logger.info("=== EXTEND DIM_DATE HOÀN TẤT ===")

if __name__ == "__main__":
    extend_dim_date()

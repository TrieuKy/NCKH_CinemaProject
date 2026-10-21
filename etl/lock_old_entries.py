import time
import uuid
from sqlalchemy import text
from etl.db import get_engine, logger
from apscheduler.schedulers.blocking import BlockingScheduler

def lock_entries():
    engine = get_engine()
    run_id = str(uuid.uuid4())
    logger.info("=== BẮT ĐẦU LOCK OLD ENTRIES (run_id: %s) ===", run_id)
    
    with engine.begin() as conn:
        # Lock showtime_summary
        res1 = conn.execute(text("""
            UPDATE cinema_dw.fact_showtime_summary
            SET is_locked = TRUE
            WHERE source = 'MANUAL' AND is_locked = FALSE 
              AND entered_at < CURRENT_TIMESTAMP - INTERVAL '3 days';
        """))
        locked_showtime = res1.rowcount
        
        # Lock daily_cinema_summary
        res2 = conn.execute(text("""
            UPDATE cinema_dw.fact_daily_cinema_summary
            SET is_locked = TRUE
            WHERE source = 'MANUAL' AND is_locked = FALSE 
              AND entered_at < CURRENT_TIMESTAMP - INTERVAL '3 days';
        """))
        locked_daily = res2.rowcount
        
        total_locked = locked_showtime + locked_daily
        msg = f"Đã khóa {locked_showtime} dòng showtime, {locked_daily} dòng daily."
        
        conn.execute(text("""
            INSERT INTO cinema_dw.etl_log 
            (run_id, step, src_rows, dwh_rows, status, message)
            VALUES (:run_id, 'lock_old_entries', :src, :dwh, 'OK', :msg)
        """), {
            "run_id": run_id, "src": total_locked, "dwh": total_locked, "msg": msg
        })
        
        logger.info(msg)
        
    logger.info("=== HOÀN TẤT LOCK OLD ENTRIES ===")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--run-now":
        lock_entries()
        sys.exit(0)
        
    logger.info("Khởi tạo APScheduler chạy lock_entries mỗi ngày lúc 01:00 AM...")
    scheduler = BlockingScheduler()
    scheduler.add_job(lock_entries, 'cron', hour=1, minute=0)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

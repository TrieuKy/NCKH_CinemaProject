"""
etl/aggregate_summary.py
------------------------
Gộp dữ liệu từ fact_ticket và fact_fnb sang các bảng summary phục vụ ứng dụng đầu cuối.
Thực hiện:
- Nạp schema từ sql/05_app_tables.sql
- Tính toán và nạp fact_showtime_summary
- Tính toán và nạp fact_daily_cinema_summary
- Đối soát tổng tickets_sold/ticket_revenue/fnb_revenue với dữ liệu gốc (fact) và ghi etl_log.
"""

import sys
import uuid
from sqlalchemy import text
from etl.db import get_engine, logger

def run_aggregation():
    engine = get_engine()
    run_id = str(uuid.uuid4())
    logger.info("=== BẮT ĐẦU AGGREGATE SUMMARY (run_id: %s) ===", run_id)
    
    with engine.begin() as conn:
        # 0. Thực thi schema table creation
        logger.info("Khởi tạo schema sql/05_app_tables.sql ...")
        with open("sql/05_app_tables.sql", "r", encoding="utf-8") as f:
            for stmt in f.read().split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))

        # Xóa dữ liệu cũ nếu chạy lại (Idempotent)
        conn.execute(text("TRUNCATE cinema_dw.fact_showtime_summary, cinema_dw.fact_daily_cinema_summary CASCADE;"))
        
        # 1. Insert fact_showtime_summary
        logger.info("Đang tính toán và nạp fact_showtime_summary ...")
        conn.execute(text("""
            INSERT INTO cinema_dw.fact_showtime_summary (
                date_key, time_key, cinema_key, room_key, movie_key,
                tickets_sold, ticket_revenue, tickets_sold_clean, ticket_revenue_clean,
                source, is_locked
            )
            SELECT 
                date_key, time_key, cinema_key, room_key, movie_key,
                SUM(qty) AS tickets_sold,
                SUM(net_amount) AS ticket_revenue,
                SUM(CASE WHEN is_anomaly = 0 THEN qty ELSE 0 END) AS tickets_sold_clean,
                SUM(CASE WHEN is_anomaly = 0 THEN net_amount ELSE 0 END) AS ticket_revenue_clean,
                'MOCK', TRUE
            FROM cinema_dw.fact_ticket
            GROUP BY date_key, time_key, cinema_key, room_key, movie_key
        """))
        
        # 2. Insert fact_daily_cinema_summary
        logger.info("Đang tính toán và nạp fact_daily_cinema_summary ...")
        conn.execute(text("""
            WITH t_agg AS (
                SELECT date_key, cinema_key, COUNT(DISTINCT txn_id) as ticket_txns
                FROM cinema_dw.fact_ticket
                GROUP BY date_key, cinema_key
            ),
            f_agg AS (
                SELECT date_key, cinema_key, 
                       COUNT(DISTINCT txn_id) as fnb_txns, 
                       SUM(line_amount) as fnb_revenue
                FROM cinema_dw.fact_fnb
                GROUP BY date_key, cinema_key
            )
            INSERT INTO cinema_dw.fact_daily_cinema_summary (
                date_key, cinema_key, ticket_txns, fnb_txns, fnb_revenue, source, is_locked
            )
            SELECT 
                t.date_key, t.cinema_key, 
                t.ticket_txns, 
                COALESCE(f.fnb_txns, 0), 
                COALESCE(f.fnb_revenue, 0),
                'MOCK', TRUE
            FROM t_agg t
            LEFT JOIN f_agg f ON t.date_key = f.date_key AND t.cinema_key = f.cinema_key
        """))
        
        # 3. Đối soát và ghi etl_log
        queries = [
            {
                "step": "reconcile_summ_tickets_sold",
                "sql": """
                    SELECT 
                        NULL::bigint, NULL::bigint,
                        (SELECT SUM(qty) FROM cinema_dw.fact_ticket) as src_amt,
                        (SELECT SUM(tickets_sold) FROM cinema_dw.fact_showtime_summary) as dwh_amt
                """,
                "check": lambda r: "OK" if r[2] == r[3] else "FAIL",
                "msg": lambda r: "Khớp" if r[2] == r[3] else f"Lệch tickets_sold: fact={r[2]}, summ={r[3]}"
            },
            {
                "step": "reconcile_summ_ticket_revenue",
                "sql": """
                    SELECT 
                        NULL::bigint, NULL::bigint,
                        (SELECT SUM(net_amount) FROM cinema_dw.fact_ticket) as src_amt,
                        (SELECT SUM(ticket_revenue) FROM cinema_dw.fact_showtime_summary) as dwh_amt
                """,
                "check": lambda r: "OK" if r[2] == r[3] else "FAIL",
                "msg": lambda r: "Khớp" if r[2] == r[3] else f"Lệch ticket_revenue: fact={r[2]}, summ={r[3]}"
            },
            {
                "step": "reconcile_summ_fnb_revenue",
                "sql": """
                    SELECT 
                        NULL::bigint, NULL::bigint,
                        (SELECT SUM(line_amount) FROM cinema_dw.fact_fnb) as src_amt,
                        (SELECT SUM(fnb_revenue) FROM cinema_dw.fact_daily_cinema_summary) as dwh_amt
                """,
                "check": lambda r: "OK" if r[2] == r[3] else "FAIL",
                "msg": lambda r: "Khớp" if r[2] == r[3] else f"Lệch fnb_revenue: fact={r[2]}, summ={r[3]}"
            }
        ]
        
        logger.info("Thực hiện kiểm tra đối soát (reconcile)...")
        for q in queries:
            row = conn.execute(text(q["sql"])).fetchone()
            status = q["check"](row)
            msg = q["msg"](row)
            
            conn.execute(text("""
                INSERT INTO cinema_dw.etl_log 
                (run_id, step, src_rows, dwh_rows, src_amount, dwh_amount, status, message)
                VALUES (:run_id, :step, :src, :dwh, :src_amt, :dwh_amt, :st, :msg)
            """), {
                "run_id": run_id, "step": q["step"],
                "src": row[0], "dwh": row[1],
                "src_amt": row[2], "dwh_amt": row[3],
                "st": status, "msg": msg
            })
            logger.info(f"{q['step']:<30} | {status:<4} | {msg}")
            
    logger.info("=== AGGREGATE SUMMARY HOÀN TẤT ===")
    return True

if __name__ == "__main__":
    run_aggregation()
    sys.exit(0)

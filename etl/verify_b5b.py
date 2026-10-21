import os
from sqlalchemy import text
from etl.db import get_engine
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

engine = get_engine()

print('\n=== (a) KẾT QUẢ CHECK VIEWS ===')
with engine.connect() as conn:
    conn.execute(text('SET search_path TO cinema_dw, public;'))
    with open('sql/04_check_views.sql', 'r', encoding='utf-8') as f:
        sql = f.read()
        statements = sql.split(';')
        for i, stmt in enumerate(statements):
            if 'SELECT ' not in stmt.upper():
                continue
            df = pd.read_sql(text(stmt), conn)
            print(f'\n[Query]')
            print(df.to_string(index=False))

print('\n=== (b) ĐỐI SOÁT SỐ DÒNG BẢNG SUMMARY ===')
with engine.connect() as conn:
    q_b = """
        SELECT 
            (SELECT COUNT(*) FROM cinema_dw.fact_showtime_summary) AS showtime_count,
            (SELECT COUNT(DISTINCT date_key) * COUNT(DISTINCT cinema_key) 
             FROM cinema_dw.fact_ticket) AS expected_daily_pairs,
            (SELECT COUNT(*) FROM cinema_dw.fact_daily_cinema_summary) AS daily_count,
            (SELECT SUM(ticket_txns) FROM cinema_dw.fact_daily_cinema_summary) AS total_ticket_txns,
            (SELECT COUNT(DISTINCT txn_id) FROM cinema_dw.fact_ticket) AS fact_ticket_txns,
            (SELECT SUM(fnb_txns) FROM cinema_dw.fact_daily_cinema_summary) AS total_fnb_txns,
            (SELECT COUNT(DISTINCT txn_id) FROM cinema_dw.fact_fnb) AS fact_fnb_txns
    """
    df_b = pd.read_sql(text(q_b), conn)
    print(df_b.to_string(index=False))

print('\n=== (c) CHỨNG MINH RÀNG BUỘC (CONSTRAINTS) ===')
queries_c = [
    ('tickets_sold âm', """
        INSERT INTO cinema_dw.fact_showtime_summary 
        (date_key, time_key, cinema_key, room_key, movie_key, tickets_sold, ticket_revenue, source)
        VALUES (20250401, 1400, 1, 1, 1, -5, 100000, 'MANUAL');
    """),
    ('fnb_txns > ticket_txns', """
        INSERT INTO cinema_dw.fact_daily_cinema_summary 
        (date_key, cinema_key, ticket_txns, fnb_txns, fnb_revenue, source)
        VALUES (20250401, 1, 100, 150, 5000000, 'MANUAL');
    """),
    ('trùng (date_key, time_key, room_key)', """
        INSERT INTO cinema_dw.fact_showtime_summary 
        (date_key, time_key, cinema_key, room_key, movie_key, tickets_sold, ticket_revenue, source)
        SELECT date_key, time_key, cinema_key, room_key, movie_key, 10, 1000000, 'MANUAL'
        FROM cinema_dw.fact_showtime_summary LIMIT 1;
    """)
]

with engine.connect() as conn:
    for name, sql in queries_c:
        print(f'\nThử INSERT: {name}')
        try:
            with conn.begin():
                conn.execute(text(sql))
            print('=> Bất ngờ: INSERT thành công!')
        except Exception as e:
            err_msg = str(e.__cause__) if e.__cause__ else str(e)
            print(f'=> BỊ CHẶN. Lỗi trả về:\n   {err_msg.strip()}')

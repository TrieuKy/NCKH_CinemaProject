import pandas as pd
from core.ingest import validate_showtime_rows, save_showtime_summary
from etl.db import get_engine
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    uid = conn.execute(text("SELECT user_id FROM cinema_dw.app_user WHERE role='STAFF' LIMIT 1")).fetchone()
    if not uid:
        uid = '00000000-0000-0000-0000-000000000000'
    else:
        uid = uid[0]
    
    ckey = conn.execute(text("SELECT cinema_key FROM cinema_dw.dim_cinema LIMIT 1")).fetchone()[0]
    rkey = conn.execute(text("SELECT room_key FROM cinema_dw.dim_room WHERE cinema_key = :ckey LIMIT 1"), {"ckey": ckey}).fetchone()[0]
    mkey = conn.execute(text("SELECT movie_key FROM cinema_dw.dim_movie LIMIT 1")).fetchone()[0]

df = pd.DataFrame([
    {'date_key': 20250401, 'time_key': 1400, 'cinema_key': ckey, 'room_key': rkey, 'movie_key': mkey, 'tickets_sold': 50, 'ticket_revenue': 5000000},
    {'date_key': 20250401, 'time_key': 1600, 'cinema_key': ckey, 'room_key': rkey, 'movie_key': mkey, 'tickets_sold': -10, 'ticket_revenue': 5000000},
    {'date_key': 20250401, 'time_key': 1900, 'cinema_key': ckey, 'room_key': rkey, 'movie_key': mkey, 'tickets_sold': 900, 'ticket_revenue': 90000000},
])

report = save_showtime_summary(df, uid)
print('=== VÍ DỤ BÁO CÁO TÓM TẮT NHẬP LIỆU ===')
print(f'Tổng số dòng: {len(df)}')
print(f'Thành công (lưu): {report["saved"]}')
print(f'Lỗi (bị chặn): {report["errors"]}')
print(f'Cảnh báo (được lưu nhưng cần chú ý): {report["warnings"]}')
print('-' * 40)
for msg in report['messages']:
    print(msg)

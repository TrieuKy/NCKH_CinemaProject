"""
core/ingest.py
--------------
Module xử lý nhập liệu Data Entry từ nhân viên.
Chứa các hàm validate và lưu dữ liệu. Không phụ thuộc vào Streamlit.
"""

import pandas as pd
from sqlalchemy import text
from etl.db import get_engine, logger
from datetime import datetime, timedelta

def get_pricing_bounds():
    engine = get_engine()
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MIN(list_price), MAX(list_price) FROM cinema_dw.dim_pricing")).fetchone()
        return float(res[0]) if res[0] else 0, float(res[1]) if res[1] else 9999999

def validate_showtime_rows(df, schedule_df, room_capacity_df, min_price, max_price):
    """
    Nhận vào DataFrame dữ liệu, trả về danh sách lỗi và cảnh báo (mỗi dòng tương ứng 1 dict trong danh sách kết quả).
    Kết quả format: 
    [
      {"row_idx": 1, "errors": ["..."], "warnings": ["..."]}, ...
    ]
    """
    results = []
    
    # Kiểm tra trùng lặp trong file
    df['dup_key'] = df['date_key'].astype(str) + "_" + df['time_key'].astype(str) + "_" + df['room_key'].astype(str)
    dup_keys = df[df.duplicated('dup_key', keep=False)]['dup_key'].unique()

    for idx, row in df.iterrows():
        errors = []
        warnings = []
        
        # 1. Ô trống (Null)
        if pd.isna(row.get('tickets_sold')) or pd.isna(row.get('ticket_revenue')):
            errors.append("Cột tickets_sold hoặc ticket_revenue bị trống.")
            results.append({"row_idx": idx, "errors": errors, "warnings": warnings})
            continue
            
        # 2. Sai kiểu
        try:
            tickets_sold = int(row['tickets_sold'])
            ticket_revenue = float(row['ticket_revenue'])
        except ValueError:
            errors.append("Sai kiểu dữ liệu. tickets_sold phải là số nguyên, ticket_revenue phải là số.")
            results.append({"row_idx": idx, "errors": errors, "warnings": warnings})
            continue
            
        # 3. Số âm
        if tickets_sold < 0 or ticket_revenue < 0:
            errors.append("tickets_sold và ticket_revenue không được là số âm.")
            
        # 4. Trùng lặp trong file
        dup_key = f"{row['date_key']}_{row['time_key']}_{row['room_key']}"
        if dup_key in dup_keys:
            errors.append("Trùng lặp suất chiếu trong cùng file upload.")
            
        # 5. Suất không có trong lịch chiếu
        date_time_room = f"{row['date_key']}_{row['time_key']}_{row['room_key']}"
        if date_time_room not in schedule_df.index:
            errors.append("Suất chiếu không tồn tại trong lịch chiếu dự kiến.")
        else:
            # Nếu có trong lịch, kiểm tra capacity (Cảnh báo)
            capacity = room_capacity_df.get(row['room_key'], 9999)
            if tickets_sold > capacity:
                warnings.append(f"Số vé bán ({tickets_sold}) vượt sức chứa phòng ({capacity}).")
                
        # 6. Doanh thu = 0 nhưng số vé > 0 (Cảnh báo)
        if ticket_revenue == 0 and tickets_sold > 0:
            warnings.append("Doanh thu bằng 0 nhưng số vé bán > 0.")
            
        # 7. Doanh thu/vé nằm ngoài khoảng giá hợp lý (Cảnh báo)
        if tickets_sold > 0:
            atp = ticket_revenue / tickets_sold
            # Áp dụng giảm giá tối đa 35% cho min_price
            if atp < (min_price * 0.65) or atp > max_price:
                warnings.append(f"Giá vé trung bình ({atp:,.0f}đ) nằm ngoài khoảng hợp lý.")
                
        results.append({"row_idx": idx, "errors": errors, "warnings": warnings})
        
    return results

def save_showtime_summary(df, user_id, source='MANUAL'):
    engine = get_engine()
    report = {"saved": 0, "errors": 0, "warnings": 0, "messages": [], "missing_schedule": []}
    
    with engine.begin() as conn:
        # Load schedule for validation
        schedule_res = conn.execute(text("SELECT TO_CHAR(show_date, 'YYYYMMDD') AS dkey, TO_CHAR(show_time, 'HH24MI') AS tkey, room_key FROM cinema_dw.showtime_schedule")).fetchall()
        schedule_keys = [f"{r[0]}_{r[1]}_{r[2]}" for r in schedule_res]
        schedule_df = pd.Series(index=schedule_keys, dtype=int)
        
        room_res = conn.execute(text("SELECT room_key, capacity FROM cinema_dw.dim_room")).fetchall()
        room_capacity_df = {r[0]: r[1] for r in room_res}
        
        min_price, max_price = get_pricing_bounds()
        
        validation_results = validate_showtime_rows(df, schedule_df, room_capacity_df, min_price, max_price)
        
        rows_to_insert = []
        for idx, row in df.iterrows():
            val = validation_results[idx]
            if val["errors"]:
                report["errors"] += 1
                report["messages"].append(f"Dòng {idx}: Lỗi - {', '.join(val['errors'])}")
            else:
                if val["warnings"]:
                    report["warnings"] += 1
                    report["messages"].append(f"Dòng {idx}: Cảnh báo - {', '.join(val['warnings'])}")
                rows_to_insert.append(row)
                
        # Insert valid rows using Upsert
        if rows_to_insert:
            upsert_sql = """
                INSERT INTO cinema_dw.fact_showtime_summary (
                    date_key, time_key, cinema_key, room_key, movie_key,
                    tickets_sold, ticket_revenue, source, entered_by
                ) VALUES (
                    :date_key, :time_key, :cinema_key, :room_key, :movie_key,
                    :tickets_sold, :ticket_revenue, :source, :entered_by
                )
                ON CONFLICT (date_key, time_key, room_key) 
                DO UPDATE SET
                    tickets_sold = EXCLUDED.tickets_sold,
                    ticket_revenue = EXCLUDED.ticket_revenue,
                    source = EXCLUDED.source,
                    entered_by = EXCLUDED.entered_by,
                    entered_at = CURRENT_TIMESTAMP
                WHERE cinema_dw.fact_showtime_summary.is_locked = FALSE;
            """
            for row in rows_to_insert:
                conn.execute(text(upsert_sql), {
                    "date_key": int(row['date_key']),
                    "time_key": int(row['time_key']),
                    "cinema_key": int(row['cinema_key']),
                    "room_key": int(row['room_key']),
                    "movie_key": int(row['movie_key']),
                    "tickets_sold": int(row['tickets_sold']),
                    "ticket_revenue": float(row['ticket_revenue']),
                    "source": source,
                    "entered_by": user_id
                })
            report["saved"] = len(rows_to_insert)
            
        # Ghi import_log
        log_msg = f"Saved: {report['saved']}, Errors: {report['errors']}, Warnings: {report['warnings']}"
        conn.execute(text("""
            INSERT INTO cinema_dw.import_log (imported_by, source, rows_total, rows_ok, rows_error, message)
            VALUES (:user_id, :source, :total, :ok, :err, :msg)
        """), {
            "user_id": user_id, "source": source, "total": len(df),
            "ok": report['saved'], "err": report['errors'], "msg": log_msg
        })
        
    return report

def validate_daily_row(row, historical_avg_revenue):
    errors = []
    warnings = []
    
    try:
        t_txns = int(row['ticket_txns'])
        f_txns = int(row['fnb_txns'])
        f_rev = float(row['fnb_revenue'])
    except ValueError:
        return ["Sai kiểu dữ liệu (phải là số)."], []
        
    if t_txns < 0 or f_txns < 0 or f_rev < 0:
        errors.append("Số lượng giao dịch và doanh thu không được âm.")
        
    if f_txns > t_txns:
        errors.append(f"Giao dịch F&B ({f_txns}) không thể lớn hơn giao dịch vé ({t_txns}).")
        
    if historical_avg_revenue > 0:
        # Nếu doanh thu F&B vượt quá 200% hoặc nhỏ hơn 20% trung bình 30 ngày
        if f_rev > historical_avg_revenue * 2 or f_rev < historical_avg_revenue * 0.2:
            warnings.append(f"Doanh thu F&B ({f_rev:,.0f}đ) bất thường so với trung bình 30 ngày ({historical_avg_revenue:,.0f}đ).")
            
    return errors, warnings

def save_daily_summary(df, user_id, source='MANUAL'):
    engine = get_engine()
    report = {"saved": 0, "errors": 0, "warnings": 0, "messages": []}
    
    with engine.begin() as conn:
        rows_to_insert = []
        for idx, row in df.iterrows():
            # Tính trung bình doanh thu F&B 30 ngày qua cho rạp này
            date_key = int(row['date_key'])
            cinema_key = int(row['cinema_key'])
            
            # Đổi date_key thành date để lùi 30 ngày (đơn giản hoá query DB)
            res = conn.execute(text("""
                SELECT AVG(fnb_revenue) FROM cinema_dw.fact_daily_cinema_summary
                WHERE cinema_key = :ckey AND date_key < :dkey AND date_key >= :dkey - 30
            """), {"ckey": cinema_key, "dkey": date_key}).fetchone()
            
            hist_avg = float(res[0]) if res[0] else 0
            
            errs, warns = validate_daily_row(row, hist_avg)
            if errs:
                report["errors"] += 1
                report["messages"].append(f"Dòng {idx}: Lỗi - {', '.join(errs)}")
            else:
                if warns:
                    report["warnings"] += 1
                    report["messages"].append(f"Dòng {idx}: Cảnh báo - {', '.join(warns)}")
                rows_to_insert.append(row)
                
        if rows_to_insert:
            upsert_sql = """
                INSERT INTO cinema_dw.fact_daily_cinema_summary (
                    date_key, cinema_key, ticket_txns, fnb_txns, fnb_revenue, source, entered_by
                ) VALUES (
                    :date_key, :cinema_key, :ticket_txns, :fnb_txns, :fnb_revenue, :source, :entered_by
                )
                ON CONFLICT (date_key, cinema_key) 
                DO UPDATE SET
                    ticket_txns = EXCLUDED.ticket_txns,
                    fnb_txns = EXCLUDED.fnb_txns,
                    fnb_revenue = EXCLUDED.fnb_revenue,
                    source = EXCLUDED.source,
                    entered_by = EXCLUDED.entered_by,
                    entered_at = CURRENT_TIMESTAMP
                WHERE cinema_dw.fact_daily_cinema_summary.is_locked = FALSE;
            """
            for row in rows_to_insert:
                conn.execute(text(upsert_sql), {
                    "date_key": int(row['date_key']),
                    "cinema_key": int(row['cinema_key']),
                    "ticket_txns": int(row['ticket_txns']),
                    "fnb_txns": int(row['fnb_txns']),
                    "fnb_revenue": float(row['fnb_revenue']),
                    "source": source,
                    "entered_by": user_id
                })
            report["saved"] = len(rows_to_insert)
            
        log_msg = f"Saved: {report['saved']}, Errors: {report['errors']}, Warnings: {report['warnings']}"
        conn.execute(text("""
            INSERT INTO cinema_dw.import_log (imported_by, source, rows_total, rows_ok, rows_error, message)
            VALUES (:user_id, :source, :total, :ok, :err, :msg)
        """), {
            "user_id": user_id, "source": source, "total": len(df),
            "ok": report['saved'], "err": report['errors'], "msg": log_msg
        })
        
    return report

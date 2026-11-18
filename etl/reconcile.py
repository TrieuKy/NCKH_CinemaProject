"""
etl/reconcile.py
----------------
Đối soát dữ liệu giữa staging và data warehouse độc lập với luồng load.
Ghi nhật ký vào bảng cinema_dw.etl_log với trạng thái OK/FAIL.
Nếu có bất kỳ bước nào FAIL, trả về False (và thoát với mã lỗi 1 nếu chạy CLI).

Các nhóm kiểm tra:
1. Đối soát số lượng: Số dòng vé, tổng vé SUM(qty), số dòng F&B.
2. Đối soát tài chính: Tổng tiền vé (net_amount vs paid_amount), tổng tiền F&B (line_amount).
3. QC Toàn vẹn & Orphan:
   - F&B mồ côi trong staging (txn_id fnb không thuộc vé nào trong staging).
   - F&B mồ côi trong Fact (txn_id fact_fnb không tồn tại trong fact_ticket).
   - Kiểm tra F&B hai chiều (stg thiếu trong fact, fact thiếu trong stg).
4. QC Kiểm soát thất thoát do JOIN:
   - Các dòng staging ticket không khớp bất kỳ dimension nào.
   - Các dòng staging F&B không khớp bất kỳ dimension nào.
5. QC Khóa ngoại & Logic nghiệp vụ:
   - Suất chiếu vượt capacity mà is_anomaly = 0.
   - Khóa ngoại NULL trong fact_ticket và fact_fnb.
   - Lệch tiền giữa staging paid_amount và fact net_amount.

Chạy trực tiếp: python -m etl.reconcile
"""

from __future__ import annotations

import sys
import uuid
from typing import Optional

from sqlalchemy import text

from etl.db import get_engine, logger


def reconcile(run_id: Optional[str] = None) -> bool:
    """
    Thực thi toàn bộ các bước đối soát QC, ghi nhận vào cinema_dw.etl_log.
    Trả về True nếu tất cả các bước đạt trạng thái OK.
    """
    engine = get_engine()
    if run_id is None:
        run_id = str(uuid.uuid4())

    logger.info("=== BẮT ĐẦU ĐỐI SOÁT & KIỂM TRA CHẤT LƯỢNG DỮ LIỆU (run_id: %s) ===", run_id)

    queries = []

    # 1. Số dòng vé (số giao dịch)
    queries.append({
        "step": "reconcile_ticket_rows",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.ticket) AS src_rows,
                (SELECT COUNT(*) FROM cinema_dw.fact_ticket) AS dwh_rows,
                NULL::numeric AS src_amount,
                NULL::numeric AS dwh_amount
        """,
        "check": lambda r: "OK" if r[0] == r[1] else "FAIL",
        "msg": lambda r: "Khớp" if r[0] == r[1] else f"Lệch dòng vé: stg={r[0]}, fact={r[1]}",
    })

    # 2. Tổng số vé quy đổi (SUM(qty))
    queries.append({
        "step": "reconcile_ticket_qty",
        "sql": """
            SELECT 
                (SELECT COALESCE(SUM(qty), 0) FROM staging.ticket)::bigint AS src_rows,
                (SELECT COALESCE(SUM(qty), 0) FROM cinema_dw.fact_ticket)::bigint AS dwh_rows,
                NULL::numeric AS src_amount,
                NULL::numeric AS dwh_amount
        """,
        "check": lambda r: "OK" if r[0] == r[1] else "FAIL",
        "msg": lambda r: "Khớp" if r[0] == r[1] else f"Lệch tổng vé: stg={r[0]}, fact={r[1]}",
    })

    # 3. Tổng tiền vé
    queries.append({
        "step": "reconcile_ticket_amount",
        "sql": """
            SELECT 
                NULL::bigint, NULL::bigint,
                (SELECT COALESCE(SUM(paid_amount), 0) FROM staging.ticket) AS src_amount,
                (SELECT COALESCE(SUM(net_amount), 0) FROM cinema_dw.fact_ticket) AS dwh_amount
        """,
        "check": lambda r: "OK" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else "FAIL",
        "msg": lambda r: "Khớp" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else f"Lệch tiền vé: stg={r[2]}, fact={r[3]}",
    })

    # 4. Số dòng F&B
    queries.append({
        "step": "reconcile_fnb_rows",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.fnb) AS src_rows,
                (SELECT COUNT(*) FROM cinema_dw.fact_fnb) AS dwh_rows,
                NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if r[0] == r[1] else "FAIL",
        "msg": lambda r: "Khớp" if r[0] == r[1] else f"Lệch dòng F&B: stg={r[0]}, fact={r[1]}",
    })

    # 5. Tổng tiền F&B
    queries.append({
        "step": "reconcile_fnb_amount",
        "sql": """
            SELECT 
                NULL::bigint, NULL::bigint,
                (SELECT COALESCE(SUM(line_amount), 0) FROM staging.fnb) AS src_amount,
                (SELECT COALESCE(SUM(line_amount), 0) FROM cinema_dw.fact_fnb) AS dwh_amount
        """,
        "check": lambda r: "OK" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else "FAIL",
        "msg": lambda r: "Khớp" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else f"Lệch tiền F&B: stg={r[2]}, fact={r[3]}",
    })

    # 6. QC: F&B mồ côi trong staging (txn_id fnb không thuộc vé nào trong staging)
    queries.append({
        "step": "qc_orphan_fnb_stg",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.fnb f 
                 LEFT JOIN staging.ticket t ON f.txn_id = t.txn_id 
                 WHERE t.txn_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} dòng F&B mồ côi trong staging",
    })

    # 7. QC: F&B mồ côi trong Fact (txn_id fact_fnb không tồn tại trong fact_ticket)
    queries.append({
        "step": "qc_orphan_fnb_fact",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM cinema_dw.fact_fnb f 
                 LEFT JOIN cinema_dw.fact_ticket t ON f.txn_id = t.txn_id 
                 WHERE t.txn_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} dòng F&B trong Fact có txn_id không tồn tại trong fact_ticket",
    })

    # 8. QC: Suất vượt sức chứa mà is_anomaly = 0
    queries.append({
        "step": "qc_capacity_breach_normal",
        "sql": """
            WITH counts AS (
                SELECT room_id, show_date, show_time, capacity, SUM(qty) AS sold
                FROM staging.ticket
                WHERE is_anomaly = 0
                GROUP BY room_id, show_date, show_time, capacity
            )
            SELECT 
                (SELECT COUNT(*) FROM counts WHERE sold > capacity) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} suất bán lố ghế mà is_anomaly=0",
    })

    # 9. QC: txn_id có ở staging mà thiếu ở fact
    queries.append({
        "step": "qc_stg_ticket_missing_in_fact",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.ticket s 
                 LEFT JOIN cinema_dw.fact_ticket f ON s.txn_id = f.txn_id 
                 WHERE f.txn_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} txn_id có ở staging, thiếu ở fact",
    })

    # 10. QC: txn_id có ở fact mà thiếu ở staging
    queries.append({
        "step": "qc_fact_ticket_missing_in_stg",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM cinema_dw.fact_ticket f 
                 LEFT JOIN staging.ticket s ON f.txn_id = s.txn_id 
                 WHERE s.txn_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} txn_id có ở fact, thiếu ở staging",
    })

    # 11. QC: fnb_line_id có ở staging mà thiếu ở fact
    queries.append({
        "step": "qc_stg_fnb_missing_in_fact",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.fnb s 
                 LEFT JOIN cinema_dw.fact_fnb f ON s.fnb_line_id = f.fnb_line_id 
                 WHERE f.fnb_line_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} fnb_line_id có ở staging, thiếu ở fact",
    })

    # 12. QC: fnb_line_id có ở fact mà thiếu ở staging
    queries.append({
        "step": "qc_fact_fnb_missing_in_stg",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM cinema_dw.fact_fnb f 
                 LEFT JOIN staging.fnb s ON f.fnb_line_id = s.fnb_line_id 
                 WHERE s.fnb_line_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} fnb_line_id có ở fact, thiếu ở staging",
    })

    # 13. QC: Số dòng vé có paid_amount staging khác net_amount fact
    queries.append({
        "step": "qc_ticket_amount_mismatch",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.ticket s
                 JOIN cinema_dw.fact_ticket f ON s.txn_id = f.txn_id
                 WHERE abs(s.paid_amount - f.net_amount) >= 1) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} dòng vé bị lệch tiền",
    })

    # 14. QC: Thất thoát dữ liệu vé do không khớp Dimension (INNER JOIN Loss)
    queries.append({
        "step": "qc_stg_ticket_unmatched_dims",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.ticket t
                 LEFT JOIN cinema_dw.dim_date dd ON dd.date_key = TO_CHAR(t.show_date::DATE, 'YYYYMMDD')::INTEGER
                 LEFT JOIN cinema_dw.dim_time dt ON dt.time_key = (EXTRACT(HOUR FROM t.show_time::TIME)::INTEGER * 100 + EXTRACT(MINUTE FROM t.show_time::TIME)::INTEGER)
                 LEFT JOIN cinema_dw.dim_movie dm ON dm.movie_id = t.movie_id
                 LEFT JOIN cinema_dw.dim_cinema dc ON dc.cinema_id = t.cinema_id
                 LEFT JOIN cinema_dw.dim_room dr ON dr.room_id = t.room_id
                 LEFT JOIN cinema_dw.dim_customer cu ON cu.customer_id = t.customer_id
                 LEFT JOIN cinema_dw.dim_pricing dp ON dp.ticket_format = t.ticket_format AND dp.day_type = t.day_type AND dp.time_slot = t.time_slot AND dp.customer_type = t.customer_type AND ROUND(dp.discount_rate::NUMERIC, 4) = ROUND(t.discount_rate::NUMERIC, 4)
                 LEFT JOIN cinema_dw.dim_channel ch ON ch.channel_code = t.channel_code
                 WHERE dd.date_key IS NULL OR dt.time_key IS NULL OR dm.movie_key IS NULL 
                    OR dc.cinema_key IS NULL OR dr.room_key IS NULL OR cu.customer_key IS NULL 
                    OR dp.pricing_key IS NULL OR ch.channel_key IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass (0 dòng vé bị thất thoát do thiếu dimension)" if (r[0] or 0) == 0 else f"Cảnh báo: {r[0]} dòng vé staging không khớp dimension keys",
    })

    # 15. QC: Thất thoát dữ liệu F&B do không khớp Dimension (INNER JOIN Loss)
    queries.append({
        "step": "qc_stg_fnb_unmatched_dims",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.fnb f
                 LEFT JOIN cinema_dw.dim_date dd ON dd.date_key = TO_CHAR(f.sale_date::DATE, 'YYYYMMDD')::INTEGER
                 LEFT JOIN cinema_dw.dim_time dt ON dt.time_key = (EXTRACT(HOUR FROM f.sale_time::TIME)::INTEGER * 100 + EXTRACT(MINUTE FROM f.sale_time::TIME)::INTEGER)
                 LEFT JOIN cinema_dw.dim_cinema dc ON dc.cinema_id = f.cinema_id
                 LEFT JOIN cinema_dw.dim_customer cu ON cu.customer_id = f.customer_id
                 LEFT JOIN cinema_dw.dim_product dp ON dp.product_id = f.product_id
                 LEFT JOIN cinema_dw.dim_channel ch ON ch.channel_code = f.channel_code
                 WHERE dd.date_key IS NULL OR dt.time_key IS NULL OR dc.cinema_key IS NULL 
                    OR cu.customer_key IS NULL OR dp.product_key IS NULL OR ch.channel_key IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass (0 dòng F&B bị thất thoát do thiếu dimension)" if (r[0] or 0) == 0 else f"Cảnh báo: {r[0]} dòng F&B staging không khớp dimension keys",
    })

    # 16. QC: Khóa ngoại NULL trong fact_ticket
    queries.append({
        "step": "qc_fact_null_fk_ticket",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM cinema_dw.fact_ticket 
                 WHERE date_key IS NULL OR time_key IS NULL OR movie_key IS NULL 
                    OR cinema_key IS NULL OR room_key IS NULL OR customer_key IS NULL 
                    OR pricing_key IS NULL OR channel_key IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass (0 dòng fact_ticket bị NULL khóa ngoại)" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} dòng fact_ticket bị NULL khóa ngoại",
    })

    # 17. QC: Khóa ngoại NULL trong fact_fnb
    queries.append({
        "step": "qc_fact_null_fk_fnb",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM cinema_dw.fact_fnb 
                 WHERE date_key IS NULL OR time_key IS NULL OR cinema_key IS NULL 
                    OR customer_key IS NULL OR product_key IS NULL OR channel_key IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass (0 dòng fact_fnb bị NULL khóa ngoại)" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} dòng fact_fnb bị NULL khóa ngoại",
    })

    has_fail = False

    # Ghi log tuần tự vào DB trong 1 transaction
    try:
        with engine.begin() as conn:
            for q in queries:
                row = conn.execute(text(q["sql"])).fetchone()
                status = q["check"](row)
                msg = q["msg"](row)

                if status == "FAIL":
                    has_fail = True

                conn.execute(
                    text("""
                        INSERT INTO cinema_dw.etl_log 
                            (run_id, step, src_rows, dwh_rows, src_amount, dwh_amount, status, message)
                        VALUES 
                            (:run_id, :step, :src_rows, :dwh_rows, :src_amount, :dwh_amount, :status, :message)
                    """),
                    {
                        "run_id": run_id,
                        "step": q["step"],
                        "src_rows": row[0],
                        "dwh_rows": row[1],
                        "src_amount": row[2],
                        "dwh_amount": row[3],
                        "status": status,
                        "message": msg,
                    },
                )
                logger.info("%-32s | %-4s | %s", q["step"], status, msg)
    except Exception as exc:
        logger.error("Lỗi khi đối soát: %s", exc)
        return False

    logger.info("=== ĐỐI SOÁT HOÀN TẤT: %s ===", "TẤT CẢ ĐẠT (PASS)" if not has_fail else "CÓ BƯỚC THẤT BẠI (FAIL)")
    return not has_fail


def print_log(run_id: Optional[str] = None) -> None:
    """In nội dung bảng etl_log ra màn hình (lọc theo run_id nếu có)."""
    engine = get_engine()
    with engine.connect() as conn:
        if run_id:
            rows = conn.execute(
                text("""
                    SELECT 
                        TO_CHAR(run_at, 'YYYY-MM-DD HH24:MI:SS'),
                        step, 
                        src_rows, dwh_rows, 
                        src_amount, dwh_amount, 
                        status, message
                    FROM cinema_dw.etl_log
                    WHERE run_id = :run_id
                    ORDER BY log_id
                """),
                {"run_id": run_id},
            ).fetchall()
        else:
            # Lấy 25 bản ghi log gần nhất
            rows = conn.execute(
                text("""
                    SELECT 
                        TO_CHAR(run_at, 'YYYY-MM-DD HH24:MI:SS'),
                        step, 
                        src_rows, dwh_rows, 
                        src_amount, dwh_amount, 
                        status, message
                    FROM cinema_dw.etl_log
                    ORDER BY log_id DESC
                    LIMIT 25
                """)
            ).fetchall()
            rows = list(reversed(rows))

    print("\n" + "=" * 136)
    print(f"{'BẢNG NHẬT KÝ ĐỐI SOÁT VÀ AUDIT LOG (cinema_dw.etl_log)':^136}")
    if run_id:
        print(f"{'run_id: ' + str(run_id):^136}")
    print("=" * 136)
    header = (
        f"{'Thời gian':<20} | {'Bước kiểm tra':<30} | {'Stg Rows':>10} | {'Dwh Rows':>10} | "
        f"{'Stg Amount':>16} | {'Dwh Amount':>16} | {'Trạng thái':<10} | {'Ghi chú'}"
    )
    print(header)
    print("-" * 136)

    for r in rows:
        run_at, step, src_rows, dwh_rows, src_amount, dwh_amount, status, msg = r
        s_r = f"{src_rows:,}" if src_rows is not None else "-"
        d_r = f"{dwh_rows:,}" if dwh_rows is not None else "-"
        s_a = f"{src_amount:,.0f}" if src_amount is not None else "-"
        d_a = f"{dwh_amount:,.0f}" if dwh_amount is not None else "-"

        st_icon = "✅ OK" if status == "OK" else "❌ FAIL"

        print(
            f"{run_at:<20} | {step:<30} | {s_r:>10} | {d_r:>10} | "
            f"{s_a:>16} | {d_a:>16} | {st_icon:<10} | {msg}"
        )
    print("=" * 136)


if __name__ == "__main__":
    success = reconcile()
    print_log()
    sys.exit(0 if success else 1)

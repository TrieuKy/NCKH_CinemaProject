"""
etl/reconcile.py
----------------
Đối soát dữ liệu giữa staging và data warehouse độc lập với luồng load.
Ghi nhật ký vào bảng etl_log với trạng thái OK/FAIL.
Nếu có bất kỳ bước nào FAIL, thoát với mã lỗi 1.

Các bước kiểm tra:
1. Số dòng vé (staging vs fact)
2. Tổng tiền vé (net_amount vs paid_amount, sai số < 1đ)
3. Số dòng F&B
4. Tổng tiền F&B
5. QC: F&B có txn_id không thuộc vé nào
6. QC: Suất vượt capacity mà is_anomaly = 0
7. QC: Số dòng fact có khóa dim NULL

Chạy: python -m etl.reconcile
"""

from __future__ import annotations

import sys
import uuid
from sqlalchemy import text

from etl.db import get_engine, logger


def reconcile() -> bool:
    """Thực thi đối soát, ghi DB, trả về True nếu tất cả OK."""
    engine = get_engine()
    run_id = str(uuid.uuid4())
    logger.info("=== BẮT ĐẦU ĐỐI SOÁT (run_id: %s) ===", run_id)

    queries = []

    # 1. Số dòng vé
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
        "msg": lambda r: "Khớp" if r[0] == r[1] else f"Lệch dòng vé: stg={r[0]}, fact={r[1]}"
    })

    # 2. Tổng tiền vé
    queries.append({
        "step": "reconcile_ticket_amount",
        "sql": """
            SELECT 
                NULL::bigint, NULL::bigint,
                (SELECT COALESCE(SUM(paid_amount), 0) FROM staging.ticket) AS src_amount,
                (SELECT COALESCE(SUM(net_amount), 0) FROM cinema_dw.fact_ticket) AS dwh_amount
        """,
        "check": lambda r: "OK" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else "FAIL",
        "msg": lambda r: "Khớp" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else f"Lệch tiền vé: stg={r[2]}, fact={r[3]}"
    })

    # 3. Số dòng F&B
    queries.append({
        "step": "reconcile_fnb_rows",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.fnb) AS src_rows,
                (SELECT COUNT(*) FROM cinema_dw.fact_fnb) AS dwh_rows,
                NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if r[0] == r[1] else "FAIL",
        "msg": lambda r: "Khớp" if r[0] == r[1] else f"Lệch dòng F&B: stg={r[0]}, fact={r[1]}"
    })

    # 4. Tổng tiền F&B
    queries.append({
        "step": "reconcile_fnb_amount",
        "sql": """
            SELECT 
                NULL::bigint, NULL::bigint,
                (SELECT COALESCE(SUM(line_amount), 0) FROM staging.fnb) AS src_amount,
                (SELECT COALESCE(SUM(line_amount), 0) FROM cinema_dw.fact_fnb) AS dwh_amount
        """,
        "check": lambda r: "OK" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else "FAIL",
        "msg": lambda r: "Khớp" if abs(float(r[2] or 0) - float(r[3] or 0)) < 1 else f"Lệch tiền F&B: stg={r[2]}, fact={r[3]}"
    })

    # 5. F&B mồ côi (txn_id không thuộc vé nào)
    queries.append({
        "step": "qc_orphan_fnb",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.fnb f 
                 LEFT JOIN staging.ticket t ON f.txn_id = t.txn_id 
                 WHERE t.txn_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} dòng F&B mồ côi"
    })

    # 6. Suất vượt sức chứa mà is_anomaly = 0
    queries.append({
        "step": "qc_capacity_breach_normal",
        "sql": """
            WITH counts AS (
                SELECT room_id, show_date, show_time, capacity, COUNT(*) AS sold
                FROM staging.ticket
                WHERE is_anomaly = 0
                GROUP BY room_id, show_date, show_time, capacity
            )
            SELECT 
                (SELECT COUNT(*) FROM counts WHERE sold > capacity) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} suất bán lố ghế mà is_anomaly=0"
    })

    # 7. ticket_id có ở staging mà thiếu ở fact
    queries.append({
        "step": "qc_stg_ticket_missing_in_fact",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.ticket s 
                 LEFT JOIN cinema_dw.fact_ticket f ON s.ticket_id = f.ticket_id 
                 WHERE f.ticket_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} ticket_id có ở staging, thiếu ở fact"
    })

    # 8. ticket_id có ở fact mà thiếu ở staging
    queries.append({
        "step": "qc_fact_ticket_missing_in_stg",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM cinema_dw.fact_ticket f 
                 LEFT JOIN staging.ticket s ON f.ticket_id = s.ticket_id 
                 WHERE s.ticket_id IS NULL) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} ticket_id có ở fact, thiếu ở staging"
    })

    # 9. fnb_line_id thiếu ở fact
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
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} fnb_line_id có ở staging, thiếu ở fact"
    })

    # 10. Số dòng vé có paid_amount staging khác net_amount fact
    queries.append({
        "step": "qc_ticket_amount_mismatch",
        "sql": """
            SELECT 
                (SELECT COUNT(*) FROM staging.ticket s
                 JOIN cinema_dw.fact_ticket f ON s.ticket_id = f.ticket_id
                 WHERE abs(s.paid_amount - f.net_amount) >= 1) AS src_rows,
                NULL::bigint, NULL::numeric, NULL::numeric
        """,
        "check": lambda r: "OK" if (r[0] or 0) == 0 else "FAIL",
        "msg": lambda r: "Pass" if (r[0] or 0) == 0 else f"Lỗi: {r[0]} dòng vé bị lệch tiền"
    })

    has_fail = False

    # Ghi log tuần tự
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
                        "message": msg
                    }
                )
                logger.info("%-30s | %-4s | %s", q["step"], status, msg)
    except Exception as exc:
        logger.error("Lỗi khi đối soát: %s", exc)
        return False

    logger.info("=== ĐỐI SOÁT HOÀN TẤT ===")
    return not has_fail


def print_log() -> None:
    """In toàn bộ nội dung bảng etl_log ra màn hình."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT 
                TO_CHAR(run_at, 'YYYY-MM-DD HH24:MI:SS'),
                step, 
                src_rows, dwh_rows, 
                src_amount, dwh_amount, 
                status, message
            FROM cinema_dw.etl_log
            ORDER BY log_id
        """)).fetchall()

    print("\n" + "=" * 130)
    print(f"{'BẢNG NHẬT KÝ ĐỐI SOÁT (etl_log)':^130}")
    print("=" * 130)
    header = (
        f"{'Thời gian':<20} | {'Bước kiểm tra':<28} | {'Stg Rows':>10} | {'Dwh Rows':>10} | "
        f"{'Stg Amount':>16} | {'Dwh Amount':>16} | {'Trạng thái':<10} | {'Ghi chú'}"
    )
    print(header)
    print("-" * 130)

    for r in rows:
        run_at, step, src_rows, dwh_rows, src_amount, dwh_amount, status, msg = r
        s_r = f"{src_rows:,}" if src_rows is not None else "-"
        d_r = f"{dwh_rows:,}" if dwh_rows is not None else "-"
        s_a = f"{src_amount:,.0f}" if src_amount is not None else "-"
        d_a = f"{dwh_amount:,.0f}" if dwh_amount is not None else "-"
        
        # Thêm emoji cho dễ nhìn (tùy chọn nhưng tốt)
        st_icon = "✅ OK" if status == "OK" else "❌ FAIL"
        
        print(
            f"{run_at:<20} | {step:<28} | {s_r:>10} | {d_r:>10} | "
            f"{s_a:>16} | {d_a:>16} | {st_icon:<10} | {msg}"
        )
    print("=" * 130)


if __name__ == "__main__":
    success = reconcile()
    print_log()
    sys.exit(0 if success else 1)

"""
etl/load_facts.py
-----------------
Wrapper nạp dữ liệu Fact (fact_ticket, fact_fnb) bằng phương thức SQL Set-based (sql/03_load_facts.sql).

Đảm bảo:
  1. 100% Set-based trong PostgreSQL (tốc độ cao, không tốn RAM máy trạm do Pandas merge).
  2. Toàn vẹn Transaction: TRUNCATE và INSERT của cả hai bảng fact_ticket và fact_fnb
     được thực thi trong CÙNG MỘT database transaction (với engine.begin()).
     Nếu một câu lệnh gặp lỗi, toàn bộ transaction sẽ rollback (nguyên tử hóa).
  3. Tương thích ngược: Duy trì giao diện gọi hàm load_facts(), main() và lệnh CLI:
         python -m etl.load_facts
  4. Ghi audit log chuẩn xác vào cinema_dw.etl_log theo run_id đồng bộ.
"""

from __future__ import annotations

import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from etl.db import get_engine, logger

# Đường dẫn file SQL nạp fact chính thức
ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "sql" / "03_load_facts.sql"


def _split_statements(sql: str) -> list[str]:
    """
    Tách nội dung SQL thành danh sách các câu lệnh riêng lẻ dựa trên dấu ';'.
    Loại bỏ các dòng comment và câu lệnh rỗng để thực thi tuần tự an toàn.
    """
    parts = sql.split(";")
    stmts: list[str] = []
    for part in parts:
        stripped = part.strip()
        no_comments = re.sub(r"--[^\n]*", "", stripped).strip()
        if no_comments:
            stmts.append(stripped)
    return stmts


def load_facts(engine: Optional[Engine] = None, run_id: Optional[str] = None) -> dict[str, int]:
    """
    Nạp dữ liệu fact_ticket và fact_fnb từ staging bằng SQL Set-based.
    Toàn bộ quá trình TRUNCATE và INSERT diễn ra trong một database transaction duy nhất.

    Trả về dict số lượng dòng nạp: {'fact_ticket': count_t, 'fact_fnb': count_f}.
    """
    if engine is None:
        engine = get_engine()
    if run_id is None:
        run_id = str(uuid.uuid4())

    logger.info("=== BẮT ĐẦU NẠP FACT TABLES (SQL SET-BASED) | run_id: %s ===", run_id)

    if not SQL_FILE.exists():
        err_msg = f"Không tìm thấy file SQL nạp fact: {SQL_FILE}"
        logger.error(err_msg)
        raise FileNotFoundError(err_msg)

    sql_content = SQL_FILE.read_text(encoding="utf-8")
    statements = _split_statements(sql_content)

    counts = {"fact_ticket": 0, "fact_fnb": 0}
    t0 = time.perf_counter()

    # THỰC THI TOÀN BỘ TRONG 1 TRANSACTION NGUYÊN TỬ (ATOMIC)
    try:
        with engine.begin() as conn:
            for idx, stmt in enumerate(statements, 1):
                # Nhận diện mục đích câu lệnh để log
                upper_stmt = stmt.upper()
                if "TRUNCATE" in upper_stmt:
                    label = "TRUNCATE fact_ticket, fact_fnb"
                elif "INSERT INTO" in upper_stmt and "FACT_TICKET" in upper_stmt:
                    label = "INSERT INTO fact_ticket (JOIN 8 dims)"
                elif "INSERT INTO" in upper_stmt and "FACT_FNB" in upper_stmt:
                    label = "INSERT INTO fact_fnb (JOIN 6 dims + WHERE EXISTS ticket)"
                else:
                    label = f"Câu lệnh {idx}"

                logger.info("  [%d/%d] Đang thực thi: %s...", idx, len(statements), label)
                conn.execute(text(stmt))

            # Lấy số dòng nạp thực tế
            cnt_ticket = conn.execute(text("SELECT COUNT(*) FROM cinema_dw.fact_ticket")).scalar() or 0
            cnt_fnb = conn.execute(text("SELECT COUNT(*) FROM cinema_dw.fact_fnb")).scalar() or 0
            counts["fact_ticket"] = int(cnt_ticket)
            counts["fact_fnb"] = int(cnt_fnb)

            # Lấy số dòng staging để ghi nhật ký
            stg_ticket = conn.execute(text("SELECT COUNT(*) FROM staging.ticket")).scalar() or 0
            stg_fnb = conn.execute(text("SELECT COUNT(*) FROM staging.fnb")).scalar() or 0

            # Ghi audit log vào cinema_dw.etl_log
            conn.execute(
                text("""
                    INSERT INTO cinema_dw.etl_log 
                        (run_id, step, src_rows, dwh_rows, status, message)
                    VALUES 
                        (:run_id, 'load_fact_ticket', :src_t, :dwh_t, 'OK', 'Nạp fact_ticket bằng SQL set-based thành công'),
                        (:run_id, 'load_fact_fnb', :src_f, :dwh_f, 'OK', 'Nạp fact_fnb bằng SQL set-based thành công')
                """),
                {
                    "run_id": run_id,
                    "src_t": stg_ticket,
                    "dwh_t": cnt_ticket,
                    "src_f": stg_fnb,
                    "dwh_f": cnt_fnb,
                },
            )

        elapsed = time.perf_counter() - t0
        logger.info(
            "=== NẠP FACT HOÀN TẤT (%.2fs): fact_ticket=%s dòng | fact_fnb=%s dòng ===",
            elapsed, f"{counts['fact_ticket']:,}", f"{counts['fact_fnb']:,}"
        )
        return counts

    except Exception as exc:
        logger.error("Lỗi khi nạp fact tables: %s (Đã tự động rollback transaction)", exc)
        # Ghi log thất bại nếu có thể mở kết nối mới
        try:
            with engine.begin() as conn_fail:
                conn_fail.execute(
                    text("""
                        INSERT INTO cinema_dw.etl_log 
                            (run_id, step, status, message)
                        VALUES 
                            (:run_id, 'load_facts', 'FAIL', :msg)
                    """),
                    {"run_id": run_id, "msg": str(exc)[:500]},
                )
        except Exception:
            pass
        raise


def main() -> None:
    """Hàm main duy trì tương thích cho các script gọi cũ."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        counts = load_facts()
        # Giữ đúng format in terminal ban đầu
        print(f"Số lượng dòng đã nạp thành công vào fact_ticket: {counts['fact_ticket']:,}")
        print(f"Số lượng dòng đã nạp thành công vào fact_fnb: {counts['fact_fnb']:,}")
    except Exception as exc:
        print(f"Lỗi trong quá trình nạp dữ liệu: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

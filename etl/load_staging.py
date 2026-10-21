"""
etl/load_staging.py
-------------------
Nạp staging_ticket.csv và staging_fnb.csv vào bảng staging.ticket / staging.fnb.

Chiến lược: TRUNCATE → COPY FROM STDIN (qua psycopg2.copy_expert).

Lý do dùng COPY thay to_sql/INSERT:
  - COPY là fast-path của PostgreSQL, bỏ qua nhiều lớp kiểm tra → ~10–50× nhanh hơn.
  - File vé ~142 MB / 518k dòng nạp trong ~5–15 giây thay vì ~5 phút.
  - NULL '' xử lý đúng các cột rỗng trong CSV (anomaly_type, gender, …).

Idempotent: TRUNCATE trước mỗi lần COPY → chạy lại không nhân đôi dữ liệu.

Chạy: python -m etl.load_staging
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from etl.db import get_engine, logger

# ---------------------------------------------------------------------------
# Đường dẫn file CSV
# ---------------------------------------------------------------------------
ROOT       = Path(__file__).resolve().parents[1]
TICKET_CSV = ROOT / "data" / "staging_ticket.csv"
FNB_CSV    = ROOT / "data" / "staging_fnb.csv"

# ---------------------------------------------------------------------------
# COPY statement — dùng HEADER TRUE để bỏ qua dòng tiêu đề CSV
#                  NULL '' để cột rỗng thành NULL (không phải chuỗi rỗng)
# ---------------------------------------------------------------------------
COPY_TICKET = "COPY staging.ticket FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')"
COPY_FNB    = "COPY staging.fnb    FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')"


def _copy_file(cursor, copy_sql: str, csv_path: Path) -> int:
    """
    Mở file CSV và truyền qua copy_expert.
    Trả về số dòng đã nạp (SELECT COUNT sau COPY để chắc chắn).
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"File không tồn tại: {csv_path}")

    with open(csv_path, "r", encoding="utf-8") as f:
        cursor.copy_expert(copy_sql, f)

    # rowcount sau COPY trên psycopg2 thường là -1 → query lại cho chắc
    table = copy_sql.split("COPY")[1].split("FROM")[0].strip()
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    return cursor.fetchone()[0]


def load_staging() -> dict[str, int]:
    """
    Nạp cả hai file CSV vào staging.
    Trả dict {'ticket': n_ticket, 'fnb': n_fnb}.
    """
    if not TICKET_CSV.exists() or not FNB_CSV.exists():
        logger.error(
            "Chưa tìm thấy file CSV. Chạy: python scripts/generate_mock_data.py"
        )
        sys.exit(1)

    engine = get_engine()
    logger.info("=== BẮT ĐẦU NẠP STAGING ===")

    # Lấy raw psycopg2 connection để dùng copy_expert
    raw_conn = engine.raw_connection()
    counts: dict[str, int] = {}

    try:
        cursor = raw_conn.cursor()

        # --- TRUNCATE (giữ cấu trúc bảng, xóa toàn bộ dữ liệu cũ) ---
        logger.info("TRUNCATE staging.ticket, staging.fnb …")
        cursor.execute("TRUNCATE staging.ticket, staging.fnb;")

        # --- COPY ticket ---
        logger.info("COPY staging.ticket ← %s …", TICKET_CSV.name)
        t0 = time.perf_counter()
        counts["ticket"] = _copy_file(cursor, COPY_TICKET, TICKET_CSV)
        elapsed = time.perf_counter() - t0
        logger.info(
            "staging.ticket: %s dòng trong %.1f giây",
            f"{counts['ticket']:,}", elapsed,
        )

        # --- COPY fnb ---
        logger.info("COPY staging.fnb ← %s …", FNB_CSV.name)
        t0 = time.perf_counter()
        counts["fnb"] = _copy_file(cursor, COPY_FNB, FNB_CSV)
        elapsed = time.perf_counter() - t0
        logger.info(
            "staging.fnb: %s dòng trong %.1f giây",
            f"{counts['fnb']:,}", elapsed,
        )

        # --- BUILD INDEXES ---
        logger.info("BUILD INDEXES cho bảng staging (tăng tốc độ B3) …")
        t0 = time.perf_counter()
        indexes_sql = """
            CREATE INDEX IF NOT EXISTS idx_stg_ticket_txn ON staging.ticket(txn_id);
            CREATE INDEX IF NOT EXISTS idx_stg_ticket_id ON staging.ticket(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_stg_ticket_cinema ON staging.ticket(cinema_id);
            CREATE INDEX IF NOT EXISTS idx_stg_ticket_room ON staging.ticket(room_id);
            CREATE INDEX IF NOT EXISTS idx_stg_ticket_movie ON staging.ticket(movie_id);
            CREATE INDEX IF NOT EXISTS idx_stg_ticket_customer ON staging.ticket(customer_id);
            CREATE INDEX IF NOT EXISTS idx_stg_ticket_channel ON staging.ticket(channel_code);
            
            CREATE INDEX IF NOT EXISTS idx_stg_fnb_txn ON staging.fnb(txn_id);
            CREATE INDEX IF NOT EXISTS idx_stg_fnb_id ON staging.fnb(fnb_line_id);
            CREATE INDEX IF NOT EXISTS idx_stg_fnb_customer ON staging.fnb(customer_id);
        """
        cursor.execute(indexes_sql)
        elapsed = time.perf_counter() - t0
        logger.info("BUILD INDEXES hoàn tất trong %.1f giây", elapsed)

        raw_conn.commit()
        cursor.close()

    except Exception as exc:
        raw_conn.rollback()
        logger.error("Lỗi khi nạp staging: %s", exc)
        raise
    finally:
        raw_conn.close()

    logger.info("=== NẠP STAGING HOÀN TẤT ===")
    return counts


if __name__ == "__main__":
    result = load_staging()
    print("\n┌─────────────────────┬──────────────┐")
    print("│ Bảng staging        │  Số dòng     │")
    print("├─────────────────────┼──────────────┤")
    for tbl, cnt in result.items():
        print(f"│ staging.{tbl:<12} │ {cnt:>12,} │")
    print("└─────────────────────┴──────────────┘")
    sys.exit(0)

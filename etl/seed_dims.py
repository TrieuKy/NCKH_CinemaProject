"""
etl/seed_dims.py
----------------
Đọc sql/02_seed_dims.sql, tách thành từng lệnh INSERT, thực thi theo thứ tự,
rồi in bảng đếm số dòng của tất cả bảng staging và dim.

Lý do tách thành các statement riêng (không thực thi cả file một lần):
  - Mỗi dim thất bại sẽ báo tên dim cụ thể, không rollback toàn bộ.
  - Dễ theo dõi tiến độ (log từng dim).

Idempotent: ON CONFLICT DO NOTHING trong SQL → chạy lại an toàn.

Chạy: python -m etl.seed_dims
"""

from __future__ import annotations

import sys
import re
from pathlib import Path

from sqlalchemy import text

from etl.db import get_engine, logger

# ---------------------------------------------------------------------------
ROOT    = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "sql" / "02_seed_dims.sql"

# Thứ tự bảng cần đếm (dim_room phụ thuộc dim_cinema → đã xử lý trong SQL)
DIM_TABLES = [
    "dim_date",
    "dim_time",
    "dim_cinema",
    "dim_room",
    "dim_movie",
    "dim_customer",
    "dim_product",
    "dim_pricing",
    "dim_channel",
]

STAGING_TABLES = ["staging.ticket", "staging.fnb"]


def _split_statements(sql: str) -> list[str]:
    """
    Tách nội dung SQL thành danh sách câu lệnh dựa trên dấu ';'.
    Bỏ qua dòng comment thuần (--) và câu lệnh rỗng.
    Giữ nguyên comment trong nội dung câu lệnh (để dim dễ đọc trong log).
    """
    # Tách theo ';' — giữ các phần
    parts = sql.split(";")
    stmts = []
    for part in parts:
        stripped = part.strip()
        # Lọc phần chỉ có comment hoặc rỗng
        no_comments = re.sub(r'--[^\n]*', '', stripped).strip()
        if no_comments:
            stmts.append(stripped)
    return stmts


def seed_dims() -> bool:
    """
    Thực thi 02_seed_dims.sql, trả True nếu tất cả dim thành công.
    """
    if not SQL_FILE.exists():
        logger.error("Không tìm thấy %s", SQL_FILE)
        return False

    sql_content = SQL_FILE.read_text(encoding="utf-8")
    statements  = _split_statements(sql_content)

    logger.info("=== BẮT ĐẦU SEED DIMENSIONS (%d câu lệnh) ===", len(statements))

    engine = get_engine()

    # Thực thi từng statement trong cùng một transaction
    # (rollback toàn bộ nếu một dim lỗi → tránh trạng thái nửa vời)
    try:
        with engine.begin() as conn:
            for i, stmt in enumerate(statements, 1):
                # Lấy tên dim từ dòng INSERT đầu tiên để log
                dim_match = re.search(r'INSERT\s+INTO\s+[\w.]*?(dim_\w+|etl_log)', stmt, re.I)
                label = dim_match.group(1) if dim_match else f"stmt_{i}"

                logger.info("[%d/%d] Seeding %-20s …", i, len(statements), label)
                conn.execute(text(stmt))

    except Exception as exc:
        logger.error("Lỗi khi seed dim: %s", exc)
        return False

    logger.info("=== SEED DIMENSIONS HOÀN TẤT ===")
    return True


def print_counts() -> None:
    """In bảng đếm số dòng cho staging và tất cả dim."""
    engine = get_engine()

    rows: list[tuple[str, int]] = []

    with engine.connect() as conn:
        # Staging
        for tbl in STAGING_TABLES:
            cnt = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
            rows.append((tbl, cnt))

        # Dims
        for dim in DIM_TABLES:
            cnt = conn.execute(
                text(f"SELECT COUNT(*) FROM cinema_dw.{dim}")
            ).scalar()
            rows.append((f"cinema_dw.{dim}", cnt))

    # --- In bảng ---
    print()
    print("┌──────────────────────────────┬───────────────┐")
    print("│ Bảng                         │   Số dòng     │")
    print("├──────────────────────────────┼───────────────┤")
    for tbl, cnt in rows:
        print(f"│ {tbl:<30} │ {cnt:>13,} │")
    print("└──────────────────────────────┴───────────────┘")

    # Kiểm tra nhanh một số kỳ vọng
    dim_counts = {r[0].split(".")[-1]: r[1] for r in rows}

    checks = [
        ("dim_date",     548,   548,   "2025-04-01→2026-09-30 = 548 ngày"),
        ("dim_time",    1440,  1440,   "1440 phút/ngày"),
        ("dim_cinema",     3,     3,   "3 rạp"),
        ("dim_room",       8,     8,   "8 phòng"),
        ("dim_movie",     24,    24,   "24 phim"),
        ("dim_channel",    3,     3,   "POS/APP/WEB"),
        ("dim_product",    8,     8,   "8 sản phẩm F&B"),
    ]

    print()
    all_ok = True
    for name, lo, hi, desc in checks:
        cnt = dim_counts.get(name, 0)
        ok  = lo <= cnt <= hi
        sym = "✅" if ok else "❌"
        print(f"  {sym} {name}: {cnt:,}  ({desc})")
        if not ok:
            all_ok = False

    # GUEST check
    with engine.connect() as conn:
        guest_cnt = conn.execute(
            text("SELECT COUNT(*) FROM cinema_dw.dim_customer WHERE customer_id = 'GUEST' AND is_guest = TRUE")
        ).scalar()
    ok_guest = (guest_cnt == 1)
    print(f"  {'✅' if ok_guest else '❌'} dim_customer GUEST: {guest_cnt} dòng (kỳ vọng đúng 1)")
    if not ok_guest:
        all_ok = False

    print()
    if all_ok:
        print("  ✅ TẤT CẢ KIỂM TRA NHANH ĐẠT")
    else:
        print("  ❌ MỘT SỐ KIỂM TRA CHƯA ĐẠT — xem log để biết chi tiết")
        logger.warning("Một số kiểm tra nhanh sau seed_dims thất bại.")


if __name__ == "__main__":
    ok = seed_dims()
    if not ok:
        sys.exit(1)
    print_counts()
    sys.exit(0)

"""
etl/check_schema.py
-------------------
Kết nối DB, in danh sách tất cả bảng trong schema cinema_dw và staging,
xác nhận có đủ 9 dim + 2 fact + etl_log + 2 bảng staging.

Chạy:
    python etl/check_schema.py

Trả mã thoát 0 nếu đủ bảng, 1 nếu thiếu.
"""

from __future__ import annotations

import sys
from sqlalchemy import text

# Import engine và logger từ db.py — logging đã được cấu hình sẵn
from etl.db import get_engine, logger

# ---------------------------------------------------------------------------
# Danh sách bảng mong đợi
# ---------------------------------------------------------------------------
EXPECTED_CINEMA_DW = {
    # 9 Dimensions
    "dim_date",
    "dim_time",
    "dim_movie",
    "dim_cinema",
    "dim_room",
    "dim_customer",
    "dim_product",
    "dim_pricing",
    "dim_channel",
    # 2 Facts
    "fact_ticket",
    "fact_fnb",
    # ETL support
    "etl_log",
}

EXPECTED_STAGING = {"ticket", "fnb"}

SQL_LIST_TABLES = text("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema IN ('cinema_dw', 'staging')
      AND table_type = 'BASE TABLE'
    ORDER BY table_schema, table_name;
""")


def check_schema() -> int:
    """
    Kiểm tra schema DB.
    Trả 0 nếu tất cả bảng tồn tại, 1 nếu thiếu.
    """
    engine = get_engine()
    logger.info("=== BẮT ĐẦU KIỂM TRA SCHEMA ===")

    try:
        with engine.connect() as conn:
            rows = conn.execute(SQL_LIST_TABLES).fetchall()
    except Exception as exc:
        logger.error("Không thể truy vấn schema: %s", exc)
        return 1

    # Phân nhóm theo schema
    cinema_dw_tables: set[str] = set()
    staging_tables: set[str] = set()

    for schema, table in rows:
        if schema == "cinema_dw":
            cinema_dw_tables.add(table)
        elif schema == "staging":
            staging_tables.add(table)

    # ---------------------------------------------------------------------------
    # In kết quả
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print(f"{'Schema cinema_dw':^60}")
    print("=" * 60)
    for t in sorted(cinema_dw_tables):
        tag = "✓" if t in EXPECTED_CINEMA_DW else "?"
        print(f"  [{tag}]  {t}")

    print(f"\n{'Schema staging':^60}")
    print("-" * 60)
    for t in sorted(staging_tables):
        tag = "✓" if t in EXPECTED_STAGING else "?"
        print(f"  [{tag}]  {t}")
    print("=" * 60)

    # ---------------------------------------------------------------------------
    # Kiểm tra thiếu
    # ---------------------------------------------------------------------------
    missing_dw  = EXPECTED_CINEMA_DW - cinema_dw_tables
    missing_stg = EXPECTED_STAGING   - staging_tables
    extra_dw    = cinema_dw_tables   - EXPECTED_CINEMA_DW

    if missing_dw:
        logger.error("THIẾU bảng trong cinema_dw: %s", sorted(missing_dw))
    if missing_stg:
        logger.error("THIẾU bảng trong staging: %s", sorted(missing_stg))
    if extra_dw:
        logger.warning("Bảng không mong đợi trong cinema_dw: %s", sorted(extra_dw))

    # Tóm tắt
    total_ok = len(cinema_dw_tables & EXPECTED_CINEMA_DW)
    print(f"\n  cinema_dw : {total_ok}/{len(EXPECTED_CINEMA_DW)} bảng đúng")
    print(f"  staging   : {len(staging_tables & EXPECTED_STAGING)}/{len(EXPECTED_STAGING)} bảng đúng")

    if missing_dw or missing_stg:
        print("\n  ❌ KIỂM TRA THẤT BẠI — Xem log để biết bảng còn thiếu.")
        logger.error("=== KIỂM TRA SCHEMA THẤT BẠI ===")
        return 1

    print("\n  ✅ KIỂM TRA SCHEMA THÀNH CÔNG")
    logger.info("Kiểm tra schema thành công: %d bảng cinema_dw, %d bảng staging",
                len(cinema_dw_tables), len(staging_tables))
    return 0


if __name__ == "__main__":
    sys.exit(check_schema())

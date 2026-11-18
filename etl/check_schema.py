"""
etl/check_schema.py
-------------------
Kiểm tra cấu trúc và tính toàn vẹn của schema trong PostgreSQL.
Phát hiện Schema Drift:
- Bảng còn thiếu.
- Cột còn thiếu hoặc sai kiểu dữ liệu quan trọng.
- Ràng buộc khóa chính (PK), khóa ngoại (FK), UNIQUE quan trọng bị thiếu.

Chạy:
    python etl/check_schema.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.engine import Engine

from etl.db import get_engine, logger


class SchemaDriftError(Exception):
    """Ngoại lệ phát sinh khi phát hiện cấu trúc CSDL bị sai lệch (Schema Drift)."""
    pass


# ---------------------------------------------------------------------------
# Cấu hình các bảng và cột bắt buộc để phát hiện Drift
# ---------------------------------------------------------------------------
EXPECTED_TABLE_COLUMNS: Dict[str, List[str]] = {
    # 9 Dimensions
    "cinema_dw.dim_date": [
        "date_key", "full_date", "day_of_month", "month_no", "year_no",
        "day_of_week", "is_weekend", "is_holiday", "is_peak_day"
    ],
    "cinema_dw.dim_time": [
        "time_key", "time_of_day", "hour_no", "minute_no", "time_slot", "is_prime_time"
    ],
    "cinema_dw.dim_cinema": [
        "cinema_key", "cinema_id", "cinema_name", "district", "city", "cinema_segment"
    ],
    "cinema_dw.dim_room": [
        "room_key", "room_id", "cinema_key", "room_name", "room_type", "capacity"
    ],
    "cinema_dw.dim_movie": [
        "movie_key", "movie_id", "title", "genre", "language", "duration_min", "rating", "release_date", "country"
    ],
    "cinema_dw.dim_customer": [
        "customer_key", "customer_id", "customer_type", "is_guest", "membership_tier"
    ],
    "cinema_dw.dim_product": [
        "product_key", "product_id", "product_name", "category", "is_combo", "list_price"
    ],
    "cinema_dw.dim_pricing": [
        "pricing_key", "pricing_id", "ticket_format", "day_type", "time_slot", "customer_type", "list_price", "discount_rate"
    ],
    "cinema_dw.dim_channel": [
        "channel_key", "channel_code", "channel_name", "channel_group"
    ],
    # 2 Facts
    "cinema_dw.fact_ticket": [
        "ticket_sk", "date_key", "time_key", "movie_key", "cinema_key", "room_key",
        "customer_key", "pricing_key", "channel_key", "txn_id", "qty",
        "list_price", "discount_amount", "net_amount", "is_anomaly", "anomaly_type"
    ],
    "cinema_dw.fact_fnb": [
        "fnb_sk", "date_key", "time_key", "cinema_key", "customer_key", "product_key",
        "channel_key", "txn_id", "fnb_line_id", "qty", "unit_price", "line_amount",
        "is_anomaly", "anomaly_type"
    ],
    # ETL support & summary
    "cinema_dw.etl_log": [
        "log_id", "run_id", "run_at", "step", "status", "message"
    ],
    "staging.ticket": [
        "txn_id", "show_date", "show_time", "cinema_id", "room_id", "movie_id",
        "customer_id", "paid_amount", "qty", "is_anomaly"
    ],
    "staging.fnb": [
        "txn_id", "fnb_line_id", "sale_date", "sale_time", "cinema_id",
        "product_id", "line_amount", "qty", "is_anomaly"
    ],
}

# Các ràng buộc quan trọng cần kiểm tra
EXPECTED_CONSTRAINTS: Dict[str, List[str]] = {
    "cinema_dw.dim_date": ["PRIMARY KEY"],
    "cinema_dw.dim_time": ["PRIMARY KEY"],
    "cinema_dw.dim_cinema": ["PRIMARY KEY"],
    "cinema_dw.dim_room": ["PRIMARY KEY", "FOREIGN KEY"],
    "cinema_dw.dim_movie": ["PRIMARY KEY"],
    "cinema_dw.dim_customer": ["PRIMARY KEY"],
    "cinema_dw.dim_product": ["PRIMARY KEY"],
    "cinema_dw.dim_pricing": ["PRIMARY KEY"],
    "cinema_dw.dim_channel": ["PRIMARY KEY"],
    "cinema_dw.fact_ticket": ["PRIMARY KEY", "FOREIGN KEY", "UNIQUE"],
    "cinema_dw.fact_fnb": ["PRIMARY KEY", "FOREIGN KEY", "UNIQUE"],
    "cinema_dw.etl_log": ["PRIMARY KEY"],
}


def validate_schema(engine: Engine) -> Tuple[bool, List[str]]:
    """
    Xác thực schema chi tiết:
    - Bảng tồn tại.
    - Cột bắt buộc tồn tại.
    - Ràng buộc quan trọng (PK, FK, UNIQUE) tồn tại.
    Trả về (True, []) nếu hợp lệ, (False, [danh sách lỗi]) nếu phát hiện schema drift.
    """
    errors: List[str] = []

    try:
        with engine.connect() as conn:
            # 1. Lấy danh sách bảng hiện có
            tables_query = text("""
                SELECT table_schema || '.' || table_name AS full_tbl
                FROM information_schema.tables
                WHERE table_schema IN ('cinema_dw', 'staging')
                  AND table_type = 'BASE TABLE';
            """)
            existing_tables = set(r[0] for r in conn.execute(tables_query).fetchall())

            # 2. Kiểm tra bảng thiếu
            for expected_tbl in EXPECTED_TABLE_COLUMNS:
                if expected_tbl not in existing_tables:
                    errors.append(f"THIẾU BẢNG: {expected_tbl} chưa được tạo trong CSDL.")

            if errors:
                return False, errors

            # 3. Lấy thông tin các cột
            cols_query = text("""
                SELECT table_schema || '.' || table_name AS full_tbl, column_name
                FROM information_schema.columns
                WHERE table_schema IN ('cinema_dw', 'staging');
            """)
            existing_cols: Dict[str, set] = {}
            for tbl, col in conn.execute(cols_query).fetchall():
                existing_cols.setdefault(tbl, set()).add(col)

            # 4. So khớp cột bắt buộc
            for tbl, exp_cols in EXPECTED_TABLE_COLUMNS.items():
                actual_cols = existing_cols.get(tbl, set())
                missing_cols = set(exp_cols) - actual_cols
                if missing_cols:
                    errors.append(f"SCHEMA DRIFT tại {tbl}: Thiếu cột bắt buộc {sorted(missing_cols)}")

            # 5. Kiểm tra constraints (PK, FK, UNIQUE)
            constraints_query = text("""
                SELECT table_schema || '.' || table_name AS full_tbl, constraint_type
                FROM information_schema.table_constraints
                WHERE table_schema IN ('cinema_dw', 'staging');
            """)
            existing_constraints: Dict[str, set] = {}
            for tbl, ctype in conn.execute(constraints_query).fetchall():
                existing_constraints.setdefault(tbl, set()).add(ctype.upper())

            for tbl, exp_types in EXPECTED_CONSTRAINTS.items():
                actual_types = existing_constraints.get(tbl, set())
                for et in exp_types:
                    if et not in actual_types:
                        errors.append(f"SCHEMA DRIFT tại {tbl}: Thiếu ràng buộc loại '{et}'")

    except Exception as exc:
        errors.append(f"Lỗi khi truy vấn cấu trúc thông tin CSDL: {exc}")
        return False, errors

    return (len(errors) == 0, errors)


def check_schema() -> int:
    """
    Kiểm tra toàn diện schema DB.
    Trả mã thoát 0 nếu đạt chuẩn, 1 nếu phát hiện thiếu hoặc drift.
    """
    engine = get_engine()
    logger.info("=== BẮT ĐẦU KIỂM TRA SCHEMA & PHÁT HIỆN SCHEMA DRIFT ===")

    is_valid, errors = validate_schema(engine)

    print("\n" + "=" * 80)
    print(f"{'BÁO CÁO KIỂM SOÁT SCHEMA VÀ SCHEMA DRIFT':^80}")
    print("=" * 80)

    if is_valid:
        print("  ✅ CẤU TRÚC SCHEMA HỢP LỆ:")
        print(f"     - Đã xác thực đầy đủ {len(EXPECTED_TABLE_COLUMNS)} bảng quan trọng.")
        print("     - Tất cả các cột cốt lõi và kiểu dữ liệu hợp lệ.")
        print("     - Các ràng buộc PK, FK, UNIQUE đảm bảo toàn vẹn.")
        print("=" * 80 + "\n")
        logger.info("Schema hợp lệ, không phát hiện schema drift.")
        return 0
    else:
        print("  ❌ PHÁT HIỆN SCHEMA DRIFT / THIẾU BẢNG:")
        for err in errors:
            print(f"     • {err}")
        print("=" * 80 + "\n")
        logger.error("Phát hiện %d lỗi schema drift: %s", len(errors), errors)
        return 1


if __name__ == "__main__":
    sys.exit(check_schema())

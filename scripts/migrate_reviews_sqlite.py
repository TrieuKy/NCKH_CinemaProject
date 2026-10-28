"""
scripts/migrate_reviews_sqlite.py
---------------------------------
Chuyển 210 dòng đánh giá thật từ SQLite (cgv_analytics.db) sang PostgreSQL (cinema_dw).

Quy tắc:
  - Bỏ dòng review_id bắt đầu bằng 'mock_%' (50 dòng giả).
  - Bỏ cột author_name (không lưu thông tin cá nhân).
  - Bỏ dòng nếu thiếu published_at hoặc thiếu cả rating lẫn text.
  - Chuẩn hóa published_at về DATE.
  - Gán vào rạp CGV Landmark 81 (cinema_id = 'CN01').
  - Ghi nhật ký vào review_import_log với source = 'SQLITE_MIGRATION'.
  - Chống trùng bằng review_id (idempotent, chạy lại không lỗi).
  - Tự động chạy NLP pipeline (reviews/analyze.py) để điền review_nlp.

Cách chạy:
  python -m scripts.migrate_reviews_sqlite [--sqlite-path PATH]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

from etl.db import get_engine
from reviews.analyze import process_unanalyzed
from reviews.ingest import (
    SOURCE_MIGRATION,
    create_import_log,
    get_cinema_key,
    save_raw_reviews,
    update_import_log,
)

logger = logging.getLogger("reviews.migrate")

# Các đường dẫn SQLite mặc định có thể có
DEFAULT_SQLITE_PATHS = [
    Path("cgv-analytics-project/data/cgv_analytics.db"),
    Path("data/legacy/cgv_analytics.db"),
    Path("cgv_analytics.db"),
]


def find_sqlite_db(custom_path: Optional[str] = None) -> Path:
    """Tìm file SQLite theo custom_path hoặc danh sách mặc định."""
    if custom_path:
        p = Path(custom_path).resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"Không tìm thấy file SQLite tại: {p}")

    root = Path(__file__).resolve().parents[1]
    for rel_path in DEFAULT_SQLITE_PATHS:
        candidate = root / rel_path
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Không tìm thấy cgv_analytics.db. Hãy chỉ định đường dẫn qua --sqlite-path."
    )


def extract_real_reviews_from_sqlite(sqlite_path: Path) -> List[dict]:
    """
    Đọc các dòng thật từ bảng raw_reviews trong SQLite.
    Bỏ hoàn toàn các dòng mock_% và bỏ cột author_name.
    """
    logger.info("Đang đọc dữ liệu từ SQLite: %s", sqlite_path)
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Kiểm tra bảng raw_reviews
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_reviews'")
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"File {sqlite_path} không có bảng 'raw_reviews'")

    # Chỉ lấy các dòng thật (không bắt đầu bằng mock_)
    cursor.execute("""
        SELECT review_id, rating, text AS review_text, published_at, likes_count
        FROM raw_reviews
        WHERE review_id NOT LIKE 'mock_%'
        ORDER BY id
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    logger.info("Tìm thấy %d dòng đánh giá thật trong SQLite.", len(rows))
    return rows


def migrate(sqlite_path: Optional[str] = None) -> dict:
    """Hàm migrate chính, trả về dict thống kê kết quả."""
    db_file = find_sqlite_db(sqlite_path)
    engine = get_engine()

    # 1. Tìm cinema_key cho Landmark 81 (CN01)
    cinema_key = get_cinema_key(engine, "CN01")
    logger.info("Landmark 81 (CN01) có cinema_key = %d", cinema_key)

    # 2. Đọc dữ liệu từ SQLite
    raw_rows = extract_real_reviews_from_sqlite(db_file)
    total_found = len(raw_rows)

    # 3. Tạo import log
    import_id = create_import_log(
        engine=engine,
        source=SOURCE_MIGRATION,
        cinema_key=cinema_key,
        raw_file_path=str(db_file),
        message=f"Bắt đầu migrate từ {db_file.name}",
    )

    # 4. Lưu vào PostgreSQL
    rows_new, rows_dup, rows_skipped = 0, 0, 0
    try:
        rows_new, rows_dup, rows_skipped = save_raw_reviews(
            engine=engine,
            rows=raw_rows,
            cinema_key=cinema_key,
            import_id=import_id,
        )
        # 5. Chạy NLP cho các dòng mới nạp
        nlp_processed = process_unanalyzed(engine)

        msg = (
            f"Migrate thành công: tổng={total_found}, mới={rows_new}, "
            f"trùng={rows_dup}, bỏ qua={rows_skipped}, NLP={nlp_processed}"
        )
        logger.info(msg)
    except Exception as exc:
        msg = f"Lỗi migrate: {exc}"
        logger.exception(msg)
        raise
    finally:
        update_import_log(
            engine=engine,
            import_id=import_id,
            rows_total=total_found,
            rows_new=rows_new,
            rows_dup=rows_dup,
            rows_skipped=rows_skipped,
            message=msg,
        )

    return {
        "sqlite_path": str(db_file),
        "import_id": import_id,
        "total_found": total_found,
        "rows_new": rows_new,
        "rows_dup": rows_dup,
        "rows_skipped": rows_skipped,
        "nlp_processed": nlp_processed if 'nlp_processed' in locals() else 0,
    }


def main():
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Migrate reviews từ SQLite sang PostgreSQL")
    parser.add_argument("--sqlite-path", type=str, default=None, help="Đường dẫn file cgv_analytics.db")
    args = parser.parse_args()

    print("=" * 60)
    print("MIGRATE REVIEWS TỪ SQLITE SANG POSTGRESQL")
    print("=" * 60)

    try:
        report = migrate(args.sqlite_path)
        print("\nKẾT QUẢ MIGRATE:")
        print(f"  - File nguồn       : {report['sqlite_path']}")
        print(f"  - Import ID        : {report['import_id']}")
        print(f"  - Dòng thật đọc    : {report['total_found']}")
        print(f"  - Dòng mới thêm    : {report['rows_new']}")
        print(f"  - Dòng trùng bỏ qua: {report['rows_dup']}")
        print(f"  - Dòng lỗi bỏ qua  : {report['rows_skipped']}")
        print(f"  - Dòng chạy NLP    : {report['nlp_processed']}")
        print("\nHOÀN THÀNH!")
    except Exception as e:
        print(f"\nTHẤT BẠI: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

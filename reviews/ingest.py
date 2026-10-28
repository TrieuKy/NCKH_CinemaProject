"""
reviews/ingest.py
-----------------
Nhập đánh giá thô vào bảng review_raw (PostgreSQL).

Quy tắc lọc (theo quyết định đã chốt):
  - Bỏ dòng nếu thiếu published_date (không gán ngày hiện tại).
  - Bỏ dòng nếu thiếu CẢ rating lẫn review_text (không có gì để phân tích).
  - rating thiếu → lưu NULL (không gán 5.0).
  - Không lưu author_name.
  - Chống trùng: review_id UNIQUE → INSERT ON CONFLICT DO NOTHING.
  - Mọi lần chạy ghi 1 dòng vào review_import_log.

Chạy module: python -m reviews.ingest  (chỉ kiểm tra import)
Không đọc .env — nhận engine từ tham số.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from reviews.nlp import detect_lang

logger = logging.getLogger("reviews.ingest")

# ---------------------------------------------------------------------------
# Hằng nguồn nhập
# ---------------------------------------------------------------------------
SOURCE_APIFY     = "APIFY_CRAWLER"
SOURCE_MIGRATION = "SQLITE_MIGRATION"
SOURCE_MANUAL    = "MANUAL"

# ---------------------------------------------------------------------------
# Tiện ích parse ngày
# ---------------------------------------------------------------------------
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",   # ISO 8601 có milli-giây UTC (định dạng Apify thật)
    "%Y-%m-%dT%H:%M:%SZ",       # ISO 8601 không milli-giây UTC
    "%Y-%m-%dT%H:%M:%S",        # ISO 8601 không timezone
    "%Y-%m-%d %H:%M:%S",        # datetime string thường
    "%Y-%m-%d",                  # chỉ ngày
]


def parse_date(raw: Any) -> Optional[date]:
    """
    Parse ngày từ nhiều định dạng → date.
    Trả None nếu không parse được (dòng sẽ bị bỏ qua).
    """
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()

    raw_str = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw_str, fmt).date()
        except ValueError:
            continue
    logger.warning("Không parse được ngày: %r", raw)
    return None


def parse_rating(raw: Any) -> Optional[float]:
    """
    Parse số sao → float [1.0, 5.0] hoặc None.
    Không gán mặc định 5.0 khi thiếu.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
        if 1.0 <= val <= 5.0:
            return val
        logger.warning("Số sao ngoài khoảng [1,5]: %r — lưu NULL", raw)
        return None
    except (ValueError, TypeError):
        logger.warning("Không parse được số sao: %r — lưu NULL", raw)
        return None


# ---------------------------------------------------------------------------
# Tạo import_log và trả import_id
# ---------------------------------------------------------------------------

def create_import_log(
    engine: Engine,
    source: str,
    cinema_key: Optional[int] = None,
    actor_name: Optional[str] = None,
    apify_run_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    raw_file_path: Optional[str] = None,
    message: Optional[str] = None,
) -> int:
    """Tạo 1 dòng log trong review_import_log, trả import_id."""
    sql = text("""
        INSERT INTO cinema_dw.review_import_log
            (cinema_key, source, actor_name, apify_run_id, dataset_id,
             raw_file_path, message)
        VALUES
            (:cinema_key, :source, :actor_name, :apify_run_id, :dataset_id,
             :raw_file_path, :message)
        RETURNING import_id
    """)
    with engine.begin() as conn:
        row = conn.execute(sql, {
            "cinema_key":    cinema_key,
            "source":        source,
            "actor_name":    actor_name,
            "apify_run_id":  apify_run_id,
            "dataset_id":    dataset_id,
            "raw_file_path": raw_file_path,
            "message":       message,
        }).fetchone()
    return row[0]


def update_import_log(
    engine: Engine,
    import_id: int,
    rows_total: int,
    rows_new: int,
    rows_dup: int,
    rows_skipped: int,
    message: Optional[str] = None,
) -> None:
    """Cập nhật số liệu sau khi nhập xong."""
    sql = text("""
        UPDATE cinema_dw.review_import_log
        SET rows_total   = :rows_total,
            rows_new     = :rows_new,
            rows_dup     = :rows_dup,
            rows_skipped = :rows_skipped,
            message      = COALESCE(:message, message)
        WHERE import_id  = :import_id
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "import_id":    import_id,
            "rows_total":   rows_total,
            "rows_new":     rows_new,
            "rows_dup":     rows_dup,
            "rows_skipped": rows_skipped,
            "message":      message,
        })


# ---------------------------------------------------------------------------
# Hàm nhập chính
# ---------------------------------------------------------------------------

def save_raw_reviews(
    engine: Engine,
    rows: List[Dict[str, Any]],
    cinema_key: int,
    import_id: int,
) -> Tuple[int, int, int]:
    """
    Nhập danh sách đánh giá thô vào review_raw.

    Mỗi dict trong rows cần:
      review_id     : str (bắt buộc)
      rating        : float | None
      review_text   : str | None
      published_at  : str | date | datetime (bắt buộc — bỏ nếu thiếu)
      likes_count   : int (mặc định 0)
      apify_lang    : str | None (để phát hiện lang)

    Trả (rows_new, rows_dup, rows_skipped).
    """
    if not rows:
        return 0, 0, 0

    sql_insert = text("""
        INSERT INTO cinema_dw.review_raw
            (review_id, import_id, cinema_key, rating, review_text,
             lang, published_date, likes_count)
        VALUES
            (:review_id, :import_id, :cinema_key, :rating, :review_text,
             :lang, :published_date, :likes_count)
        ON CONFLICT (review_id) DO NOTHING
    """)

    rows_new = rows_dup = rows_skipped = 0

    with engine.begin() as conn:
        for r in rows:
            review_id = r.get("review_id")
            if not review_id:
                logger.warning("Bỏ dòng không có review_id")
                rows_skipped += 1
                continue

            # Kiểm tra ngày: bỏ nếu thiếu (không gán ngày hiện tại)
            published_date = parse_date(r.get("published_at"))
            if published_date is None:
                logger.debug("Bỏ %s: thiếu published_date", review_id)
                rows_skipped += 1
                continue

            rating     = parse_rating(r.get("rating"))
            text_val   = (r.get("review_text") or "").strip() or None
            has_data   = (rating is not None) or (text_val is not None)

            # Bỏ nếu thiếu cả sao lẫn chữ
            if not has_data:
                logger.debug("Bỏ %s: thiếu cả rating lẫn review_text", review_id)
                rows_skipped += 1
                continue

            lang = detect_lang(text_val, r.get("apify_lang"))

            result = conn.execute(sql_insert, {
                "review_id":      review_id,
                "import_id":      import_id,
                "cinema_key":     cinema_key,
                "rating":         rating,
                "review_text":    text_val,
                "lang":           lang,
                "published_date": published_date,
                "likes_count":    int(r.get("likes_count") or 0),
            })

            if result.rowcount > 0:
                rows_new += 1
            else:
                rows_dup += 1

    logger.info(
        "save_raw_reviews: total=%d new=%d dup=%d skipped=%d",
        len(rows), rows_new, rows_dup, rows_skipped,
    )
    return rows_new, rows_dup, rows_skipped


def ingest(
    engine: Engine,
    rows: List[Dict[str, Any]],
    cinema_key: int,
    source: str = SOURCE_APIFY,
    actor_name: Optional[str] = None,
    apify_run_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    raw_file_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Entry-point tích hợp: tạo log → nhập raw → cập nhật log.
    Trả dict báo cáo.
    """
    # Tạo log trước để có import_id ngay cả khi bị lỗi giữa chừng
    import_id = create_import_log(
        engine, source,
        cinema_key=cinema_key,
        actor_name=actor_name,
        apify_run_id=apify_run_id,
        dataset_id=dataset_id,
        raw_file_path=raw_file_path,
    )

    rows_new = rows_dup = rows_skipped = 0
    try:
        rows_new, rows_dup, rows_skipped = save_raw_reviews(
            engine, rows, cinema_key, import_id
        )
        msg = f"OK: new={rows_new} dup={rows_dup} skipped={rows_skipped}"
    except Exception as exc:
        msg = f"LỖI: {exc}"
        logger.exception("ingest thất bại: %s", exc)
        raise
    finally:
        update_import_log(
            engine, import_id,
            rows_total=len(rows),
            rows_new=rows_new,
            rows_dup=rows_dup,
            rows_skipped=rows_skipped,
            message=msg,
        )

    return {
        "import_id":    import_id,
        "rows_total":   len(rows),
        "rows_new":     rows_new,
        "rows_dup":     rows_dup,
        "rows_skipped": rows_skipped,
    }


# ---------------------------------------------------------------------------
# Truy vấn tiện ích
# ---------------------------------------------------------------------------

def get_cinema_key(engine: Engine, cinema_id: str) -> int:
    """Lấy cinema_key từ cinema_id. Raise ValueError nếu không tìm thấy."""
    sql = text("SELECT cinema_key FROM cinema_dw.dim_cinema WHERE cinema_id = :cid")
    with engine.connect() as conn:
        row = conn.execute(sql, {"cid": cinema_id}).fetchone()
    if row is None:
        raise ValueError(f"Không tìm thấy cinema_id={cinema_id!r} trong dim_cinema")
    return row[0]


if __name__ == "__main__":
    print("reviews.ingest — import OK")

"""
reviews/analyze.py
------------------
Chạy NLP pipeline trên review_raw chưa được xử lý → ghi kết quả vào review_nlp.

Hai chế độ:
  1. process_unanalyzed(engine) : xử lý các dòng is_processed=FALSE
  2. reanalyze_all(engine, model_version) : phân tích lại TOÀN BỘ
     (dùng khi từ điển thay đổi → tăng model_version)

Từ điển chủ đề: tải từ bảng review_topic_dict (is_active=TRUE).
Nếu DB không có topic nào → dùng DEFAULT_TOPIC_KEYWORDS từ nlp.py.

Không đọc .env — nhận engine từ tham số.
Chạy module: python -m reviews.analyze --help
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from reviews.nlp import DEFAULT_TOPIC_KEYWORDS, process_batch

logger = logging.getLogger("reviews.analyze")

# Phiên bản model mặc định — tăng khi đổi từ điển hoặc thuật toán
CURRENT_MODEL_VERSION = "v1"

# Số dòng xử lý mỗi lần (tránh load toàn bộ vào RAM)
BATCH_SIZE = 200


# ---------------------------------------------------------------------------
# Tải từ điển từ DB
# ---------------------------------------------------------------------------

def load_topic_dict(engine: Engine) -> Optional[Dict[str, List[str]]]:
    """
    Tải từ điển chủ đề từ review_topic_dict (is_active=TRUE, có keywords).
    Bỏ qua 'Khác' (keywords rỗng).
    Trả None nếu không có dữ liệu → pipeline dùng DEFAULT_TOPIC_KEYWORDS.
    """
    sql = text("""
        SELECT topic_name, keywords
        FROM cinema_dw.review_topic_dict
        WHERE is_active = TRUE
          AND array_length(keywords, 1) > 0
        ORDER BY topic_id
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()

    if not rows:
        logger.info("Không tìm thấy từ điển trong DB — dùng mặc định.")
        return None

    topic_dict: Dict[str, List[str]] = {}
    for row in rows:
        topic_dict[row[0]] = list(row[1])
    logger.info("Đã tải %d chủ đề từ DB.", len(topic_dict))
    return topic_dict


# ---------------------------------------------------------------------------
# Lấy dữ liệu thô cần xử lý
# ---------------------------------------------------------------------------

def _fetch_unprocessed(
    engine: Engine,
    limit: int = BATCH_SIZE,
    offset: int = 0,
) -> List[Dict]:
    """Lấy các dòng review_raw is_processed=FALSE."""
    sql = text("""
        SELECT review_id, rating, review_text
        FROM cinema_dw.review_raw
        WHERE is_processed = FALSE
        ORDER BY raw_id
        LIMIT :limit OFFSET :offset
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": limit, "offset": offset}).fetchall()
    return [{"review_id": r[0], "rating": r[1], "review_text": r[2]} for r in rows]


def _fetch_all(
    engine: Engine,
    limit: int = BATCH_SIZE,
    offset: int = 0,
) -> List[Dict]:
    """Lấy TẤT CẢ dòng review_raw (dùng khi tái phân tích)."""
    sql = text("""
        SELECT review_id, rating, review_text
        FROM cinema_dw.review_raw
        ORDER BY raw_id
        LIMIT :limit OFFSET :offset
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": limit, "offset": offset}).fetchall()
    return [{"review_id": r[0], "rating": r[1], "review_text": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Ghi kết quả NLP vào DB
# ---------------------------------------------------------------------------

def _upsert_nlp_batch(
    engine: Engine,
    results: List[Dict],
) -> int:
    """
    INSERT hoặc UPDATE review_nlp.
    ON CONFLICT (review_id) DO UPDATE — ghi đè kết quả cũ khi tái phân tích.
    Đánh dấu is_processed=TRUE trong review_raw.
    Trả số dòng đã ghi.
    """
    if not results:
        return 0

    sql_nlp = text("""
        INSERT INTO cinema_dw.review_nlp
            (review_id, sentiment_rating, sentiment_text, sentiment_final,
             sentiment_score, topics, model_version)
        VALUES
            (:review_id, :sentiment_rating, :sentiment_text, :sentiment_final,
             :sentiment_score, :topics, :model_version)
        ON CONFLICT (review_id) DO UPDATE SET
            sentiment_rating = EXCLUDED.sentiment_rating,
            sentiment_text   = EXCLUDED.sentiment_text,
            sentiment_final  = EXCLUDED.sentiment_final,
            sentiment_score  = EXCLUDED.sentiment_score,
            topics           = EXCLUDED.topics,
            model_version    = EXCLUDED.model_version,
            analyzed_at      = NOW()
    """)

    sql_mark = text("""
        UPDATE cinema_dw.review_raw
        SET is_processed = TRUE
        WHERE review_id = :review_id
    """)

    with engine.begin() as conn:
        for r in results:
            conn.execute(sql_nlp, {
                "review_id":        r["review_id"],
                "sentiment_rating": r["sentiment_rating"],
                "sentiment_text":   r["sentiment_text"],
                "sentiment_final":  r["sentiment_final"],
                "sentiment_score":  r["sentiment_score"],
                "topics":           r["topics"],      # list → psycopg2 tự convert ARRAY
                "model_version":    r["model_version"],
            })
            conn.execute(sql_mark, {"review_id": r["review_id"]})

    return len(results)


# ---------------------------------------------------------------------------
# API chính
# ---------------------------------------------------------------------------

def process_unanalyzed(
    engine: Engine,
    model_version: str = CURRENT_MODEL_VERSION,
) -> int:
    """
    Xử lý tất cả dòng review_raw có is_processed=FALSE.
    Trả tổng số dòng đã phân tích.
    """
    topic_dict = load_topic_dict(engine)
    if topic_dict is None:
        topic_dict = DEFAULT_TOPIC_KEYWORDS

    total = 0
    while True:
        # Không dùng offset vì các dòng vừa xử lý đã được gán is_processed=TRUE
        batch = _fetch_unprocessed(engine, limit=BATCH_SIZE, offset=0)
        if not batch:
            break
        results = process_batch(batch, topic_keywords=topic_dict, model_version=model_version)
        written = _upsert_nlp_batch(engine, results)
        total += written
        logger.info("Đã phân tích %d dòng (tổng: %d).", written, total)

    logger.info("process_unanalyzed hoàn thành: %d dòng.", total)
    return total


def reanalyze_all(
    engine: Engine,
    model_version: str = CURRENT_MODEL_VERSION,
) -> int:
    """
    Tái phân tích TOÀN BỘ review_raw với model_version mới.
    Dùng khi từ điển chủ đề thay đổi.
    Trả tổng số dòng.
    """
    topic_dict = load_topic_dict(engine)
    if topic_dict is None:
        topic_dict = DEFAULT_TOPIC_KEYWORDS

    total = 0
    offset = 0
    while True:
        batch = _fetch_all(engine, limit=BATCH_SIZE, offset=offset)
        if not batch:
            break
        results = process_batch(batch, topic_keywords=topic_dict, model_version=model_version)
        written = _upsert_nlp_batch(engine, results)
        total += written
        offset += BATCH_SIZE
        logger.info("Tái phân tích %d dòng (tổng: %d).", written, total)

    logger.info("reanalyze_all hoàn thành: %d dòng.", total)
    return total


# ---------------------------------------------------------------------------
# CLI nhẹ
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("Dùng: python -m reviews.analyze [all|new] [model_version]")
    print("  new : chỉ xử lý is_processed=FALSE (mặc định)")
    print("  all : tái phân tích toàn bộ (dùng khi đổi từ điển)")
    print()
    print("Ví dụ:")
    print("  python -m scripts.migrate_reviews_sqlite  # nhập dữ liệu trước")
    print("  python -m reviews.analyze new             # phân tích các dòng mới")
    print("  python -m reviews.analyze all v2          # tái phân tích với model v2")

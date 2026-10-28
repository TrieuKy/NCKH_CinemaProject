"""
Module: database.py
Mục đích: Quản trị kết nối và thực hiện các thao tác CRUD với SQLite Database
File DB: data/cgv_analytics.db
"""

import os
import sys
import sqlite3
import datetime
import json
from typing import List, Dict, Any, Optional
import pandas as pd

# Đảm bảo in tiếng Việt trên console Windows không bị UnicodeEncodeError
if sys.platform.startswith("win"):
    try:
        if sys.stdout.encoding.lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# Đường dẫn mặc định tới file SQLite Database
DEFAULT_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "cgv_analytics.db")
)


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Tạo hoặc trả về kết nối tới file cơ sở dữ liệu SQLite.
    Tự động tạo thư mục 'data/' nếu chưa tồn tại.
    """
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Cho phép truy cập cột theo tên
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Khởi tạo cấu trúc các bảng trong cơ sở dữ liệu SQLite:
    - raw_reviews: Lưu trữ dữ liệu đánh giá thô sau khi cào về
    - analyzed_reviews: Lưu trữ dữ liệu đánh giá đã qua xử lý NLP
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Bảng lưu trữ đánh giá thô
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raw_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        review_id TEXT UNIQUE NOT NULL,
        author_name TEXT,
        rating REAL,
        text TEXT,
        published_at TEXT,
        likes_count INTEGER DEFAULT 0,
        scraped_at TEXT,
        is_processed INTEGER DEFAULT 0
    );
    """)

    # Bảng lưu trữ đánh giá đã phân tích NLP
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analyzed_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        review_id TEXT UNIQUE NOT NULL,
        author_name TEXT,
        rating REAL,
        text TEXT,
        published_at TEXT,
        likes_count INTEGER DEFAULT 0,
        sentiment TEXT NOT NULL,
        sentiment_score REAL DEFAULT 0.0,
        topics TEXT,
        analyzed_at TEXT,
        FOREIGN KEY (review_id) REFERENCES raw_reviews(review_id)
    );
    """)

    # Tạo chỉ mục (Index) để tối ưu hoá tốc độ truy vấn
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_review_id ON raw_reviews(review_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyzed_sentiment ON analyzed_reviews(sentiment);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyzed_rating ON analyzed_reviews(rating);")

    conn.commit()
    conn.close()
    print(f"[DATABASE] Khởi tạo thành công database tại: {db_path}")


def save_raw_reviews(reviews: List[Dict[str, Any]], db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Lưu danh sách đánh giá thô vào bảng raw_reviews.
    Sử dụng INSERT OR IGNORE để tránh trùng lặp đánh giá đã có theo review_id.

    Returns:
        int: Số lượng bản ghi mới được thêm thành công
    """
    if not reviews:
        return 0

    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted_count = 0

    for r in reviews:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO raw_reviews 
                (review_id, author_name, rating, text, published_at, likes_count, scraped_at, is_processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                r.get("review_id"),
                r.get("author_name"),
                r.get("rating", 5.0),
                r.get("text", ""),
                r.get("published_at"),
                r.get("likes_count", 0),
                now_str
            ))
            if cursor.rowcount > 0:
                inserted_count += 1
        except Exception as e:
            print(f"[DATABASE] Lỗi khi lưu bản ghi {r.get('review_id')}: {e}")

    conn.commit()
    conn.close()
    print(f"[DATABASE] Đã lưu {inserted_count} bản ghi thô mới vào database.")
    return inserted_count


def get_unprocessed_reviews(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """
    Lấy danh sách các đánh giá thô chưa được phân tích NLP (is_processed = 0).
    """
    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT review_id, author_name, rating, text, published_at, likes_count
        FROM raw_reviews
        WHERE is_processed = 0
        ORDER BY id ASC
    """)

    rows = cursor.fetchall()
    unprocessed = [dict(row) for row in rows]
    conn.close()
    return unprocessed


def save_analyzed_reviews(analyzed_records: List[Dict[str, Any]], db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Lưu danh sách kết quả phân tích NLP vào bảng analyzed_reviews
    và cập nhật cờ is_processed = 1 trong bảng raw_reviews.
    """
    if not analyzed_records:
        return 0

    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved_count = 0

    for rec in analyzed_records:
        review_id = rec.get("review_id")
        topics = rec.get("topics", [])
        topics_json = json.dumps(topics, ensure_ascii=False) if isinstance(topics, list) else str(topics)

        try:
            # Lưu hoặc cập nhật vào analyzed_reviews
            cursor.execute("""
                INSERT INTO analyzed_reviews
                (review_id, author_name, rating, text, published_at, likes_count, sentiment, sentiment_score, topics, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    sentiment=excluded.sentiment,
                    sentiment_score=excluded.sentiment_score,
                    topics=excluded.topics,
                    analyzed_at=excluded.analyzed_at
            """, (
                review_id,
                rec.get("author_name"),
                rec.get("rating", 5.0),
                rec.get("text", ""),
                rec.get("published_at"),
                rec.get("likes_count", 0),
                rec.get("sentiment", "NEUTRAL"),
                rec.get("sentiment_score", 0.0),
                topics_json,
                now_str
            ))

            # Đánh dấu đã xử lý trong raw_reviews
            cursor.execute("UPDATE raw_reviews SET is_processed = 1 WHERE review_id = ?", (review_id,))
            saved_count += 1
        except Exception as e:
            print(f"[DATABASE] Lỗi khi lưu kết quả NLP {review_id}: {e}")

    conn.commit()
    conn.close()
    print(f"[DATABASE] Đã lưu {saved_count} bản ghi đã phân tích NLP.")
    return saved_count


def get_all_analyzed_data(db_path: str = DEFAULT_DB_PATH) -> pd.DataFrame:
    """
    Lấy toàn bộ dữ liệu từ bảng analyzed_reviews trả về DataFrame của Pandas.
    Tự động giải mã cột topics từ JSON thành list.
    """
    init_db(db_path)
    conn = get_connection(db_path)

    query = """
        SELECT review_id, author_name, rating, text, published_at, likes_count, 
               sentiment, sentiment_score, topics, analyzed_at
        FROM analyzed_reviews
        ORDER BY published_at DESC
    """
    try:
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"[DATABASE] Lỗi truy vấn bảng analyzed_reviews: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()

    if not df.empty and "topics" in df.columns:
        def parse_topics(val):
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return [val] if val else []
            elif isinstance(val, list):
                return val
            return []

        df["topics_list"] = df["topics"].apply(parse_topics)
    else:
        df["topics_list"] = [[] for _ in range(len(df))]

    return df


def get_database_stats(db_path: str = DEFAULT_DB_PATH) -> Dict[str, int]:
    """
    Lấy thống kê nhanh về số lượng bản ghi trong database.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM raw_reviews")
    total_raw = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM raw_reviews WHERE is_processed = 0")
    unprocessed = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM analyzed_reviews")
    total_analyzed = cursor.fetchone()[0]

    conn.close()
    return {
        "total_raw": total_raw,
        "unprocessed": unprocessed,
        "total_analyzed": total_analyzed
    }


def clear_database(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Xoá sạch toàn bộ dữ liệu trong cả 2 bảng (phục vụ reset/testing).
    """
    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM analyzed_reviews")
    cursor.execute("DELETE FROM raw_reviews")
    conn.commit()
    conn.close()
    print("[DATABASE] Đã xoá toàn bộ dữ liệu trong database.")

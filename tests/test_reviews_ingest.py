"""
tests/test_reviews_ingest.py
----------------------------
Integration tests cho module reviews/ingest.py (kết nối PostgreSQL).
"""

import uuid
import pytest
from sqlalchemy import text
from etl.db import get_engine
from reviews.ingest import (
    create_import_log,
    get_cinema_key,
    ingest,
    parse_date,
    parse_rating,
    save_raw_reviews,
    update_import_log,
    SOURCE_MANUAL,
)


@pytest.fixture(scope="module")
def db_context():
    engine = get_engine()
    # Lấy cinema_key thật của rạp đầu tiên
    with engine.connect() as conn:
        ckey = conn.execute(text("SELECT cinema_key FROM cinema_dw.dim_cinema LIMIT 1")).fetchone()[0]

    # Prefix riêng để dễ dọn dẹp sau khi test
    test_prefix = f"test_{uuid.uuid4().hex[:8]}_"
    yield (engine, ckey, test_prefix)

    # Cleanup test data
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM cinema_dw.review_nlp WHERE review_id LIKE :prefix"),
            {"prefix": f"{test_prefix}%"},
        )
        conn.execute(
            text("DELETE FROM cinema_dw.review_raw WHERE review_id LIKE :prefix"),
            {"prefix": f"{test_prefix}%"},
        )
        conn.execute(
            text("DELETE FROM cinema_dw.review_import_log WHERE message LIKE :prefix"),
            {"prefix": f"%{test_prefix}%"},
        )


def test_parse_date_formats():
    assert str(parse_date("2026-10-16T04:02:06.174Z")) == "2026-10-16"
    assert str(parse_date("2026-10-16T04:02:06Z")) == "2026-10-16"
    assert str(parse_date("2026-10-16")) == "2026-10-16"
    assert parse_date(None) is None
    assert parse_date("invalid_date_string") is None


def test_parse_rating():
    assert parse_rating(5) == 5.0
    assert parse_rating("4.5") == 4.5
    assert parse_rating(None) is None
    assert parse_rating("0.5") is None  # ngoài [1, 5] -> None
    assert parse_rating("6.0") is None  # ngoài [1, 5] -> None


def test_ingest_new_review(db_context):
    engine, ckey, prefix = db_context
    import_id = create_import_log(engine, source=SOURCE_MANUAL, cinema_key=ckey, message=f"{prefix}run1")

    rid = f"{prefix}01"
    rows = [{
        "review_id": rid,
        "rating": 5.0,
        "review_text": "Phim rất hay, âm thanh đỉnh",
        "published_at": "2026-10-16T04:02:06.174Z",
        "likes_count": 2,
    }]

    new, dup, skipped = save_raw_reviews(engine, rows, ckey, import_id)
    assert new == 1
    assert dup == 0
    assert skipped == 0

    # Kiểm tra trong DB
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rating, has_text, published_date FROM cinema_dw.review_raw WHERE review_id = :rid"),
            {"rid": rid},
        ).fetchone()
        assert row is not None
        assert float(row[0]) == 5.0
        assert row[1] is True  # has_text generated column
        assert str(row[2]) == "2026-10-16"


def test_ingest_duplicate(db_context):
    engine, ckey, prefix = db_context
    import_id = create_import_log(engine, source=SOURCE_MANUAL, cinema_key=ckey, message=f"{prefix}run2")

    rid = f"{prefix}01"  # đã nạp ở test trước
    rows = [{
        "review_id": rid,
        "rating": 5.0,
        "review_text": "Phim rất hay",
        "published_at": "2026-10-16T04:02:06.174Z",
    }]

    new, dup, skipped = save_raw_reviews(engine, rows, ckey, import_id)
    assert new == 0
    assert dup == 1
    assert skipped == 0


def test_ingest_null_rating(db_context):
    engine, ckey, prefix = db_context
    import_id = create_import_log(engine, source=SOURCE_MANUAL, cinema_key=ckey, message=f"{prefix}run3")

    rid = f"{prefix}null_rating"
    rows = [{
        "review_id": rid,
        "rating": None,
        "review_text": "Không có số sao nhưng có văn bản",
        "published_at": "2026-10-10",
    }]

    new, dup, skipped = save_raw_reviews(engine, rows, ckey, import_id)
    assert new == 1

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rating FROM cinema_dw.review_raw WHERE review_id = :rid"),
            {"rid": rid},
        ).fetchone()
        assert row[0] is None  # Lưu đúng NULL, không gán 5.0


def test_ingest_missing_date_skip(db_context):
    engine, ckey, prefix = db_context
    import_id = create_import_log(engine, source=SOURCE_MANUAL, cinema_key=ckey, message=f"{prefix}run4")

    rid = f"{prefix}no_date"
    rows = [{
        "review_id": rid,
        "rating": 5.0,
        "review_text": "Thiếu ngày đăng",
        "published_at": None,
    }]

    new, dup, skipped = save_raw_reviews(engine, rows, ckey, import_id)
    assert new == 0
    assert skipped == 1


def test_ingest_missing_both_rating_and_text_skip(db_context):
    engine, ckey, prefix = db_context
    import_id = create_import_log(engine, source=SOURCE_MANUAL, cinema_key=ckey, message=f"{prefix}run5")

    rid = f"{prefix}empty_data"
    rows = [{
        "review_id": rid,
        "rating": None,
        "review_text": "   ",
        "published_at": "2026-10-01",
    }]

    new, dup, skipped = save_raw_reviews(engine, rows, ckey, import_id)
    assert new == 0
    assert skipped == 1


def test_ingest_content_update_resets_is_processed(db_context):
    engine, ckey, prefix = db_context
    import_id = create_import_log(engine, source=SOURCE_MANUAL, cinema_key=ckey, message=f"{prefix}run6")

    rid = f"{prefix}edit_content"
    rows = [{
        "review_id": rid,
        "rating": 5.0,
        "review_text": "Lúc đầu đánh giá tốt",
        "published_at": "2026-10-01",
    }]
    save_raw_reviews(engine, rows, ckey, import_id)

    # Đánh dấu dòng là đã xử lý
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cinema_dw.review_raw SET is_processed = TRUE WHERE review_id = :rid"),
            {"rid": rid},
        )

    # Khách sửa lại nội dung đánh giá (đổi text và rating)
    updated_rows = [{
        "review_id": rid,
        "rating": 1.0,
        "review_text": "Sửa lại: dịch vụ tệ quá",
        "published_at": "2026-10-01",
    }]
    save_raw_reviews(engine, updated_rows, ckey, import_id)

    # is_processed phải được đặt lại thành FALSE để NLP pipeline tái phân tích
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rating, review_text, is_processed FROM cinema_dw.review_raw WHERE review_id = :rid"),
            {"rid": rid},
        ).fetchone()
        assert float(row[0]) == 1.0
        assert row[1] == "Sửa lại: dịch vụ tệ quá"
        assert row[2] is False


def test_ingest_idempotency_full_flow(db_context):
    engine, ckey, prefix = db_context
    rows = [
        {
            "review_id": f"{prefix}idem_1",
            "rating": 5.0,
            "review_text": "Rạp sạch đẹp",
            "published_at": "2026-10-12",
        },
        {
            "review_id": f"{prefix}idem_2",
            "rating": 2.0,
            "review_text": "Xếp hàng lâu",
            "published_at": "2026-10-13",
        },
    ]

    rep1 = ingest(engine, rows, cinema_key=ckey, source=SOURCE_MANUAL, raw_file_path=f"{prefix}idem")
    assert rep1["rows_new"] == 2
    assert rep1["rows_dup"] == 0
    assert rep1["rows_skipped"] == 0

    # Chạy lại cùng dữ liệu -> idempotent: 0 mới, 2 trùng
    rep2 = ingest(engine, rows, cinema_key=ckey, source=SOURCE_MANUAL, raw_file_path=f"{prefix}idem")
    assert rep2["rows_new"] == 0
    assert rep2["rows_dup"] == 2
    assert rep2["rows_skipped"] == 0

    # Dọn dữ liệu test theo đúng thứ tự FK (review_raw trước rồi mới đến review_import_log)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM cinema_dw.review_raw WHERE review_id LIKE :prefix"),
            {"prefix": f"{prefix}%"},
        )
        conn.execute(
            text("DELETE FROM cinema_dw.review_import_log WHERE raw_file_path LIKE :prefix"),
            {"prefix": f"%{prefix}%"},
        )


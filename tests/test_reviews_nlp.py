"""
tests/test_reviews_nlp.py
-------------------------
Unit tests cho module reviews/nlp.py (không cần kết nối database).
"""

import pytest
from decimal import Decimal
from reviews.nlp import (
    clean_text,
    detect_lang,
    extract_topics,
    merge_sentiment_final,
    process_batch,
    process_review,
    sentiment_from_rating,
    sentiment_from_text,
    TOPIC_FALLBACK,
)


# ===========================================================================
# 1. sentiment_from_rating
# ===========================================================================

def test_sentiment_rating_positive():
    assert sentiment_from_rating(5.0) == "POSITIVE"
    assert sentiment_from_rating(4.0) == "POSITIVE"
    assert sentiment_from_rating(Decimal("4.5")) == "POSITIVE"


def test_sentiment_rating_neutral():
    assert sentiment_from_rating(3.0) == "NEUTRAL"
    assert sentiment_from_rating(3.5) == "NEUTRAL"
    assert sentiment_from_rating(Decimal("3.0")) == "NEUTRAL"


def test_sentiment_rating_negative():
    assert sentiment_from_rating(1.0) == "NEGATIVE"
    assert sentiment_from_rating(2.0) == "NEGATIVE"
    assert sentiment_from_rating(Decimal("1.5")) == "NEGATIVE"


def test_sentiment_rating_null():
    assert sentiment_from_rating(None) is None
    assert sentiment_from_rating("invalid") is None


# ===========================================================================
# 2. sentiment_from_text
# ===========================================================================

def test_sentiment_text_no_text():
    assert sentiment_from_text(None) is None
    assert sentiment_from_text("") is None
    assert sentiment_from_text("   \n\t  ") is None


def test_sentiment_text_positive():
    text = "Rạp chiếu sạch đẹp, nhân viên phục vụ rất tuyệt vời và thân thiện."
    assert sentiment_from_text(text) == "POSITIVE"


def test_sentiment_text_negative():
    text = "Thái độ nhân viên cọc cằn, nhà vệ sinh dơ bẩn và rất hôi."
    assert sentiment_from_text(text) == "NEGATIVE"


def test_sentiment_text_neutral():
    text = "Tôi xem phim vào chiều chủ nhật."
    assert sentiment_from_text(text) == "NEUTRAL"


# ===========================================================================
# 3. merge_sentiment_final
# ===========================================================================

def test_sentiment_final_rating_only():
    label, score = merge_sentiment_final("POSITIVE", None, 5.0)
    assert label == "POSITIVE"
    assert score > 0

    label, score = merge_sentiment_final("NEGATIVE", None, 1.0)
    assert label == "NEGATIVE"
    assert score < 0


def test_sentiment_final_text_only():
    label, score = merge_sentiment_final(None, "POSITIVE", None)
    assert label == "POSITIVE"
    assert score > 0

    label, score = merge_sentiment_final(None, "NEGATIVE", None)
    assert label == "NEGATIVE"
    assert score < 0


def test_sentiment_final_both_consistent():
    label, score = merge_sentiment_final("POSITIVE", "POSITIVE", 5.0)
    assert label == "POSITIVE"
    assert score >= 0.5


def test_sentiment_final_low_rating_protection():
    # Khách chấm 1 sao nhưng khen đểu hoặc dùng từ tích cực
    label, score = merge_sentiment_final("NEGATIVE", "POSITIVE", 1.0)
    assert label == "NEGATIVE"
    assert score <= -0.2


# ===========================================================================
# 4. extract_topics
# ===========================================================================

def test_extract_topics_basic():
    text = "Xếp hàng mua bắp nước lâu quá, quầy thu ngân có mỗi một bạn phục vụ."
    topics = extract_topics(text)
    assert "Hàng chờ & Bắp nước" in topics
    assert "Thái độ phục vụ" in topics


def test_extract_topics_sound_and_screen():
    text = "Màn hình chiếu sắc nét, âm thanh sống động nhưng máy lạnh quá rét."
    topics = extract_topics(text)
    assert "Phòng chiếu & Âm thanh" in topics


def test_extract_topics_generic_fallback():
    # Không khớp từ khóa nào -> ["Khác"]
    text = "Hôm nay đi xem cùng bạn bè ngày mưa."
    topics = extract_topics(text)
    assert topics == [TOPIC_FALLBACK]


def test_extract_topics_no_generic_keyword():
    # Từ "rạp" đã bị loại bỏ khỏi từ điển, không được trigger topic nào ngoài Khác
    text = "Tôi đã đến rạp này."
    topics = extract_topics(text)
    assert topics == [TOPIC_FALLBACK]


def test_extract_topics_empty_text():
    assert extract_topics("") == []
    assert extract_topics(None) == []


# ===========================================================================
# 5. detect_lang
# ===========================================================================

def test_detect_lang_apify_priority():
    assert detect_lang("Phim hay", apify_lang="vi") == "vi"
    assert detect_lang("Great cinema", apify_lang="en") == "en"


def test_detect_lang_accent_heuristic():
    assert detect_lang("Rạp chiếu phim rất đẹp và sạch sẽ", apify_lang=None) == "vi"
    assert detect_lang("Nothing special here", apify_lang=None) == "unknown"


# ===========================================================================
# 6. process_review & process_batch
# ===========================================================================

def test_process_review_complete():
    res = process_review(
        review_id="rev_01",
        rating=5.0,
        text="Bắp phô mai thơm ngon, nhân viên nhiệt tình!",
        model_version="v1",
    )
    assert res["review_id"] == "rev_01"
    assert res["sentiment_rating"] == "POSITIVE"
    assert res["sentiment_final"] == "POSITIVE"
    assert "Hàng chờ & Bắp nước" in res["topics"]
    assert res["model_version"] == "v1"


def test_process_batch():
    rows = [
        {"review_id": "r1", "rating": 5.0, "review_text": "Tuyệt vời"},
        {"review_id": "r2", "rating": 1.0, "review_text": "Tệ dở"},
    ]
    batch_res = process_batch(rows)
    assert len(batch_res) == 2
    assert batch_res[0]["sentiment_final"] == "POSITIVE"
    assert batch_res[1]["sentiment_final"] == "NEGATIVE"

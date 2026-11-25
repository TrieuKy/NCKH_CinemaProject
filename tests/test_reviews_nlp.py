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


# ===========================================================================
# 7. Scientific validation & regression tests (Task 8)
# ===========================================================================

def test_sentiment_text_vietnamese_negation():
    assert sentiment_from_text("Cố úp sale không hài lòng") == "NEGATIVE"
    assert sentiment_from_text("Thái độ nhân viên ko tốt") == "NEGATIVE"
    assert sentiment_from_text("Phòng chiếu chẳng đẹp chút nào") == "NEGATIVE"
    assert sentiment_from_text("Phim cũng không tệ lắm") == "POSITIVE"


def test_sentiment_text_word_boundary_no_false_substring():
    # "ok" không được khớp nhầm trong "booking" hay "tiktok"
    from reviews.nlp import _count_lexicon_polarity
    assert _count_lexicon_polarity("đặt vé qua booking trên tiktok") == (0, 0)


def test_sentiment_text_difficult_vietnamese_and_reproducibility():
    # Tái lập: gọi nhiều lần cùng input phải cho cùng kết quả
    cases = [
        "Không gian ok, không quá đông nên rất thoải mái 😍",
        "Hôm nay đi xem phim, đặt vé 10h30",
        "Âm thanh tuyệt vời nhưng nhân viên thái độ cọc cằn",
    ]
    for txt in cases:
        r1 = process_review("rep_1", 3.0, txt)
        r2 = process_review("rep_1", 3.0, txt)
        assert r1 == r2


def test_sentiment_mixed_and_sarcasm_rating_mismatch():
    # Rating thấp (1 sao) nhưng văn bản chứa từ tích cực (mỉa mai / khen đểu)
    sent_r = sentiment_from_rating(1.0)
    sent_t = sentiment_from_text("Rạp tuyệt vời quá, chờ 45 phút mới được vào")
    final_label, score = merge_sentiment_final(sent_r, sent_t, 1.0)
    assert final_label == "NEGATIVE"
    assert score <= -0.2


def test_extract_topics_multi_topic():
    text = "Giá vé đắt, nhà vệ sinh dơ bẩn, màn hình mờ và nhân viên cọc cằn."
    topics = extract_topics(text)
    assert "Giá cả & Vé" in topics
    assert "Vệ sinh" in topics
    assert "Phòng chiếu & Âm thanh" in topics
    assert "Thái độ phục vụ" in topics
    assert len(topics) == 4


def test_evaluation_metrics_computation():
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support

    y_true = ["POSITIVE", "POSITIVE", "NEGATIVE", "NEGATIVE", "NEUTRAL", "POSITIVE"]
    y_pred = ["POSITIVE", "NEGATIVE", "NEGATIVE", "NEGATIVE", "NEUTRAL", "POSITIVE"]
    labels = ["NEGATIVE", "NEUTRAL", "POSITIVE"]

    cm = confusion_matrix(y_true, y_pred, labels=labels)
    assert cm.shape == (3, 3)
    assert cm.sum() == len(y_true)

    acc = accuracy_score(y_true, y_pred)
    macro_f1 = f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    weighted_f1 = f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0)
    prec, rec, _, _ = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)

    assert 0.0 <= acc <= 1.0
    assert 0.0 <= macro_f1 <= 1.0
    assert 0.0 <= weighted_f1 <= 1.0
    assert len(prec) == 3 and len(rec) == 3

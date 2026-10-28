"""
reviews/nlp.py
--------------
Phân tích cảm xúc và chủ đề cho đánh giá Google Maps.

Quy ước:
  - Không đọc .env, không kết nối DB — module thuần Python.
  - Chạy module: python -m reviews.nlp
  - 3 chiều sentiment:
      sentiment_rating : suy từ số sao (None nếu rating=None)
      sentiment_text   : suy từ văn bản (None nếu không có chữ)
      sentiment_final  : tổng hợp — KHÔNG BAO GIỜ None
  - Từ điển chủ đề: bản hardcode mặc định;
      pipeline analyze.py tải từ DB để ưu tiên phiên bản đang dùng.
  - Lang detection: ưu tiên trường Apify; fallback heuristic ký tự Việt.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Thử import underthesea — fallback an toàn nếu chưa cài
# ---------------------------------------------------------------------------
try:
    from underthesea import sentiment as _uts_sentiment
    _HAS_UNDERTHESEA = True
except Exception:
    _uts_sentiment = None
    _HAS_UNDERTHESEA = False

# ---------------------------------------------------------------------------
# Từ điển mặc định — pipeline analyze.py tải lại từ DB để override
# Bỏ từ quá chung: "rạp", "vé", "nước", "tối", "tiền"
# ---------------------------------------------------------------------------
DEFAULT_TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "Hàng chờ & Bắp nước": [
        "bắp", "popcorn", "xếp hàng", "quầy", "combo", "order", "thanh toán",
        "đồ ăn", "nước ngọt", "phô mai", "caramel", "bỏng ngô", "chờ đợi",
        "máy tính tiền", "thu ngân", "xếp hàng lâu", "quầy bắp", "hàng đợi", "lấy bắp",
    ],
    "Giá cả & Vé": [
        "giá", "đắt", "mắc", "khuyến mãi", "voucher", "đắt đỏ", "phụ thu",
        "giá cả", "chi phí", "giảm giá", "u22", "ghế vip", "vé phim", "tốn kém",
        "chát", "bảng giá", "giá vé",
    ],
    "Phòng chiếu & Âm thanh": [
        "màn hình", "loa", "âm thanh", "ghế", "lạnh", "máy lạnh",
        "mờ", "phòng chiếu", "máy chiếu", "âm lượng", "nhiệt độ", "rét", "rung",
        "imax", "gold class", "starium", "cúp điện", "hình ảnh", "chói", "nhức mắt",
        "điều hoà", "ghế ngồi", "mùi máy lạnh",
    ],
    "Thái độ phục vụ": [
        "nhân viên", "thái độ", "bảo vệ", "soát vé", "phục vụ", "quản lý", "cọc cằn",
        "khó chịu", "nhiệt tình", "hỗ trợ", "lịch sự", "thân thiện", "chậm chạp",
        "coi thường", "quát", "mặt mày", "tư vấn", "chăm sóc khách hàng",
    ],
    "Vệ sinh": [
        "dơ", "bẩn", "mùi", "rác", "hôi", "dơ dáy", "toilet", "nhà vệ sinh", "wc",
        "sàn", "dính", "bốc mùi", "ẩm mốc", "giấy lau", "giấy vệ sinh", "lau dọn",
        "rác rưởi", "vết bẩn", "dơ bẩn", "hôi hám",
    ],
}

# Chủ đề fallback: gán khi không khớp bất kỳ chủ đề nào
TOPIC_FALLBACK = "Khác"

# ---------------------------------------------------------------------------
# Lexicon cảm xúc tiếng Việt
# ---------------------------------------------------------------------------
_POSITIVE_WORDS = frozenset([
    "tuyệt vời", "tuyệt", "xuất sắc", "thích", "đẹp", "sạch", "thơm", "ngon", "rẻ",
    "ưng ý", "hài lòng", "chuyên nghiệp", "nhanh", "chu đáo", "thân thiện", "giòn",
    "đỉnh", "chất lượng", "thoải mái", "êm ái", "rực rỡ", "vui vẻ", "tốt", "ok", "tuyệt hảo",
])
_NEGATIVE_WORDS = frozenset([
    "tệ", "dở", "chán", "kém", "thất vọng", "tồi", "đắt", "mắc", "cọc cằn", "lâu",
    "chậm", "dơ", "bẩn", "hôi", "rác", "lạnh buốt", "lạnh cóng", "rè", "mờ", "khó chịu",
    "tức giận", "kinh khủng", "bực", "phí tiền", "ỉu", "nguội", "bất lịch sự", "coi thường",
])

# Ký tự Việt có dấu — dùng để phát hiện ngôn ngữ
_VIET_PATTERN = re.compile(
    r"[àáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỷỹỵ"
    r"ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỶỸỴ]"
)


# ===========================================================================
# 1. Tiện ích văn bản
# ===========================================================================

def clean_text(text: str) -> str:
    """Chuẩn hóa văn bản: chữ thường, thu gọn khoảng trắng."""
    if not isinstance(text, str):
        return ""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def detect_lang(text: Optional[str], apify_lang: Optional[str] = None) -> str:
    """
    Phát hiện ngôn ngữ.
    Ưu tiên: giá trị từ Apify → heuristic ký tự Việt → 'unknown'.
    Không dùng thư viện ngoài (không cài langdetect).
    """
    if apify_lang and isinstance(apify_lang, str) and apify_lang.strip():
        return apify_lang.strip().lower()
    if text and isinstance(text, str) and _VIET_PATTERN.search(text):
        return "vi"
    return "unknown"


# ===========================================================================
# 2. Sentiment theo từng chiều
# ===========================================================================

def sentiment_from_rating(rating: Optional[Any]) -> Optional[str]:
    """
    Chiều 1: cảm xúc từ số sao.
    >= 4  → POSITIVE
    <= 2  → NEGATIVE
    khác  → NEUTRAL
    None  → None (rating bị thiếu)
    """
    if rating is None:
        return None
    try:
        r = float(rating)
    except (ValueError, TypeError):
        return None

    if r >= 4.0:
        return "POSITIVE"
    if r <= 2.0:
        return "NEGATIVE"
    return "NEUTRAL"


def sentiment_from_text(text: Optional[str]) -> Optional[str]:
    """
    Chiều 2: cảm xúc từ văn bản.
    Dùng lexicon tiếng Việt; kết hợp underthesea nếu có.
    Trả None nếu không có văn bản.
    """
    cleaned = clean_text(text or "")
    if not cleaned:
        return None

    # Đếm từ tích cực / tiêu cực
    pos = sum(1 for w in _POSITIVE_WORDS if w in cleaned)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in cleaned)

    # Kết hợp underthesea nếu có
    uts_label: Optional[str] = None
    if _HAS_UNDERTHESEA and _uts_sentiment:
        try:
            uts_label = _uts_sentiment(cleaned)
        except Exception:
            uts_label = None

    # Tính điểm lexicon [-1, 1]
    total = pos + neg
    if total > 0:
        lex_score = (pos - neg) / total
        # Nếu lexicon đã phát hiện từ cảm xúc rõ ràng, underthesea chỉ củng cố thêm,
        # không được đảo chiều trái ngược với lexicon
        if uts_label == "positive":
            if neg == 0:
                lex_score = max(lex_score, 0.5)
            elif pos >= neg:
                lex_score = max(lex_score, 0.3)
        elif uts_label == "negative":
            if pos == 0:
                lex_score = min(lex_score, -0.5)
            elif neg >= pos:
                lex_score = min(lex_score, -0.3)
    else:
        # Không có từ trong lexicon -> tin cậy underthesea
        if uts_label == "positive":
            lex_score = 0.4
        elif uts_label == "negative":
            lex_score = -0.4
        else:
            lex_score = 0.0

    if lex_score >= 0.15:
        return "POSITIVE"
    if lex_score <= -0.15:
        return "NEGATIVE"
    return "NEUTRAL"


def merge_sentiment_final(
    sent_rating: Optional[str],
    sent_text: Optional[str],
    rating: Optional[Any],
) -> Tuple[str, float]:
    """
    Chiều 3: tổng hợp sentiment_final (không bao giờ None).
    Logic:
      - Có cả rating lẫn text: 60% rating score + 40% text score → ngưỡng
      - Chỉ có rating: dùng sentiment_rating
      - Chỉ có text:   dùng sentiment_text
      - Không có gì:   NEUTRAL (không xảy ra vì ingest đã lọc)

    Trả (label: str, score: float).
    """
    def _to_score(label: Optional[str]) -> float:
        if label == "POSITIVE":
            return 0.8
        if label == "NEGATIVE":
            return -0.8
        return 0.0  # NEUTRAL hoặc None

    r_float: Optional[float] = None
    if rating is not None:
        try:
            r_float = float(rating)
        except (ValueError, TypeError):
            r_float = None

    # Điểm số sao quy chuẩn [-1, 1]
    rating_score = ((r_float - 3.0) / 2.0) if r_float is not None else None

    has_rating = sent_rating is not None
    has_text = sent_text is not None

    if has_rating and has_text:
        rs = rating_score if rating_score is not None else _to_score(sent_rating)
        ts = _to_score(sent_text)
        # Bảo vệ: rating 1-2 không được có combined score dương
        combined = round(0.6 * rs + 0.4 * ts, 2)
        if r_float is not None and r_float <= 2.0 and combined > 0:
            combined = -0.5
        if r_float is not None and r_float >= 4.5 and sent_text != "NEGATIVE":
            combined = max(combined, 0.5)
    elif has_rating:
        combined = rating_score if rating_score is not None else _to_score(sent_rating)
        combined = round(combined, 2)
    elif has_text:
        combined = round(_to_score(sent_text), 2)
    else:
        # Không bao giờ đến đây sau khi ingest lọc
        return "NEUTRAL", 0.0

    if combined >= 0.2:
        label = "POSITIVE"
    elif combined <= -0.2:
        label = "NEGATIVE"
    else:
        label = "NEUTRAL"

    return label, combined


# ===========================================================================
# 3. Phân loại chủ đề
# ===========================================================================

def extract_topics(
    text: Optional[str],
    topic_keywords: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """
    Khớp chủ đề dựa trên keyword matching.
    topic_keywords: dùng DEFAULT_TOPIC_KEYWORDS nếu không truyền.
    Trả ["Khác"] nếu không khớp bất kỳ chủ đề nào và có văn bản.
    """
    if topic_keywords is None:
        topic_keywords = DEFAULT_TOPIC_KEYWORDS

    cleaned = clean_text(text or "")
    if not cleaned:
        return []

    matched: List[str] = []
    for topic, keywords in topic_keywords.items():
        for kw in keywords:
            # Cụm từ: tìm trực tiếp; từ đơn: tìm word boundary
            if " " in kw:
                pattern = re.escape(kw)
            else:
                pattern = r"(?i)\b" + re.escape(kw) + r"\b"
            if re.search(pattern, cleaned):
                matched.append(topic)
                break  # khớp 1 từ khoá trong nhóm là đủ

    if not matched:
        return [TOPIC_FALLBACK]
    return matched


# ===========================================================================
# 4. Xử lý hàng loạt
# ===========================================================================

def process_review(
    review_id: str,
    rating: Optional[float],
    text: Optional[str],
    apify_lang: Optional[str] = None,
    topic_keywords: Optional[Dict[str, List[str]]] = None,
    model_version: str = "v1",
) -> Dict:
    """
    Phân tích một đánh giá: trả dict sẵn sàng INSERT vào review_nlp.
    """
    sent_r = sentiment_from_rating(rating)
    sent_t = sentiment_from_text(text)
    sent_f, score = merge_sentiment_final(sent_r, sent_t, rating)
    topics = extract_topics(text, topic_keywords)
    lang = detect_lang(text, apify_lang)

    return {
        "review_id":        review_id,
        "sentiment_rating": sent_r,
        "sentiment_text":   sent_t,
        "sentiment_final":  sent_f,
        "sentiment_score":  score,
        "topics":           topics,
        "model_version":    model_version,
        "lang":             lang,
    }


def process_batch(
    rows: List[Dict],
    topic_keywords: Optional[Dict[str, List[str]]] = None,
    model_version: str = "v1",
) -> List[Dict]:
    """
    Xử lý danh sách đánh giá thô.
    Mỗi dict đầu vào cần: review_id, rating, review_text, [apify_lang].
    """
    results = []
    for r in rows:
        result = process_review(
            review_id=r["review_id"],
            rating=r.get("rating"),
            text=r.get("review_text"),
            apify_lang=r.get("apify_lang"),
            topic_keywords=topic_keywords,
            model_version=model_version,
        )
        results.append(result)
    return results


# ===========================================================================
# Kiểm tra nhanh khi chạy trực tiếp: python -m reviews.nlp
# ===========================================================================
if __name__ == "__main__":
    import sys
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    tests = [
        ("r1", 5.0,  "Rạp đẹp, nhân viên thân thiện, âm thanh tuyệt vời!", None),
        ("r2", 1.0,  "Nhân viên thái độ cọc cằn, xếp hàng quá lâu!", None),
        ("r3", 3.0,  None,                                               None),
        ("r4", None, "Dơ bẩn khủng khiếp, nhà vệ sinh hôi hám.",        None),
        ("r5", 4.0,  "",                                                  "vi"),
    ]
    for rid, rating, text, lang in tests:
        result = process_review(rid, rating, text, lang)
        print(f"[{rid}] rating={rating!r:5} | "
              f"sent_r={result['sentiment_rating']!r:10} | "
              f"sent_t={result['sentiment_text']!r:10} | "
              f"final={result['sentiment_final']!r:10} | "
              f"score={result['sentiment_score']:+.2f} | "
              f"topics={result['topics']} | lang={result['lang']!r}")

"""
Module: nlp_pipeline.py
Mục đích: Xử lý ngôn ngữ tự nhiên (NLP) phân loại Sentiment & Topic Modeling
Sử dụng underthesea và bộ từ điển Lexicon tiếng Việt chuyên sâu cho dịch vụ rạp chiếu phim
"""

import re
from typing import Tuple, List, Dict, Any, Optional

# Thử import underthesea với fallback an toàn
try:
    from underthesea import sentiment as uts_sentiment
    HAS_UNDERTHESEA = True
except (ImportError, Exception):
    uts_sentiment = None
    HAS_UNDERTHESEA = False


# ==========================================
# 1. BỘ TỪ ĐIỂN CHỦ ĐỀ VẬN HÀNH (TOPIC KEYWORDS)
# ==========================================
TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "Hàng chờ & Bắp nước": [
        "bắp", "nước", "popcorn", "xếp hàng", "quầy", "combo", "order", "thanh toán",
        "đồ ăn", "nước ngọt", "phô mai", "caramel", "bỏng ngô", "chờ đợi", "máy tính tiền",
        "thu ngân", "xếp hàng lâu", "quầy bắp", "hàng đợi", "lấy bắp"
    ],
    "Giá cả & Vé": [
        "giá", "đắt", "mắc", "vé", "khuyến mãi", "voucher", "đắt đỏ", "phụ thu",
        "giá cả", "tiền", "chi phí", "giảm giá", "u22", "ghế vip", "vé phim", "tốn kém",
        "chát", "bảng giá", "giá vé"
    ],
    "Phòng chiếu & Âm thanh": [
        "màn hình", "loa", "âm thanh", "ghế", "lạnh", "máy lạnh", "rạp", "tối",
        "mờ", "phòng chiếu", "máy chiếu", "âm lượng", "nhiệt độ", "rét", "rung",
        "imax", "gold class", "starium", "cúp điện", "hình ảnh", "chói", "nhức mắt",
        "điều hoà", "ghế ngồi", "mùi máy lạnh"
    ],
    "Thái độ phục vụ": [
        "nhân viên", "thái độ", "bảo vệ", "soát vé", "phục vụ", "quản lý", "cọc cằn",
        "khó chịu", "nhiệt tình", "hỗ trợ", "lịch sự", "thân thiện", "chậm chạp",
        "coi thường", "lồi lõm", "quát", "mặt mày", "tư vấn", "chăm sóc khách hàng"
    ],
    "Vệ sinh": [
        "dơ", "bẩn", "mùi", "rác", "hôi", "dơ dáy", "toilet", "nhà vệ sinh", "wc",
        "sàn", "dính", "bốc mùi", "ẩm mốc", "giấy lau", "giấy vệ sinh", "lau dọn",
        "rác rưởi", "vết bẩn", "dơ bẩn", "hôi hám"
    ]
}

# ==========================================
# 2. BỘ TỪ ĐIỂN TÌNH THÁI CẢM XÚC (SENTIMENT LEXICON)
# ==========================================
POSITIVE_WORDS = {
    "tuyệt vời", "tuyệt", "xuất sắc", "thích", "đẹp", "sạch", "thơm", "ngon", "rẻ",
    "ưng ý", "hài lòng", "chuyên nghiệp", "nhanh", "chu đáo", "thân thiện", "giòn",
    "đỉnh", "chất lượng", "thoải mái", "êm ái", "rực rỡ", "vui vẻ", "tốt", "ok", "tuyệt hảo"
}

NEGATIVE_WORDS = {
    "tệ", "dở", "chán", "kém", "thất vọng", "tồi", "đắt", "mắc", "cọc cằn", "lâu",
    "chậm", "dơ", "bẩn", "hôi", "rác", "lạnh buốt", "lạnh cóng", "rè", "mờ", "khó chịu",
    "tức giận", "kinh khủng", "bực", "phí tiền", "ỉu", "nguội", "bất lịch sự", "coi thường"
}


def clean_text(text: str) -> str:
    """Làm sạch văn bản đầu vào: loại bỏ ký tự đặc biệt, chuẩn hoá khoảng trắng."""
    if not isinstance(text, str):
        return ""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def analyze_sentiment(rating: float, text: str) -> Tuple[str, float]:
    """
    Phân loại cảm xúc (POSITIVE, NEUTRAL, NEGATIVE) và điểm số cảm xúc (-1.0 đến 1.0)
    dựa trên kết hợp số sao và mô hình xử lý văn bản tiếng Việt.

    Args:
        rating (float): Số sao từ 1.0 đến 5.0
        text (str): Nội dung văn bản đánh giá

    Returns:
        Tuple[str, float]: (Nhãn cảm xúc, Điểm số cảm xúc)
    """
    cleaned = clean_text(text)

    # 1. Nếu không có văn bản nhận xét (chỉ chấm sao)
    if not cleaned:
        if rating >= 4.0:
            return "POSITIVE", 0.8
        elif rating <= 2.0:
            return "NEGATIVE", -0.8
        else:
            return "NEUTRAL", 0.0

    # 2. Phân tích Lexicon nội tại tiếng Việt
    pos_matches = sum(1 for word in POSITIVE_WORDS if word in cleaned)
    neg_matches = sum(1 for word in NEGATIVE_WORDS if word in cleaned)

    # 3. Kết hợp với mô hình underthesea (nếu có sẵn)
    uts_result = None
    if HAS_UNDERTHESEA and uts_sentiment:
        try:
            uts_result = uts_sentiment(cleaned)
        except Exception:
            uts_result = None

    # Tính điểm văn bản (Text Score từ -1.0 đến 1.0)
    lexicon_score = 0.0
    if (pos_matches + neg_matches) > 0:
        lexicon_score = (pos_matches - neg_matches) / (pos_matches + neg_matches)

    if uts_result == "positive":
        lexicon_score = max(lexicon_score, 0.6)
    elif uts_result == "negative":
        lexicon_score = min(lexicon_score, -0.6)

    # Điểm quy đổi từ số sao rating: [1..5] -> [-1.0 .. 1.0]
    rating_score = (rating - 3.0) / 2.0

    # Kết hợp trọng số: 60% từ số sao của người dùng + 40% từ sắc thái văn bản
    combined_score = round(0.6 * rating_score + 0.4 * lexicon_score, 2)

    # Điều kiện đặc biệt: Khách cho 1 sao nhưng ghi chữ mỉa mai hoặc ngắn -> Chắc chắn NEGATIVE
    if rating <= 2.0 and combined_score > 0:
        combined_score = -0.5
    # Khách cho 5 sao -> Chắc chắn POSITIVE (trừ khi có nhiều từ ngữ tiêu cực rõ rệt)
    if rating >= 4.5 and neg_matches == 0:
        combined_score = max(combined_score, 0.6)

    # Gán nhãn kết luận
    if combined_score >= 0.2:
        sentiment_label = "POSITIVE"
    elif combined_score <= -0.2:
        sentiment_label = "NEGATIVE"
    else:
        sentiment_label = "NEUTRAL"

    return sentiment_label, combined_score


def extract_topics(text: str) -> List[str]:
    """
    Phân loại bình luận vào 5 nhóm chủ đề vận hành chính dựa trên Keyword Matching.

    Args:
        text (str): Nội dung đánh giá

    Returns:
        List[str]: Danh sách các nhóm chủ đề được phát hiện
    """
    cleaned = clean_text(text)
    if not cleaned:
        return []

    matched_topics: List[str] = []

    for topic, keywords in TOPIC_KEYWORDS.items():
        for kw in keywords:
            # Tìm kiếm từ khoá độc lập hoặc cụm từ
            pattern = r"(?i)\b" + re.escape(kw) + r"\b" if " " not in kw else re.escape(kw)
            if re.search(pattern, cleaned):
                matched_topics.append(topic)
                break  # Khớp 1 từ khoá trong nhóm là đủ

    return matched_topics


def process_batch_reviews(raw_reviews: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Xử lý hàng loạt đánh giá thô qua luồng NLP (Sentiment + Topics).

    Args:
        raw_reviews (List[Dict[str, Any]]): Danh sách đánh giá thô

    Returns:
        List[Dict[str, Any]]: Danh sách đánh giá đã gán nhãn NLP hoàn chỉnh
    """
    analyzed_list: List[Dict[str, Any]] = []

    for r in raw_reviews:
        text = r.get("text", "")
        rating = float(r.get("rating", 5.0))

        sentiment_label, score = analyze_sentiment(rating, text)
        topics = extract_topics(text)

        # Nếu không khớp chủ đề nào cụ thể nhưng có nhận xét
        if not topics and text.strip():
            topics = ["Trải nghiệm chung"]

        analyzed_list.append({
            "review_id": r.get("review_id"),
            "author_name": r.get("author_name"),
            "rating": rating,
            "text": text,
            "published_at": r.get("published_at"),
            "likes_count": r.get("likes_count", 0),
            "sentiment": sentiment_label,
            "sentiment_score": score,
            "topics": topics
        })

    return analyzed_list

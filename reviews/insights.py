"""
reviews/insights.py
-------------------
Sinh đề xuất vận hành (Actionable Operational Insights) dựa trên bộ quy tắc.
Hỗ trợ kiểm tra điều kiện đủ dữ liệu (du_du_lieu, mặc định n >= 10)
và so sánh với kỳ trước (nếu có prev_df).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import pandas as pd

# ---------------------------------------------------------------------------
# Bộ từ điển quy tắc hành động vận hành (Action Rules Mapping)
# ---------------------------------------------------------------------------
ACTION_RULES_CATALOG: Dict[str, Dict[str, Any]] = {
    "Hàng chờ & Bắp nước": {
        "icon": "🍿",
        "root_causes": "Nghẽn hàng đợi giờ cao điểm, máy POS/quầy thu ngân ít nhân sự, bắp không giữ được độ giòn hoặc phân phối chậm.",
        "quick_win": "Mở làn 'Express Line' chuyên nhận combo cho khách đã đặt trước trên CGV App; kiểm tra nhiệt độ tủ giữ ấm bắp 15 phút trước mỗi suất chiếu.",
        "long_term": "Đầu tư thêm Kiosk tự phục vụ (Self-service Kiosk) và tích hợp thanh toán mã QR tự động; tối ưu hóa quy trình phân luồng quầy bar.",
        "kpi_target": "Thời gian chờ nhận bắp nước trung bình < 3 phút/khách; giảm 70% khiếu nại xếp hàng.",
        "priority_weight": 1.2,
    },
    "Giá cả & Vé": {
        "icon": "🎟️",
        "root_causes": "Giá vé cuối tuần và phụ thu ghế VIP cao hơn mặt bằng chung mà chưa tạo được trải nghiệm vượt trội tương xứng.",
        "quick_win": "Truyền thông rõ ràng về quyền lợi ghế VIP và phòng IMAX; tặng kèm voucher ưu đãi bắp nước cho vé suất chiếu muộn.",
        "long_term": "Áp dụng cơ chế giá linh hoạt (Dynamic Pricing) cho các suất chiếu thấp điểm; mở rộng quyền lợi thành viên CGV U22 và CGV Member Club.",
        "kpi_target": "Tỷ lệ đánh giá 'Giá trị xứng đáng' (Value for Money) đạt > 80%.",
        "priority_weight": 0.9,
    },
    "Phòng chiếu & Âm thanh": {
        "icon": "🔊",
        "root_causes": "Nhiệt độ máy lạnh quá buốt hoặc không đồng đều; loa bị rè ở dải âm bass lớn; góc chiếu bị mờ/khuất.",
        "quick_win": "Cài đặt nhiệt độ phòng chiếu cố định ở mức 23-25°C; thực hiện quy trình Soundcheck & Image Check 10 phút trước khi mở cửa phòng.",
        "long_term": "Bảo dưỡng định kỳ hệ thống âm thanh vòm Dolby Atmos & máy chiếu laser IMAX; bọc nệm da công thái học cao cấp cho toàn bộ rạp.",
        "kpi_target": "Xử lý triệt để 100% lỗi rè loa và lệch nhiệt độ; tỷ lệ đánh giá kỹ thuật phòng chiếu đạt 4.8/5.0 sao.",
        "priority_weight": 1.3,
    },
    "Thái độ phục vụ": {
        "icon": "👥",
        "root_causes": "Nhân viên soát vé, thu ngân hoặc bảo vệ mệt mỏi trong giờ cao điểm dẫn đến thiếu niềm nở, giao tiếp cộc cằn.",
        "quick_win": "Họp giao ban đầu ca nhắc nhở văn hoá 'CGV Smile & Care'; phân công Giám sát rạp túc trực tại sảnh chính để hỗ trợ giải quyết sự cố ngay.",
        "long_term": "Tổ chức đào tạo chuẩn mực dịch vụ khách hàng 5 sao định kỳ hàng tháng; gắn KPI thưởng dịch vụ khách hàng (CSAT) trực tiếp vào lương thưởng.",
        "kpi_target": "Tỷ lệ phản hồi tích cực về nhân viên đạt > 90%; không phát sinh phàn nàn gay gắt.",
        "priority_weight": 1.4,
    },
    "Vệ sinh": {
        "icon": "🧹",
        "root_causes": "Nhà vệ sinh quá tải vào giờ tan phim; thời gian dọn phòng chiếu giữa 2 suất chiếu quá gấp làm sót rác.",
        "quick_win": "Tăng cường vệ sinh nhà vệ sinh mỗi 30 phút vào tối Thứ 6 & cuối tuần; bổ sung bảng Cleaning Checklist có chữ ký nhân viên.",
        "long_term": "Điều chỉnh giãn cách giữa các suất chiếu từ 15 phút lên 20 phút vào khung giờ cao điểm để đội vệ sinh quét dọn kỹ lưỡng.",
        "kpi_target": "Tỷ lệ phàn nàn vệ sinh duy trì dưới 1% tổng số đánh giá.",
        "priority_weight": 1.5,
    },
}

# ---------------------------------------------------------------------------
# Hằng số ngưỡng phân loại mức độ ưu tiên
# ---------------------------------------------------------------------------
MIN_SAMPLE_DEFAULT         = 10

THRESHOLD_URGENT_PCT       = 50.0  # Tiêu cực chủ đề >= 50%
THRESHOLD_URGENT_MIN_N     = 10    # VÀ tối thiểu 10 lượt nhắc
THRESHOLD_URGENT_DELTA_PCT = 10.0  # VÀ tăng >= 10 điểm % so với kỳ liền trước

THRESHOLD_HIGH_PCT         = 40.0  # Tiêu cực chủ đề >= 40%
THRESHOLD_HIGH_MIN_N       = 10    # VÀ tối thiểu 10 lượt nhắc

THRESHOLD_MEDIUM_PCT       = 25.0  # Tiêu cực chủ đề >= 25%
THRESHOLD_MEDIUM_MIN_N     = 5     # VÀ tối thiểu 5 lượt nhắc


def _get_topics_series(df: pd.DataFrame) -> pd.Series:
    """Trích xuất cột topics thành dạng List[str] an toàn."""
    if "topics" in df.columns:
        col = df["topics"]
    elif "topics_list" in df.columns:
        col = df["topics_list"]
    else:
        return pd.Series([[]] * len(df), index=df.index)

    def _normalize(val):
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            if val.startswith("{") and val.endswith("}"):
                # PostgreSQL array literal format: {a,b}
                content = val[1:-1].strip()
                return [x.strip() for x in content.split(",") if x.strip()]
            return [x.strip() for x in val.split(",") if x.strip()]
        return []

    return col.apply(_normalize)


def _get_sentiment_series(df: pd.DataFrame) -> pd.Series:
    """Trích xuất cột sentiment an toàn."""
    if "sentiment_final" in df.columns:
        return df["sentiment_final"]
    if "sentiment" in df.columns:
        return df["sentiment"]
    return pd.Series(["NEUTRAL"] * len(df), index=df.index)


def analyze_topic_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """
    Thống kê chi tiết theo từng chủ đề vận hành trong tập dữ liệu:
    - total_mentions (n)
    - negative_count
    - positive_count
    - neutral_count
    - pct_negative (tỷ lệ %)
    - avg_rating
    """
    if df.empty:
        return pd.DataFrame()

    topics_series = _get_topics_series(df)
    sentiment_series = _get_sentiment_series(df)
    rating_series = df["rating"] if "rating" in df.columns else pd.Series([None] * len(df), index=df.index)

    topic_stats = []

    for topic_name in ACTION_RULES_CATALOG.keys():
        matched_mask = topics_series.apply(lambda lst: topic_name in lst)
        sub_indices = df.index[matched_mask]
        total_count = len(sub_indices)

        if total_count > 0:
            sub_sentiments = sentiment_series.loc[sub_indices]
            neg_count = int((sub_sentiments == "NEGATIVE").sum())
            pos_count = int((sub_sentiments == "POSITIVE").sum())
            neu_count = int((sub_sentiments == "NEUTRAL").sum())
            neg_rate = round((neg_count / total_count) * 100.0, 1)

            sub_ratings = pd.to_numeric(rating_series.loc[sub_indices], errors="coerce").dropna()
            avg_rating = round(float(sub_ratings.mean()), 2) if not sub_ratings.empty else None
        else:
            neg_count = 0
            pos_count = 0
            neu_count = 0
            neg_rate = 0.0
            avg_rating = None

        topic_stats.append({
            "topic": topic_name,
            "n": total_count,
            "total_mentions": total_count,
            "negative_count": neg_count,
            "positive_count": pos_count,
            "neutral_count": neu_count,
            "pct_negative": neg_rate,
            "negative_rate": neg_rate,
            "avg_rating": avg_rating,
        })

    result_df = pd.DataFrame(topic_stats)
    return result_df.sort_values(by=["negative_count", "pct_negative"], ascending=[False, False])


def generate_operational_insights(
    df: pd.DataFrame,
    prev_df: Optional[pd.DataFrame] = None,
    min_sample: int = MIN_SAMPLE_DEFAULT,
) -> List[Dict[str, Any]]:
    """
    Sinh danh sách đề xuất hành động cụ thể cho từng chủ đề vận hành.
    Chỉ sinh đề xuất khi len(df) >= min_sample (mặc định 10).
    Nếu thiếu dữ liệu (len(df) < min_sample) -> trả danh sách rỗng [].

    Mỗi insight gồm:
      - topic, icon, priority ('KHẨN CẤP' | 'ƯU TIÊN CAO' | 'TRUNG BÌNH' | 'DUY TRÌ')
      - evidence: n, negative_count, pct_negative, prev_pct_negative, delta_pct_negative
      - root_causes, quick_win, long_term, kpi_target
      - severity_score
    """
    if df is None or len(df) < min_sample:
        return []

    breakdown_df = analyze_topic_breakdown(df)
    if breakdown_df.empty:
        return []

    # Phân tích kỳ trước nếu có và đủ mẫu (n >= min_sample)
    prev_map: Dict[str, float] = {}
    if prev_df is not None and len(prev_df) >= min_sample:
        prev_breakdown = analyze_topic_breakdown(prev_df)
        for _, prow in prev_breakdown.iterrows():
            # Kỳ trước chỉ tính % tiêu cực nếu chủ đề có ít nhất THRESHOLD_MEDIUM_MIN_N lượt nhắc
            if int(prow["n"]) >= THRESHOLD_MEDIUM_MIN_N:
                prev_map[str(prow["topic"])] = float(prow["pct_negative"])

    insights = []

    for _, row in breakdown_df.iterrows():
        topic = str(row["topic"])
        n = int(row["n"])
        neg_count = int(row["negative_count"])
        pct_negative = float(row["pct_negative"])
        avg_rating = row["avg_rating"]

        prev_pct_negative = prev_map.get(topic)
        delta_pct_negative = round(pct_negative - prev_pct_negative, 1) if prev_pct_negative is not None else None

        rule = ACTION_RULES_CATALOG.get(topic, {})
        weight = float(rule.get("priority_weight", 1.0))
        severity_score = round((neg_count * 2.5 + pct_negative * 0.4) * weight, 2)

        # Phân loại mức độ ưu tiên theo 4 mức chuẩn
        if (
            pct_negative >= THRESHOLD_URGENT_PCT
            and n >= THRESHOLD_URGENT_MIN_N
            and delta_pct_negative is not None
            and delta_pct_negative >= THRESHOLD_URGENT_DELTA_PCT
        ):
            priority_level = "KHẨN CẤP"
            badge_color = "#E50914"
        elif pct_negative >= THRESHOLD_HIGH_PCT and n >= THRESHOLD_HIGH_MIN_N:
            priority_level = "ƯU TIÊN CAO"
            badge_color = "#FF8C00"
        elif pct_negative >= THRESHOLD_MEDIUM_PCT and n >= THRESHOLD_MEDIUM_MIN_N:
            priority_level = "TRUNG BÌNH"
            badge_color = "#F4C430"
        else:
            priority_level = "DUY TRÌ"
            badge_color = "#2ECC71"

        insights.append({
            "topic": topic,
            "icon": rule.get("icon", "📌"),
            "priority": priority_level,
            "badge_color": badge_color,
            "n": n,
            "negative_count": neg_count,
            "pct_negative": pct_negative,
            "negative_rate": pct_negative,
            "prev_pct_negative": prev_pct_negative,
            "delta_pct_negative": delta_pct_negative,
            "avg_rating": avg_rating,
            "root_causes": rule.get("root_causes", "Đang theo dõi thêm phản hồi từ khách hàng."),
            "quick_win": rule.get("quick_win", "Tăng cường kiểm soát chất lượng tại chỗ."),
            "long_term": rule.get("long_term", "Chuẩn hóa quy trình vận hành và kiểm tra chéo định kỳ."),
            "kpi_target": rule.get("kpi_target", "Duy trì tỷ lệ hài lòng > 90%."),
            "severity_score": severity_score,
        })

    insights.sort(key=lambda x: x["severity_score"], reverse=True)
    return insights

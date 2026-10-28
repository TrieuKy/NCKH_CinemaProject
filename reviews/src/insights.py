"""
Module: insights.py
Mục đích: Sinh đề xuất vận hành (Actionable Insights & Operational Action Plan)
dựa trên bộ quy tắc chuyên biệt cho Ban Quản Lý rạp chiếu phim CGV Landmark 81.
Được tối ưu an toàn dữ liệu, chống lỗi NaN và hỗ trợ mọi kích thước dữ liệu.
"""

from typing import List, Dict, Any
import pandas as pd


# Bộ từ điển quy tắc hành động vận hành (Action Rules Mapping)
ACTION_RULES_CATALOG: Dict[str, Dict[str, Any]] = {
    "Hàng chờ & Bắp nước": {
        "icon": "🍿",
        "root_causes": "Nghẽn hàng đợi giờ cao điểm, máy POS/quầy thu ngân ít nhân sự, bắp không giữ được độ giòn hoặc phân phối chậm.",
        "quick_win": "Mở làn 'Express Line' chuyên nhận combo cho khách đã đặt trước trên CGV App; kiểm tra nhiệt độ tủ giữ ấm bắp 15 phút trước mỗi suất chiếu.",
        "long_term": "Đầu tư thêm Kiosk tự phục vụ (Self-service Kiosk) và tích hợp thanh toán mã QR tự động; tối ưu hóa quy trình phân luồng quầy bar.",
        "kpi_target": "Thời gian chờ nhận bắp nước trung bình < 3 phút/khách; giảm 70% khiếu nại xếp hàng.",
        "priority_weight": 1.2
    },
    "Giá cả & Vé": {
        "icon": "🎟️",
        "root_causes": "Giá vé cuối tuần và phụ thu ghế VIP cao hơn mặt bằng chung mà chưa tạo được trải nghiệm vượt trội tương xứng.",
        "quick_win": "Truyền thông rõ ràng về quyền lợi ghế VIP và phòng IMAX; tặng kèm voucher ưu đãi bắp nước cho vé suất chiếu muộn.",
        "long_term": "Áp dụng cơ chế giá linh hoạt (Dynamic Pricing) cho các suất chiếu thấp điểm; mở rộng quyền lợi thành viên CGV U22 và CGV Member Club.",
        "kpi_target": "Tỷ lệ đánh giá 'Giá trị xứng đáng' (Value for Money) đạt > 80%.",
        "priority_weight": 0.9
    },
    "Phòng chiếu & Âm thanh": {
        "icon": "🔊",
        "root_causes": "Nhiệt độ máy lạnh quá buốt hoặc không đồng đều; loa bị rè ở dải âm bass lớn; góc chiếu bị mờ/khuất.",
        "quick_win": "Cài đặt nhiệt độ phòng chiếu cố định ở mức 23-25°C; thực hiện quy trình Soundcheck & Image Check 10 phút trước khi mở cửa phòng.",
        "long_term": "Bảo dưỡng định kỳ hệ thống âm thanh vòm Dolby Atmos & máy chiếu laser IMAX; bọc nệm da công thái học cao cấp cho toàn bộ rạp.",
        "kpi_target": "Xử lý triệt để 100% lỗi rè loa và lệch nhiệt độ; tỷ lệ đánh giá kỹ thuật phòng chiếu đạt 4.8/5.0 sao.",
        "priority_weight": 1.3
    },
    "Thái độ phục vụ": {
        "icon": "👥",
        "root_causes": "Nhân viên soát vé, thu ngân hoặc bảo vệ mệt mỏi trong giờ cao điểm dẫn đến thiếu niềm nở, giao tiếp cộc cằn.",
        "quick_win": "Họp giao ban đầu ca nhắc nhở văn hoá 'CGV Smile & Care'; phân công Giám sát rạp túc trực tại sảnh chính để hỗ trợ giải quyết sự cố ngay.",
        "long_term": "Tổ chức đào tạo chuẩn mực dịch vụ khách hàng 5 sao định kỳ hàng tháng; gắn KPI thưởng dịch vụ khách hàng (CSAT) trực tiếp vào lương thưởng.",
        "kpi_target": "Tỷ lệ phản hồi tích cực về nhân viên đạt > 90%; không phát sinh phàn nàn gay gắt.",
        "priority_weight": 1.4
    },
    "Vệ sinh": {
        "icon": "🧹",
        "root_causes": "Nhà vệ sinh quá tải vào giờ tan phim; thời gian dọn phòng chiếu giữa 2 suất chiếu quá gấp làm sót rác.",
        "quick_win": "Tăng cường vệ sinh nhà vệ sinh mỗi 30 phút vào tối Thứ 6 & cuối tuần; bổ sung bảng Cleaning Checklist có chữ ký nhân viên.",
        "long_term": "Điều chỉnh giãn cách giữa các suất chiếu từ 15 phút lên 20 phút vào khung giờ cao điểm để đội vệ sinh quét dọn kỹ lưỡng.",
        "kpi_target": "Tỷ lệ phàn nàn vệ sinh duy trì dưới 1% tổng số đánh giá.",
        "priority_weight": 1.5
    }
}


def analyze_topic_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """
    Thống kê chi tiết theo từng chủ đề vận hành:
    - Tổng số đánh giá
    - Số lượng đánh giá tiêu cực (NEGATIVE)
    - Tỷ lệ tiêu cực (%)
    - Điểm rating trung bình
    """
    if df.empty or "topics_list" not in df.columns:
        return pd.DataFrame()

    topic_stats = []

    for topic_name in ACTION_RULES_CATALOG.keys():
        # Lọc an toàn các dòng có chứa topic
        matched_mask = df["topics_list"].apply(
            lambda t_list: (topic_name in t_list) if isinstance(t_list, list) else False
        )
        sub_df = df[matched_mask]
        total_count = len(sub_df)

        if total_count > 0:
            neg_count = len(sub_df[sub_df["sentiment"] == "NEGATIVE"])
            pos_count = len(sub_df[sub_df["sentiment"] == "POSITIVE"])
            neu_count = len(sub_df[sub_df["sentiment"] == "NEUTRAL"])
            neg_rate = round((neg_count / total_count) * 100, 1)
            raw_avg = sub_df["rating"].mean()
            avg_rating = round(float(raw_avg), 2) if not pd.isna(raw_avg) else 5.0
        else:
            neg_count = 0
            pos_count = 0
            neu_count = 0
            neg_rate = 0.0
            avg_rating = 5.0

        topic_stats.append({
            "topic": topic_name,
            "total_mentions": total_count,
            "negative_count": neg_count,
            "positive_count": pos_count,
            "neutral_count": neu_count,
            "negative_rate": neg_rate,
            "avg_rating": avg_rating
        })

    result_df = pd.DataFrame(topic_stats)
    # Sắp xếp theo số lượng tiêu cực giảm dần
    result_df = result_df.sort_values(by=["negative_count", "negative_rate"], ascending=[False, False])
    return result_df


def generate_operational_insights(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Sinh danh sách đề xuất hành động cụ thể cho từng chủ đề vận hành.
    Xếp hạng mức độ ưu tiên (Priority: KHẨN CẤP / CAO / TRUNG BÌNH / DUY TRÌ).
    """
    if df.empty:
        return []

    breakdown_df = analyze_topic_breakdown(df)
    if breakdown_df.empty:
        return []

    insights = []

    for _, row in breakdown_df.iterrows():
        topic = str(row["topic"])
        neg_count = int(row["negative_count"])
        neg_rate = float(row["negative_rate"])
        avg_rating = float(row["avg_rating"]) if not pd.isna(row["avg_rating"]) else 5.0
        rule = ACTION_RULES_CATALOG.get(topic, {})

        # Tính toán mức độ nghiêm trọng (Severity Score)
        weight = float(rule.get("priority_weight", 1.0))
        severity_score = round((neg_count * 2.5 + neg_rate * 0.4) * weight, 2)

        if neg_count >= 5 or neg_rate >= 35.0:
            priority_level = "KHẨN CẤP"
            badge_color = "#E50914"  # Đỏ CGV
        elif neg_count >= 3 or neg_rate >= 20.0:
            priority_level = "ƯU TIÊN CAO"
            badge_color = "#FF8C00"  # Cam
        elif neg_count >= 1 or neg_rate > 0:
            priority_level = "TRUNG BÌNH"
            badge_color = "#F4C430"  # Vàng
        else:
            priority_level = "DUY TRÌ TỐT"
            badge_color = "#2ECC71"  # Xanh lá

        insights.append({
            "topic": topic,
            "icon": rule.get("icon", "📌"),
            "priority": priority_level,
            "badge_color": badge_color,
            "negative_count": neg_count,
            "negative_rate": neg_rate,
            "avg_rating": avg_rating,
            "root_causes": rule.get("root_causes", "Đang theo dõi thêm dữ liệu phản hồi từ khách hàng."),
            "quick_win": rule.get("quick_win", "Tăng cường kiểm soát chất lượng dịch vụ tại chỗ."),
            "long_term": rule.get("long_term", "Chuẩn hóa quy trình vận hành và kiểm tra chéo định kỳ."),
            "kpi_target": rule.get("kpi_target", "Duy trì tỷ lệ hài lòng > 90%."),
            "severity_score": severity_score
        })

    # Sắp xếp các đề xuất theo severity_score giảm dần
    insights.sort(key=lambda x: x["severity_score"], reverse=True)
    return insights

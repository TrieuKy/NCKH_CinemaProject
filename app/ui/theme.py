"""
app/ui/theme.py
---------------
Định nghĩa bảng màu chuẩn (Mục 2.1), CSS dùng chung, và hàm cấu hình trang Streamlit.
"""

from __future__ import annotations
import streamlit as st

# =============================================================================
# 1. BẢNG MÃ MÀU CHUẨN (MỤC 2.1)
# =============================================================================

# Màu chủ đạo (Brand / Primary)
COLOR_PRIMARY = "#E50914"        # Đỏ rạp phim / CGV Red
COLOR_PRIMARY_DARK = "#B91C1C"   # Đỏ đậm nhấn
COLOR_PRIMARY_LIGHT = "#FEE2E2"  # Đỏ nhạt nền

# Màu phụ & Nền trung tính (Neutrals)
COLOR_SECONDARY = "#1E293B"      # Xanh than đậm (Slate 800)
COLOR_SECONDARY_LIGHT = "#334155"# Slate 700
COLOR_BG_MAIN = "#FFFFFF"        # Nền trang chính
COLOR_BG_SECONDARY = "#F8FAFC"   # Nền khối / phụ (Slate 50)
COLOR_BG_CARD = "#FFFFFF"        # Nền thẻ KPI
COLOR_BORDER = "#E2E8F0"         # Viền nhẹ (Slate 200)

# Màu chữ (Typography)
COLOR_TEXT_MAIN = "#1E293B"      # Văn bản chính
COLOR_TEXT_MUTED = "#64748B"     # Văn bản thứ cấp (Slate 500)
COLOR_TEXT_LIGHT = "#94A3B8"     # Chú thích mờ (Slate 400)
COLOR_TEXT_WHITE = "#FFFFFF"

# Màu trạng thái (Status / Alerts)
COLOR_SUCCESS = "#10B981"        # Xanh lá (Green 500)
COLOR_SUCCESS_BG = "#ECFDF5"
COLOR_SUCCESS_BORDER = "#A7F3D0"

COLOR_WARNING = "#F59E0B"        # Cam vàng (Amber 500)
COLOR_WARNING_BG = "#FFFBEB"
COLOR_WARNING_BORDER = "#FDE68A"

COLOR_DANGER = "#EF4444"         # Đỏ cảnh báo (Red 500)
COLOR_DANGER_BG = "#FEF2F2"
COLOR_DANGER_BORDER = "#FECACA"

COLOR_INFO = "#3B82F6"           # Xanh dương (Blue 500)
COLOR_INFO_BG = "#EFF6FF"
COLOR_INFO_BORDER = "#BFDBFE"

# 4 Mức độ ưu tiên (Priority Levels)
PRIORITY_COLORS = {
    "Rất cao": {"bg": "#FEF2F2", "text": "#DC2626", "border": "#FCA5A5"},
    "Cao":     {"bg": "#FFF7ED", "text": "#EA580C", "border": "#FDBA74"},
    "Trung bình": {"bg": "#FEFCE8", "text": "#CA8A04", "border": "#FDE047"},
    "Thấp":    {"bg": "#F1F5F9", "text": "#475569", "border": "#CBD5E1"},
}

# Màu cảm xúc đánh giá (Sentiment)
SENTIMENT_COLORS = {
    "Tích cực": "#10B981",   # Green
    "Trung tính": "#F59E0B", # Amber
    "Tiêu cực": "#EF4444",   # Red
}

# Nguồn dữ liệu (Data Source)
DATA_SOURCE_COLORS = {
    "MOCK":   {"bg": "#EEF2FF", "text": "#4F46E5", "border": "#C7D2FE"},
    "MANUAL": {"bg": "#FFF7ED", "text": "#C2410C", "border": "#FFEDD5"},
    "SYSTEM": {"bg": "#ECFDF5", "text": "#047857", "border": "#A7F3D0"},
}

# Bảng màu phân loại cho đồ thị (Categorical Palette)
CHART_PALETTE = [
    "#E50914",  # Đỏ rạp phim
    "#1E293B",  # Xanh than
    "#3B82F6",  # Xanh lam
    "#10B981",  # Xanh ngọc
    "#F59E0B",  # Vàng hổ phách
    "#8B5CF6",  # Tím violet
    "#EC4899",  # Hồng sẫm
    "#14B8A6",  # Xanh mòng két
]

# Thang màu Heatmap tỷ lệ lấp đầy
HEATMAP_COLORSCALE = [
    [0.0, "#F1F5F9"],  # 0% - Trắng ngà / xám nhạt
    [0.2, "#DBEAFE"],  # 20% - Xanh rất nhạt
    [0.5, "#60A5FA"],  # 50% - Xanh lam vừa
    [0.8, "#2563EB"],  # 80% - Xanh dương đậm
    [1.0, "#1E3A8A"],  # 100% - Xanh biển sâu (chật kín chỗ)
]

# =============================================================================
# 2. CSS TÙY BIẾN GỌN GÀNG (CUSTOM CSS)
# =============================================================================

CUSTOM_CSS = """
<style>
/* Font chữ và căn bản */
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1E293B;
}

/* Thẻ KPI Card */
.kpi-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    padding: 16px 20px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    transition: all 0.2s ease-in-out;
    margin-bottom: 12px;
}
.kpi-card:hover {
    border-color: #CBD5E1;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.kpi-title {
    font-size: 13px;
    font-weight: 600;
    color: #64748B;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
}
.kpi-value {
    font-size: 26px;
    font-weight: 700;
    color: #0F172A;
    line-height: 1.2;
    margin-bottom: 6px;
}
.kpi-delta {
    font-size: 12px;
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.delta-up {
    color: #10B981;
}
.delta-down {
    color: #EF4444;
}
.delta-flat {
    color: #64748B;
}

/* Badge (Huy hiệu) */
.custom-badge {
    display: inline-flex;
    align-items: center;
    padding: 3px 10px;
    border-radius: 9999px;
    font-size: 12px;
    font-weight: 600;
    line-height: 1.2;
}

/* Khung thông báo (Notice) */
.notice-box {
    padding: 12px 16px;
    border-radius: 8px;
    font-size: 14px;
    line-height: 1.5;
    margin: 8px 0 16px 0;
    border-left: 4px solid;
}
.notice-info {
    background-color: #EFF6FF;
    border-left-color: #3B82F6;
    color: #1E40AF;
}
.notice-success {
    background-color: #ECFDF5;
    border-left-color: #10B981;
    color: #065F46;
}
.notice-warning {
    background-color: #FFFBEB;
    border-left-color: #F59E0B;
    color: #92400E;
}
.notice-danger {
    background-color: #FEF2F2;
    border-left-color: #EF4444;
    color: #991B1B;
}

/* Tiêu đề phân mục */
.section-header-box {
    margin: 20px 0 12px 0;
    padding-bottom: 8px;
    border-bottom: 2px solid #F1F5F9;
}
.section-title {
    font-size: 18px;
    font-weight: 700;
    color: #1E293B;
    margin: 0;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-subtitle {
    font-size: 13px;
    color: #64748B;
    margin-top: 2px;
}

/* Dòng tóm tắt */
.summary-strip {
    background: #F8FAFC;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 10px 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 20px;
    align-items: center;
    margin-bottom: 14px;
}
.summary-item {
    font-size: 13px;
    color: #475569;
}
.summary-item strong {
    color: #0F172A;
    font-weight: 700;
    margin-left: 4px;
}

/* Trạng thái trống */
.empty-box {
    text-align: center;
    padding: 40px 20px;
    background: #FAFAFA;
    border: 1px dashed #CBD5E1;
    border-radius: 12px;
    margin: 16px 0;
}
.empty-box-icon {
    font-size: 36px;
    margin-bottom: 8px;
}
.empty-box-text {
    font-size: 14px;
    color: #64748B;
}

/* Sidebar profile card */
.sidebar-user-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 16px;
}
.sidebar-user-name {
    font-weight: 700;
    font-size: 14px;
    color: #0F172A;
}
.sidebar-user-role {
    font-size: 12px;
    color: #64748B;
    margin-top: 2px;
}
</style>
"""

# =============================================================================
# 3. HÀM CẤU HÌNH TRANG (PAGE SETUP)
# =============================================================================

def setup_page(
    page_title: str = "Hệ Thống Phân Tích Rạp Phim",
    page_icon: str = "🎬",
    layout: str = "wide",
    initial_sidebar_state: str = "expanded"
) -> None:
    """
    Cấu hình tiêu đề trang, biểu tượng và inject CSS chung.
    An toàn khi gọi nhiều lần trong session.
    """
    try:
        st.set_page_config(
            page_title=page_title,
            page_icon=page_icon,
            layout=layout,
            initial_sidebar_state=initial_sidebar_state
        )
    except Exception:
        # Nếu đã gọi st.set_page_config trước đó thì bỏ qua lỗi
        pass

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


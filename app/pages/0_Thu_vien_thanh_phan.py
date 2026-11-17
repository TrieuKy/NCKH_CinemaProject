"""
app/pages/0_Thu_vien_thanh_phan.py
----------------------------------
Trang Thư viện Thành phần Giao diện (UI Component Showcase):
- Hiển thị MỌI thành phần giao diện đã xây dựng trong UI-1.
- Toàn bộ dữ liệu hiển thị là CỐ ĐỊNH ngay trong file này (KHÔNG ghi vào database).
- Dùng để kiểm thử, nghiệm thu và duyệt giao diện trước khi triển khai các trang nghiệp vụ.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

# Đường dẫn gốc
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import (
    setup_page,
    COLOR_PRIMARY,
    PRIORITY_COLORS,
    DATA_SOURCE_COLORS,
    SENTIMENT_COLORS,
)
from app.ui.format import (
    format_currency,
    format_decimal,
    format_percent,
    format_date,
    format_weekday,
    format_date_with_weekday,
    format_delta,
)
from app.ui.charts import (
    plot_occupancy_heatmap,
    plot_sentiment_donut,
)
from app.ui.components import (
    KPI_DICTIONARY,
    kpi_card,
    formula_tip,
    badge,
    data_source_tag,
    section_header,
    summary_line,
    empty_state,
    notice,
    run_job,
    confirm_dialog,
    data_table,
)
from app.ui.auth import check_permission, get_current_user
from app.main import render_standard_filters


# Khởi tạo giao diện
setup_page("Thư Viện Thành Phần UI — Cinema DW", "🧩")

# Kiểm tra quyền truy cập (cả STAFF và MANAGER đều xem được, nếu chưa đăng nhập thì hiện cảnh báo thân thiện)
current_user = get_current_user()
user_role = current_user.get("role", "MANAGER") if current_user else "MANAGER"


# =============================================================================
# HEADER TRANG
# =============================================================================

st.markdown(
    f"""
    <div style="background: linear-gradient(90deg, #1E293B 0%, #0F172A 100%); padding: 22px 28px; border-radius: 12px; margin-bottom: 24px; color: white;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <span style="background: {COLOR_PRIMARY}; color: white; padding: 3px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase;">
                    NHIỆM VỤ UI-1
                </span>
                <h1 style="color: white; margin: 8px 0 4px 0; font-size: 26px; font-weight: 800;">
                    🧩 Thư Viện Thành Phần Giao Diện (UI Library)
                </h1>
                <p style="color: #94A3B8; margin: 0; font-size: 14px;">
                    Duyệt toàn bộ linh kiện UI chuẩn: Thẻ KPI, Badge, Heatmap, Bảng dữ liệu, Nút tác vụ, Bộ lọc chuẩn & Từ điển chỉ số.
                </p>
            </div>
            <div style="text-align: right;">
                <span style="color: #CBD5E1; font-size: 12px;">Dữ liệu kiểm thử:</span><br>
                <span style="color: #38BDF8; font-weight: 700; font-size: 14px;">CỐ ĐỊNH (In-Memory)</span>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Nút quay về trang chính
if st.button("⬅️ Quay lại Trang chính (Main)"):
    st.switch_page("main.py")

st.markdown("---")


# =============================================================================
# PHẦN 1: THẺ CHỈ SỐ KPI (KPI CARDS) & MẸO CÔNG THỨC (FORMULA TIP)
# =============================================================================

section_header("1. Thẻ KPI (KPI Cards) & Tooltip Công Thức", subtitle="Hiển thị các chỉ số đo lường hiệu suất rạp kèm delta so với kỳ trước", icon="📊")

st.markdown("##### 💡 Hàng thẻ chỉ số chính (Dùng Từ điển KPI một nơi duy nhất)")

row1_col1, row1_col2, row1_col3, row1_col4 = st.columns(4)

with row1_col1:
    kpi_card(
        kpi_key="revenue",
        value=1_850_000_000,
        delta=0.085,
        compact_currency=True,
    )
    formula_tip("revenue")

with row1_col2:
    kpi_card(
        kpi_key="tickets",
        value=21_750,
        delta=0.123,
    )
    formula_tip("tickets")

with row1_col3:
    kpi_card(
        kpi_key="occupancy",
        value=0.684,
        delta=-0.021,
    )
    formula_tip("occupancy")

with row1_col4:
    kpi_card(
        kpi_key="atp",
        value=85_050,
        delta=0.0,
    )
    formula_tip("atp")

st.markdown("##### 💡 Hàng thẻ chỉ số bổ trợ & Cảnh báo")

row2_col1, row2_col2, row2_col3, row2_col4 = st.columns(4)

with row2_col1:
    kpi_card(
        kpi_key="revpas",
        value=58_200,
        delta=0.042,
    )
    formula_tip("revpas")

with row2_col2:
    kpi_card(
        kpi_key="fnb_attach_rate",
        value=0.426,
        delta=0.038,
    )
    formula_tip("fnb_attach_rate")

with row2_col3:
    kpi_card(
        kpi_key="sph",
        value=38_500,
        delta=0.050,
    )
    formula_tip("sph")

with row2_col4:
    # Cảnh báo bất thường: Tăng là số xấu nên reverse_delta_color=True (màu đỏ)
    kpi_card(
        kpi_key="anomaly_count",
        value=4,
        delta=0.33,
        delta_text="↑ +1 vụ (25%)",
        reverse_delta_color=True,
    )
    formula_tip("anomaly_count")

st.markdown("---")


# =============================================================================
# PHẦN 2: HUY HIỆU (BADGES) & 4 MỨC ĐỘ ƯU TIÊN
# =============================================================================

section_header("2. Huy hiệu (Badges) & 4 Mức Độ Ưu Tiên", subtitle="Chuẩn hóa màu sắc trạng thái nghiệp vụ và mức độ ưu tiên BRD", icon="🏷️")

col_badge_pri, col_badge_status = st.columns(2)

with col_badge_pri:
    st.markdown("##### 🎯 4 Mức độ ưu tiên (Priority Levels):")
    html_pri = f"""
    <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:12px; background:#F8FAFC; border-radius:8px;">
        {badge("Rất cao", level="critical")}
        {badge("Cao", level="high")}
        {badge("Trung bình", level="medium")}
        {badge("Thấp", level="low")}
    </div>
    """
    st.markdown(html_pri, unsafe_allow_html=True)
    st.caption("Dùng cho danh sách cảnh báo, nhiệm vụ ETL, hoặc phân loại đề xuất vận hành.")

with col_badge_status:
    st.markdown("##### 🚦 Huy hiệu trạng thái nghiệp vụ:")
    html_status = f"""
    <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:12px; background:#F8FAFC; border-radius:8px;">
        {badge("Đã khóa số", level="success")}
        {badge("Chờ duyệt", level="warning")}
        {badge("Bất thường", level="danger")}
        {badge("Đang chạy", level="info")}
        {badge("Bản nháp", level="secondary")}
    </div>
    """
    st.markdown(html_status, unsafe_allow_html=True)
    st.caption("Dùng cho trạng thái suất chiếu, phiếu nhập bắp nước, nhật ký nạp kho.")

st.markdown("---")


# =============================================================================
# PHẦN 3: THẺ NGUỒN DỮ LIỆU (DATA SOURCE TAGS)
# =============================================================================

section_header("3. Thẻ Nguồn Dữ Liệu (Data Source Tags)", subtitle="Minh bạch nguồn gốc dữ liệu phục vụ đối soát và NCKH", icon="🔖")

html_ds = f"""
<div style="display:flex; gap:16px; align-items:center; flex-wrap:wrap; padding:14px; background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px;">
    <div>{data_source_tag("MOCK")} &nbsp; <span style="font-size:13px; color:#475569;">Dữ liệu mô phỏng theo giả định NCKH (18 tháng)</span></div>
    <div>{data_source_tag("MANUAL")} &nbsp; <span style="font-size:13px; color:#475569;">Dữ liệu do nhân viên rạp nhập thủ công</span></div>
    <div>{data_source_tag("SYSTEM")} &nbsp; <span style="font-size:13px; color:#475569;">Dữ liệu tự động đồng bộ từ kho dữ liệu DW</span></div>
</div>
"""
st.markdown(html_ds, unsafe_allow_html=True)

st.markdown("---")


# =============================================================================
# PHẦN 4: TIÊU ĐỀ PHÂN MỤC & DÒNG TÓM TẮT (SUMMARY LINE)
# =============================================================================

section_header("4. Tiêu đề Phân Mục & Dòng Tóm Tắt (Summary Strip)", subtitle="Hiển thị nhanh các tổng kết quan trọng đầu mỗi trang", icon="📑")

summary_line([
    ("Cụm rạp theo dõi", "3 rạp (Landmark 81, Đồng Khởi, Pearl Plaza)"),
    ("Tổng phòng chiếu", "15 phòng"),
    ("Tổng suất trong kỳ", "450 suất"),
    ("Doanh thu gộp", "1.850.000.000 đ"),
    ("Trạng thái", "Toàn vẹn (100% khớp nguồn)"),
])

st.markdown("---")


# =============================================================================
# PHẦN 5: THÔNG BÁO (NOTICE) & TRẠNG THÁI TRỐNG (EMPTY STATE)
# =============================================================================

section_header("5. Khung Thông Báo (Notice) & Trạng Thái Trống (Empty State)", subtitle="Các hộp cảnh báo và thông điệp tương tác khi dữ liệu rỗng", icon="📢")

col_notice, col_empty = st.columns(2)

with col_notice:
    st.markdown("##### 🔔 Các loại thông báo (Notices):")
    notice("Đây là thông báo thông tin chung (Info notice).", type="info")
    notice("Dữ liệu nạp thành công, đã đối soát 100% doanh thu (Success notice).", type="success")
    notice("Phát hiện 2 suất chiếu có tỷ lệ lấp đầy dưới 15% (Warning notice).", type="warning")
    notice("Cảnh báo: Suất chiếu 20:00 phòng 3 vượt quá sức chứa tối đa (Danger notice).", type="danger")

with col_empty:
    st.markdown("##### 📭 Minh họa trạng thái trống (Empty State):")
    empty_state(
        message="Không có cảnh báo bất thường nào trong khoảng ngày đã chọn.",
        icon="🎉",
        action_label="🔄 Làm mới bộ lọc",
        on_action=lambda: st.toast("Đã làm mới bộ lọc!"),
    )

st.markdown("---")


# =============================================================================
# PHẦN 6: BIỂU ĐỒ PLOTLY CHUNG & HEATMAP TỶ LỆ LẤP ĐẦY
# =============================================================================

section_header("6. Biểu Đồ Plotly & Heatmap Tỷ Lệ Lấp Đầy", subtitle="Giao diện biểu đồ nền trong suốt, lưới nhạt, thang màu chuẩn", icon="📈")

col_chart1, col_chart2 = st.columns([1.6, 1.2])

with col_chart1:
    # Dữ liệu cố định minh họa tỷ lệ lấp đầy theo suất chiếu (%)
    heatmap_df = pd.DataFrame(
        [
            [35.2, 52.0, 78.4, 95.0, 82.5],
            [22.0, 41.5, 65.0, 88.0, 71.0],
            [15.5, 28.0, 45.0, 84.5, 60.0],
            [42.0, 68.5, 89.0, 98.5, 91.0],
            [18.0, 32.0, 50.5, 76.0, 55.0],
        ],
        index=["Phòng 1 (IMAX)", "Phòng 2 (Gold Class)", "Phòng 3 (Standard)", "Phòng 4 (Standard)", "Phòng 5 (VIP)"],
        columns=["09:30 (Sáng)", "13:00 (Trưa)", "16:30 (Chiều)", "19:45 (Tối)", "22:15 (Đêm)"],
    )

    fig_heat = plot_occupancy_heatmap(
        heatmap_df,
        title="Tỷ lệ lấp đầy theo phòng chiếu & khung giờ (%)",
        height=380,
    )
    st.plotly_chart(fig_heat, use_container_width=True)

with col_chart2:
    # Biểu đồ cảm xúc Google Maps cố định
    sentiment_data = {
        "Tích cực": 1420,
        "Trung tính": 380,
        "Tiêu cực": 150,
    }
    fig_sent = plot_sentiment_donut(
        sentiment_data,
        title="Phân bố cảm xúc đánh giá rạp",
        height=380,
    )
    st.plotly_chart(fig_sent, use_container_width=True)

st.markdown("---")


# =============================================================================
# PHẦN 7: BẢNG DỮ LIỆU TƯƠNG TÁC (DATA TABLE) & XUẤT CSV / EXCEL
# =============================================================================

section_header("7. Bảng Dữ Liệu Tương Tác & Xuất File CSV / Excel", subtitle="Trình bày bảng rõ ràng, có phân trang và tải xuống định dạng .csv, .xlsx", icon="📋")

# Dữ liệu minh họa cố định
sample_cinema_table = pd.DataFrame([
    {"Mã suất": "SC-101", "Phim": "Mai", "Cụm rạp": "CGV Landmark 81", "Phòng": "P1 - IMAX", "Khung giờ": "19:45", "Vé bán": 245, "Doanh thu (đ)": 28500000, "Lấp đầy": "98,0%", "Nguồn": "MOCK"},
    {"Mã suất": "SC-102", "Phim": "Đào, Phở và Piano", "Cụm rạp": "CGV Vincom Đồng Khởi", "Phòng": "P2", "Khung giờ": "19:00", "Vé bán": 180, "Doanh thu (đ)": 17100000, "Lấp đầy": "90,0%", "Nguồn": "MOCK"},
    {"Mã suất": "SC-103", "Phim": "Kung Fu Panda 4", "Cụm rạp": "CGV Pearl Plaza", "Phòng": "P1", "Khung giờ": "16:30", "Vé bán": 150, "Doanh thu (đ)": 13500000, "Lấp đầy": "75,0%", "Nguồn": "MANUAL"},
    {"Mã suất": "SC-104", "Phim": "Godzilla x Kong", "Cụm rạp": "CGV Landmark 81", "Phòng": "P3", "Khung giờ": "21:30", "Vé bán": 195, "Doanh thu (đ)": 21450000, "Lấp đầy": "88,6%", "Nguồn": "MOCK"},
    {"Mã suất": "SC-105", "Phim": "Lật Mặt 7", "Cụm rạp": "CGV Pearl Plaza", "Phòng": "P2", "Khung giờ": "10:00", "Vé bán": 42, "Doanh thu (đ)": 3780000, "Lấp đầy": "28,0%", "Nguồn": "MANUAL"},
])

data_table(
    sample_cinema_table,
    download_name="danh_sach_suat_chieu_mau.csv",
    allow_download=True,
    key="sample_table",
)

st.markdown("---")


# =============================================================================
# PHẦN 8: HÀNH ĐỘNG NỀN (RUN JOB) & HỘP THOẠI XÁC NHẬN (CONFIRM DIALOG)
# =============================================================================

section_header("8. Nút Chạy Tác Vụ (Run Job) & Hộp Thoại Xác Nhận (Confirm Dialog)", subtitle="Xử lý an toàn cho các tác vụ tốn thời gian hoặc thao tác nhạy cảm", icon="⚙️")

col_job, col_dialog = st.columns(2)

with col_job:
    st.markdown("##### 🚀 Minh họa chạy tác vụ nền (Run Job):")
    st.write("Nhấn nút để chạy mô phỏng ETL / huấn luyện dự báo với spinner:")

    def _simulate_long_job():
        time.sleep(1.2)
        return "OK - Đã xử lý 4.500 bản ghi."

    run_job(
        job_name="Đồng bộ ETL kho dữ liệu",
        func=_simulate_long_job,
        button_label="⚡ Chạy mô phỏng Job ETL",
        success_message="Đồng bộ kho dữ liệu thành công! 4.500 bản ghi đã được nạp.",
    )

with col_dialog:
    st.markdown("##### ⚠️ Minh họa hộp thoại xác nhận (Confirm Dialog):")
    st.write("Yêu cầu xác nhận trước khi thực hiện thao tác nhạy cảm:")

    is_confirmed = confirm_dialog(
        title="Khóa dữ liệu ngày 15/11/2026",
        message="Hành động này sẽ đặt `is_locked = TRUE`. Dữ liệu đã khóa sẽ không thể chỉnh sửa bởi nhân viên rạp.",
        confirm_label="🔒 Đồng ý khóa",
        cancel_label="Hủy",
        key="confirm_demo",
    )
    if is_confirmed:
        st.success("✅ Đã ghi nhận xác nhận khóa dữ liệu thành công!")

st.markdown("---")


# =============================================================================
# PHẦN 9: TỪ ĐIỂN KPI TẬP TRUNG (SINGLE SOURCE OF TRUTH)
# =============================================================================

section_header("9. Từ Điển KPI Tập Trung (KPI Dictionary)", subtitle="Nơi định nghĩa duy nhất toàn bộ công thức, ý nghĩa và đơn vị đo lường", icon="📖")

dict_rows = []
for k, meta in KPI_DICTIONARY.items():
    dict_rows.append({
        "Mã chỉ số (Key)": k,
        "Tên chỉ số": meta["name"],
        "Công thức tính": meta["formula"],
        "Đơn vị": meta.get("unit", "-"),
        "Ý nghĩa nghiệp vụ": meta["tooltip"],
    })

kpi_dict_df = pd.DataFrame(dict_rows)
st.dataframe(kpi_dict_df, use_container_width=True, hide_index=True)

st.markdown("---")


# =============================================================================
# PHẦN 10: THANH BỘ LỌC CHUẨN (MỤC 3.3)
# =============================================================================

section_header("10. Thử Nghiệm Thanh Bộ Lọc Chuẩn (Mục 3.3)", subtitle="Tái sử dụng cho mọi màn hình báo cáo và bảng điều khiển", icon="🎛️")

tab_mgr, tab_staff = st.tabs(["Vai trò Quản lý (MANAGER)", "Vai trò Nhân viên (STAFF)"])

with tab_mgr:
    st.caption("Góc nhìn MANAGER: Có quyền chọn toàn hệ thống hoặc lọc riêng từng rạp.")
    mgr_filters = render_standard_filters(
        user_role="MANAGER",
        key_prefix="demo_mgr_",
    )
    st.write("Giá trị bộ lọc MANAGER trả về:", mgr_filters)

with tab_staff:
    st.caption("Góc nhìn STAFF: Rạp bị khóa cố định theo chi nhánh được phân công (ví dụ: Landmark 81).")
    staff_filters = render_standard_filters(
        user_role="STAFF",
        fixed_cinema_key=1,
        key_prefix="demo_staff_",
    )
    st.write("Giá trị bộ lọc STAFF trả về:", staff_filters)

st.markdown("---")


# =============================================================================
# PHẦN 11: BẢNG KIỂM TRA ĐỊNH DẠNG (FORMAT SHOWCASE)
# =============================================================================

section_header("11. Quy Chuẩn Định Dạng Số & Ngày (Format Showcase)", subtitle="Đối chiếu định dạng chuẩn Việt Nam: tiền tệ, số phẩy, ngày thứ", icon="🔢")

format_demo_data = [
    {"Khoản mục": "Tiền đầy đủ", "Giá trị gốc": "1234567", "Kết quả": format_currency(1234567)},
    {"Khoản mục": "Tiền rút gọn (Tỷ)", "Giá trị gốc": "1850000000", "Kết quả": format_currency(1850000000, compact=True)},
    {"Khoản mục": "Tiền rút gọn (Triệu)", "Giá trị gốc": "25400000", "Kết quả": format_currency(25400000, compact=True)},
    {"Khoản mục": "Số thập phân dấu phẩy", "Giá trị gốc": "1234.56", "Kết quả": format_decimal(1234.56, decimals=2)},
    {"Khoản mục": "Phần trăm (1 chữ số)", "Giá trị gốc": "0.754", "Kết quả": format_percent(0.754)},
    {"Khoản mục": "Ngày chuẩn dd/mm/yyyy", "Giá trị gốc": "2026-11-16", "Kết quả": format_date("2026-11-16")},
    {"Khoản mục": "Thứ trong tuần", "Giá trị gốc": "2026-11-16", "Kết quả": format_weekday("2026-11-16")},
    {"Khoản mục": "Ngày + Thứ kết hợp", "Giá trị gốc": "2026-11-16", "Kết quả": format_date_with_weekday("2026-11-16")},
    {"Khoản mục": "Delta tăng (Xanh)", "Giá trị gốc": "+0.052", "Kết quả": format_delta(0.052)["text"]},
    {"Khoản mục": "Delta giảm (Đỏ)", "Giá trị gốc": "-0.031", "Kết quả": format_delta(-0.031)["text"]},
    {"Khoản mục": "Delta bất thường tăng (Đảo màu Đỏ)", "Giá trị gốc": "+0.05", "Kết quả": format_delta(0.05, reverse_color=True)["text"]},
]

st.table(pd.DataFrame(format_demo_data))

st.success("🎉 Hoàn tất duyệt toàn bộ thư viện thành phần giao diện Streamlit UI-1!")

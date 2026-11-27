"""
app/main.py
-----------
Điểm khởi động chính của ứng dụng Streamlit:
- Màn hình đăng nhập bảo mật (bằng cinema_dw.app_user bcrypt).
- Sidebar điều hướng động theo vai trò người dùng (Mục 3.2: STAFF / MANAGER).
- Thanh bộ lọc chuẩn dùng lại (Mục 3.3: Rạp, Khoảng ngày, Nguồn dữ liệu).
- Điều hướng và kết nối với trang Thư viện thành phần (0_Thu_vien_thanh_phan.py).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

# Đảm bảo đường dẫn gốc của dự án có trong sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.theme import (
    setup_page,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    COLOR_TEXT_MAIN,
)
from app.ui.format import (
    format_date,
    format_date_with_weekday,
)
from app.ui.components import (
    badge,
    notice,
    section_header,
    summary_line,
)
from app.ui.auth import (
    init_session_state,
    login_user,
    logout_user,
    is_authenticated,
    get_current_user,
    authenticate_user,
    hash_password,
)

# Danh mục 3 rạp chuẩn của dự án CGV
CINEMA_MAP = {
    1: "CGV Landmark 81",
    2: "CGV Vincom Đồng Khởi",
    3: "CGV Pearl Plaza",
}


# =============================================================================
# 1. THANH BỘ LỌC CHUẨN DÙNG LẠI (MỤC 3.3)
# =============================================================================

def render_standard_filters(
    user_role: str = "MANAGER",
    fixed_cinema_key: int | None = None,
    show_cinema: bool = True,
    show_date: bool = True,
    show_source: bool = True,
    key_prefix: str = "filter_",
) -> dict[str, Any]:
    """
    Thanh bộ lọc chuẩn nghiệp vụ (Mục 3.3):
    - Rạp:
        * Nếu là STAFF (hoặc có fixed_cinema_key): Khóa cứng rạp được phân công, không cho chọn rạp khác.
        * Nếu là MANAGER: Menu thả xuống chọn 'Tất cả cụm rạp' hoặc từng rạp cụ thể.
    - Khoảng ngày:
        * Tùy chọn nhanh: 30 ngày gần nhất, 7 ngày gần nhất, Hôm nay, Tháng này, Quý này, Tùy chọn ngày.
    - Nguồn dữ liệu: Tất cả, MOCK (Mô phỏng), MANUAL (Nhập tay).
    Trả về dict chứa các giá trị lọc đã chọn.
    """
    filters: dict[str, Any] = {}

    with st.container():
        st.markdown(
            """
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:12px 16px; margin-bottom:16px;">
                <span style="font-weight:700; font-size:13px; color:#475569; text-transform:uppercase; letter-spacing:0.5px;">
                    🔍 Bộ lọc dữ liệu chuẩn
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        cols = st.columns(3)

        # 1. Bộ lọc Rạp
        if show_cinema:
            with cols[0]:
                if user_role == "STAFF" and fixed_cinema_key:
                    cinema_name = CINEMA_MAP.get(fixed_cinema_key, f"Rạp ID {fixed_cinema_key}")
                    st.selectbox(
                        "Cụm rạp phân công:",
                        options=[cinema_name],
                        index=0,
                        disabled=True,
                        key=f"{key_prefix}cinema_fixed",
                        help="Tài khoản nhân viên được gán cố định theo rạp làm việc.",
                    )
                    filters["cinema_key"] = fixed_cinema_key
                    filters["cinema_name"] = cinema_name
                else:
                    cinema_options = ["Tất cả cụm rạp"] + list(CINEMA_MAP.values())
                    selected_cinema = st.selectbox(
                        "Cụm rạp:",
                        options=cinema_options,
                        index=0,
                        key=f"{key_prefix}cinema_select",
                    )
                    if selected_cinema == "Tất cả cụm rạp":
                        filters["cinema_key"] = None
                        filters["cinema_name"] = "Tất cả cụm rạp"
                    else:
                        for k, v in CINEMA_MAP.items():
                            if v == selected_cinema:
                                filters["cinema_key"] = k
                                filters["cinema_name"] = v
                                break

        # 2. Bộ lọc Thời gian
        if show_date:
            with cols[1]:
                date_presets = [
                    "30 ngày gần nhất",
                    "7 ngày gần nhất",
                    "Hôm nay",
                    "Tháng này",
                    "Quý này",
                    "Tùy chọn...",
                ]
                preset = st.selectbox(
                    "Khoảng thời gian:",
                    options=date_presets,
                    index=0,
                    key=f"{key_prefix}date_preset",
                )

                today = date.today()
                if preset == "Hôm nay":
                    start_d, end_d = today, today
                elif preset == "7 ngày gần nhất":
                    start_d, end_d = today - timedelta(days=7), today
                elif preset == "30 ngày gần nhất":
                    start_d, end_d = today - timedelta(days=30), today
                elif preset == "Tháng này":
                    start_d, end_d = today.replace(day=1), today
                elif preset == "Quý này":
                    quarter_month = ((today.month - 1) // 3) * 3 + 1
                    start_d, end_d = today.replace(month=quarter_month, day=1), today
                else:
                    # Tùy chọn ngày tùy biến
                    custom_dates = st.date_input(
                        "Chọn ngày bắt đầu - kết thúc:",
                        value=(today - timedelta(days=30), today),
                        key=f"{key_prefix}custom_dates",
                    )
                    if isinstance(custom_dates, (tuple, list)) and len(custom_dates) == 2:
                        start_d, end_d = custom_dates
                    elif isinstance(custom_dates, (tuple, list)) and len(custom_dates) == 1:
                        start_d = end_d = custom_dates[0]
                    else:
                        start_d = end_d = today

                filters["start_date"] = start_d
                filters["end_date"] = end_d
                filters["date_label"] = f"{format_date(start_d)} → {format_date(end_d)}"

        # 3. Bộ lọc Nguồn dữ liệu
        if show_source:
            with cols[2]:
                source_options = ["Tất cả", "MOCK (Mô phỏng)", "MANUAL (Nhập tay)"]
                selected_source = st.selectbox(
                    "Nguồn dữ liệu:",
                    options=source_options,
                    index=0,
                    key=f"{key_prefix}source_select",
                )
                if "MOCK" in selected_source:
                    filters["source"] = "MOCK"
                elif "MANUAL" in selected_source:
                    filters["source"] = "MANUAL"
                else:
                    filters["source"] = None

    return filters


# =============================================================================
# 2. MÀN HÌNH ĐĂNG NHẬP (LOGIN SCREEN)
# =============================================================================

def render_login_screen() -> None:
    """Hiển thị giao diện đăng nhập an toàn."""
    col_left, col_center, col_right = st.columns([1, 1.8, 1])

    with col_center:
        st.markdown(
            f"""
            <div style="text-align:center; margin-top:30px; margin-bottom:24px;">
                <div style="font-size:42px;">🎬</div>
                <h1 style="color:{COLOR_PRIMARY}; font-size:24px; font-weight:800; margin:6px 0;">
                    CINEMA ANALYTICS DW
                </h1>
                <p style="color:#64748B; font-size:14px; margin:0;">
                    Hệ thống Phân Tích Hành Vi Khách Hàng & Vận Hành Rạp Phim
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.container():
            with st.form(key="login_form", clear_on_submit=False):
                st.markdown("##### 🔐 Đăng nhập hệ thống")
                username = st.text_input("Tên đăng nhập:", placeholder="Nhập tài khoản (vd: manager hoặc staff)")
                password = st.text_input("Mật khẩu:", type="password", placeholder="Nhập mật khẩu")

                submitted = st.form_submit_button("Đăng nhập", type="primary", use_container_width=True)

                if submitted:
                    if not username or not password:
                        st.warning("Vui lòng điền đầy đủ tên đăng nhập và mật khẩu.")
                    else:
                        with st.spinner("Đang xác thực tài khoản..."):
                            user = authenticate_user(username, password)

                            # Chế độ DEMO: chỉ bật khi biến môi trường CINEMA_DEMO_MODE=1
                            # KHÔNG được kích hoạt mặc định trong vận hành.
                            import os as _os
                            _demo_mode = _os.environ.get("CINEMA_DEMO_MODE", "0").strip() == "1"
                            if not user and _demo_mode:
                                _DEMO_USERS = {
                                    ("manager", "manager123"): {
                                        "user_id": "demo-manager-uuid",
                                        "username": "manager",
                                        "role": "MANAGER",
                                        "cinema_key": None,
                                        "login_time": datetime.now(),
                                    },
                                    ("staff", "staff123"): {
                                        "user_id": "demo-staff-uuid",
                                        "username": "staff",
                                        "role": "STAFF",
                                        "cinema_key": 1,
                                        "login_time": datetime.now(),
                                    },
                                }
                                user = _DEMO_USERS.get((username, password))

                            if user:
                                login_user(user)
                                st.success("Đăng nhập thành công! Đang chuyển hướng...")
                                st.rerun()
                            else:
                                st.error("❌ Tên đăng nhập hoặc mật khẩu không chính xác.")

        # Thông tin hướng dẫn đăng nhập
        import os as _os2
        _demo_mode_ui = _os2.environ.get("CINEMA_DEMO_MODE", "0").strip() == "1"
        if _demo_mode_ui:
            st.markdown(
                """
                <div style="margin-top:20px; padding:12px 14px; background:#FFFBEB; border:1px dashed #FDE68A; border-radius:8px; font-size:12px; color:#92400E;">
                    <strong>⚠️ Chế độ DEMO đang bật (CINEMA_DEMO_MODE=1):</strong><br>
                    • Quản lý tổng: <code>manager</code> / <code>manager123</code> (Vai trò: MANAGER)<br>
                    • Nhân viên rạp: <code>staff</code> / <code>staff123</code> (Vai trò: STAFF - Rạp Landmark 81)<br>
                    <em>Chế độ demo chỉ dùng cho nghiên cứu — tắt biến môi trường khi vận hành thật.</em>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
                <div style="margin-top:20px; padding:12px 14px; background:#F8FAFC; border:1px dashed #CBD5E1; border-radius:8px; font-size:12px; color:#64748B;">
                    <strong>💡 Đăng nhập bằng tài khoản PostgreSQL:</strong><br>
                    Tạo tài khoản bằng lệnh: <code>python etl/create_user.py</code><br>
                    <em>Nếu chưa có cơ sở dữ liệu, liên hệ quản trị viên hệ thống.</em>
                </div>
                """,
                unsafe_allow_html=True,
            )


# =============================================================================
# 3. SIDEBAR ĐIỀU HƯỚNG THEO VAI TRÒ (MỤC 3.2)
# =============================================================================

def render_sidebar_navigation(user: dict[str, Any]) -> None:
    """Hiển thị sidebar thông tin user và cây menu điều hướng theo vai trò."""
    role = user.get("role", "STAFF")
    username = user.get("username", "Người dùng")
    cinema_key = user.get("cinema_key")

    with st.sidebar:
        # Tiêu đề hệ thống
        st.markdown(
            f"""
            <div style="padding-bottom:12px; border-bottom:1px solid #E2E8F0; margin-bottom:14px;">
                <div style="font-size:22px; font-weight:800; color:{COLOR_PRIMARY};">🎬 CINEMA DW</div>
                <div style="font-size:12px; color:#64748B;">Hệ thống Vận hành & Phân tích</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Thẻ thông tin người dùng
        cinema_info = (
            f"📍 {CINEMA_MAP.get(cinema_key, f'Rạp ID {cinema_key}')}"
            if role == "STAFF" and cinema_key
            else "🌐 Toàn hệ thống (3 cụm rạp)"
        )
        role_badge = (
            badge("QUẢN LÝ", level="warning") if role == "MANAGER" else badge("NHÂN VIÊN", level="info")
        )

        st.markdown(
            f"""
            <div class="sidebar-user-card">
                <div class="sidebar-user-name">👤 {username}</div>
                <div style="margin: 4px 0;">{role_badge}</div>
                <div style="font-size:11px; color:#64748B; margin-top:4px;">{cinema_info}</div>
                <div style="font-size:10px; color:#94A3B8; margin-top:2px;">Phiên làm việc: 8 giờ</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("🚪 Đăng xuất", type="secondary", use_container_width=True):
            logout_user()
            st.rerun()

        st.markdown("---")
        st.markdown("### 📌 Menu nghiệp vụ")

        # Điều hướng danh mục theo vai trò (Task 10)
        if role == "STAFF":
            st.caption("CHỨC NĂNG NHÂN VIÊN RẠP (STAFF)")
            st.page_link("pages/4_Review_Analytics.py", label="📝 Review Analytics", icon="⭐")
            st.page_link("pages/0_Thu_vien_thanh_phan.py", label="🧩 Thư viện thành phần", icon="🎨")
        else:
            st.caption("CHỨC NĂNG BAN QUẢN LÝ (MANAGER)")
            st.page_link("pages/1_KPI_Dashboard.py",    label="📊 Tổng quan KPI vận hành",   icon="📊")
            st.page_link("pages/2_RFM_Segmentation.py", label="👥 Phân khúc khách hàng RFM", icon="👥")
            st.page_link("pages/3_Forecasting.py",      label="📈 Dự báo nhu cầu vé",        icon="📈")
            st.page_link("pages/4_Review_Analytics.py", label="⭐ Review Analytics",          icon="⭐")
            st.page_link("pages/5_DSS_Dashboard.py",    label="💡 DSS Recommendations",       icon="💡")
            st.page_link("pages/0_Thu_vien_thanh_phan.py", label="🧩 Thư viện thành phần",   icon="🎨")

        st.markdown("---")
        st.caption("🎬 Đề tài NCKH Sinh viên HUTECH")


# =============================================================================
# 4. TRANG CHÍNH (MAIN HOME PAGE KHI ĐÃ ĐĂNG NHẬP)
# =============================================================================

def render_home_page(user: dict[str, Any]) -> None:
    """Hiển thị trang tổng quan chính khi người dùng đã đăng nhập."""
    role = user.get("role", "STAFF")
    username = user.get("username", "")
    cinema_key = user.get("cinema_key")

    render_sidebar_navigation(user)

    section_header(
        f"Xin chào, {username}!",
        subtitle=f"Vai trò: {role} | Phiên làm việc hợp lệ trong 8 giờ.",
        icon="👋",
    )

    # Trạng thái kết nối DB
    try:
        from app.data_access import check_db_connection
        db_ok, db_msg = check_db_connection()
        if db_ok:
            notice("✅ Đã kết nối PostgreSQL cinema_dw thành công.", type="success")
        else:
            notice(f"⚠️ Chưa kết nối được PostgreSQL: {db_msg}. Một số trang sẽ hiển thị dữ liệu trống.", type="warning")
    except Exception:
        notice("⚠️ Không thể kiểm tra kết nối PostgreSQL.", type="warning")

    notice(
        "Task 10 — Dashboard đã tích hợp dữ liệu thực từ PostgreSQL. "
        "Chọn trang nghiệp vụ từ sidebar để xem dữ liệu.",
        type="info",
    )

    # Các nút điều hướng nhanh theo vai trò
    if role == "MANAGER":
        st.markdown("#### 🚀 Truy cập nhanh:")
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.page_link("pages/1_KPI_Dashboard.py",    label="📊 KPI Dashboard")
        with c2:
            st.page_link("pages/2_RFM_Segmentation.py", label="👥 RFM Segments")
        with c3:
            st.page_link("pages/3_Forecasting.py",      label="📈 Forecasting")
        with c4:
            st.page_link("pages/4_Review_Analytics.py", label="⭐ Reviews")
        with c5:
            st.page_link("pages/5_DSS_Dashboard.py",    label="💡 DSS")
    else:
        st.markdown("#### 🚀 Truy cập nhanh:")
        c1, c2 = st.columns(2)
        with c1:
            st.page_link("pages/4_Review_Analytics.py", label="⭐ Review Analytics")
        with c2:
            st.page_link("pages/0_Thu_vien_thanh_phan.py", label="🧩 Thư viện thành phần")

    st.markdown("---")
    st.markdown("#### 🛠️ Trải nghiệm thanh bộ lọc chuẩn:")
    filters = render_standard_filters(
        user_role=role,
        fixed_cinema_key=cinema_key,
        key_prefix="home_",
    )

    st.markdown("**Kết quả bộ lọc đang chọn:**")
    st.json(
        {
            "Cụm rạp": filters.get("cinema_name"),
            "Mã rạp (cinema_key)": filters.get("cinema_key"),
            "Khoảng ngày": filters.get("date_label"),
            "Từ ngày": str(filters.get("start_date")),
            "Đến ngày": str(filters.get("end_date")),
            "Nguồn": filters.get("source") or "Tất cả",
        }
    )


# =============================================================================
# 5. ENTRYPOINT CHÍNH CỦA APP
# =============================================================================

def main() -> None:
    setup_page("Cinema Analytics DW — Hệ Thống Phân Tích Rạp Phim", "🎬")
    init_session_state()

    if not is_authenticated():
        render_login_screen()
    else:
        user = get_current_user()
        render_home_page(user)


if __name__ == "__main__":
    main()


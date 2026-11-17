"""
app/ui/auth.py
--------------
Quản lý xác thực và phân quyền người dùng (Authentication & Authorization):
- Xác thực bằng bảng cinema_dw.app_user (mật khẩu băm bcrypt).
- Vai trò: STAFF (nhân viên rạp) và MANAGER (quản lý tổng).
- Kiểm tra quyền truy cập cho từng trang.
- Tự động hết phiên đăng nhập sau 8 giờ (Session Timeout).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Sequence
import bcrypt
import streamlit as st

SESSION_TIMEOUT_HOURS = 8


# =============================================================================
# 1. HÀM MÃ HÓA & KIỂM TRA MẬT KHẨU (BCRYPT)
# =============================================================================

def hash_password(plain_password: str) -> str:
    """Băm mật khẩu plain text bằng thuật toán bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Kiểm tra mật khẩu plain text so với hash bcrypt đã lưu."""
    if not plain_password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            password_hash.encode("utf-8")
        )
    except Exception:
        return False


# =============================================================================
# 2. TRUY VẤN VÀ XÁC THỰC NGƯỜI DÙNG (DATABASE QUERY & AUTH)
# =============================================================================

def _default_db_fetch_user(username: str) -> dict[str, Any] | None:
    """Truy vấn thông tin user từ bảng cinema_dw.app_user trong PostgreSQL."""
    try:
        from etl.db import get_engine
        from sqlalchemy import text

        engine = get_engine()
        with engine.connect() as conn:
            stmt = text("""
                SELECT user_id, username, password_hash, role, cinema_key, is_active
                FROM cinema_dw.app_user
                WHERE username = :username
                LIMIT 1;
            """)
            row = conn.execute(stmt, {"username": username.strip()}).fetchone()
            if row:
                return {
                    "user_id": str(row[0]),
                    "username": row[1],
                    "password_hash": row[2],
                    "role": row[3],
                    "cinema_key": row[4],
                    "is_active": bool(row[5]),
                }
    except Exception as e:
        st.error(f"Lỗi kết nối cơ sở dữ liệu xác thực: {e}")
    return None


def authenticate_user(
    username: str,
    password: str,
    fetch_user_fn: Callable[[str], dict[str, Any] | None] | None = None
) -> dict[str, Any] | None:
    """
    Xác thực người dùng:
    - Tìm kiếm tài khoản theo username.
    - Kiểm tra trạng thái is_active.
    - Đối chiếu mật khẩu băm bcrypt.
    - Hỗ trợ truyền fetch_user_fn để giả lập (mocking) khi viết Unit Test mà không cần DB.
    """
    u_clean = username.strip() if username else ""
    if not u_clean or not password:
        return None

    fetch_fn = fetch_user_fn or _default_db_fetch_user
    user_record = fetch_fn(u_clean)

    if not user_record:
        return None

    if not user_record.get("is_active", True):
        return None

    pw_hash = user_record.get("password_hash", "")
    if verify_password(password, pw_hash):
        return {
            "user_id": user_record.get("user_id"),
            "username": user_record.get("username"),
            "role": user_record.get("role", "STAFF"),
            "cinema_key": user_record.get("cinema_key"),
            "login_time": datetime.now(),
        }
    return None


# =============================================================================
# 3. QUẢN LÝ PHIÊN (SESSION MANAGEMENT & TIMEOUT 8 GIỜ)
# =============================================================================

def is_session_expired(login_time: datetime | None, timeout_hours: int = SESSION_TIMEOUT_HOURS) -> bool:
    """Kiểm tra thời lượng đăng nhập đã vượt quá số giờ quy định chưa."""
    if not login_time:
        return True
    return datetime.now() - login_time > timedelta(hours=timeout_hours)


def init_session_state() -> None:
    """Khởi tạo trạng thái phiên đăng nhập trong Streamlit nếu chưa có."""
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user" not in st.session_state:
        st.session_state.user = None
    if "login_time" not in st.session_state:
        st.session_state.login_time = None


def login_user(user: dict[str, Any]) -> None:
    """Thiết lập trạng thái đăng nhập cho phiên hiện tại."""
    init_session_state()
    st.session_state.logged_in = True
    st.session_state.user = user
    st.session_state.login_time = user.get("login_time") or datetime.now()


def logout_user() -> None:
    """Xóa bỏ thông tin phiên và đưa về trạng thái chưa đăng nhập."""
    init_session_state()
    st.session_state.logged_in = False
    st.session_state.user = None
    st.session_state.login_time = None


def is_authenticated() -> bool:
    """
    Kiểm tra xem người dùng hiện tại có đang đăng nhập hợp lệ hay không.
    Nếu phiên đã quá 8 giờ, tự động đăng xuất và trả về False.
    """
    init_session_state()
    if not st.session_state.logged_in or not st.session_state.user:
        return False

    login_t = st.session_state.get("login_time")
    if is_session_expired(login_t, SESSION_TIMEOUT_HOURS):
        logout_user()
        return False

    return True


def get_current_user() -> dict[str, Any] | None:
    """Lấy thông tin người dùng hiện tại (nếu đã đăng nhập)."""
    if is_authenticated():
        return st.session_state.get("user")
    return None


# =============================================================================
# 4. BẢO VỆ TRANG & KIỂM TRA PHÂN QUYỀN (PAGE PERMISSION GUARD)
# =============================================================================

def check_permission(
    required_role: str | Sequence[str] | None = None,
    auto_stop: bool = True
) -> bool:
    """
    Hàm kiểm tra quyền mỗi trang:
    - Nếu chưa đăng nhập: Yêu cầu đăng nhập.
    - Nếu vai trò không khớp: Báo lỗi quyền truy cập.
    - auto_stop=True: Tự động dừng thực thi trang Streamlit (st.stop()).
    """
    if not is_authenticated():
        if auto_stop:
            st.error("🔒 Bạn chưa đăng nhập. Vui lòng quay lại trang Đăng nhập chính để tiếp tục.")
            st.stop()
        return False

    current_user = get_current_user()
    user_role = current_user.get("role", "") if current_user else ""

    if required_role is not None:
        roles = [required_role] if isinstance(required_role, str) else list(required_role)
        if user_role not in roles:
            if auto_stop:
                st.error(
                    f"⛔ Bạn không có quyền truy cập trang này. "
                    f"Trang yêu cầu vai trò: **{', '.join(roles)}**, vai trò hiện tại của bạn là: **{user_role}**."
                )
                st.stop()
            return False

    return True


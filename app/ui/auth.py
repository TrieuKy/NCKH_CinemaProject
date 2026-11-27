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

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence
import bcrypt
import streamlit as st

logger = logging.getLogger(__name__)
SESSION_TIMEOUT_HOURS = 8
VALID_CINEMA_KEYS = {1, 2, 3}


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
    except Exception:
        logger.exception("Database error during user authentication")
        st.error("Lỗi kết nối cơ sở dữ liệu xác thực. Vui lòng thử lại sau.")
    return None


def is_demo_mode_enabled() -> bool:
    """Chỉ bật demo khi biến môi trường CINEMA_DEMO_MODE=1."""
    return os.environ.get("CINEMA_DEMO_MODE", "0").strip() == "1"


def load_external_demo_users() -> dict[str, dict[str, Any]]:
    """
    Đọc danh sách tài khoản demo từ cấu hình bên ngoài repository:
    - Biến môi trường CINEMA_DEMO_USERS_JSON (JSON dict), hoặc
    - File ngoài repo qua CINEMA_DEMO_CONFIG_PATH.
    Không lưu mật khẩu demo cố định trong source code.
    """
    if not is_demo_mode_enabled():
        return {}

    raw_json = os.environ.get("CINEMA_DEMO_USERS_JSON", "").strip()
    if not raw_json:
        cfg_path = os.environ.get("CINEMA_DEMO_CONFIG_PATH", "").strip()
        if cfg_path:
            p = Path(cfg_path)
            if p.is_file():
                try:
                    raw_json = p.read_text(encoding="utf-8")
                except Exception:
                    logger.exception("Failed to read external demo config file")
                    return {}

    if not raw_json:
        return {}

    try:
        data = json.loads(raw_json)
        if isinstance(data, dict):
            return data
    except Exception:
        logger.warning("Invalid external demo config JSON")
    return {}


def authenticate_demo_user(
    username: str,
    password: str,
    fetch_user_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """
    Xác thực tài khoản demo từ cấu hình bên ngoài khi CINEMA_DEMO_MODE=1.
    - KHÔNG tự động thay thế khi tài khoản thật tồn tại trong DB nhưng sai mật khẩu.
    - KHÔNG cho phép STAFF demo thiếu hoặc sai cinema_key.
    """
    if not is_demo_mode_enabled():
        return None
    u_clean = username.strip() if username else ""
    if not u_clean or not password:
        return None

    # Nếu tài khoản tồn tại trong PostgreSQL thật, tuyệt đối không fallback sang demo
    fetch_fn = fetch_user_fn or _default_db_fetch_user
    if fetch_fn(u_clean) is not None:
        return None

    demo_users = load_external_demo_users()
    rec = demo_users.get(u_clean)
    if not isinstance(rec, dict):
        return None

    pw_hash = rec.get("password_hash")
    plain_pw = rec.get("password")
    ok = False
    if pw_hash:
        ok = verify_password(password, str(pw_hash))
    elif plain_pw is not None:
        ok = str(plain_pw) == password
    if not ok:
        return None

    role = str(rec.get("role", "STAFF")).upper()
    cinema_key = rec.get("cinema_key")
    if role == "STAFF" and cinema_key not in VALID_CINEMA_KEYS:
        return None

    return {
        "user_id": str(rec.get("user_id", f"demo-{u_clean}")),
        "username": u_clean,
        "role": role,
        "cinema_key": cinema_key if role == "STAFF" else None,
        "is_demo": True,
        "login_time": datetime.now(),
    }


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


SENSITIVE_SESSION_KEYS = (
    "dss_decisions",
    "dss_config",
    "dss_error",
    "dss_owner",
)


def clear_sensitive_session_state() -> None:
    """Xóa state nghiệp vụ nhạy cảm khi đăng xuất hoặc chuyển phiên đăng nhập."""
    for key in SENSITIVE_SESSION_KEYS:
        if key in st.session_state:
            del st.session_state[key]
    for key in list(st.session_state.keys()):
        if isinstance(key, str) and key.startswith(("dss_", "rev_", "kpi_", "fc_")):
            del st.session_state[key]


def login_user(user: dict[str, Any]) -> None:
    """Thiết lập trạng thái đăng nhập cho phiên hiện tại (clear state phiên cũ)."""
    init_session_state()
    clear_sensitive_session_state()
    st.session_state.logged_in = True
    st.session_state.user = user
    st.session_state.login_time = user.get("login_time") or datetime.now()


def logout_user() -> None:
    """Xóa bỏ thông tin phiên và state nhạy cảm, đưa về trạng thái chưa đăng nhập."""
    init_session_state()
    clear_sensitive_session_state()
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


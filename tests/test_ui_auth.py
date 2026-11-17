"""
tests/test_ui_auth.py
---------------------
Unit test cho app/ui/auth.py (KHÔNG cần DB thật, giả lập truy vấn).
"""

from datetime import datetime, timedelta
from unittest.mock import patch
import pytest

from app.ui.auth import (
    hash_password,
    verify_password,
    authenticate_user,
    is_session_expired,
    SESSION_TIMEOUT_HOURS,
    check_permission,
)


class TestPasswordHashing:
    def test_hash_and_verify_correct_password(self):
        pwd = "CinemaSecurePassword@2026"
        hashed = hash_password(pwd)
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")
        assert verify_password(pwd, hashed) is True

    def test_verify_wrong_password(self):
        pwd = "correct_password"
        hashed = hash_password(pwd)
        assert verify_password("wrong_password", hashed) is False

    def test_verify_empty_or_none(self):
        assert verify_password("", "") is False
        assert verify_password("pwd", "") is False
        assert verify_password("", "hash") is False
        assert verify_password(None, None) is False


class TestAuthenticateUserMocked:
    @pytest.fixture
    def mock_users_db(self):
        """Giả lập bảng cinema_dw.app_user trong bộ nhớ."""
        staff_pwd = "staff_secret_123"
        mgr_pwd = "manager_secret_123"
        inactive_pwd = "inactive_pass"

        return {
            "staff_hutech": {
                "user_id": "11111111-1111-1111-1111-111111111111",
                "username": "staff_hutech",
                "password_hash": hash_password(staff_pwd),
                "role": "STAFF",
                "cinema_key": 1,
                "is_active": True,
            },
            "manager_tong": {
                "user_id": "22222222-2222-2222-2222-222222222222",
                "username": "manager_tong",
                "password_hash": hash_password(mgr_pwd),
                "role": "MANAGER",
                "cinema_key": None,
                "is_active": True,
            },
            "user_locked": {
                "user_id": "33333333-3333-3333-3333-333333333333",
                "username": "user_locked",
                "password_hash": hash_password(inactive_pwd),
                "role": "STAFF",
                "cinema_key": 2,
                "is_active": False,
            },
        }

    def test_staff_login_success(self, mock_users_db):
        fetch_fn = lambda u: mock_users_db.get(u)
        user = authenticate_user("staff_hutech", "staff_secret_123", fetch_user_fn=fetch_fn)

        assert user is not None
        assert user["username"] == "staff_hutech"
        assert user["role"] == "STAFF"
        assert user["cinema_key"] == 1
        assert isinstance(user["login_time"], datetime)

    def test_manager_login_success(self, mock_users_db):
        fetch_fn = lambda u: mock_users_db.get(u)
        user = authenticate_user("manager_tong", "manager_secret_123", fetch_user_fn=fetch_fn)

        assert user is not None
        assert user["username"] == "manager_tong"
        assert user["role"] == "MANAGER"
        assert user["cinema_key"] is None

    def test_wrong_password_returns_none(self, mock_users_db):
        fetch_fn = lambda u: mock_users_db.get(u)
        user = authenticate_user("staff_hutech", "wrong_pass_here", fetch_user_fn=fetch_fn)
        assert user is None

    def test_nonexistent_user_returns_none(self, mock_users_db):
        fetch_fn = lambda u: mock_users_db.get(u)
        user = authenticate_user("unknown_user", "any_password", fetch_user_fn=fetch_fn)
        assert user is None

    def test_inactive_user_rejected(self, mock_users_db):
        fetch_fn = lambda u: mock_users_db.get(u)
        user = authenticate_user("user_locked", "inactive_pass", fetch_user_fn=fetch_fn)
        assert user is None

    def test_username_with_whitespace(self, mock_users_db):
        fetch_fn = lambda u: mock_users_db.get(u)
        user = authenticate_user("  staff_hutech  ", "staff_secret_123", fetch_user_fn=fetch_fn)
        assert user is not None
        assert user["username"] == "staff_hutech"


class TestSessionTimeout:
    def test_session_not_expired_within_8_hours(self):
        login_time = datetime.now() - timedelta(hours=4)
        assert is_session_expired(login_time, timeout_hours=8) is False

        # 7 giờ 59 phút
        login_time_edge = datetime.now() - timedelta(hours=7, minutes=59)
        assert is_session_expired(login_time_edge, timeout_hours=8) is False

    def test_session_expired_after_8_hours(self):
        # 8 giờ 1 phút
        login_time = datetime.now() - timedelta(hours=8, minutes=1)
        assert is_session_expired(login_time, timeout_hours=8) is True

        # 24 giờ trước
        login_time_old = datetime.now() - timedelta(days=1)
        assert is_session_expired(login_time_old, timeout_hours=8) is True

    def test_session_expired_with_none(self):
        assert is_session_expired(None) is True


class TestCheckPermissionLogic:
    def test_permission_when_not_authenticated(self):
        with patch("app.ui.auth.is_authenticated", return_value=False):
            assert check_permission("MANAGER", auto_stop=False) is False
            assert check_permission("STAFF", auto_stop=False) is False

    def test_permission_matching_role(self):
        mock_user = {"username": "admin", "role": "MANAGER"}
        with patch("app.ui.auth.is_authenticated", return_value=True), \
             patch("app.ui.auth.get_current_user", return_value=mock_user):
            assert check_permission("MANAGER", auto_stop=False) is True
            assert check_permission(["STAFF", "MANAGER"], auto_stop=False) is True

    def test_permission_mismatched_role(self):
        mock_user = {"username": "staff1", "role": "STAFF"}
        with patch("app.ui.auth.is_authenticated", return_value=True), \
             patch("app.ui.auth.get_current_user", return_value=mock_user):
            assert check_permission("MANAGER", auto_stop=False) is False
            assert check_permission("STAFF", auto_stop=False) is True


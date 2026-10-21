"""
etl/db.py
---------
Tạo SQLAlchemy engine từ file .env và cấu hình logging hai kênh:
  - Console: INFO trở lên
  - File: logs/etl.log (INFO trở lên, append, UTF-8)

Lý do dùng SQLAlchemy engine (không dùng psycopg2 trực tiếp):
  - Cho phép viết SQL set-based với text() mà không cần viết cursor thủ công.
  - Dễ tái sử dụng, tránh lặp boilerplate kết nối ở mỗi script ETL.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

# ---------------------------------------------------------------------------
# Đường dẫn gốc dự án (2 cấp trên etl/db.py)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Cấu hình logging — gọi một lần ở import
# ---------------------------------------------------------------------------
def _setup_logging() -> logging.Logger:
    """Cấu hình logger 'etl' ghi ra console và file logs/etl.log."""
    logger = logging.getLogger("etl")
    if logger.handlers:           # tránh thêm handler trùng khi import lại
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler 1: Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # Handler 2: File (append, UTF-8; KHÔNG in mật khẩu — xem get_engine)
    fh = logging.FileHandler(LOG_DIR / "etl.log", mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


logger = _setup_logging()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def get_engine(echo: bool = False) -> Engine:
    """
    Đọc biến môi trường từ .env, trả về SQLAlchemy Engine.

    KHÔNG in mật khẩu ra log — chỉ log host:port/dbname.
    Gọi echo=True ở môi trường dev để xem SQL sinh ra.
    """
    load_dotenv(ROOT / ".env", override=False)

    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "cinema_dw")
    user = os.getenv("DB_USER", "postgres")
    pwd  = os.getenv("DB_PASSWORD", "")

    if not pwd:
        logger.warning("DB_PASSWORD chưa được đặt trong .env — kết nối có thể thất bại.")

    # Dùng URL.create() thay f-string để sqlalchemy tự percent-encode mật khẩu.
    # Tránh lỗi nếu password chứa ký tự đặc biệt: @, /, #, ?, %, v.v.
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=user,
        password=pwd,        # URL.create tự xử lý encoding — không bao giờ xuất hiện trong log
        host=host,
        port=int(port),
        database=name,
    )

    # Chỉ log thông tin không nhạy cảm (URL.render_as_string(hide_password=True))
    logger.info("Khởi tạo engine: %s", url.render_as_string(hide_password=True))

    engine = create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,   # kiểm tra kết nối trước khi dùng
        future=True,
    )
    return engine


def test_connection(engine: Engine) -> bool:
    """Kiểm tra kết nối nhanh. Trả True nếu OK, False nếu lỗi."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT current_database(), version()")).fetchone()
        logger.info("Kết nối thành công: DB=%s | %s", row[0], row[1][:60])
        return True
    except Exception as exc:
        logger.error("Kết nối thất bại: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Chạy trực tiếp để kiểm tra nhanh: python etl/db.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    eng = get_engine()
    ok = test_connection(eng)
    sys.exit(0 if ok else 1)

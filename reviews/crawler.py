"""
reviews/crawler.py
------------------
Cào dữ liệu đánh giá từ Google Maps thông qua Apify API Client.
Actor: compass/google-maps-reviews-scraper

Quy định bắt buộc:
  - Chỉ đọc token từ biến môi trường APIFY_TOKEN trong .env.
  - Đọc google_place_id từ bảng review_source (bỏ qua nếu is_active = FALSE).
  - So khớp địa điểm: tên/địa chỉ từ Apify phải chứa "cgv" và mọi từ khóa trong expected_keywords.
  - Dữ liệu thô chỉ giữ các trường cần thiết: review_id, rating (NULL nếu thiếu),
    review_text, published_at (bỏ nếu thiếu), likes_count, apify_lang.
  - Tuyệt đối KHÔNG lưu author_name, ảnh hay URL hồ sơ người dùng.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

try:
    from apify_client import ApifyClient
except ImportError:
    ApifyClient = None

logger = logging.getLogger("reviews.crawler")

ACTOR_ID = "compass/google-maps-reviews-scraper"
MAX_REVIEWS_HARD_LIMIT = 3000


# ---------------------------------------------------------------------------
# Tiện ích chuẩn hóa tiếng Việt không dấu
# ---------------------------------------------------------------------------

def remove_accents(s: Optional[str]) -> str:
    """Loại bỏ dấu tiếng Việt, chuyển về chữ thường, thay đ/Đ thành d/D."""
    if not s:
        return ""
    nfd = unicodedata.normalize("NFD", str(s))
    cleaned = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    cleaned = cleaned.replace("đ", "d").replace("Đ", "D")
    return cleaned.lower().strip()


def verify_place_identity(
    place_text: str,
    expected_keywords: List[str],
) -> Tuple[bool, str]:
    """
    So khớp địa điểm từ dữ liệu Apify trả về:
      - Phải chứa thương hiệu 'cgv'
      - VÀ chứa tất cả các từ trong expected_keywords (chuẩn hóa không dấu, chữ thường)
    """
    norm = remove_accents(place_text)
    if "cgv" not in norm:
        return False, f"Địa điểm không chứa thương hiệu 'cgv': '{place_text}'"

    for kw in expected_keywords:
        norm_kw = remove_accents(kw)
        if norm_kw not in norm:
            return False, f"Địa điểm thiếu từ khóa rạp '{kw}': '{place_text}'"

    return True, "OK"


# ---------------------------------------------------------------------------
# Đọc thông tin cấu hình rạp từ review_source
# ---------------------------------------------------------------------------

def get_cinema_crawl_config(engine: Engine, cinema_key_or_id: Any) -> Optional[Dict[str, Any]]:
    """
    Lấy thông tin rạp và cấu hình cào từ review_source + dim_cinema.
    cinema_key_or_id: có thể là integer cinema_key hoặc string cinema_id (ví dụ 'CN01').
    Trả None nếu không tìm thấy.
    """
    sql = text("""
        SELECT
            c.cinema_key,
            c.cinema_id,
            c.cinema_name,
            c.district,
            rs.google_place_id,
            rs.maps_url,
            rs.is_active,
            rs.expected_keywords
        FROM cinema_dw.dim_cinema c
        LEFT JOIN cinema_dw.review_source rs ON c.cinema_key = rs.cinema_key
        WHERE c.cinema_key::TEXT = :key_or_id OR c.cinema_id = :key_or_id
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"key_or_id": str(cinema_key_or_id)}).fetchone()

    if not row:
        return None

    return {
        "cinema_key":        row[0],
        "cinema_id":         row[1],
        "cinema_name":       row[2],
        "district":          row[3],
        "google_place_id":   row[4],
        "maps_url":          row[5],
        "is_active":         bool(row[6]) if row[6] is not None else False,
        "expected_keywords": list(row[7]) if row[7] is not None else [],
    }


# ---------------------------------------------------------------------------
# Hàm cào chính
# ---------------------------------------------------------------------------

def fetch_reviews_apify(
    cinema_config: Dict[str, Any],
    max_reviews: int = 100,
    apify_client: Optional[Any] = None,
    timeout_secs: int = 180,
) -> Dict[str, Any]:
    """
    Gọi Apify Actor để cào đánh giá cho một rạp.

    Tham số:
      - cinema_config: dict lấy từ get_cinema_crawl_config
      - max_reviews: số lượng đánh giá tối đa (tối đa 3000)
      - apify_client: instance ApifyClient (hoặc Mock client trong unit test)
      - timeout_secs: thời gian chờ tối đa

    Trả dict chứa:
      - status: 'SUCCESS' | 'SKIPPED_INACTIVE'
      - cinema_key, cinema_id
      - place_title, place_address
      - actor_name, apify_run_id, dataset_id
      - reviews: danh sách dict đã chuẩn hóa (chỉ giữ trường cần thiết)
    """
    cinema_id = cinema_config["cinema_id"]
    cinema_key = cinema_config["cinema_key"]

    # 1. Bỏ qua nếu rạp chưa kích hoạt
    if not cinema_config.get("is_active"):
        logger.warning("Rạp %s (key=%d) có is_active=FALSE -> Bỏ qua cào.", cinema_id, cinema_key)
        return {
            "status": "SKIPPED_INACTIVE",
            "cinema_key": cinema_key,
            "cinema_id": cinema_id,
            "reviews": [],
            "message": f"Rạp {cinema_id} chưa được kích hoạt cào (is_active=FALSE)",
        }

    place_id = cinema_config.get("google_place_id")
    if not place_id:
        raise ValueError(f"Rạp {cinema_id} chưa cấu hình google_place_id trong review_source.")

    # 2. Khởi tạo client (chỉ đọc APIFY_TOKEN trong .env)
    if apify_client is None:
        load_dotenv(override=False)
        token = os.getenv("APIFY_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "Chưa cấu hình APIFY_TOKEN. Vui lòng thiết lập biến môi trường APIFY_TOKEN trong file .env."
            )
        if ApifyClient is None:
            raise ImportError("Chưa cài đặt thư viện apify-client. Chạy: pip install apify-client")
        client = ApifyClient(token)
    else:
        client = apify_client

    # Giới hạn trần tối đa
    limit = min(int(max_reviews), MAX_REVIEWS_HARD_LIMIT)

    run_input = {
        "placeIds": [place_id],
        "maxReviews": limit,
        "language": "vi",
        "reviewsSort": "newest",
        "personalData": False,
    }
    if cinema_config.get("maps_url"):
        run_input["startUrls"] = [{"url": cinema_config["maps_url"]}]

    logger.info("Gọi Apify Actor %s cho rạp %s (max=%d)...", ACTOR_ID, cinema_id, limit)
    run = client.actor(ACTOR_ID).call(run_input=run_input, timeout_secs=timeout_secs)

    if not run:
        raise RuntimeError("Apify Actor không trả về kết quả.")

    dataset_id = getattr(run, "default_dataset_id", None) or run.get("defaultDatasetId") or run.get("default_dataset_id")
    run_id = getattr(run, "id", None) or run.get("id")

    if not dataset_id:
        raise RuntimeError(f"Không tìm thấy dataset_id từ Apify run: {run}")

    # 3. Lấy dữ liệu từ dataset
    items_page = client.dataset(dataset_id).list_items()
    raw_items = items_page.items if hasattr(items_page, "items") else list(items_page)

    logger.info("Apify trả về %d bản ghi cho rạp %s.", len(raw_items), cinema_id)

    # 4. Kiểm tra đối soát địa điểm
    place_title = ""
    place_address = ""
    for it in raw_items:
        place_title = it.get("title") or it.get("placeTitle") or place_title
        place_address = it.get("address") or it.get("street") or place_address
        if place_title:
            break

    combined_place = f"{place_title} {place_address}".strip()
    expected_kws = cinema_config.get("expected_keywords") or []

    # Nếu có dữ liệu địa điểm trả về, bắt buộc đối soát
    if combined_place and expected_kws:
        is_valid, reason = verify_place_identity(combined_place, expected_kws)
        if not is_valid:
            err_msg = (
                f"ĐỐI SOÁT ĐỊA ĐIỂM THẤT BẠI: Rạp {cinema_id} mong đợi {expected_kws}, "
                f"nhưng Apify trả về: '{combined_place}'. Lý do: {reason}"
            )
            logger.error(err_msg)
            raise ValueError(err_msg)

    # 5. Làm sạch và chuẩn hóa trường (chỉ giữ trường cần thiết, loại bỏ thông tin cá nhân)
    sanitized_reviews: List[Dict[str, Any]] = []
    for it in raw_items:
        rid = it.get("reviewId") or it.get("id")
        pub_at = it.get("publishedAtDate") or it.get("publishAt") or it.get("publishedAt")

        # Bỏ dòng nếu thiếu review_id hoặc thiếu ngày đăng
        if not rid or not pub_at:
            continue

        raw_stars = it.get("stars") if it.get("stars") is not None else it.get("rating")
        try:
            rating_val = float(raw_stars) if raw_stars is not None else None
            if rating_val is not None and not (1.0 <= rating_val <= 5.0):
                rating_val = None
        except (ValueError, TypeError):
            rating_val = None

        text_content = it.get("text") or it.get("reviewText") or it.get("textTranslated")
        text_str = str(text_content).strip() if text_content is not None else None

        # Bỏ dòng nếu thiếu cả sao lẫn chữ
        if rating_val is None and not text_str:
            continue

        likes_val = it.get("likesCount") if it.get("likesCount") is not None else it.get("likes", 0)
        try:
            likes = int(likes_val)
        except (ValueError, TypeError):
            likes = 0

        lang = it.get("language") or it.get("originalLanguage")

        sanitized_reviews.append({
            "review_id":    str(rid),
            "rating":       rating_val,
            "review_text":  text_str,
            "published_at": str(pub_at),
            "likes_count":  likes,
            "apify_lang":   str(lang) if lang else None,
        })

    return {
        "status":        "SUCCESS",
        "cinema_key":    cinema_key,
        "cinema_id":     cinema_id,
        "place_title":   place_title,
        "place_address": place_address,
        "actor_name":    ACTOR_ID,
        "apify_run_id":  str(run_id) if run_id else None,
        "dataset_id":    str(dataset_id),
        "reviews":       sanitized_reviews,
    }

"""
tests/test_reviews_crawler.py
-----------------------------
Unit tests cho reviews/crawler.py: giả lập (mock) ApifyClient hoàn toàn,
KHÔNG kết nối mạng và không tốn phí Apify.
"""

from unittest.mock import MagicMock
import pytest

from reviews.crawler import (
    fetch_reviews_apify,
    remove_accents,
    verify_place_identity,
)


def _create_mock_apify_client(items: list, run_id: str = "run_123", dataset_id: str = "ds_456"):
    """Tạo mock ApifyClient trả về danh sách items định sẵn."""
    mock_client = MagicMock()
    mock_actor = MagicMock()
    mock_run = MagicMock()
    mock_run.id = run_id
    mock_run.default_dataset_id = dataset_id
    mock_actor.call.return_value = mock_run

    mock_client.actor.return_value = mock_actor

    mock_dataset = MagicMock()
    mock_dataset_items = MagicMock()
    mock_dataset_items.items = items
    mock_dataset.list_items.return_value = mock_dataset_items

    mock_client.dataset.return_value = mock_dataset
    return mock_client


def test_remove_accents():
    assert remove_accents("CGV Vincom Center Landmark 81") == "cgv vincom center landmark 81"
    assert remove_accents("Đồng Khởi, Quận 1") == "dong khoi, quan 1"
    assert remove_accents("") == ""
    assert remove_accents(None) == ""


def test_verify_place_identity_success():
    place_text = "CGV Vincom Center Landmark 81, 720A Điện Biên Phủ, Phường 22, Bình Thạnh"
    keywords = ["landmark", "81"]
    is_valid, reason = verify_place_identity(place_text, keywords)
    assert is_valid is True
    assert reason == "OK"


def test_verify_place_identity_missing_cgv():
    place_text = "BHD Star Cineplex Landmark 81"
    keywords = ["landmark", "81"]
    is_valid, reason = verify_place_identity(place_text, keywords)
    assert is_valid is False
    assert "cgv" in reason


def test_verify_place_identity_missing_keyword():
    place_text = "CGV Vincom Center Đồng Khởi"
    keywords = ["landmark", "81"]
    is_valid, reason = verify_place_identity(place_text, keywords)
    assert is_valid is False
    assert "landmark" in reason


def test_crawler_skipped_when_inactive():
    # Rạp có is_active = FALSE -> Không gọi ApifyClient, trả SKIPPED_INACTIVE
    config = {
        "cinema_key": 2,
        "cinema_id": "CN02",
        "cinema_name": "CGV Vincom Đồng Khởi",
        "google_place_id": "place_123",
        "is_active": False,
        "expected_keywords": ["dong khoi"],
    }
    mock_client = MagicMock()
    res = fetch_reviews_apify(config, max_reviews=10, apify_client=mock_client)

    assert res["status"] == "SKIPPED_INACTIVE"
    assert res["reviews"] == []
    mock_client.actor.assert_not_called()


def test_crawler_match_place_success():
    config = {
        "cinema_key": 1,
        "cinema_id": "CN01",
        "cinema_name": "CGV Vincom Landmark 81",
        "google_place_id": "ChIJn5Uzw-8pdTERyxTk1eLXCjc",
        "is_active": True,
        "expected_keywords": ["landmark", "81"],
    }

    mock_items = [
        {
            "title": "CGV Vincom Center Landmark 81",
            "address": "208 Nguyễn Hữu Cảnh, TP.HCM",
            "reviewId": "rev_01",
            "stars": 5.0,
            "text": "Rạp rất đẹp, phòng chiếu âm thanh tuyệt vời",
            "publishedAtDate": "2026-10-16T04:02:06.174Z",
            "likesCount": 3,
            "language": "vi",
        }
    ]
    client = _create_mock_apify_client(mock_items)

    res = fetch_reviews_apify(config, max_reviews=10, apify_client=client)
    assert res["status"] == "SUCCESS"
    assert len(res["reviews"]) == 1
    rev = res["reviews"][0]
    assert rev["review_id"] == "rev_01"
    assert rev["rating"] == 5.0
    assert rev["published_at"] == "2026-10-16T04:02:06.174Z"
    assert rev["likes_count"] == 3
    assert rev["apify_lang"] == "vi"


def test_crawler_wrong_place_stops_and_raises():
    config = {
        "cinema_key": 1,
        "cinema_id": "CN01",
        "cinema_name": "CGV Vincom Landmark 81",
        "google_place_id": "ChIJn5Uzw-8pdTERyxTk1eLXCjc",
        "is_active": True,
        "expected_keywords": ["landmark", "81"],
    }

    # Apify trả về địa chỉ rạp khác (ví dụ Lotte hoặc CGV Đồng Khởi)
    mock_items = [
        {
            "title": "CGV Vincom Đồng Khởi",
            "address": "72 Lê Thánh Tôn, Bến Nghé, Quận 1",
            "reviewId": "rev_02",
            "stars": 4.0,
            "publishedAtDate": "2026-10-15T00:00:00Z",
        }
    ]
    client = _create_mock_apify_client(mock_items)

    with pytest.raises(ValueError) as exc_info:
        fetch_reviews_apify(config, max_reviews=10, apify_client=client)

    assert "ĐỐI SOÁT ĐỊA ĐIỂM THẤT BẠI" in str(exc_info.value)


def test_crawler_missing_rating_is_null():
    config = {
        "cinema_key": 1,
        "cinema_id": "CN01",
        "google_place_id": "place_ok",
        "is_active": True,
        "expected_keywords": ["landmark", "81"],
    }
    # Item không có số sao (stars=None) nhưng có văn bản
    mock_items = [
        {
            "title": "CGV Landmark 81",
            "reviewId": "rev_null_stars",
            "stars": None,
            "text": "Có văn bản nhưng không có số sao",
            "publishedAtDate": "2026-10-16T04:02:06.174Z",
        }
    ]
    client = _create_mock_apify_client(mock_items)
    res = fetch_reviews_apify(config, max_reviews=10, apify_client=client)
    assert len(res["reviews"]) == 1
    assert res["reviews"][0]["rating"] is None  # Bắt buộc là None, không gán 5.0


def test_crawler_missing_date_is_skipped():
    config = {
        "cinema_key": 1,
        "cinema_id": "CN01",
        "google_place_id": "place_ok",
        "is_active": True,
        "expected_keywords": ["landmark", "81"],
    }
    mock_items = [
        {
            "title": "CGV Landmark 81",
            "reviewId": "rev_missing_date",
            "stars": 5.0,
            "text": "Có sao có chữ nhưng thiếu ngày đăng",
            "publishedAtDate": None,
            "publishAt": None,
        }
    ]
    client = _create_mock_apify_client(mock_items)
    res = fetch_reviews_apify(config, max_reviews=10, apify_client=client)
    # Dòng thiếu ngày bị bỏ qua
    assert len(res["reviews"]) == 0


def test_crawler_no_personal_data_retained():
    config = {
        "cinema_key": 1,
        "cinema_id": "CN01",
        "google_place_id": "place_ok",
        "is_active": True,
        "expected_keywords": ["landmark", "81"],
    }
    # Item chứa nhiều trường thông tin cá nhân của người đánh giá
    mock_items = [
        {
            "title": "CGV Landmark 81",
            "reviewId": "rev_privacy_test",
            "name": "Nguyễn Văn A",
            "authorName": "Nguyen Van A",
            "authorId": "1122334455",
            "authorUrl": "https://google.com/maps/contrib/123",
            "profilePhotoUrl": "https://lh3.googleusercontent.com/photo.jpg",
            "reviewerNumberOfReviews": 42,
            "isLocalGuide": True,
            "stars": 5.0,
            "text": "Bảo mật thông tin",
            "publishedAtDate": "2026-10-16T00:00:00Z",
        }
    ]
    client = _create_mock_apify_client(mock_items)
    res = fetch_reviews_apify(config, max_reviews=10, apify_client=client)
    assert len(res["reviews"]) == 1
    rev = res["reviews"][0]

    # Kiểm tra chỉ có đúng các trường cho phép
    allowed_fields = {"review_id", "rating", "review_text", "published_at", "likes_count", "apify_lang"}
    assert set(rev.keys()) == allowed_fields

    # Tuyệt đối không chứa bất kỳ trường cá nhân nào
    forbidden_keys = {"name", "authorName", "authorId", "authorUrl", "profilePhotoUrl", "reviewerNumberOfReviews", "isLocalGuide"}
    for k in forbidden_keys:
        assert k not in rev

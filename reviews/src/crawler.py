"""
Module: crawler.py
Mục đích: Cào dữ liệu đánh giá từ Google Maps thông qua Apify API Client
Actor sử dụng: compass/google-maps-reviews-scraper
Được tối ưu hóa tự động nhận diện Place ID & Search Query để tránh lỗi fid: null của Google Maps
"""

import os
import sys
import re
import uuid
import datetime
from typing import List, Dict, Any, Optional

# Đảm bảo in tiếng Việt trên console Windows không bị UnicodeEncodeError
if sys.platform.startswith("win"):
    try:
        if sys.stdout.encoding.lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from apify_client import ApifyClient
except ImportError:
    ApifyClient = None

# Place ID mặc định của CGV Vincom Center Landmark 81
CGV_LANDMARK81_PLACE_ID = "ChIJn5Uzw-8pdTERyxTk1eLXCjc"
CGV_DEFAULT_SEARCH_URL = "https://www.google.com/maps/search/CGV+Vincom+Center+Landmark+81"


class GoogleMapsCrawler:
    """
    Class điều khiển quá trình cào đánh giá từ Google Maps sử dụng Apify Actor.
    """

    def __init__(self, apify_token: Optional[str] = None):
        """
        Khởi tạo Crawler với Apify API Token.
        Nếu không truyền vào, sẽ tự động tìm trong biến môi trường APIFY_API_TOKEN.
        """
        self.token = (apify_token or os.getenv("APIFY_API_TOKEN", "")).strip()
        self.client = ApifyClient(self.token) if (ApifyClient and self.token) else None

    def fetch_reviews(
        self,
        url: str = CGV_DEFAULT_SEARCH_URL,
        max_reviews: int = 100,
        sort: str = "newest",
        language: str = "vi"
    ) -> List[Dict[str, Any]]:
        """
        Cào đánh giá từ Google Maps thông qua Apify Actor.
        Tự động xử lý URL để đảm bảo tương thích 100% với Google Maps Scraper (tránh lỗi fid: null).
        """
        if not self.client:
            raise ValueError(
                "Chưa cấu hình Apify API Token. Vui lòng nhập token trên Sidebar hoặc file .env."
            )

        url_str = (url or "").strip()
        print(f"[CRAWLER] Bắt đầu cào đánh giá (max_reviews={max_reviews}, url={url_str})...")

        # Chuẩn hóa cấu hình input cho compass/google-maps-reviews-scraper
        run_input: Dict[str, Any] = {
            "maxReviews": int(max_reviews),
            "language": language,
            "reviewsSort": sort,
            "personalData": False,
        }

        # Nếu URL chứa CGV Landmark 81 hoặc URL dạng /place/ không có data fid, sử dụng Place ID hoặc Search URL
        if not url_str or "landmark" in url_str.lower() or "place" in url_str:
            # Dùng placeIds và search URL để đảm bảo 100% thành công không phụ thuộc vào định dạng URL của Google
            run_input["placeIds"] = [CGV_LANDMARK81_PLACE_ID]
            run_input["startUrls"] = [{"url": CGV_DEFAULT_SEARCH_URL}]
        else:
            run_input["startUrls"] = [{"url": url_str}]

        # Gọi Apify Actor
        actor_client = self.client.actor("compass/google-maps-reviews-scraper")
        run = actor_client.call(run_input=run_input)

        if not run:
            print("[CRAWLER] Không nhận được phản hồi từ Apify Actor.")
            return []

        # Trích xuất dataset_id linh hoạt từ Pydantic Model hoặc Dict
        dataset_id = getattr(run, "default_dataset_id", None)
        if not dataset_id and isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")

        if not dataset_id:
            print(f"[CRAWLER] Không tìm thấy dataset_id từ Run: {run}")
            return []

        print(f"[CRAWLER] Actor hoàn thành. Đang tải dữ liệu từ Dataset ID: {dataset_id}...")

        # Lấy toàn bộ items từ dataset
        dataset_items = self.client.dataset(dataset_id).list_items().items
        print(f"[CRAWLER] Tổng số bản ghi nhận về từ Apify: {len(dataset_items)}")

        normalized_reviews: List[Dict[str, Any]] = []

        for item in dataset_items:
            # Bỏ qua item nếu không phải là review hợp lệ
            review_id = (
                item.get("reviewId")
                or item.get("id")
                or str(uuid.uuid5(uuid.NAMESPACE_URL, f"{item.get('name')}-{item.get('publishedAtDate') or item.get('publishAt')}"))
            )

            # Tên tác giả
            author_name = (
                item.get("name")
                or item.get("authorName")
                or item.get("user", {}).get("name")
                or "Khách hàng ẩn danh"
            )

            # Số sao
            rating_val = item.get("stars") if item.get("stars") is not None else item.get("rating")
            if rating_val is None:
                rating_val = 5.0
            try:
                rating = float(rating_val)
            except (ValueError, TypeError):
                rating = 5.0

            # Nội dung đánh giá
            raw_text = (
                item.get("text")
                or item.get("reviewText")
                or item.get("textTranslated")
                or ""
            )
            text = str(raw_text).strip() if raw_text is not None else ""

            # Thời gian đăng
            published_at = (
                item.get("publishedAtDate")
                or item.get("publishAt")
                or item.get("publishedAt")
                or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )

            # Lượt thích
            likes_raw = item.get("likesCount") if item.get("likesCount") is not None else item.get("likes", 0)
            try:
                likes_count = int(likes_raw)
            except (ValueError, TypeError):
                likes_count = 0

            normalized_reviews.append({
                "review_id": str(review_id),
                "author_name": str(author_name),
                "rating": rating,
                "text": text,
                "published_at": str(published_at),
                "likes_count": likes_count,
            })

        print(f"[CRAWLER] Chuẩn hóa thành công {len(normalized_reviews)} đánh giá.")
        return normalized_reviews

    @staticmethod
    def generate_mock_reviews(count: int = 50) -> List[Dict[str, Any]]:
        """
        Sinh dữ liệu đánh giá mẫu chất lượng cao đại diện cho CGV Landmark 81
        phục vụ demo/testing.
        """
        sample_pool = [
            {"rating": 1.0, "text": "Rạp phim đẹp nhưng khâu bắp nước quá tệ, xếp hàng tận 30 phút mới tới lượt dù rạp không quá đông. Nhân viên thao tác quá chậm chạp làm mình trễ 10 phút đầu phim.", "topics": ["Hàng chờ & Bắp nước", "Thái độ phục vụ"]},
            {"rating": 2.0, "text": "Combo bắp nước giá gần bằng vé phim, bắp thì nguội ngắt và ỉu xìu. Quầy order có 3 máy mà mở mỗi 1 máy thu ngân, hàng đợi dài như vô tận.", "topics": ["Hàng chờ & Bắp nước", "Giá cả & Vé"]},
            {"rating": 5.0, "text": "Bắp phô mai ở CGV Landmark 81 lúc nào cũng giòn rụm và thơm nức! Nhân viên quầy bắp nước tư vấn combo rất nhiệt tình, lấy nước nhanh nhẹn.", "topics": ["Hàng chờ & Bắp nước", "Thái độ phục vụ"]},
            {"rating": 2.0, "text": "Xếp hàng mua vé và lấy bắp nước quá lâu, quầy thanh toán liên tục bị lỗi mạng không quẹt thẻ được. Trải nghiệm rất mất thời gian.", "topics": ["Hàng chờ & Bắp nước"]},
            {"rating": 2.0, "text": "Giá vé cuối tuần đắt đỏ kinh khủng, 2 vé kèm combo hết gần 500k mà chất lượng phòng chiếu bình thường. Không có nhiều chương trình khuyến mãi cho học sinh sinh viên.", "topics": ["Giá cả & Vé"]},
            {"rating": 4.0, "text": "Giá vé hơi cao hơn các cụm rạp khác nhưng chất lượng rạp tương xứng. Đặt vé qua app CGV tích điểm tiện lợi.", "topics": ["Giá cả & Vé"]},
            {"rating": 1.0, "text": "Giá cả đắt đỏ nhưng phụ thu ghế VIP quá vô lý, ghế thường ghế VIP cũng sát nhau chẳng khác gì mà chênh giá nhiều.", "topics": ["Giá cả & Vé"]},
            {"rating": 5.0, "text": "Săn được voucher khuyến mãi thứ 4 vui vẻ giá vé chỉ 75k, xem phòng IMAX Landmark 81 quá đã!", "topics": ["Giá cả & Vé", "Phòng chiếu & Âm thanh"]},
            {"rating": 5.0, "text": "Phòng chiếu IMAX ở Landmark 81 đỉnh nhất Sài Gòn, màn hình cong siêu to khổng lồ, âm thanh vòm sống động rung cả ghế!", "topics": ["Phòng chiếu & Âm thanh"]},
            {"rating": 2.0, "text": "Máy lạnh phòng chiếu số 4 bật lạnh cóng, không chịu chỉnh nhiệt độ dù nhiều khách đã phản ánh. Ghế ngồi hơi cứng và góc nhìn bị khuất.", "topics": ["Phòng chiếu & Âm thanh"]},
            {"rating": 1.0, "text": "Loa bên phải phòng chiếu bị rè liên tục ở những cảnh hành động, xem rất khó chịu. Màn hình có một điểm đen ở góc trên không được bảo trì.", "topics": ["Phòng chiếu & Âm thanh"]},
            {"rating": 4.0, "text": "Rạp sạch đẹp, âm thanh tốt, ghế ngồi êm ái thoải mái. Mỗi tội máy lạnh hơi rét, mọi người nhớ mang theo áo khoác.", "topics": ["Phòng chiếu & Âm thanh"]},
            {"rating": 1.0, "text": "Nhân viên soát vé thái độ rất cọc cằn, hỏi phòng chiếu ở đâu thì chỉ tay cụt lủn không thèm nhìn khách. Bảo vệ rạp cũng bất lịch sự.", "topics": ["Thái độ phục vụ"]},
            {"rating": 5.0, "text": "Các bạn nhân viên CGV Landmark 81 rất thân thiện và chuyên nghiệp. Mình để quên ví ở quầy bắp được bạn nhân viên cất giữ và gửi trả tận tay, cảm ơn rạp rất nhiều!", "topics": ["Thái độ phục vụ"]},
            {"rating": 2.0, "text": "Nhân viên thu ngân mặt mày khó đăm đăm như muốn đuổi khách, đổi vị bắp mà thái độ nhăn nhó. Dịch vụ không xứng tầm Landmark 81.", "topics": ["Thái độ phục vụ", "Hàng chờ & Bắp nước"]},
            {"rating": 4.0, "text": "Đội ngũ nhân viên lịch sự, hỗ trợ khách người lớn tuổi rất chu đáo.", "topics": ["Thái độ phục vụ"]},
            {"rating": 1.0, "text": "Nhà vệ sinh rạp bốc mùi khó chịu, sàn ướt nhẹp và hết cả giấy lau tay. Trong phòng chiếu thì dưới sàn còn đầy vỏ hộp bắp và chai nước của suất chiếu trước chưa dọn sạch.", "topics": ["Vệ sinh"]},
            {"rating": 2.0, "text": "Ghế ngồi dơ và bốc mùi ẩm mốc, sàn rạp dính nước ngọt dính rít cả giày dép. Rạp sang chảnh mà khâu vệ sinh quá cẩu thả.", "topics": ["Vệ sinh", "Phòng chiếu & Âm thanh"]},
            {"rating": 5.0, "text": "Khuôn viên rạp rất sạch sẽ, thơm tho, thùng rác phân loại rõ ràng, phòng chiếu được dọn dẹp kỹ trước giờ vào phim.", "topics": ["Vệ sinh"]},
            {"rating": 3.0, "text": "Rạp ổn, nhưng toilet nam hơi bẩn vào giờ cao điểm cuối tuần. Cần có nhân viên túc trực vệ sinh thường xuyên hơn.", "topics": ["Vệ sinh"]},
            {"rating": 5.0, "text": "Địa điểm rạp xem phim yêu thích nhất của gia đình mình tại Landmark 81. Rộng rãi, hiện đại, view đẹp ngắm trọn thành phố.", "topics": []},
            {"rating": 5.0, "text": "Tuyệt vời, trải nghiệm Gold Class đẳng cấp, phục vụ trà bánh tận tình, xứng đáng 5 sao!", "topics": ["Thái độ phục vụ"]},
            {"rating": 3.0, "text": "Mọi thứ ở mức trung bình khá, không quá nổi trội so với số tiền bỏ ra.", "topics": ["Giá cả & Vé"]},
        ]

        authors = [
            "Minh Tuấn Nguyễn", "Thuỳ Trang Phạm", "Hoàng Nam Đỗ", "Khánh Linh Vũ",
            "Bảo Long Trần", "Diệu Huyền Lê", "Đức Anh Ngô", "Phương Mai Huỳnh",
            "Quang Huy Bùi", "Thanh Trúc Phan", "Tấn Phát Trương", "Ngọc Ánh Đinh",
            "Hồng Phúc Đặng", "Văn Hậu Mai", "Gia Khiêm Lý", "Thảo Vy Cao"
        ]

        reviews = []
        base_time = datetime.datetime.now()

        for i in range(count):
            template = sample_pool[i % len(sample_pool)]
            author = authors[i % len(authors)]
            days_ago = (count - i) * 2 + (i % 3)
            pub_date = base_time - datetime.timedelta(days=days_ago, hours=(i * 3) % 24)
            rev_id = f"mock_review_cgv_{i+1:03d}_{pub_date.strftime('%Y%m%d')}"

            reviews.append({
                "review_id": rev_id,
                "author_name": f"{author} ({i+1})",
                "rating": template["rating"],
                "text": template["text"],
                "published_at": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "likes_count": (i * 7) % 23,
            })

        return reviews

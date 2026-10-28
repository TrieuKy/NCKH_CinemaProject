"""
scripts/fetch_reviews.py
------------------------
Lệnh cào đánh giá từ Google Maps cho từng rạp cụ thể qua Apify.

Cách dùng:
  python -m scripts.fetch_reviews --cinema CN01 --max 50
  python -m scripts.fetch_reviews --cinema CN01 --test
  python -m scripts.fetch_reviews --cinema CN02 --max 500 -y

Tùy chọn:
  --cinema CID : Mã rạp cần cào (CN01, CN02, CN03...) — bắt buộc.
  --max N      : Số lượng đánh giá tối đa (mặc định: 20, trần: 3000).
                 Nếu > 200: yêu cầu xác nhận 'y' trước khi gọi API.
  --test       : Chế độ thử nghiệm: tối đa 20 đánh giá, không ghi DB,
                 lưu JSON vào data/reviews_raw/test_*, in tên/địa chỉ,
                 các trường nhận được và phân bố ngôn ngữ.
  -y, --yes    : Tự động đồng ý bỏ qua hỏi xác nhận khi --max > 200.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from etl.db import get_engine
from reviews.analyze import process_unanalyzed
from reviews.crawler import (
    ACTOR_ID,
    MAX_REVIEWS_HARD_LIMIT,
    fetch_reviews_apify,
    get_cinema_crawl_config,
)
from reviews.ingest import ingest

logger = logging.getLogger("reviews.fetch")


def setup_console_encoding():
    """Đảm bảo in tiếng Việt chuẩn trên console Windows."""
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def save_raw_json(
    items: List[Dict[str, Any]],
    cinema_id: str,
    is_test: bool = False,
) -> Path:
    """Lưu danh sách đánh giá thô đã làm sạch ra file JSON."""
    raw_dir = Path("data/reviews_raw")
    raw_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "test_" if is_test else ""
    filename = f"{prefix}{cinema_id}_{timestamp}.json"
    target_path = raw_dir / filename

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    return target_path


def print_coverage_report(
    reviews: List[Dict[str, Any]],
    ingest_report: Dict[str, Any] | None = None,
    nlp_count: int = 0,
):
    """In bảng báo cáo độ phủ dữ liệu thu thập được."""
    total = len(reviews)
    if total == 0:
        print("\nKhông có đánh giá nào được xử lý.")
        return

    dates = [r["published_at"][:10] for r in reviews if r.get("published_at")]
    min_date = min(dates) if dates else "N/A"
    max_date = max(dates) if dates else "N/A"

    has_text_count = sum(1 for r in reviews if r.get("review_text"))
    pct_has_text = round((has_text_count / total) * 100, 1)

    lang_counts = Counter(r.get("apify_lang") or "unknown" for r in reviews)

    print("\n" + "=" * 60)
    print("BÁO CÁO ĐỘ PHỦ DỮ LIỆU ĐÁNH GIÁ (COVERAGE REPORT)")
    print("=" * 60)
    if ingest_report:
        print(f"  - Import ID             : {ingest_report.get('import_id')}")
        print(f"  - Dòng mới thêm         : {ingest_report.get('rows_new')}")
        print(f"  - Dòng trùng cập nhật   : {ingest_report.get('rows_dup')}")
        print(f"  - Dòng lỗi bỏ qua       : {ingest_report.get('rows_skipped')}")
        print(f"  - Phân tích NLP mới     : {nlp_count}")
    print(f"  - Tổng số bản ghi nhận  : {total}")
    print(f"  - Khoảng ngày đăng      : {min_date} -> {max_date}")
    print(f"  - Đánh giá có văn bản   : {has_text_count}/{total} ({pct_has_text}%)")
    print("  - Phân bố ngôn ngữ      :")
    for lang, count in lang_counts.most_common():
        pct = round((count / total) * 100, 1)
        print(f"      • {lang:10} : {count:4} ({pct:5.1f}%)")
    print("=" * 60)


def main():
    setup_console_encoding()

    parser = argparse.ArgumentParser(description="Cào đánh giá Google Maps qua Apify")
    parser.add_argument("--cinema", type=str, required=True, help="Mã rạp (CN01, CN02...)")
    parser.add_argument("--max", type=int, default=20, help="Số lượng đánh giá tối đa (mặc định 20, trần 3000)")
    parser.add_argument("--test", action="store_true", help="Chế độ test: max 20, không ghi DB, lưu JSON thử")
    parser.add_argument("-y", "--yes", action="store_true", help="Tự động đồng ý khi --max > 200")

    args = parser.parse_args()

    # 1. Kiểm tra trần số lượng
    max_reviews = args.max
    if args.test:
        max_reviews = min(max_reviews, 20)
    else:
        if max_reviews > MAX_REVIEWS_HARD_LIMIT:
            print(f"[CẢNH BÁO] Số lượng {max_reviews} vượt trần cho phép. Giới hạn về {MAX_REVIEWS_HARD_LIMIT}.")
            max_reviews = MAX_REVIEWS_HARD_LIMIT

        # Hỏi xác nhận nếu số lượng > 200
        if max_reviews > 200 and not args.yes:
            print(f"\n[XÁC NHẬN] Bạn yêu cầu cào {max_reviews} đánh giá qua Apify.")
            print("Quá trình này có thể mất vài phút và tiêu tốn hạn ngạch Apify API.")
            ans = input("Bạn có chắc chắn muốn tiếp tục? (y/N): ").strip().lower()
            if ans != "y":
                print("Đã hủy lệnh cào.")
                sys.exit(0)

    engine = get_engine()

    # 2. Lấy cấu hình rạp
    cinema_config = get_cinema_crawl_config(engine, args.cinema.strip())
    if not cinema_config:
        print(f"[LỖI] Không tìm thấy rạp với mã: {args.cinema}")
        sys.exit(1)

    cinema_id = cinema_config["cinema_id"]
    cinema_name = cinema_config["cinema_name"]

    print("\n" + "=" * 60)
    print(f"CÀO ĐÁNH GIÁ GOOGLE MAPS — RẠP {cinema_id} ({cinema_name})")
    print(f"Chế độ: {'THỬ NGHIỆM (--test)' if args.test else 'CHÍNH THỨC'}")
    print("=" * 60)

    # 3. Gọi crawler
    try:
        fetch_result = fetch_reviews_apify(
            cinema_config=cinema_config,
            max_reviews=max_reviews,
        )
    except Exception as exc:
        print(f"\n[LỖI CÀO DỮ LIỆU]: {exc}")
        sys.exit(1)

    if fetch_result.get("status") == "SKIPPED_INACTIVE":
        print(f"\n[BỎ QUA] {fetch_result.get('message')}")
        sys.exit(0)

    reviews = fetch_result.get("reviews", [])
    place_title = fetch_result.get("place_title", "N/A")
    place_address = fetch_result.get("place_address", "N/A")

    # 4. Lưu file JSON thô đã làm sạch
    saved_path = save_raw_json(reviews, cinema_id, is_test=args.test)
    print(f"\nĐã lưu JSON thô vào: {saved_path}")

    # 5. Xử lý theo chế độ --test hoặc chính thức
    if args.test:
        print("\n--- KẾT QUẢ CHẾ ĐỘ THỬ NGHIỆM (--test) ---")
        print(f"  - Tên địa điểm nhận từ Apify    : {place_title}")
        print(f"  - Địa chỉ nhận từ Apify         : {place_address}")
        print(f"  - Tổng số bản ghi nhận          : {len(reviews)}")
        if reviews:
            sample_fields = list(reviews[0].keys())
            print(f"  - Các trường dữ liệu chuẩn hóa  : {sample_fields}")
        print_coverage_report(reviews)
        print("\n[CHÚ Ý] Chế độ --test KHÔNG ghi dữ liệu vào cơ sở dữ liệu.")
        return

    # Chế độ chính thức: Nhập vào DB và chạy NLP
    print(f"\nĐang nhập {len(reviews)} bản ghi vào database...")
    ingest_report = ingest(
        engine=engine,
        rows=reviews,
        cinema_key=cinema_config["cinema_key"],
        source="APIFY",
        actor_name=ACTOR_ID,
        apify_run_id=fetch_result.get("apify_run_id"),
        dataset_id=fetch_result.get("dataset_id"),
        raw_file_path=str(saved_path),
    )

    print("Đang chạy phân tích NLP cho các bản ghi mới...")
    nlp_count = process_unanalyzed(engine)

    print_coverage_report(reviews, ingest_report=ingest_report, nlp_count=nlp_count)
    print("\nHOÀN THÀNH!")


if __name__ == "__main__":
    main()

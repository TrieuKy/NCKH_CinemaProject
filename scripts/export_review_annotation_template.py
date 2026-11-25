"""
scripts/export_review_annotation_template.py
--------------------------------------------
Xuất tối đa 165 review thực có văn bản từ PostgreSQL (cinema_dw.review_raw)
ra file CSV phục vụ gán nhãn thủ công (Human Ground Truth).

Quy tắc bắt buộc:
  - Chỉ lấy các dòng có has_text = TRUE.
  - KHÔNG xuất cột rating, sentiment_* hay topics (tránh lộ nhãn cho annotator).
  - KHÔNG điền sẵn nhãn bằng AI, rating hoặc NLP.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from etl.db import get_engine

DEFAULT_OUTPUT = Path("data/review_ground_truth_template.csv")
MAX_REVIEWS = 165


def export_annotation_template(
    output_path: Path = DEFAULT_OUTPUT,
    limit: int = MAX_REVIEWS,
) -> int:
    """Xuất tập review có văn bản từ PostgreSQL ra CSV để gán nhãn thủ công."""
    engine = get_engine()
    sql = text("""
        SELECT review_id, review_text
        FROM cinema_dw.review_raw
        WHERE has_text = TRUE
          AND review_text IS NOT NULL
          AND BTRIM(review_text) <> ''
        ORDER BY published_date, raw_id
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": int(limit)}).fetchall()

    df = pd.DataFrame(rows, columns=["review_id", "review_text"])
    df["human_sentiment"] = ""
    df["human_topics"] = ""
    df["annotator_id"] = ""
    df["annotation_status"] = "PENDING"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return len(df)


def main() -> None:
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Xuất tập review có văn bản để gán nhãn Human Ground Truth."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Đường dẫn file CSV đầu ra (mặc định: data/review_ground_truth_template.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=MAX_REVIEWS,
        help="Số lượng review tối đa (mặc định: 165)",
    )
    args = parser.parse_args()

    count = export_annotation_template(output_path=args.output, limit=args.limit)
    print(f"Đã xuất {count} review có văn bản (không kèm rating/NLP) -> {args.output}")


if __name__ == "__main__":
    main()

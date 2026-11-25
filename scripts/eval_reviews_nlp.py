"""
scripts/eval_reviews_nlp.py
---------------------------
Đánh giá khoa học module reviews/nlp.py trên tập Human Ground Truth.

Tính năng:
  - Đọc tập nhãn thủ công CSV (review_id, review_text, human_sentiment,
    human_topics, annotator_id, annotation_status).
  - Nếu chưa có dòng nào hoàn thành gán nhãn thủ công -> báo 'NOT YET EVALUABLE'
    và KHÔNG tự sinh metric giả.
  - Khi có nhãn hợp lệ:
      1. Sentiment: Confusion Matrix, Precision, Recall, Macro-F1, Weighted-F1, Accuracy.
      2. Topic (multi-label): Micro-F1, Macro-F1 và Precision/Recall/F1 từng nhãn.
      3. Phân tích lỗi tiếng Việt (phủ định, văn bản ngắn, không dấu/ngoại ngữ, đa chủ đề).
      4. Độ đồng thuận liên người gán nhãn (Cohen's Kappa) nếu có file annotator thứ 2.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.preprocessing import MultiLabelBinarizer

from reviews.nlp import (
    DEFAULT_TOPIC_KEYWORDS,
    TOPIC_FALLBACK,
    extract_topics,
    sentiment_from_text,
)

DEFAULT_INPUT = Path("data/review_ground_truth_template.csv")
SENTIMENT_LABELS = ["NEGATIVE", "NEUTRAL", "POSITIVE"]
ALL_TOPICS = list(DEFAULT_TOPIC_KEYWORDS.keys()) + [TOPIC_FALLBACK]

_NEG_PATTERN = re.compile(r"\b(?:không|ko|k|chẳng|chả|chưa|hok)\b", re.IGNORECASE)
_VIET_PATTERN = re.compile(
    r"[àáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỷỹỵ]",
    re.IGNORECASE,
)


def parse_topics_cell(raw: Any) -> List[str]:
    """Parse ô human_topics (phân tách bởi ';' hoặc ',') thành danh sách chuẩn hóa."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    text_val = str(raw).strip()
    if not text_val:
        return []
    sep = ";" if ";" in text_val else ","
    return [t.strip() for t in text_val.split(sep) if t.strip()]


def load_annotated_rows(csv_path: Path) -> pd.DataFrame:
    """Đọc CSV và lọc các dòng đã được gán nhãn thủ công hợp lệ."""
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    required_cols = {
        "review_id",
        "review_text",
        "human_sentiment",
        "human_topics",
        "annotator_id",
        "annotation_status",
    }
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Thiếu cột bắt buộc trong {csv_path}: {sorted(missing)}")

    df["human_sentiment"] = df["human_sentiment"].str.strip().str.upper()
    df["annotation_status"] = df["annotation_status"].str.strip().str.upper()
    df["human_topics_list"] = df["human_topics"].apply(parse_topics_cell)

    valid_mask = (
        df["human_sentiment"].isin(SENTIMENT_LABELS)
        & (df["human_topics_list"].apply(len) > 0)
        & (~df["annotation_status"].isin(["PENDING", "SKIP", ""]))
    )
    return df[valid_mask].copy()


def evaluate_ground_truth(
    csv_path: Path = DEFAULT_INPUT,
    annotator2_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Chạy đánh giá NLP trên tập Human Ground Truth.
    Trả status='NOT YET EVALUABLE' nếu chưa có nhãn thủ công hoàn chỉnh.
    """
    if not csv_path.exists():
        return {
            "status": "NOT YET EVALUABLE",
            "reason": f"Không tìm thấy file {csv_path}",
            "n_total": 0,
            "n_annotated": 0,
        }

    raw_df = pd.read_csv(csv_path, dtype=str).fillna("")
    annotated_df = load_annotated_rows(csv_path)

    if annotated_df.empty:
        return {
            "status": "NOT YET EVALUABLE",
            "reason": "Chưa có dòng nào hoàn thành gán nhãn thủ công (human_sentiment & human_topics).",
            "n_total": len(raw_df),
            "n_annotated": 0,
        }

    # Dự đoán bằng module reviews/nlp.py (chỉ từ review_text, không dùng rating)
    annotated_df["pred_sentiment"] = annotated_df["review_text"].apply(
        lambda t: sentiment_from_text(t) or "NEUTRAL"
    )
    annotated_df["pred_topics"] = annotated_df["review_text"].apply(extract_topics)

    # 1. Đánh giá Sentiment
    y_true_s = annotated_df["human_sentiment"].tolist()
    y_pred_s = annotated_df["pred_sentiment"].tolist()
    cm = confusion_matrix(y_true_s, y_pred_s, labels=SENTIMENT_LABELS)
    acc = float(accuracy_score(y_true_s, y_pred_s))
    macro_f1_s = float(
        f1_score(y_true_s, y_pred_s, labels=SENTIMENT_LABELS, average="macro", zero_division=0)
    )
    weighted_f1_s = float(
        f1_score(y_true_s, y_pred_s, labels=SENTIMENT_LABELS, average="weighted", zero_division=0)
    )
    prec_s, rec_s, f1_s, sup_s = precision_recall_fscore_support(
        y_true_s, y_pred_s, labels=SENTIMENT_LABELS, zero_division=0
    )
    per_class_sentiment = {
        lbl: {
            "precision": round(float(prec_s[i]), 4),
            "recall": round(float(rec_s[i]), 4),
            "f1": round(float(f1_s[i]), 4),
            "support": int(sup_s[i]),
            "coverage_pct": round(100.0 * int(sup_s[i]) / len(annotated_df), 2),
        }
        for i, lbl in enumerate(SENTIMENT_LABELS)
    }

    # 2. Đánh giá Topic (Multi-label)
    mlb = MultiLabelBinarizer(classes=ALL_TOPICS)
    y_true_t = mlb.fit_transform(annotated_df["human_topics_list"])
    y_pred_t = mlb.transform(annotated_df["pred_topics"])

    micro_f1_t = float(f1_score(y_true_t, y_pred_t, average="micro", zero_division=0))
    macro_f1_t = float(f1_score(y_true_t, y_pred_t, average="macro", zero_division=0))
    prec_t, rec_t, f1_t, sup_t = precision_recall_fscore_support(
        y_true_t, y_pred_t, average=None, zero_division=0
    )
    per_label_topics = {
        lbl: {
            "precision": round(float(prec_t[i]), 4),
            "recall": round(float(rec_t[i]), 4),
            "f1": round(float(f1_t[i]), 4),
            "support": int(sup_t[i]),
            "coverage_pct": round(100.0 * int(sup_t[i]) / len(annotated_df), 2),
        }
        for i, lbl in enumerate(ALL_TOPICS)
    }

    # 3. Phân tích lỗi tiếng Việt
    sent_errors = annotated_df[annotated_df["human_sentiment"] != annotated_df["pred_sentiment"]]
    error_breakdown = {
        "total_sentiment_errors": int(len(sent_errors)),
        "negation_errors": int(
            sent_errors["review_text"].str.contains(_NEG_PATTERN, regex=True).sum()
        ),
        "short_text_errors_lt15": int(
            (sent_errors["review_text"].str.len() < 15).sum()
        ),
        "non_viet_accent_errors": int(
            (~sent_errors["review_text"].str.contains(_VIET_PATTERN, regex=True)).sum()
        ),
        "multi_topic_errors": int(
            (sent_errors["human_topics_list"].apply(len) > 1).sum()
        ),
        "sample_errors": [
            {
                "review_id": r["review_id"],
                "human": r["human_sentiment"],
                "pred": r["pred_sentiment"],
                "text": r["review_text"][:120],
            }
            for _, r in sent_errors.head(10).iterrows()
        ],
    }

    # 4. Cohen's Kappa nếu có annotator thứ 2
    iaa: Optional[Dict[str, Any]] = None
    if annotator2_path is not None and annotator2_path.exists():
        df2 = load_annotated_rows(annotator2_path)
        merged = annotated_df.merge(
            df2[["review_id", "human_sentiment", "human_topics_list"]],
            on="review_id",
            suffixes=("_1", "_2"),
        )
        if len(merged) >= 2:
            kappa_s = float(
                cohen_kappa_score(merged["human_sentiment_1"], merged["human_sentiment_2"])
            )
            iaa = {
                "n_overlap": int(len(merged)),
                "sentiment_cohens_kappa": round(kappa_s, 4),
            }

    return {
        "status": "EVALUATED",
        "n_total": int(len(raw_df)),
        "n_annotated": int(len(annotated_df)),
        "sentiment": {
            "labels": SENTIMENT_LABELS,
            "confusion_matrix": cm.tolist(),
            "accuracy": round(acc, 4),
            "macro_f1": round(macro_f1_s, 4),
            "weighted_f1": round(weighted_f1_s, 4),
            "per_class": per_class_sentiment,
        },
        "topics": {
            "micro_f1": round(micro_f1_t, 4),
            "macro_f1": round(macro_f1_t, 4),
            "per_label": per_label_topics,
        },
        "error_analysis": error_breakdown,
        "inter_annotator_agreement": iaa,
    }


def main() -> None:
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Đánh giá NLP trên tập Human Ground Truth."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--annotator2", type=Path, default=None)
    args = parser.parse_args()

    res = evaluate_ground_truth(csv_path=args.input, annotator2_path=args.annotator2)
    print(f"STATUS: {res['status']}")
    if res["status"] != "EVALUATED":
        print(f"Reason: {res['reason']}")
        print(f"Rows in file: {res['n_total']} | Annotated: {res['n_annotated']}")
        return

    s = res["sentiment"]
    print(f"Samples evaluated: {res['n_annotated']}/{res['n_total']}")
    print("\n=== SENTIMENT ===")
    print(f"Confusion Matrix {s['labels']}: {s['confusion_matrix']}")
    print(
        f"Accuracy={s['accuracy']} | Macro-F1={s['macro_f1']} | Weighted-F1={s['weighted_f1']}"
    )
    for lbl, m in s["per_class"].items():
        print(
            f"  {lbl:8}: P={m['precision']} R={m['recall']} F1={m['f1']} "
            f"n={m['support']} ({m['coverage_pct']}%)"
        )

    t = res["topics"]
    print("\n=== TOPICS (MULTI-LABEL) ===")
    print(f"Micro-F1={t['micro_f1']} | Macro-F1={t['macro_f1']}")
    for lbl, m in t["per_label"].items():
        print(
            f"  {lbl:24}: P={m['precision']} R={m['recall']} F1={m['f1']} "
            f"n={m['support']} ({m['coverage_pct']}%)"
        )

    print("\n=== ERROR ANALYSIS ===")
    print(res["error_analysis"])
    if res["inter_annotator_agreement"]:
        print("\n=== INTER-ANNOTATOR AGREEMENT ===")
        print(res["inter_annotator_agreement"])


if __name__ == "__main__":
    main()

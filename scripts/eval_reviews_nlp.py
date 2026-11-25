"""
scripts/eval_reviews_nlp.py
---------------------------
Đánh giá khoa học module reviews/nlp.py trên tập Human Ground Truth
và kiểm định chất lượng quy trình Dual Annotation / Gold Standard.

Tính năng:
  - Kiểm định chặt chẽ file gán nhãn:
      * Chỉ chấp nhận hoàn thành khi annotation_status == 'DONE'.
      * Kiểm tra human_sentiment thuộc {POSITIVE, NEUTRAL, NEGATIVE}.
      * Kiểm tra mọi nhãn trong human_topics thuộc 6 chủ đề hợp lệ.
      * Từ chối review_id trùng trong cùng một file.
      * Từ chối file annotator thứ 2 có cùng annotator_id với annotator thứ 1.
      * Báo cáo đầy đủ: hợp lệ (DONE), thiếu nhãn, chưa hoàn thành (PENDING), sai định dạng.
      * Không âm thầm loại bỏ dòng sai rồi công bố metric như thể tập đã hoàn chỉnh.
  - Inter-Annotator Agreement (IAA):
      * Cohen's Kappa cho sentiment và cho từng topic (nhị phân có/không).
      * Số review cả hai annotator hoàn thành, số bất đồng, danh sách review_id cần adjudication.
      * Trả None nếu không đủ dữ liệu hoặc Kappa không xác định (không bịa số).
  - Xuất danh sách bất đồng / Gold Standard candidate:
      * Cột: review_id, review_text, ann1_sentiment, ann2_sentiment,
             ann1_topics, ann2_topics, final_sentiment, final_topics, adjudication_status.
      * Đồng thuận -> AGREED; Bất đồng -> PENDING_ADJUDICATION (không tự chọn nhãn).
  - Đánh giá NLP (chỉ đánh giá sentiment_from_text và extract_topics trên review_text,
    tách biệt hoàn toàn với sentiment_final có dùng rating):
      * Nếu chưa có nhãn thủ công -> trả 'NOT YET EVALUABLE', không tự sinh metric.
"""

from __future__ import annotations

import argparse
import math
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
DEFAULT_DISAGREEMENTS_OUT = Path("data/review_annotation_disagreements.csv")

SENTIMENT_LABELS = ["NEGATIVE", "NEUTRAL", "POSITIVE"]
ALL_TOPICS = list(DEFAULT_TOPIC_KEYWORDS.keys()) + [TOPIC_FALLBACK]
VALID_TOPIC_SET = set(ALL_TOPICS)
VALID_STATUSES = {"PENDING", "DONE"}

REQUIRED_COLUMNS = {
    "review_id",
    "review_text",
    "human_sentiment",
    "human_topics",
    "annotator_id",
    "annotation_status",
}

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


def _safe_cohens_kappa(y1: List[Any], y2: List[Any]) -> Optional[float]:
    """
    Tính Cohen's Kappa an toàn.
    Trả None nếu < 2 mẫu hoặc Kappa không xác định (NaN do nhãn hằng số).
    """
    if len(y1) < 2 or len(y2) < 2 or len(y1) != len(y2):
        return None
    try:
        val = float(cohen_kappa_score(y1, y2))
    except Exception:
        return None
    if math.isnan(val):
        return None
    return round(val, 4)


def inspect_annotation_file(csv_path: Path) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Đọc và kiểm định toàn diện một file gán nhãn thủ công.
    Raise ValueError nếu thiếu cột bắt buộc hoặc có review_id trùng lặp.
    Trả về (df_đã_chuẩn_hóa, báo_cáo_chất_lượng).
    """
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    missing_cols = REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        raise ValueError(f"Thiếu cột bắt buộc trong {csv_path}: {sorted(missing_cols)}")

    df["review_id"] = df["review_id"].str.strip()
    if (df["review_id"] == "").any():
        raise ValueError(f"Có dòng thiếu review_id trong {csv_path}")

    dup_ids = df[df["review_id"].duplicated(keep=False)]["review_id"].unique().tolist()
    if dup_ids:
        raise ValueError(
            f"Phát hiện review_id trùng lặp trong {csv_path}: {dup_ids[:10]}"
        )

    df["human_sentiment"] = df["human_sentiment"].str.strip().str.upper()
    df["annotation_status"] = df["annotation_status"].str.strip().str.upper()
    df["annotator_id"] = df["annotator_id"].str.strip()
    df["human_topics_list"] = df["human_topics"].apply(parse_topics_cell)

    n_valid_done = 0
    n_pending = 0
    n_missing_labels = 0
    n_invalid_format = 0
    invalid_rows: List[Dict[str, str]] = []
    valid_mask: List[bool] = []

    for _, row in df.iterrows():
        rid = row["review_id"]
        status = row["annotation_status"]
        sent = row["human_sentiment"]
        topics = row["human_topics_list"]

        if status not in VALID_STATUSES:
            n_invalid_format += 1
            invalid_rows.append({
                "review_id": rid,
                "reason": f"annotation_status không hợp lệ: {status!r} (chỉ chấp nhận PENDING hoặc DONE)",
            })
            valid_mask.append(False)
            continue

        if status == "PENDING":
            n_pending += 1
            valid_mask.append(False)
            continue

        # status == "DONE": bắt buộc đủ và đúng định dạng cả sentiment lẫn topics
        reasons: List[str] = []
        if not sent:
            reasons.append("thiếu human_sentiment")
        elif sent not in SENTIMENT_LABELS:
            reasons.append(f"human_sentiment không hợp lệ: {sent!r}")

        if not topics:
            reasons.append("thiếu human_topics")
        else:
            bad_topics = [t for t in topics if t not in VALID_TOPIC_SET]
            if bad_topics:
                reasons.append(f"human_topics không hợp lệ: {bad_topics}")

        if reasons:
            if not sent or not topics:
                n_missing_labels += 1
            if (sent and sent not in SENTIMENT_LABELS) or any(
                t not in VALID_TOPIC_SET for t in topics
            ):
                n_invalid_format += 1
            invalid_rows.append({"review_id": rid, "reason": "; ".join(reasons)})
            valid_mask.append(False)
            continue

        n_valid_done += 1
        valid_mask.append(True)

    df["is_valid_done"] = valid_mask
    report = {
        "n_total": int(len(df)),
        "n_valid_done": int(n_valid_done),
        "n_pending": int(n_pending),
        "n_missing_labels": int(n_missing_labels),
        "n_invalid_format": int(n_invalid_format),
        "invalid_rows": invalid_rows,
    }
    return df, report


def load_annotated_rows(csv_path: Path) -> pd.DataFrame:
    """
    Trả về DataFrame các dòng đã hoàn thành hợp lệ (annotation_status == 'DONE').
    Raise ValueError nếu phát hiện dòng DONE bị thiếu nhãn hoặc sai định dạng.
    """
    df, report = inspect_annotation_file(csv_path)
    if report["invalid_rows"]:
        raise ValueError(
            f"Phát hiện {len(report['invalid_rows'])} dòng lỗi định dạng/thiếu nhãn trong {csv_path}: "
            f"{report['invalid_rows'][:5]}"
        )
    return df[df["is_valid_done"]].copy()


def _validate_distinct_annotators(df1: pd.DataFrame, df2: pd.DataFrame) -> None:
    """Không chấp nhận hai file có cùng annotator_id khi tính đồng thuận."""
    ids1 = {x for x in df1["annotator_id"].tolist() if x}
    ids2 = {x for x in df2["annotator_id"].tolist() if x}
    if not ids1 or not ids2:
        raise ValueError("Cả hai file phải có annotator_id khác rỗng để kiểm tra đồng thuận.")
    overlap = ids1 & ids2
    if overlap:
        raise ValueError(
            f"Hai annotator có cùng annotator_id: {sorted(overlap)}. "
            "Yêu cầu hai người gán nhãn độc lập với mã khác nhau."
        )


def compute_inter_annotator_agreement(
    ann1_path: Path,
    ann2_path: Path,
    disagreements_output: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Tính Inter-Annotator Agreement (Cohen's Kappa cho sentiment và từng topic)
    giữa hai annotator độc lập, đồng thời xuất danh sách bất đồng nếu được yêu cầu.
    """
    df1 = load_annotated_rows(ann1_path)
    df2 = load_annotated_rows(ann2_path)
    _validate_distinct_annotators(df1, df2)

    merged = df1.merge(
        df2[[
            "review_id",
            "human_sentiment",
            "human_topics_list",
            "annotator_id",
        ]],
        on="review_id",
        suffixes=("_1", "_2"),
    )

    n_both_done = int(len(merged))
    if n_both_done == 0:
        return {
            "n_both_done": 0,
            "n_disagreements": 0,
            "adjudication_review_ids": [],
            "sentiment_cohens_kappa": None,
            "topic_cohens_kappa": {t: None for t in ALL_TOPICS},
            "status": "INSUFFICIENT_OVERLAP",
        }

    # Sentiment Kappa
    kappa_sent = _safe_cohens_kappa(
        merged["human_sentiment_1"].tolist(),
        merged["human_sentiment_2"].tolist(),
    )

    # Topic Kappa (nhị phân cho từng chủ đề)
    topic_kappas: Dict[str, Optional[float]] = {}
    for topic in ALL_TOPICS:
        y1_t = [1 if topic in lst else 0 for lst in merged["human_topics_list_1"]]
        y2_t = [1 if topic in lst else 0 for lst in merged["human_topics_list_2"]]
        topic_kappas[topic] = _safe_cohens_kappa(y1_t, y2_t)

    # Xác định bất đồng & tạo bảng adjudication
    disagreement_ids: List[str] = []
    adjudication_rows: List[Dict[str, Any]] = []

    for _, row in merged.iterrows():
        s1 = row["human_sentiment_1"]
        s2 = row["human_sentiment_2"]
        t1_list = sorted(row["human_topics_list_1"])
        t2_list = sorted(row["human_topics_list_2"])
        t1_str = ";".join(t1_list)
        t2_str = ";".join(t2_list)

        agreed = (s1 == s2) and (t1_list == t2_list)
        if not agreed:
            disagreement_ids.append(row["review_id"])

        adjudication_rows.append({
            "review_id": row["review_id"],
            "review_text": row["review_text"],
            "ann1_sentiment": s1,
            "ann2_sentiment": s2,
            "ann1_topics": t1_str,
            "ann2_topics": t2_str,
            "final_sentiment": s1 if agreed else "",
            "final_topics": t1_str if agreed else "",
            "adjudication_status": "AGREED" if agreed else "PENDING_ADJUDICATION",
        })

    if disagreements_output is not None:
        disagreements_output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(adjudication_rows).to_csv(
            disagreements_output, index=False, encoding="utf-8-sig"
        )

    return {
        "n_both_done": n_both_done,
        "n_disagreements": len(disagreement_ids),
        "adjudication_review_ids": disagreement_ids,
        "sentiment_cohens_kappa": kappa_sent,
        "topic_cohens_kappa": topic_kappas,
        "disagreements_file": str(disagreements_output) if disagreements_output else None,
        "status": "COMPUTED" if n_both_done >= 2 else "INSUFFICIENT_OVERLAP",
    }


def evaluate_ground_truth(
    csv_path: Path = DEFAULT_INPUT,
    annotator2_path: Optional[Path] = None,
    disagreements_output: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Chạy đánh giá NLP trên tập Human Ground Truth.
    - Trả status='NOT YET EVALUABLE' nếu chưa có dòng DONE nào.
    - Trả status='INVALID_ANNOTATIONS' nếu có dòng sai định dạng/thiếu nhãn
      (không âm thầm loại bỏ dòng lỗi để công bố metric).
    - Chỉ đánh giá sentiment_from_text và extract_topics trên review_text
      (tách biệt với sentiment_final có sử dụng rating).
    """
    if not csv_path.exists():
        return {
            "status": "NOT YET EVALUABLE",
            "reason": f"Không tìm thấy file {csv_path}",
            "quality_report": {
                "n_total": 0,
                "n_valid_done": 0,
                "n_pending": 0,
                "n_missing_labels": 0,
                "n_invalid_format": 0,
                "invalid_rows": [],
            },
            "n_total": 0,
            "n_annotated": 0,
        }

    full_df, quality_report = inspect_annotation_file(csv_path)

    if quality_report["invalid_rows"]:
        return {
            "status": "INVALID_ANNOTATIONS",
            "reason": (
                f"Phát hiện {len(quality_report['invalid_rows'])} dòng sai định dạng hoặc thiếu nhãn. "
                "Không thể công bố metric trước khi sửa các dòng lỗi."
            ),
            "quality_report": quality_report,
            "n_total": quality_report["n_total"],
            "n_annotated": quality_report["n_valid_done"],
        }

    annotated_df = full_df[full_df["is_valid_done"]].copy()
    if annotated_df.empty:
        return {
            "status": "NOT YET EVALUABLE",
            "reason": "Chưa có dòng nào có annotation_status='DONE' với nhãn thủ công hợp lệ.",
            "quality_report": quality_report,
            "n_total": quality_report["n_total"],
            "n_annotated": 0,
        }

    # Dự đoán bằng module reviews/nlp.py (CHỈ từ review_text, KHÔNG dùng rating)
    annotated_df["pred_sentiment_text"] = annotated_df["review_text"].apply(
        lambda t: sentiment_from_text(t) or "NEUTRAL"
    )
    annotated_df["pred_topics"] = annotated_df["review_text"].apply(extract_topics)

    # 1. Đánh giá Sentiment (sentiment_from_text)
    y_true_s = annotated_df["human_sentiment"].tolist()
    y_pred_s = annotated_df["pred_sentiment_text"].tolist()
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
    sent_errors = annotated_df[
        annotated_df["human_sentiment"] != annotated_df["pred_sentiment_text"]
    ]
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
                "pred": r["pred_sentiment_text"],
                "text": r["review_text"][:120],
            }
            for _, r in sent_errors.head(10).iterrows()
        ],
    }

    # 4. Inter-Annotator Agreement nếu có annotator thứ 2
    iaa: Optional[Dict[str, Any]] = None
    if annotator2_path is not None and annotator2_path.exists():
        iaa = compute_inter_annotator_agreement(
            ann1_path=csv_path,
            ann2_path=annotator2_path,
            disagreements_output=disagreements_output,
        )

    return {
        "status": "EVALUATED",
        "evaluated_target": "sentiment_from_text & extract_topics (text-only, without rating)",
        "quality_report": quality_report,
        "n_total": quality_report["n_total"],
        "n_annotated": quality_report["n_valid_done"],
        "is_full_dataset_done": quality_report["n_pending"] == 0,
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
        description="Kiểm định chất lượng gán nhãn và đánh giá NLP trên tập Human Ground Truth."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--annotator2", type=Path, default=None)
    parser.add_argument(
        "--export-disagreements",
        type=Path,
        default=None,
        help="Đường dẫn xuất file CSV bất đồng giữa 2 annotator (ví dụ data/review_annotation_disagreements.csv)",
    )
    args = parser.parse_args()

    res = evaluate_ground_truth(
        csv_path=args.input,
        annotator2_path=args.annotator2,
        disagreements_output=args.export_disagreements,
    )
    print(f"STATUS: {res['status']}")
    qr = res["quality_report"]
    print(
        f"Quality: total={qr['n_total']} | valid_done={qr['n_valid_done']} | "
        f"pending={qr['n_pending']} | missing={qr['n_missing_labels']} | invalid={qr['n_invalid_format']}"
    )

    if res["status"] != "EVALUATED":
        print(f"Reason: {res['reason']}")
        if qr["invalid_rows"]:
            print("Invalid rows sample:", qr["invalid_rows"][:5])
        return

    s = res["sentiment"]
    print(f"Evaluated target: {res['evaluated_target']}")
    print(f"Samples evaluated: {res['n_annotated']}/{res['n_total']} (full={res['is_full_dataset_done']})")
    print("\n=== SENTIMENT (sentiment_from_text) ===")
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

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

STATUS_PRELIMINARY = "PRELIMINARY_EVALUATION"
STATUS_AWAITING_ADJUDICATION = "AWAITING_ADJUDICATION"
STATUS_FINAL_EVALUATED = "FINAL_EVALUATED"
STATUS_NOT_YET_EVALUABLE = "NOT YET EVALUABLE"
STATUS_INVALID_ANNOTATIONS = "INVALID_ANNOTATIONS"

VALID_ADJUDICATION_STATUSES = {"AGREED", "PENDING_ADJUDICATION", "ADJUDICATED"}
VALID_GOLD_CONFIRMATION_STATUSES = {"", "PENDING", "DRAFT", "CONFIRMED"}
FORBIDDEN_LEAKAGE_COLUMNS = {
    "rating",
    "sentiment_final",
    "pred_sentiment",
    "pred_topics",
    "nlp_sentiment",
    "nlp_topics",
}

REQUIRED_COLUMNS = {
    "review_id",
    "review_text",
    "human_sentiment",
    "human_topics",
    "annotator_id",
    "annotation_status",
}

GOLD_STANDARD_REQUIRED_COLUMNS = {
    "review_id",
    "review_text",
    "ann1_sentiment",
    "ann2_sentiment",
    "ann1_topics",
    "ann2_topics",
    "final_sentiment",
    "final_topics",
    "adjudication_status",
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
    Raise ValueError nếu thiếu cột bắt buộc, chứa cột rò rỉ NLP/rating, hoặc có review_id trùng lặp.
    Trả về (df_đã_chuẩn_hóa, báo_cáo_chất_lượng).
    """
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    leakage_cols = FORBIDDEN_LEAKAGE_COLUMNS & set(df.columns)
    if leakage_cols:
        raise ValueError(
            f"Phát hiện cột rò rỉ NLP/rating trong {csv_path}: {sorted(leakage_cols)}"
        )

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

    df["review_text"] = df["review_text"].str.strip()
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
        text = row["review_text"]
        status = row["annotation_status"]
        sent = row["human_sentiment"]
        topics = row["human_topics_list"]
        ann_id = row["annotator_id"]

        if not text:
            n_invalid_format += 1
            invalid_rows.append({
                "review_id": rid,
                "reason": "review_text rỗng",
            })
            valid_mask.append(False)
            continue

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

        # status == "DONE": bắt buộc đủ annotator_id, sentiment và topics hợp lệ
        reasons: List[str] = []
        missing_flag = False
        invalid_flag = False

        if not ann_id:
            reasons.append("thiếu annotator_id")
            missing_flag = True

        if not sent:
            reasons.append("thiếu human_sentiment")
            missing_flag = True
        elif sent not in SENTIMENT_LABELS:
            reasons.append(f"human_sentiment không hợp lệ: {sent!r}")
            invalid_flag = True

        if not topics:
            reasons.append("thiếu human_topics")
            missing_flag = True
        else:
            bad_topics = [t for t in topics if t not in VALID_TOPIC_SET]
            if bad_topics:
                reasons.append(f"human_topics không hợp lệ: {bad_topics}")
                invalid_flag = True

        if reasons:
            if missing_flag:
                n_missing_labels += 1
            if invalid_flag:
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


def _validate_two_annotators_consistency(df1: pd.DataFrame, df2: pd.DataFrame) -> None:
    """
    Kiểm tra tính nhất quán review_id và review_text giữa hai file annotator.
    Raise ValueError nếu tập review_id không khớp hoặc review_text khác nhau cho cùng review_id.
    """
    ids1 = set(df1["review_id"].tolist())
    ids2 = set(df2["review_id"].tolist())
    if ids1 != ids2:
        only1 = sorted(ids1 - ids2)[:5]
        only2 = sorted(ids2 - ids1)[:5]
        raise ValueError(
            f"review_id không nhất quán giữa hai annotator "
            f"(chỉ có ở ann1: {only1}, chỉ có ở ann2: {only2})."
        )

    merged_texts = df1[["review_id", "review_text"]].merge(
        df2[["review_id", "review_text"]],
        on="review_id",
        suffixes=("_1", "_2"),
    )
    mismatches = merged_texts[
        merged_texts["review_text_1"].str.strip() != merged_texts["review_text_2"].str.strip()
    ]
    if not mismatches.empty:
        bad_ids = mismatches["review_id"].tolist()[:5]
        raise ValueError(
            f"review_text không khớp giữa hai annotator cho review_id: {bad_ids}"
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
    full_df1, qr1 = inspect_annotation_file(ann1_path)
    full_df2, qr2 = inspect_annotation_file(ann2_path)
    if qr1["invalid_rows"]:
        raise ValueError(
            f"Phát hiện {len(qr1['invalid_rows'])} dòng lỗi định dạng/thiếu nhãn trong {ann1_path}: "
            f"{qr1['invalid_rows'][:5]}"
        )
    if qr2["invalid_rows"]:
        raise ValueError(
            f"Phát hiện {len(qr2['invalid_rows'])} dòng lỗi định dạng/thiếu nhãn trong {ann2_path}: "
            f"{qr2['invalid_rows'][:5]}"
        )
    _validate_two_annotators_consistency(full_df1, full_df2)

    df1 = full_df1[full_df1["is_valid_done"]].copy()
    df2 = full_df2[full_df2["is_valid_done"]].copy()
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


def _empty_quality_report() -> Dict[str, Any]:
    return {
        "n_total": 0,
        "n_valid_done": 0,
        "n_pending": 0,
        "n_missing_labels": 0,
        "n_invalid_format": 0,
        "invalid_rows": [],
    }


def inspect_gold_standard_file(csv_path: Path) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Đọc và xác thực quy trình Gold Standard từ file adjudication.
    Kiểm tra:
      - Không chứa cột rò rỉ NLP/rating.
      - Đủ các cột bắt buộc của Gold Standard (không thể giả mạo bằng file ANN_01 đơn lẻ).
      - review_id duy nhất, review_text không rỗng.
      - Nhãn ann1/ann2 hợp lệ.
      - Dòng đồng thuận giữ nguyên nhãn (AGREED, final == ann1 == ann2).
      - Dòng bất đồng phải được adjudicate (ADJUDICATED + final_sentiment/final_topics hợp lệ + adjudicator_id);
        không tự động hoàn thành các dòng PENDING_ADJUDICATION.
      - Trạng thái xác nhận cuối gold_standard_status == 'CONFIRMED' trên toàn bộ tập.
    """
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    leakage_cols = FORBIDDEN_LEAKAGE_COLUMNS & set(df.columns)
    if leakage_cols:
        raise ValueError(
            f"Phát hiện cột rò rỉ NLP/rating trong file Gold Standard {csv_path}: {sorted(leakage_cols)}"
        )

    missing_cols = GOLD_STANDARD_REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        raise ValueError(
            f"File {csv_path} không đúng cấu trúc Gold Standard (thiếu cột: {sorted(missing_cols)}). "
            "Không chấp nhận file annotation đơn lẻ làm Gold Standard."
        )

    df["review_id"] = df["review_id"].str.strip()
    if (df["review_id"] == "").any():
        raise ValueError(f"Có dòng thiếu review_id trong {csv_path}")

    dup_ids = df[df["review_id"].duplicated(keep=False)]["review_id"].unique().tolist()
    if dup_ids:
        raise ValueError(
            f"Phát hiện review_id trùng lặp trong {csv_path}: {dup_ids[:10]}"
        )

    df["review_text"] = df["review_text"].str.strip()
    df["ann1_sentiment"] = df["ann1_sentiment"].str.strip().str.upper()
    df["ann2_sentiment"] = df["ann2_sentiment"].str.strip().str.upper()
    df["final_sentiment"] = df["final_sentiment"].str.strip().str.upper()
    df["adjudication_status"] = df["adjudication_status"].str.strip().str.upper()
    df["adjudicator_id"] = (
        df["adjudicator_id"].str.strip() if "adjudicator_id" in df.columns else ""
    )
    has_gold_status_col = "gold_standard_status" in df.columns
    df["gold_standard_status"] = (
        df["gold_standard_status"].str.strip().str.upper() if has_gold_status_col else ""
    )

    df["ann1_topics_list"] = df["ann1_topics"].apply(parse_topics_cell)
    df["ann2_topics_list"] = df["ann2_topics"].apply(parse_topics_cell)
    df["final_topics_list"] = df["final_topics"].apply(parse_topics_cell)

    n_agreed = 0
    n_adjudicated = 0
    n_pending_adjudication = 0
    n_missing_labels = 0
    n_invalid_format = 0
    invalid_rows: List[Dict[str, str]] = []
    adjudicator_ids: set[str] = set()

    for _, row in df.iterrows():
        rid = row["review_id"]
        text = row["review_text"]
        s1 = row["ann1_sentiment"]
        s2 = row["ann2_sentiment"]
        t1 = row["ann1_topics_list"]
        t2 = row["ann2_topics_list"]
        fs = row["final_sentiment"]
        ft = row["final_topics_list"]
        adj_status = row["adjudication_status"]
        adj_id = row["adjudicator_id"]
        gold_status = row["gold_standard_status"]

        row_reasons: List[str] = []
        missing_flag = False
        invalid_flag = False

        if not text:
            row_reasons.append("review_text rỗng")
            invalid_flag = True

        for label_name, s_val in [("ann1_sentiment", s1), ("ann2_sentiment", s2)]:
            if s_val not in SENTIMENT_LABELS:
                row_reasons.append(f"{label_name} không hợp lệ: {s_val!r}")
                invalid_flag = True

        for col_name, t_val in [("ann1_topics", t1), ("ann2_topics", t2)]:
            if not t_val:
                row_reasons.append(f"thiếu {col_name}")
                missing_flag = True
            else:
                bad = [t for t in t_val if t not in VALID_TOPIC_SET]
                if bad:
                    row_reasons.append(f"{col_name} không hợp lệ: {bad}")
                    invalid_flag = True

        if adj_status not in VALID_ADJUDICATION_STATUSES:
            row_reasons.append(f"adjudication_status không hợp lệ: {adj_status!r}")
            invalid_flag = True

        if gold_status not in VALID_GOLD_CONFIRMATION_STATUSES:
            row_reasons.append(f"gold_standard_status không hợp lệ: {gold_status!r}")
            invalid_flag = True

        if row_reasons:
            if missing_flag:
                n_missing_labels += 1
            if invalid_flag:
                n_invalid_format += 1
            invalid_rows.append({"review_id": rid, "reason": "; ".join(row_reasons)})
            continue

        agreed = (s1 == s2) and (sorted(t1) == sorted(t2))
        if agreed:
            if adj_status != "AGREED":
                n_invalid_format += 1
                invalid_rows.append({
                    "review_id": rid,
                    "reason": f"dòng đồng thuận phải có adjudication_status='AGREED', nhận {adj_status!r}",
                })
                continue
            if fs != s1 or sorted(ft) != sorted(t1):
                n_invalid_format += 1
                invalid_rows.append({
                    "review_id": rid,
                    "reason": "dòng đồng thuận không giữ nguyên nhãn của 2 annotator",
                })
                continue
            n_agreed += 1
            continue

        # Bất đồng giữa ann1 và ann2
        if adj_status == "PENDING_ADJUDICATION":
            n_pending_adjudication += 1
            continue

        if adj_status != "ADJUDICATED":
            n_invalid_format += 1
            invalid_rows.append({
                "review_id": rid,
                "reason": f"dòng bất đồng không thể có adjudication_status={adj_status!r}",
            })
            continue

        # adj_status == "ADJUDICATED"
        adj_reasons: List[str] = []
        if not fs:
            adj_reasons.append("thiếu final_sentiment")
            missing_flag = True
        elif fs not in SENTIMENT_LABELS:
            adj_reasons.append(f"final_sentiment không hợp lệ: {fs!r}")
            invalid_flag = True

        if not ft:
            adj_reasons.append("thiếu final_topics")
            missing_flag = True
        else:
            bad_ft = [t for t in ft if t not in VALID_TOPIC_SET]
            if bad_ft:
                adj_reasons.append(f"final_topics không hợp lệ: {bad_ft}")
                invalid_flag = True

        if not adj_id:
            adj_reasons.append("thiếu adjudicator_id cho dòng ADJUDICATED")
            missing_flag = True

        if adj_reasons:
            if missing_flag:
                n_missing_labels += 1
            if invalid_flag:
                n_invalid_format += 1
            invalid_rows.append({"review_id": rid, "reason": "; ".join(adj_reasons)})
            continue

        adjudicator_ids.add(adj_id)
        n_adjudicated += 1

    is_gold_confirmed = (
        has_gold_status_col
        and len(df) > 0
        and len(invalid_rows) == 0
        and n_pending_adjudication == 0
        and bool((df["gold_standard_status"] == "CONFIRMED").all())
    )

    report = {
        "n_total": int(len(df)),
        "n_valid_done": int(n_agreed + n_adjudicated),
        "n_pending": int(n_pending_adjudication),
        "n_agreed": int(n_agreed),
        "n_adjudicated": int(n_adjudicated),
        "n_pending_adjudication": int(n_pending_adjudication),
        "n_missing_labels": int(n_missing_labels),
        "n_invalid_format": int(n_invalid_format),
        "invalid_rows": invalid_rows,
        "adjudicator_ids": sorted(adjudicator_ids),
        "is_gold_confirmed": bool(is_gold_confirmed),
    }
    return df, report


def confirm_gold_standard(
    disagreements_path: Path,
    output_path: Optional[Path] = None,
    adjudicator_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Xác nhận chính thức file Gold Standard sau khi đã hoàn tất adjudication.
    Không tự động hoàn thành các dòng còn PENDING_ADJUDICATION.
    """
    df = pd.read_csv(disagreements_path, dtype=str).fillna("")
    if adjudicator_id is not None and adjudicator_id.strip():
        adj_clean = adjudicator_id.strip()
        if "adjudicator_id" not in df.columns:
            df["adjudicator_id"] = ""
        mask_adj = df["adjudication_status"].str.strip().str.upper() == "ADJUDICATED"
        mask_empty = df["adjudicator_id"].str.strip() == ""
        df.loc[mask_adj & mask_empty, "adjudicator_id"] = adj_clean

    target_path = output_path or disagreements_path
    df["gold_standard_status"] = "CONFIRMED"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(target_path, index=False, encoding="utf-8-sig")

    _, report = inspect_gold_standard_file(target_path)
    if report["invalid_rows"]:
        raise ValueError(
            f"File Gold Standard có lỗi: {report['invalid_rows'][:5]}"
        )
    if report["n_pending_adjudication"] > 0:
        raise ValueError(
            f"Không thể xác nhận Gold Standard khi còn {report['n_pending_adjudication']} dòng PENDING_ADJUDICATION."
        )
    return report


def _compute_nlp_metrics(
    eval_df: pd.DataFrame,
    sentiment_col: str,
    topics_col: str,
) -> Dict[str, Any]:
    """Tính metric đánh giá sentiment_from_text và extract_topics (chỉ dùng review_text)."""
    work_df = eval_df.copy()
    work_df["pred_sentiment_text"] = work_df["review_text"].apply(
        lambda t: sentiment_from_text(t) or "NEUTRAL"
    )
    work_df["pred_topics"] = work_df["review_text"].apply(extract_topics)

    y_true_s = work_df[sentiment_col].tolist()
    y_pred_s = work_df["pred_sentiment_text"].tolist()
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
            "coverage_pct": round(100.0 * int(sup_s[i]) / len(work_df), 2),
        }
        for i, lbl in enumerate(SENTIMENT_LABELS)
    }

    mlb = MultiLabelBinarizer(classes=ALL_TOPICS)
    y_true_t = mlb.fit_transform(work_df[topics_col])
    y_pred_t = mlb.transform(work_df["pred_topics"])

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
            "coverage_pct": round(100.0 * int(sup_t[i]) / len(work_df), 2),
        }
        for i, lbl in enumerate(ALL_TOPICS)
    }

    sent_errors = work_df[work_df[sentiment_col] != work_df["pred_sentiment_text"]]
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
            (sent_errors[topics_col].apply(len) > 1).sum()
        ),
        "sample_errors": [
            {
                "review_id": r["review_id"],
                "human": r[sentiment_col],
                "pred": r["pred_sentiment_text"],
                "text": r["review_text"][:120],
            }
            for _, r in sent_errors.head(10).iterrows()
        ],
    }

    return {
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
    }


def evaluate_ground_truth(
    csv_path: Path = DEFAULT_INPUT,
    annotator2_path: Optional[Path] = None,
    disagreements_output: Optional[Path] = None,
    gold_standard_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Chạy đánh giá NLP trên tập Human Ground Truth với phân biệt rõ ràng các trạng thái:
      - NOT YET EVALUABLE: chưa có nhãn thủ công DONE hoặc chưa có file.
      - INVALID_ANNOTATIONS: sai định dạng, thiếu nhãn, thiếu annotator_id, review_text rỗng, hoặc không nhất quán.
      - PRELIMINARY_EVALUATION: mới chỉ có annotator đơn lẻ (hoặc 2 annotator chưa qua xác nhận Gold Standard).
      - AWAITING_ADJUDICATION: có 2 annotator còn bất đồng/chưa hoàn tất, hoặc Gold Standard còn PENDING_ADJUDICATION/chưa xác nhận.
      - FINAL_EVALUATED: chỉ khi toàn bộ tập Gold Standard đã được xác nhận đầy đủ.
    """
    # Nhánh 1: Đánh giá Gold Standard chính thức
    if gold_standard_path is not None:
        if not gold_standard_path.exists():
            return {
                "status": STATUS_NOT_YET_EVALUABLE,
                "is_official_result": False,
                "reason": f"Không tìm thấy file Gold Standard {gold_standard_path}",
                "quality_report": _empty_quality_report(),
                "n_total": 0,
                "n_annotated": 0,
            }

        try:
            gold_df, gold_report = inspect_gold_standard_file(gold_standard_path)
        except ValueError as exc:
            return {
                "status": STATUS_INVALID_ANNOTATIONS,
                "is_official_result": False,
                "reason": str(exc),
                "quality_report": _empty_quality_report(),
                "n_total": 0,
                "n_annotated": 0,
            }

        if gold_report["invalid_rows"]:
            return {
                "status": STATUS_INVALID_ANNOTATIONS,
                "is_official_result": False,
                "reason": (
                    f"File Gold Standard có {len(gold_report['invalid_rows'])} dòng lỗi. "
                    "Không chấp nhận file có lỗi để công bố F1 chính thức."
                ),
                "quality_report": gold_report,
                "n_total": gold_report["n_total"],
                "n_annotated": gold_report["n_valid_done"],
            }

        if gold_report["n_total"] == 0:
            return {
                "status": STATUS_NOT_YET_EVALUABLE,
                "is_official_result": False,
                "reason": "File Gold Standard rỗng.",
                "quality_report": gold_report,
                "n_total": 0,
                "n_annotated": 0,
            }

        if gold_report["n_pending_adjudication"] > 0 or not gold_report["is_gold_confirmed"]:
            reasons: List[str] = []
            if gold_report["n_pending_adjudication"] > 0:
                reasons.append(
                    f"còn {gold_report['n_pending_adjudication']} dòng PENDING_ADJUDICATION chưa được xử lý"
                )
            if not gold_report["is_gold_confirmed"]:
                reasons.append(
                    "chưa có xác nhận cuối gold_standard_status='CONFIRMED' trên toàn bộ tập"
                )
            return {
                "status": STATUS_AWAITING_ADJUDICATION,
                "is_official_result": False,
                "reason": "Chưa đủ điều kiện công bố FINAL_EVALUATED: " + "; ".join(reasons),
                "quality_report": gold_report,
                "n_total": gold_report["n_total"],
                "n_annotated": gold_report["n_valid_done"],
                "is_full_dataset_done": False,
            }

        metrics = _compute_nlp_metrics(
            gold_df,
            sentiment_col="final_sentiment",
            topics_col="final_topics_list",
        )
        return {
            "status": STATUS_FINAL_EVALUATED,
            "is_official_result": True,
            "evaluated_target": "sentiment_from_text & extract_topics (text-only, without rating)",
            "quality_report": gold_report,
            "n_total": gold_report["n_total"],
            "n_annotated": gold_report["n_valid_done"],
            "is_full_dataset_done": True,
            "adjudicator_ids": gold_report["adjudicator_ids"],
            "sentiment": metrics["sentiment"],
            "topics": metrics["topics"],
            "error_analysis": metrics["error_analysis"],
            "inter_annotator_agreement": None,
        }

    # Nhánh 2 & 3: Single annotator hoặc Dual annotator
    if not csv_path.exists():
        return {
            "status": STATUS_NOT_YET_EVALUABLE,
            "is_official_result": False,
            "reason": f"Không tìm thấy file {csv_path}",
            "quality_report": _empty_quality_report(),
            "n_total": 0,
            "n_annotated": 0,
        }

    try:
        full_df, quality_report = inspect_annotation_file(csv_path)
    except ValueError as exc:
        return {
            "status": STATUS_INVALID_ANNOTATIONS,
            "is_official_result": False,
            "reason": str(exc),
            "quality_report": _empty_quality_report(),
            "n_total": 0,
            "n_annotated": 0,
        }

    if quality_report["invalid_rows"]:
        return {
            "status": STATUS_INVALID_ANNOTATIONS,
            "is_official_result": False,
            "reason": (
                f"Phát hiện {len(quality_report['invalid_rows'])} dòng sai định dạng hoặc thiếu nhãn. "
                "Không thể công bố metric trước khi sửa các dòng lỗi."
            ),
            "quality_report": quality_report,
            "n_total": quality_report["n_total"],
            "n_annotated": quality_report["n_valid_done"],
        }

    annotated_df = full_df[full_df["is_valid_done"]].copy()

    # Nhánh 2: Có annotator thứ 2
    if annotator2_path is not None:
        if not annotator2_path.exists():
            return {
                "status": STATUS_NOT_YET_EVALUABLE,
                "is_official_result": False,
                "reason": f"Không tìm thấy file annotator thứ 2: {annotator2_path}",
                "quality_report": quality_report,
                "n_total": quality_report["n_total"],
                "n_annotated": quality_report["n_valid_done"],
            }

        try:
            full_df2, quality_report2 = inspect_annotation_file(annotator2_path)
            if quality_report2["invalid_rows"]:
                return {
                    "status": STATUS_INVALID_ANNOTATIONS,
                    "is_official_result": False,
                    "reason": (
                        f"File annotator 2 có {len(quality_report2['invalid_rows'])} dòng lỗi định dạng/thiếu nhãn."
                    ),
                    "quality_report": quality_report2,
                    "n_total": quality_report2["n_total"],
                    "n_annotated": quality_report2["n_valid_done"],
                }
            _validate_two_annotators_consistency(full_df, full_df2)
        except ValueError as exc:
            return {
                "status": STATUS_INVALID_ANNOTATIONS,
                "is_official_result": False,
                "reason": str(exc),
                "quality_report": quality_report,
                "n_total": quality_report["n_total"],
                "n_annotated": quality_report["n_valid_done"],
            }

        annotated_df2 = full_df2[full_df2["is_valid_done"]].copy()
        if annotated_df.empty and annotated_df2.empty:
            return {
                "status": STATUS_NOT_YET_EVALUABLE,
                "is_official_result": False,
                "reason": "Cả hai annotator đều chưa có dòng annotation_status='DONE' hợp lệ.",
                "quality_report": quality_report,
                "n_total": quality_report["n_total"],
                "n_annotated": 0,
            }

        iaa: Optional[Dict[str, Any]] = None
        if not annotated_df.empty and not annotated_df2.empty:
            try:
                iaa = compute_inter_annotator_agreement(
                    ann1_path=csv_path,
                    ann2_path=annotator2_path,
                    disagreements_output=disagreements_output,
                )
            except ValueError as exc:
                return {
                    "status": STATUS_INVALID_ANNOTATIONS,
                    "is_official_result": False,
                    "reason": str(exc),
                    "quality_report": quality_report,
                    "n_total": quality_report["n_total"],
                    "n_annotated": quality_report["n_valid_done"],
                }

        incomplete_dual = (
            quality_report["n_pending"] > 0
            or quality_report2["n_pending"] > 0
            or iaa is None
            or iaa["n_both_done"] < quality_report["n_total"]
        )
        has_disagreements = iaa is not None and iaa["n_disagreements"] > 0

        if has_disagreements or incomplete_dual:
            status = STATUS_AWAITING_ADJUDICATION
            reason = (
                f"Có {iaa['n_disagreements'] if iaa else 0} bất đồng cần adjudication "
                f"(ann1 pending={quality_report['n_pending']}, ann2 pending={quality_report2['n_pending']}). "
                "Chưa thể công bố kết quả chính thức."
            )
        else:
            status = STATUS_PRELIMINARY
            reason = (
                "Hai annotator đã hoàn thành và không có bất đồng, nhưng chưa qua bước xác nhận Gold Standard chính thức."
            )

        metrics = (
            _compute_nlp_metrics(
                annotated_df,
                sentiment_col="human_sentiment",
                topics_col="human_topics_list",
            )
            if not annotated_df.empty
            else None
        )

        result: Dict[str, Any] = {
            "status": status,
            "is_official_result": False,
            "reason": reason,
            "evaluated_target": "sentiment_from_text & extract_topics (text-only, without rating)",
            "quality_report": quality_report,
            "annotator2_quality_report": quality_report2,
            "n_total": quality_report["n_total"],
            "n_annotated": quality_report["n_valid_done"],
            "is_full_dataset_done": (
                quality_report["n_pending"] == 0 and quality_report2["n_pending"] == 0
            ),
            "inter_annotator_agreement": iaa,
        }
        if metrics is not None:
            result["sentiment"] = metrics["sentiment"]
            result["topics"] = metrics["topics"]
            result["error_analysis"] = metrics["error_analysis"]
        return result

    # Nhánh 3: Chỉ có 1 annotator (ví dụ ANN_01)
    if annotated_df.empty:
        return {
            "status": STATUS_NOT_YET_EVALUABLE,
            "is_official_result": False,
            "reason": "Chưa có dòng nào có annotation_status='DONE' với nhãn thủ công hợp lệ.",
            "quality_report": quality_report,
            "n_total": quality_report["n_total"],
            "n_annotated": 0,
        }

    metrics = _compute_nlp_metrics(
        annotated_df,
        sentiment_col="human_sentiment",
        topics_col="human_topics_list",
    )
    return {
        "status": STATUS_PRELIMINARY,
        "is_official_result": False,
        "reason": "Đánh giá sơ bộ trên 1 annotator; chưa có Dual Annotation & Gold Standard xác nhận.",
        "evaluated_target": "sentiment_from_text & extract_topics (text-only, without rating)",
        "quality_report": quality_report,
        "n_total": quality_report["n_total"],
        "n_annotated": quality_report["n_valid_done"],
        "is_full_dataset_done": quality_report["n_pending"] == 0,
        "sentiment": metrics["sentiment"],
        "topics": metrics["topics"],
        "error_analysis": metrics["error_analysis"],
        "inter_annotator_agreement": None,
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
    parser.add_argument(
        "--gold-standard",
        type=Path,
        default=None,
        help="Đường dẫn file Gold Standard đã được adjudicate và xác nhận cuối",
    )
    args = parser.parse_args()

    res = evaluate_ground_truth(
        csv_path=args.input,
        annotator2_path=args.annotator2,
        disagreements_output=args.export_disagreements,
        gold_standard_path=args.gold_standard,
    )
    print(f"STATUS: {res['status']}")
    print(f"OFFICIAL_RESULT: {res.get('is_official_result', False)}")
    qr = res["quality_report"]
    print(
        f"Quality: total={qr['n_total']} | valid_done={qr['n_valid_done']} | "
        f"pending={qr['n_pending']} | missing={qr['n_missing_labels']} | invalid={qr['n_invalid_format']}"
    )

    if res.get("reason"):
        print(f"Note: {res['reason']}")

    if res["status"] in (STATUS_NOT_YET_EVALUABLE, STATUS_INVALID_ANNOTATIONS):
        if qr["invalid_rows"]:
            print("Invalid rows sample:", qr["invalid_rows"][:5])
        return

    if "sentiment" not in res:
        return

    if not res.get("is_official_result", False):
        print("[PRELIMINARY METRICS ONLY — KHÔNG PHẢI KẾT QUẢ GOLD STANDARD CHÍNH THỨC]")

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

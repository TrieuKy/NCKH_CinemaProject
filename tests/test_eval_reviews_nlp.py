"""
tests/test_eval_reviews_nlp.py
------------------------------
Unit tests cho scripts/eval_reviews_nlp.py và scripts/export_review_annotation_template.py.
"""

from pathlib import Path
import pandas as pd
import pytest

from scripts.eval_reviews_nlp import evaluate_ground_truth, parse_topics_cell


def test_parse_topics_cell():
    assert parse_topics_cell("Vệ sinh;Thái độ phục vụ") == ["Vệ sinh", "Thái độ phục vụ"]
    assert parse_topics_cell("Giá cả & Vé, Khác") == ["Giá cả & Vé", "Khác"]
    assert parse_topics_cell("") == []
    assert parse_topics_cell(None) == []


def test_template_has_no_rating_or_nlp_leakage():
    template_path = Path("data/review_ground_truth_template.csv")
    if not template_path.exists():
        pytest.skip("Chưa xuất data/review_ground_truth_template.csv")
    df = pd.read_csv(template_path, dtype=str).fillna("")
    expected_cols = [
        "review_id",
        "review_text",
        "human_sentiment",
        "human_topics",
        "annotator_id",
        "annotation_status",
    ]
    assert list(df.columns) == expected_cols
    assert 1 <= len(df) <= 165
    assert "rating" not in df.columns
    assert (df["human_sentiment"] == "").all()
    assert (df["human_topics"] == "").all()
    assert (df["annotation_status"] == "PENDING").all()


def test_eval_returns_not_yet_evaluable_when_unannotated(tmp_path):
    csv_path = tmp_path / "unannotated.csv"
    pd.DataFrame([
        {
            "review_id": "r1",
            "review_text": "Rạp đẹp lắm",
            "human_sentiment": "",
            "human_topics": "",
            "annotator_id": "",
            "annotation_status": "PENDING",
        }
    ]).to_csv(csv_path, index=False)

    res = evaluate_ground_truth(csv_path=csv_path)
    assert res["status"] == "NOT YET EVALUABLE"
    assert res["n_annotated"] == 0
    assert "sentiment" not in res


def test_eval_computes_metrics_and_cohens_kappa(tmp_path):
    ann1 = tmp_path / "ann1.csv"
    ann2 = tmp_path / "ann2.csv"

    rows_1 = [
        {
            "review_id": "r1",
            "review_text": "Nhân viên thân thiện, phòng chiếu âm thanh tuyệt vời",
            "human_sentiment": "POSITIVE",
            "human_topics": "Thái độ phục vụ;Phòng chiếu & Âm thanh",
            "annotator_id": "ANN_01",
            "annotation_status": "DONE",
        },
        {
            "review_id": "r2",
            "review_text": "Nhà vệ sinh dơ bẩn, cố úp sale không hài lòng",
            "human_sentiment": "NEGATIVE",
            "human_topics": "Vệ sinh;Thái độ phục vụ",
            "annotator_id": "ANN_01",
            "annotation_status": "DONE",
        },
        {
            "review_id": "r3",
            "review_text": "Hôm nay đi xem phim suất 10h30",
            "human_sentiment": "NEUTRAL",
            "human_topics": "Khác",
            "annotator_id": "ANN_01",
            "annotation_status": "DONE",
        },
    ]
    rows_2 = [
        {**rows_1[0], "annotator_id": "ANN_02"},
        {**rows_1[1], "annotator_id": "ANN_02"},
        {**rows_1[2], "annotator_id": "ANN_02", "human_sentiment": "POSITIVE"},
    ]
    pd.DataFrame(rows_1).to_csv(ann1, index=False)
    pd.DataFrame(rows_2).to_csv(ann2, index=False)

    res = evaluate_ground_truth(csv_path=ann1, annotator2_path=ann2)
    assert res["status"] == "EVALUATED"
    assert res["n_annotated"] == 3
    assert len(res["sentiment"]["confusion_matrix"]) == 3
    assert 0.0 <= res["sentiment"]["macro_f1"] <= 1.0
    assert 0.0 <= res["sentiment"]["weighted_f1"] <= 1.0
    assert 0.0 <= res["topics"]["micro_f1"] <= 1.0
    assert 0.0 <= res["topics"]["macro_f1"] <= 1.0
    assert res["inter_annotator_agreement"] is not None
    assert res["inter_annotator_agreement"]["n_overlap"] == 3
    assert -1.0 <= res["inter_annotator_agreement"]["sentiment_cohens_kappa"] <= 1.0

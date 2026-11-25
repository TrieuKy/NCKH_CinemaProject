"""
tests/test_eval_reviews_nlp.py
------------------------------
Unit tests cho scripts/eval_reviews_nlp.py và scripts/export_review_annotation_template.py.
Lưu ý: các test dưới đây dùng dữ liệu synthetic để kiểm thử chức năng,
KHÔNG phải kết quả đánh giá trên Human Ground Truth thật.
"""

from pathlib import Path
import pandas as pd
import pytest

from scripts.eval_reviews_nlp import (
    ALL_TOPICS,
    compute_inter_annotator_agreement,
    evaluate_ground_truth,
    inspect_annotation_file,
    parse_topics_cell,
)


def _write_csv(path: Path, rows: list) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


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
    _write_csv(csv_path, [
        {
            "review_id": "r1",
            "review_text": "Rạp đẹp lắm",
            "human_sentiment": "",
            "human_topics": "",
            "annotator_id": "",
            "annotation_status": "PENDING",
        }
    ])

    res = evaluate_ground_truth(csv_path=csv_path)
    assert res["status"] == "NOT YET EVALUABLE"
    assert res["n_annotated"] == 0
    assert res["quality_report"]["n_pending"] == 1
    assert "sentiment" not in res


def test_rejects_invalid_topic(tmp_path):
    csv_path = tmp_path / "bad_topic.csv"
    _write_csv(csv_path, [
        {
            "review_id": "r1",
            "review_text": "Ghế ngồi êm",
            "human_sentiment": "POSITIVE",
            "human_topics": "Chủ đề không tồn tại",
            "annotator_id": "ANN_01",
            "annotation_status": "DONE",
        }
    ])

    res = evaluate_ground_truth(csv_path=csv_path)
    assert res["status"] == "INVALID_ANNOTATIONS"
    assert res["quality_report"]["n_invalid_format"] == 1
    assert "sentiment" not in res


def test_rejects_invalid_annotation_status(tmp_path):
    csv_path = tmp_path / "bad_status.csv"
    _write_csv(csv_path, [
        {
            "review_id": "r1",
            "review_text": "Ghế ngồi êm",
            "human_sentiment": "POSITIVE",
            "human_topics": "Phòng chiếu & Âm thanh",
            "annotator_id": "ANN_01",
            "annotation_status": "COMPLETED",  # sai, chỉ chấp nhận DONE hoặc PENDING
        }
    ])

    res = evaluate_ground_truth(csv_path=csv_path)
    assert res["status"] == "INVALID_ANNOTATIONS"
    assert res["quality_report"]["n_invalid_format"] == 1


def test_rejects_missing_labels_on_done_row(tmp_path):
    csv_path = tmp_path / "missing_label.csv"
    _write_csv(csv_path, [
        {
            "review_id": "r1",
            "review_text": "Rạp đẹp",
            "human_sentiment": "POSITIVE",
            "human_topics": "",
            "annotator_id": "ANN_01",
            "annotation_status": "DONE",
        }
    ])

    res = evaluate_ground_truth(csv_path=csv_path)
    assert res["status"] == "INVALID_ANNOTATIONS"
    assert res["quality_report"]["n_missing_labels"] == 1


def test_rejects_duplicate_review_id(tmp_path):
    csv_path = tmp_path / "dup_id.csv"
    row = {
        "review_id": "r_dup",
        "review_text": "Rạp đẹp",
        "human_sentiment": "POSITIVE",
        "human_topics": "Khác",
        "annotator_id": "ANN_01",
        "annotation_status": "DONE",
    }
    _write_csv(csv_path, [row, row])

    with pytest.raises(ValueError, match="trùng lặp"):
        inspect_annotation_file(csv_path)


def test_rejects_same_annotator_id(tmp_path):
    ann1 = tmp_path / "ann1.csv"
    ann2 = tmp_path / "ann2.csv"
    rows = [
        {
            "review_id": "r1",
            "review_text": "Rạp đẹp",
            "human_sentiment": "POSITIVE",
            "human_topics": "Khác",
            "annotator_id": "ANN_01",
            "annotation_status": "DONE",
        },
        {
            "review_id": "r2",
            "review_text": "Rạp dơ",
            "human_sentiment": "NEGATIVE",
            "human_topics": "Vệ sinh",
            "annotator_id": "ANN_01",
            "annotation_status": "DONE",
        },
    ]
    _write_csv(ann1, rows)
    _write_csv(ann2, rows)

    with pytest.raises(ValueError, match="cùng annotator_id"):
        compute_inter_annotator_agreement(ann1, ann2)


def test_iaa_topic_kappa_and_disagreement_export(tmp_path):
    ann1 = tmp_path / "ann1.csv"
    ann2 = tmp_path / "ann2.csv"
    dis_csv = tmp_path / "review_annotation_disagreements.csv"

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
        {
            **rows_1[2],
            "annotator_id": "ANN_02",
            "human_sentiment": "POSITIVE",  # bất đồng với ANN_01
        },
    ]
    _write_csv(ann1, rows_1)
    _write_csv(ann2, rows_2)

    res = evaluate_ground_truth(
        csv_path=ann1,
        annotator2_path=ann2,
        disagreements_output=dis_csv,
    )
    assert res["status"] == "EVALUATED"
    iaa = res["inter_annotator_agreement"]
    assert iaa["n_both_done"] == 3
    assert iaa["n_disagreements"] == 1
    assert iaa["adjudication_review_ids"] == ["r3"]
    assert -1.0 <= iaa["sentiment_cohens_kappa"] <= 1.0
    assert set(iaa["topic_cohens_kappa"].keys()) == set(ALL_TOPICS)
    # Các topic có mặt đồng thuận hoàn toàn -> kappa = 1.0; topic không xuất hiện ở cả 2 -> None
    assert iaa["topic_cohens_kappa"]["Vệ sinh"] == 1.0
    assert iaa["topic_cohens_kappa"]["Giá cả & Vé"] is None

    # Kiểm tra file bất đồng không tự chọn nhãn của ANN_01 khi bất đồng
    dis_df = pd.read_csv(dis_csv, dtype=str).fillna("")
    expected_cols = [
        "review_id",
        "review_text",
        "ann1_sentiment",
        "ann2_sentiment",
        "ann1_topics",
        "ann2_topics",
        "final_sentiment",
        "final_topics",
        "adjudication_status",
    ]
    assert list(dis_df.columns) == expected_cols
    r1_row = dis_df[dis_df["review_id"] == "r1"].iloc[0]
    r3_row = dis_df[dis_df["review_id"] == "r3"].iloc[0]
    assert r1_row["adjudication_status"] == "AGREED"
    assert r1_row["final_sentiment"] == "POSITIVE"
    assert r3_row["adjudication_status"] == "PENDING_ADJUDICATION"
    assert r3_row["final_sentiment"] == ""
    assert r3_row["final_topics"] == ""

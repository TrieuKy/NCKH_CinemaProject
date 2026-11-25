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
    STATUS_AWAITING_ADJUDICATION,
    STATUS_FINAL_EVALUATED,
    STATUS_INVALID_ANNOTATIONS,
    STATUS_NOT_YET_EVALUABLE,
    STATUS_PRELIMINARY,
    compute_inter_annotator_agreement,
    confirm_gold_standard,
    evaluate_ground_truth,
    inspect_annotation_file,
    inspect_gold_standard_file,
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
    assert res["status"] == STATUS_AWAITING_ADJUDICATION
    assert res.get("is_official_result") is False
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


# ============================================================
# TASK 8.1 — NEW TESTS (A–D)
# ============================================================

# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

VALID_ROW_ANN1 = {
    "review_id": "r1",
    "review_text": "Phòng chiếu rất đẹp và âm thanh tuyệt vời",
    "human_sentiment": "POSITIVE",
    "human_topics": "Phòng chiếu & Âm thanh",
    "annotator_id": "ANN_01",
    "annotation_status": "DONE",
}

VALID_ROW_ANN2_AGREE = {**VALID_ROW_ANN1, "annotator_id": "ANN_02"}

VALID_ROW_ANN2_DISAGREE = {
    **VALID_ROW_ANN1,
    "annotator_id": "ANN_02",
    "human_sentiment": "NEUTRAL",   # bất đồng với ANN_01
}


def _make_gold_row(
    review_id: str,
    review_text: str,
    ann1_s: str,
    ann2_s: str,
    ann1_t: str,
    ann2_t: str,
    final_s: str,
    final_t: str,
    adj_status: str,
    adjudicator_id: str = "",
    gold_status: str = "",
) -> dict:
    return {
        "review_id": review_id,
        "review_text": review_text,
        "ann1_sentiment": ann1_s,
        "ann2_sentiment": ann2_s,
        "ann1_topics": ann1_t,
        "ann2_topics": ann2_t,
        "final_sentiment": final_s,
        "final_topics": final_t,
        "adjudication_status": adj_status,
        "adjudicator_id": adjudicator_id,
        "gold_standard_status": gold_status,
    }


# ----------------------------------------------------------------
# D-1: ANN_01 chưa phải Gold Standard → PRELIMINARY_EVALUATION
# ----------------------------------------------------------------

def test_ann01_only_returns_preliminary_evaluation(tmp_path):
    """
    File chỉ có ANN_01, chưa qua Dual Annotation và Gold Standard xác nhận
    → trả PRELIMINARY_EVALUATION (không phải FINAL_EVALUATED hay EVALUATED cũ).
    """
    csv = tmp_path / "ann1_only.csv"
    _write_csv(csv, [VALID_ROW_ANN1])
    res = evaluate_ground_truth(csv_path=csv)
    assert res["status"] == STATUS_PRELIMINARY, (
        f"Mong PRELIMINARY_EVALUATION, nhận {res['status']!r}"
    )
    assert res.get("is_official_result") is False
    assert "sentiment" in res, "Có thể tính metric sơ bộ nhưng phải đánh dấu không chính thức"


# ----------------------------------------------------------------
# D-2: Hai annotator bất đồng → AWAITING_ADJUDICATION
# ----------------------------------------------------------------

def test_two_annotators_disagreement_returns_awaiting_adjudication(tmp_path):
    """
    Hai annotator đã DONE nhưng bất đồng sentiment
    → trả AWAITING_ADJUDICATION, is_official_result=False.
    """
    ann1 = tmp_path / "ann1.csv"
    ann2 = tmp_path / "ann2.csv"
    dis = tmp_path / "dis.csv"
    _write_csv(ann1, [VALID_ROW_ANN1])
    _write_csv(ann2, [VALID_ROW_ANN2_DISAGREE])

    res = evaluate_ground_truth(csv_path=ann1, annotator2_path=ann2, disagreements_output=dis)
    assert res["status"] == STATUS_AWAITING_ADJUDICATION, (
        f"Mong AWAITING_ADJUDICATION khi có bất đồng, nhận {res['status']!r}"
    )
    assert res.get("is_official_result") is False
    iaa = res["inter_annotator_agreement"]
    assert iaa is not None
    assert iaa["n_disagreements"] == 1


# ----------------------------------------------------------------
# D-3: Gold Standard còn PENDING_ADJUDICATION → AWAITING_ADJUDICATION
# ----------------------------------------------------------------

def test_gold_standard_pending_adjudication_returns_awaiting(tmp_path):
    """
    File Gold Standard còn dòng PENDING_ADJUDICATION chưa được xử lý
    → trả AWAITING_ADJUDICATION, is_official_result=False.
    Hệ thống KHÔNG tự động hoàn thành dòng đó.
    """
    gold = tmp_path / "gold.csv"
    rows = [
        _make_gold_row(
            "r1", "Rạp đẹp lắm",
            "POSITIVE", "POSITIVE", "Khác", "Khác",
            "POSITIVE", "Khác",
            "AGREED", gold_status="CONFIRMED",
        ),
        _make_gold_row(
            "r2", "Nhân viên không thân thiện",
            "NEGATIVE", "NEUTRAL", "Thái độ phục vụ", "Thái độ phục vụ",
            "", "",  # chưa adjudicate
            "PENDING_ADJUDICATION", gold_status="",
        ),
    ]
    _write_csv(gold, rows)

    res = evaluate_ground_truth(gold_standard_path=gold)
    assert res["status"] == STATUS_AWAITING_ADJUDICATION, (
        f"Mong AWAITING_ADJUDICATION khi còn PENDING, nhận {res['status']!r}"
    )
    assert res.get("is_official_result") is False


# ----------------------------------------------------------------
# D-4: Gold Standard được xác nhận đầy đủ → FINAL_EVALUATED
# ----------------------------------------------------------------

def test_fully_confirmed_gold_standard_returns_final_evaluated(tmp_path):
    """
    File Gold Standard đã ADJUDICATED hết, tất cả gold_standard_status='CONFIRMED'
    → trả FINAL_EVALUATED, is_official_result=True.
    """
    gold = tmp_path / "gold_confirmed.csv"
    rows = [
        _make_gold_row(
            "r1", "Phòng chiếu rất đẹp và âm thanh tuyệt vời",
            "POSITIVE", "POSITIVE", "Phòng chiếu & Âm thanh", "Phòng chiếu & Âm thanh",
            "POSITIVE", "Phòng chiếu & Âm thanh",
            "AGREED", gold_status="CONFIRMED",
        ),
        _make_gold_row(
            "r2", "Nhà vệ sinh bẩn thỉu không chấp nhận được",
            "NEGATIVE", "NEUTRAL", "Vệ sinh", "Vệ sinh",
            "NEGATIVE", "Vệ sinh",
            "ADJUDICATED", adjudicator_id="ADJ_01", gold_status="CONFIRMED",
        ),
    ]
    _write_csv(gold, rows)

    res = evaluate_ground_truth(gold_standard_path=gold)
    assert res["status"] == STATUS_FINAL_EVALUATED, (
        f"Mong FINAL_EVALUATED sau khi Gold Standard xác nhận đầy đủ, nhận {res['status']!r}"
    )
    assert res.get("is_official_result") is True
    assert "sentiment" in res
    assert "topics" in res
    assert res["n_annotated"] == 2


# ----------------------------------------------------------------
# D-5: Dòng DONE thiếu annotator_id → INVALID_ANNOTATIONS
# ----------------------------------------------------------------

def test_done_row_missing_annotator_id_is_rejected(tmp_path):
    """
    Dòng annotation_status='DONE' mà annotator_id rỗng
    → trả INVALID_ANNOTATIONS, không công bố metric.
    """
    csv = tmp_path / "no_ann_id.csv"
    row = {**VALID_ROW_ANN1, "annotator_id": ""}  # thiếu annotator_id
    _write_csv(csv, [row])

    res = evaluate_ground_truth(csv_path=csv)
    assert res["status"] == STATUS_INVALID_ANNOTATIONS, (
        f"Mong INVALID_ANNOTATIONS khi thiếu annotator_id, nhận {res['status']!r}"
    )
    assert "sentiment" not in res


# ----------------------------------------------------------------
# D-6: review_text không khớp giữa hai annotator → INVALID_ANNOTATIONS
# ----------------------------------------------------------------

def test_mismatched_review_text_between_annotators_is_rejected(tmp_path):
    """
    Hai file annotator có cùng review_id nhưng review_text khác nhau
    → trả INVALID_ANNOTATIONS (tính nhất quán bị vi phạm).
    """
    ann1 = tmp_path / "ann1_text.csv"
    ann2 = tmp_path / "ann2_text.csv"
    _write_csv(ann1, [VALID_ROW_ANN1])
    # ANN_02 có review_text khác cho cùng r1
    row2 = {
        **VALID_ROW_ANN2_AGREE,
        "review_text": "Nội dung khác hẳn",
    }
    _write_csv(ann2, [row2])

    res = evaluate_ground_truth(csv_path=ann1, annotator2_path=ann2)
    assert res["status"] == STATUS_INVALID_ANNOTATIONS, (
        f"Mong INVALID_ANNOTATIONS khi review_text không khớp, nhận {res['status']!r}"
    )


# ----------------------------------------------------------------
# D-7: Không tự chuyển trạng thái sơ bộ thành chính thức
# ----------------------------------------------------------------

def test_preliminary_cannot_become_final_without_gold_standard(tmp_path):
    """
    Hai annotator đồng thuận hoàn toàn (Kappa=1), nhưng nếu không truyền
    gold_standard_path thì không được trả FINAL_EVALUATED.
    Trạng thái phải là PRELIMINARY_EVALUATION hoặc AWAITING_ADJUDICATION,
    và is_official_result phải False.
    """
    ann1 = tmp_path / "ann1_agree.csv"
    ann2 = tmp_path / "ann2_agree.csv"
    # cả hai ĐỒNG THUẬN hoàn toàn
    _write_csv(ann1, [VALID_ROW_ANN1])
    _write_csv(ann2, [VALID_ROW_ANN2_AGREE])

    res = evaluate_ground_truth(csv_path=ann1, annotator2_path=ann2)
    assert res["status"] != STATUS_FINAL_EVALUATED, (
        "Không được tự động trả FINAL_EVALUATED khi chưa có Gold Standard xác nhận"
    )
    assert res.get("is_official_result") is False


# ----------------------------------------------------------------
# review_text rỗng → INVALID_ANNOTATIONS
# ----------------------------------------------------------------

def test_empty_review_text_is_rejected(tmp_path):
    """
    Dòng có review_text rỗng phải bị từ chối → INVALID_ANNOTATIONS.
    """
    csv = tmp_path / "empty_text.csv"
    row = {**VALID_ROW_ANN1, "review_text": ""}
    _write_csv(csv, [row])

    res = evaluate_ground_truth(csv_path=csv)
    assert res["status"] == STATUS_INVALID_ANNOTATIONS, (
        f"Mong INVALID_ANNOTATIONS khi review_text rỗng, nhận {res['status']!r}"
    )
    assert "sentiment" not in res


# ----------------------------------------------------------------
# Gold Standard chỉ có ANN_01 → ValueError (sai cấu trúc)
# ----------------------------------------------------------------

def test_ann01_file_rejected_as_gold_standard(tmp_path):
    """
    File annotation đơn lẻ (cấu trúc ANN_01) không được chấp nhận làm Gold Standard
    → phải trả INVALID_ANNOTATIONS (do thiếu cột ann2_*, final_*, adjudication_status).
    Gold Standard phải nhận diện qua quy trình xác nhận, không chỉ qua tên file.
    """
    csv = tmp_path / "ann1_pretend_gold.csv"
    _write_csv(csv, [VALID_ROW_ANN1])

    res = evaluate_ground_truth(gold_standard_path=csv)
    assert res["status"] == STATUS_INVALID_ANNOTATIONS, (
        "Không được nhận file annotation đơn lẻ làm Gold Standard"
    )
    assert res.get("is_official_result") is False


# ----------------------------------------------------------------
# confirm_gold_standard từ chối khi còn PENDING_ADJUDICATION
# ----------------------------------------------------------------

def test_confirm_gold_standard_rejects_pending(tmp_path):
    """
    confirm_gold_standard() không tự hoàn thành dòng PENDING_ADJUDICATION
    → phải raise ValueError.
    """
    dis = tmp_path / "dis_with_pending.csv"
    rows = [
        _make_gold_row(
            "r1", "Ok rạp đẹp",
            "POSITIVE", "POSITIVE", "Khác", "Khác",
            "POSITIVE", "Khác", "AGREED",
        ),
        _make_gold_row(
            "r2", "Nhân viên không nhiệt tình",
            "NEGATIVE", "NEUTRAL", "Thái độ phục vụ", "Thái độ phục vụ",
            "", "", "PENDING_ADJUDICATION",
        ),
    ]
    _write_csv(dis, rows)

    with pytest.raises(ValueError, match="PENDING_ADJUDICATION"):
        confirm_gold_standard(dis, adjudicator_id="ADJ_01")

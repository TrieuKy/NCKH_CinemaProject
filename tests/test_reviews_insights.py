"""
tests/test_reviews_insights.py
------------------------------
Unit tests cho module reviews/insights.py.
"""

import pandas as pd
import pytest
from reviews.insights import (
    analyze_topic_breakdown,
    generate_operational_insights,
    ACTION_RULES_CATALOG,
)


def _create_sample_df(n_rows: int, neg_topic: str = "Vệ sinh", neg_count: int = 0) -> pd.DataFrame:
    rows = []
    for i in range(n_rows):
        rows.append({
            "review_id": f"rev_{i}",
            "topics": [neg_topic],
            "sentiment_final": "NEGATIVE" if i < neg_count else "POSITIVE",
            "rating": 1.0 if i < neg_count else 5.0,
        })
    return pd.DataFrame(rows)


def test_insights_du_du_lieu_false():
    # n = 5 < 10 -> không sinh đề xuất (trả danh sách rỗng)
    df_small = _create_sample_df(n_rows=5, neg_topic="Vệ sinh", neg_count=2)
    insights = generate_operational_insights(df_small, min_sample=10)
    assert insights == []


def test_insights_du_du_lieu_true():
    # n = 15 >= 10 -> sinh đề xuất
    df_sufficient = _create_sample_df(n_rows=15, neg_topic="Vệ sinh", neg_count=3)
    insights = generate_operational_insights(df_sufficient, min_sample=10)
    assert len(insights) > 0


def test_insights_has_evidence():
    # Mỗi insight phải chứa đủ bằng chứng số liệu
    df_sufficient = _create_sample_df(n_rows=12, neg_topic="Vệ sinh", neg_count=4)
    insights = generate_operational_insights(df_sufficient, min_sample=10)
    
    for ins in insights:
        assert "n" in ins
        assert "negative_count" in ins
        assert "pct_negative" in ins
        assert "prev_pct_negative" in ins
        assert "delta_pct_negative" in ins
        assert "root_causes" in ins
        assert "quick_win" in ins
        assert "long_term" in ins
        assert "kpi_target" in ins


def test_insights_priority_urgent():
    # pct_neg >= 50%, n >= 10, delta >= 10% -> "KHẨN CẤP"
    prev_df = _create_sample_df(n_rows=10, neg_topic="Vệ sinh", neg_count=2)  # 20%
    curr_df = _create_sample_df(n_rows=10, neg_topic="Vệ sinh", neg_count=6)  # 60%
    insights = generate_operational_insights(curr_df, prev_df=prev_df, min_sample=10)
    
    vesinh_ins = next((x for x in insights if x["topic"] == "Vệ sinh"), None)
    assert vesinh_ins is not None
    assert vesinh_ins["priority"] == "KHẨN CẤP"
    assert vesinh_ins["badge_color"] == "#E50914"


def test_insights_no_prev_caps_at_high_priority():
    # Khi không có kỳ trước (delta is None), dù pct_neg >= 50% thì tối đa chỉ đạt "ƯU TIÊN CAO"
    curr_df = _create_sample_df(n_rows=10, neg_topic="Vệ sinh", neg_count=6)  # 60%
    insights = generate_operational_insights(curr_df, prev_df=None, min_sample=10)
    
    vesinh_ins = next((x for x in insights if x["topic"] == "Vệ sinh"), None)
    assert vesinh_ins is not None
    assert vesinh_ins["priority"] == "ƯU TIÊN CAO"
    assert vesinh_ins["badge_color"] == "#FF8C00"


def test_insights_priority_medium():
    # pct_neg >= 25% và n >= 5 -> "TRUNG BÌNH"
    # Tổng df >= 10 dòng để đủ dữ liệu chạy insights, nhưng chủ đề Vệ sinh chỉ có 6 dòng (2 tiêu cực = 33.3%)
    rows = []
    for i in range(12):
        if i < 6:
            rows.append({
                "review_id": f"rev_{i}",
                "topics": ["Vệ sinh"],
                "sentiment_final": "NEGATIVE" if i < 2 else "POSITIVE",
                "rating": 1.0 if i < 2 else 5.0,
            })
        else:
            rows.append({
                "review_id": f"rev_{i}",
                "topics": ["Hàng chờ & Bắp nước"],
                "sentiment_final": "POSITIVE",
                "rating": 5.0,
            })
    df = pd.DataFrame(rows)
    insights = generate_operational_insights(df, min_sample=10)
    vesinh_ins = next((x for x in insights if x["topic"] == "Vệ sinh"), None)
    assert vesinh_ins is not None
    assert vesinh_ins["n"] == 6
    assert vesinh_ins["pct_negative"] == 33.3
    assert vesinh_ins["priority"] == "TRUNG BÌNH"


def test_insights_priority_maintain():
    # neg_count = 0 -> "DUY TRÌ"
    df = _create_sample_df(n_rows=15, neg_topic="Vệ sinh", neg_count=0)
    insights = generate_operational_insights(df, min_sample=10)
    
    vesinh_ins = next((x for x in insights if x["topic"] == "Vệ sinh"), None)
    assert vesinh_ins is not None
    assert vesinh_ins["priority"] == "DUY TRÌ"


def test_insights_comparison_with_prev_period():
    # Kỳ trước: Vệ sinh tiêu cực 20%
    prev_df = _create_sample_df(n_rows=10, neg_topic="Vệ sinh", neg_count=2)
    # Kỳ này: Vệ sinh tiêu cực 50%
    curr_df = _create_sample_df(n_rows=10, neg_topic="Vệ sinh", neg_count=5)
    
    insights = generate_operational_insights(curr_df, prev_df=prev_df, min_sample=10)
    vesinh_ins = next((x for x in insights if x["topic"] == "Vệ sinh"), None)
    assert vesinh_ins is not None
    assert vesinh_ins["prev_pct_negative"] == 20.0
    assert vesinh_ins["pct_negative"] == 50.0
    assert vesinh_ins["delta_pct_negative"] == 30.0

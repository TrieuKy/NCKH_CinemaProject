"""
tests/test_ui_format.py
-----------------------
Unit test cho app/ui/format.py.
"""

from datetime import date, datetime
import pytest

from app.ui.format import (
    format_currency,
    format_decimal,
    format_percent,
    format_date,
    format_weekday,
    format_date_with_weekday,
    format_delta,
)


class TestFormatDecimal:
    def test_standard_decimal(self):
        assert format_decimal(1234.56, decimals=2) == "1.234,56"
        assert format_decimal(12.5, decimals=1) == "12,5"
        assert format_decimal(0, decimals=1) == "0,0"

    def test_integer_and_large_numbers(self):
        assert format_decimal(1000000, decimals=0) == "1.000.000"
        assert format_decimal(9876543.21, decimals=2) == "9.876.543,21"

    def test_negative_decimal(self):
        assert format_decimal(-1234.5, decimals=1) == "-1.234,5"

    def test_trim_zero(self):
        assert format_decimal(12.500, decimals=2, trim_zero=True) == "12,5"
        assert format_decimal(12.000, decimals=2, trim_zero=True) == "12"

    def test_none_and_invalid(self):
        assert format_decimal(None) == "-"
        assert format_decimal("abc") == "-"
        assert format_decimal(float("nan")) == "-"


class TestFormatCurrency:
    def test_standard_full_currency(self):
        assert format_currency(1234567) == "1.234.567 đ"
        assert format_currency(0) == "0 đ"
        assert format_currency(50000) == "50.000 đ"
        assert format_currency(-1234567) == "-1.234.567 đ"

    def test_compact_currency_billions(self):
        # >= 1 tỷ
        assert format_currency(1_200_000_000, compact=True) == "1,2 tỷ đ"
        assert format_currency(2_000_000_000, compact=True) == "2 tỷ đ"
        assert format_currency(-1_500_000_000, compact=True) == "-1,5 tỷ đ"

    def test_compact_currency_millions(self):
        # >= 1 triệu
        assert format_currency(25_400_000, compact=True) == "25,4 triệu đ"
        assert format_currency(1_000_000, compact=True) == "1 triệu đ"
        assert format_currency(-12_500_000, compact=True) == "-12,5 triệu đ"

    def test_compact_currency_under_one_million(self):
        # < 1 triệu giữ nguyên định dạng đầy đủ
        assert format_currency(500_000, compact=True) == "500.000 đ"
        assert format_currency(85_000, compact=True) == "85.000 đ"

    def test_none_currency(self):
        assert format_currency(None) == "- đ"
        assert format_currency(None, symbol="") == "-"


class TestFormatPercent:
    def test_multiply_true(self):
        assert format_percent(0.754) == "75,4%"
        assert format_percent(1.0) == "100,0%"
        assert format_percent(0.0) == "0,0%"
        assert format_percent(-0.052) == "-5,2%"

    def test_multiply_false(self):
        assert format_percent(75.4, multiply=False) == "75,4%"
        assert format_percent(12.345, decimals=2, multiply=False) == "12,35%"

    def test_none_percent(self):
        assert format_percent(None) == "-"
        assert format_percent("xyz") == "-"


class TestFormatDate:
    def test_date_objects(self):
        d = date(2026, 11, 16)
        assert format_date(d) == "16/11/2026"

        dt = datetime(2026, 5, 1, 14, 30)
        assert format_date(dt) == "01/05/2026"

    def test_string_dates(self):
        assert format_date("2026-11-16") == "16/11/2026"
        assert format_date("2026-05-01 10:00:00") == "01/05/2026"

    def test_int_date_keys(self):
        assert format_date(20261116) == "16/11/2026"
        assert format_date(20250105) == "05/01/2025"

    def test_invalid_date(self):
        assert format_date(None) == "-"
        assert format_date("not-a-date") == "-"


class TestFormatWeekday:
    def test_weekdays(self):
        # 2026-11-16 is Monday
        assert format_weekday(date(2026, 11, 16)) == "Thứ Hai"
        assert format_weekday(date(2026, 11, 17)) == "Thứ Ba"
        assert format_weekday(date(2026, 11, 18)) == "Thứ Tư"
        assert format_weekday(date(2026, 11, 19)) == "Thứ Năm"
        assert format_weekday(date(2026, 11, 20)) == "Thứ Sáu"
        assert format_weekday(date(2026, 11, 21)) == "Thứ Bảy"
        assert format_weekday(date(2026, 11, 22)) == "Chủ Nhật"

    def test_combined_date_with_weekday(self):
        assert format_date_with_weekday(date(2026, 11, 16)) == "Thứ Hai, 16/11/2026"

    def test_none_weekday(self):
        assert format_weekday(None) == "-"
        assert format_date_with_weekday(None) == "-"


class TestFormatDelta:
    def test_positive_delta(self):
        res = format_delta(0.052, is_percent=True)
        assert res["direction"] == "up"
        assert res["color"] == "green"
        assert res["arrow"] == "↑"
        assert "+5,2%" in res["text"]

    def test_negative_delta(self):
        res = format_delta(-0.031, is_percent=True)
        assert res["direction"] == "down"
        assert res["color"] == "red"
        assert res["arrow"] == "↓"
        assert "-3,1%" in res["text"]

    def test_zero_delta(self):
        res = format_delta(0.0, is_percent=True)
        assert res["direction"] == "flat"
        assert res["color"] == "gray"
        assert res["arrow"] == "→"

    def test_reversed_color(self):
        # Tăng nhưng là số xấu (ví dụ tỷ lệ bất thường tăng)
        res = format_delta(0.05, is_percent=True, reverse_color=True)
        assert res["color"] == "red"

        # Giảm số xấu -> màu xanh
        res2 = format_delta(-0.05, is_percent=True, reverse_color=True)
        assert res2["color"] == "green"

    def test_non_percent_delta(self):
        res = format_delta(1500, is_percent=False, symbol="vé")
        assert res["direction"] == "up"
        assert "+1.500 vé" in res["text"]

    def test_none_delta(self):
        res = format_delta(None)
        assert res["text"] == "-"


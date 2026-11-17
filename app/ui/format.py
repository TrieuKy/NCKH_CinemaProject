"""
app/ui/format.py
----------------
Quy chuẩn định dạng hiển thị cho giao diện Streamlit:
- Tiền tệ: 1.234.567 đ (phân cách hàng nghìn bằng dấu chấm), rút gọn tỷ/triệu cho thẻ KPI.
- Số thập phân dấu phẩy: 1.234,56 (dấu phẩy cho phần thập phân).
- Phần trăm: 75,4% (1 chữ số thập phân, dấu phẩy).
- Ngày tháng: dd/mm/yyyy.
- Thứ: Thứ Hai, Thứ Ba, ..., Chủ Nhật.
- Delta: Kèm mũi tên (↑, ↓, →) và dấu âm dương.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Union

Numeric = Union[int, float]
DateLike = Union[date, datetime, str, int]


# =============================================================================
# 1. ĐỊNH DẠNG SỐ THẬP PHÂN (DẤU PHẨY CHO THẬP PHÂN, DẤU CHẤM CHO HÀNG NGHÌN)
# =============================================================================

def format_decimal(value: Any, decimals: int = 1, trim_zero: bool = False) -> str:
    """
    Định dạng số theo chuẩn Việt Nam:
    - Phân tách hàng nghìn: dấu chấm '.'
    - Phân tách thập phân: dấu phẩy ','
    Ví dụ:
        format_decimal(1234.56, decimals=2) -> "1.234,56"
        format_decimal(12.5, decimals=1) -> "12,5"
        format_decimal(None) -> "-"
    """
    if value is None:
        return "-"
    try:
        val = float(value)
        if math.isnan(val) or math.isinf(val):
            return "-"
    except (ValueError, TypeError):
        return "-"

    # Format với format chuẩn US trước (dấu phẩy hàng nghìn, chấm thập phân)
    fmt = f"{{:,.{decimals}f}}"
    formatted = fmt.format(val)

    # Đổi: ',' thành '#', '.' thành ',', '#' thành '.'
    parts = formatted.split(".")
    integer_part = parts[0].replace(",", ".")
    if decimals > 0 and len(parts) > 1:
        decimal_part = parts[1]
        if trim_zero:
            decimal_part = decimal_part.rstrip("0")
            if not decimal_part:
                return integer_part
        return f"{integer_part},{decimal_part}"
    return integer_part


# =============================================================================
# 2. ĐỊNH DẠNG TIỀN TỆ (1.234.567 đ & RÚT GỌN TỶ/TRIỆU CHO THẺ KPI)
# =============================================================================

def format_currency(
    value: Any,
    compact: bool = False,
    symbol: str = "đ",
    decimals_compact: int = 1
) -> str:
    """
    Định dạng tiền Việt Nam Đồng (VND):
    - Đầy đủ (compact=False): 1.234.567 đ
    - Rút gọn (compact=True): dùng cho thẻ KPI:
        >= 1.000.000.000 -> "1,2 tỷ đ"
        >= 1.000.000 -> "25,4 triệu đ"
        < 1.000.000 -> "500.000 đ"
    Hỗ trợ số âm: -1.234.567 đ, -1,2 tỷ đ.
    """
    if value is None:
        return f"- {symbol}" if symbol else "-"
    try:
        val = float(value)
        if math.isnan(val) or math.isinf(val):
            return f"- {symbol}" if symbol else "-"
    except (ValueError, TypeError):
        return f"- {symbol}" if symbol else "-"

    is_negative = val < 0
    abs_val = abs(val)
    sign = "-" if is_negative else ""

    if compact:
        if abs_val >= 1_000_000_000:
            compact_num = format_decimal(abs_val / 1_000_000_000, decimals=decimals_compact, trim_zero=True)
            unit_str = f" tỷ {symbol}" if symbol else " tỷ"
            return f"{sign}{compact_num}{unit_str}"
        elif abs_val >= 1_000_000:
            compact_num = format_decimal(abs_val / 1_000_000, decimals=decimals_compact, trim_zero=True)
            unit_str = f" triệu {symbol}" if symbol else " triệu"
            return f"{sign}{compact_num}{unit_str}"
        # Dưới 1 triệu thì giữ dạng phân cách hàng nghìn bình thường

    # compact=False hoặc số < 1 triệu
    int_val = int(round(abs_val))
    formatted_int = f"{int_val:,}".replace(",", ".")
    res = f"{sign}{formatted_int}"
    return f"{res} {symbol}" if symbol else res


# =============================================================================
# 3. ĐỊNH DẠNG PHẦN TRĂM (1 CHỮ SỐ THẬP PHÂN)
# =============================================================================

def format_percent(value: Any, decimals: int = 1, multiply: bool = True) -> str:
    """
    Định dạng phần trăm 1 chữ số thập phân với dấu phẩy:
    - Nếu multiply=True: 0.754 -> "75,4%"
    - Nếu multiply=False: 75.4 -> "75,4%"
    """
    if value is None:
        return "-"
    try:
        val = float(value)
        if math.isnan(val) or math.isinf(val):
            return "-"
    except (ValueError, TypeError):
        return "-"

    if multiply:
        val = val * 100.0

    formatted_num = format_decimal(val, decimals=decimals)
    return f"{formatted_num}%"


# =============================================================================
# 4. ĐỊNH DẠNG NGÀY & THỨ
# =============================================================================

WEEKDAY_NAMES_VI = {
    0: "Thứ Hai",
    1: "Thứ Ba",
    2: "Thứ Tư",
    3: "Thứ Năm",
    4: "Thứ Sáu",
    5: "Thứ Bảy",
    6: "Chủ Nhật",
}

def _parse_date(d: DateLike) -> date | None:
    """Chuyển đổi linh hoạt date, datetime, str (YYYY-MM-DD), int (YYYYMMDD) thành date."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, int):
        # int dạng date_key 20261116
        s = str(d)
        if len(s) == 8:
            try:
                return datetime.strptime(s, "%Y%m%d").date()
            except ValueError:
                return None
    if isinstance(d, str):
        s = d.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
            try:
                return datetime.strptime(s[:10], fmt[: len(s[:10])]).date()
            except ValueError:
                continue
    return None


def format_date(d: DateLike, fmt: str = "%d/%m/%Y") -> str:
    """
    Định dạng ngày thành dd/mm/yyyy.
    Ví dụ: date(2026, 11, 16) -> "16/11/2026"
    """
    dt = _parse_date(d)
    if dt is None:
        return "-"
    return dt.strftime(fmt)


def format_weekday(d: DateLike) -> str:
    """
    Trả về tên thứ trong tuần bằng tiếng Việt:
    Thứ Hai, Thứ Ba, ..., Thứ Bảy, Chủ Nhật.
    """
    dt = _parse_date(d)
    if dt is None:
        return "-"
    return WEEKDAY_NAMES_VI.get(dt.weekday(), "-")


def format_date_with_weekday(d: DateLike) -> str:
    """
    Định dạng kết hợp: "Thứ Hai, 16/11/2026".
    """
    dt = _parse_date(d)
    if dt is None:
        return "-"
    w = format_weekday(dt)
    return f"{w}, {dt.strftime('%d/%m/%Y')}"


# =============================================================================
# 5. ĐỊNH DẠNG DELTA KÈM MŨI TÊN (CHO THẺ KPI / SO SÁNH)
# =============================================================================

def format_delta(
    delta: Any,
    is_percent: bool = True,
    reverse_color: bool = False,
    symbol: str = "",
    decimals: int = 1,
    multiply: bool = True
) -> dict[str, str]:
    """
    Định dạng delta kèm mũi tên và màu sắc:
    - delta > 0: "↑ +5,2%" (xanh lá mặc định)
    - delta < 0: "↓ -3,1%" (đỏ mặc định)
    - delta == 0: "→ 0,0%" (xám)
    - reverse_color=True: Đảo màu (dành cho chỉ số tiêu cực như tỷ lệ hủy, lỗi, chi phí)
    Trả về dict: {"text": "↑ +5,2%", "direction": "up", "color": "green", "value_str": "+5,2%"}
    """
    if delta is None:
        return {"text": "-", "direction": "flat", "color": "gray", "value_str": "-"}

    try:
        val = float(delta)
        if math.isnan(val) or math.isinf(val):
            return {"text": "-", "direction": "flat", "color": "gray", "value_str": "-"}
    except (ValueError, TypeError):
        return {"text": "-", "direction": "flat", "color": "gray", "value_str": "-"}

    sign = "+" if val > 0 else ("-" if val < 0 else "")

    if is_percent:
        pct_val = abs(val) * 100.0 if multiply else abs(val)
        val_str = f"{sign}{format_decimal(pct_val, decimals=decimals)}%"
    else:
        num_str = format_decimal(abs(val), decimals=decimals, trim_zero=True)
        unit = f" {symbol}" if symbol else ""
        val_str = f"{sign}{num_str}{unit}"

    if val > 0.00001:
        direction = "up"
        arrow = "↑"
        color = "red" if reverse_color else "green"
    elif val < -0.00001:
        direction = "down"
        arrow = "↓"
        color = "green" if reverse_color else "red"
    else:
        direction = "flat"
        arrow = "→"
        color = "gray"
        val_str = "0,0%" if is_percent else ("0" + (f" {symbol}" if symbol else ""))

    return {
        "text": f"{arrow} {val_str}",
        "direction": direction,
        "color": color,
        "value_str": val_str,
        "arrow": arrow,
    }


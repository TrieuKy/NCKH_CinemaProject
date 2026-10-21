"""
core/holidays.py
-----------------
Nguồn duy nhất (single source of truth) cho danh sách ngày lễ Việt Nam.
Cả scripts/generate_mock_data.py và etl/extend_dim_date.py đều import từ đây.

Phân loại:
  - is_holiday  : Ngày lễ chính thức (được nghỉ theo pháp luật), KỂ CẢ nghỉ bù.
  - is_peak_day : Ngày cao điểm thương mại (rạp đông nhưng không nghỉ chính thức).

Ngày lễ chính thức:
  - Tết Dương lịch (01/01)
  - Tết Nguyên đán (khoảng nghỉ thực tế theo công văn Chính phủ)
  - Giỗ Tổ Hùng Vương (10/3 âm lịch + nghỉ bù nếu rơi vào CN)
  - 30/4 – 1/5 (Giải phóng miền Nam & Quốc tế Lao động + nghỉ bù)
  - Quốc khánh 02/9 (+ nghỉ bù)

Ngày cao điểm (is_peak_day, KHÔNG phải lễ chính thức):
  - Giáng sinh 24–25/12
  - 20/10 Ngày Phụ nữ Việt Nam

Nguồn:
  - Công văn 9859/VPCP-KGVX ngày 13/10/2025 → Tết 2026, Quốc khánh 2026
  - Thông báo 6150/TB-BLĐTBXH → lịch nghỉ 2025
  - Giỗ Tổ 2026 nghỉ bù 27/04: nguồn thứ cấp (suy ra từ quy tắc nghỉ bù CN)
  - 30/4–1/5/2026: suy ra từ cuối tuần liền kề — CẦN XÁC NHẬN khi có công văn

Ghi chú về lunarcalendar:
  - Dùng để tính ngày âm → dương cho Tết Nguyên đán và Giỗ Tổ Hùng Vương.
  - Một số năm lunarcalendar tính sai Giỗ Tổ → cho phép override thủ công.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Tuple

from lunarcalendar import Converter, Lunar


# ──────────────────────────────────────────────────────────────────────
# Override thủ công cho các ngày lunarcalendar tính sai hoặc cần cố định
# ──────────────────────────────────────────────────────────────────────
_GIO_TO_OVERRIDE: dict[int, date] = {
    2025: date(2025, 4, 7),     # 10/3 Ất Tỵ
    2026: date(2026, 4, 26),    # 10/3 Bính Ngọ → CN, nghỉ bù T2 27/04
}

_TET_MUNG1_OVERRIDE: dict[int, date] = {
    2026: date(2026, 2, 17),    # Mùng 1 Tết Bính Ngọ = 17/02/2026
}


# ──────────────────────────────────────────────────────────────────────
# Khoảng nghỉ CHÍNH THỨC (bao gồm nghỉ bù) — hardcode theo công văn
# Mỗi entry: (start_date, end_date, tên lễ, nguồn)
# ──────────────────────────────────────────────────────────────────────
_OFFICIAL_HOLIDAY_RANGES: List[Tuple[date, date, str, str]] = [
    # ── Quốc khánh 2025 ──
    # 02/09 (T3) + 01/09 (T2 nghỉ bù) → nghỉ từ 30/08 (T7) đến 02/09 (T3)
    # Nguồn: thông báo 6150/TB-BLĐTBXH
    (date(2025, 8, 30), date(2025, 9, 2), "Quốc khánh", "TB 6150/TB-BLĐTBXH"),

    # ── 30/4 – 1/5/2025 ──
    # 30/04 (T4) → 04/05 (CN); ngày hoán đổi 02/05 T6 (làm bù 26/04 T7)
    # Nguồn: thông báo 6150/TB-BLĐTBXH
    (date(2025, 4, 30), date(2025, 5, 4), "30/4-1/5", "TB 6150/TB-BLĐTBXH"),

    # ── Tết Dương lịch 2026 ──
    # 01/01 (T5) → chỉ nghỉ 1 ngày (không nghỉ bù vì giữa tuần)
    (date(2026, 1, 1), date(2026, 1, 1), "Tết Dương lịch", "Bộ luật Lao động"),

    # ── Tết Nguyên đán 2026 ──
    # Mùng 1 = 17/02/2026 (T3). Nghỉ từ 14/02 (T7, 27 tháng Chạp) → 22/02 (CN, mùng 6)
    # = 9 ngày (gồm 2 ngày cuối tuần trước + 5 ngày lễ + 2 ngày cuối tuần sau)
    # Nguồn: công văn 9859/VPCP-KGVX ngày 13/10/2025
    (date(2026, 2, 14), date(2026, 2, 22), "Tết Nguyên đán", "CV 9859/VPCP-KGVX"),

    # ── Giỗ Tổ Hùng Vương 2026 ──
    # 10/3 Bính Ngọ = 26/04/2026 (CN), nghỉ bù T2 27/04
    # Nguồn thứ cấp (quy tắc nghỉ bù khi lễ rơi CN)
    (date(2026, 4, 26), date(2026, 4, 27), "Giỗ Tổ Hùng Vương", "nguồn thứ cấp"),

    # ── 30/4 – 1/5/2026 ──
    # 30/04 (T5) → 03/05 (CN): T5-T6 là lễ, T7-CN liền kề
    # ⚠ CẦN XÁC NHẬN khi Chính phủ ra công văn chính thức
    (date(2026, 4, 30), date(2026, 5, 3), "30/4-1/5", "suy ra từ cuối tuần — cần xác nhận"),

    # ── Quốc khánh 2026 ──
    # 02/09 (T4); nghỉ 29/08 (T7) → 02/09 (T4)
    # 01/09 (T3 nghỉ bù), 31/08 (T2 nghỉ liền)
    # Nguồn: công văn 9859/VPCP-KGVX ngày 13/10/2025
    (date(2026, 8, 29), date(2026, 9, 2), "Quốc khánh", "CV 9859/VPCP-KGVX"),
]

# ── Ngày lễ cố định hàng năm (chỉ dùng cho năm không có trong _OFFICIAL_HOLIDAY_RANGES) ──
_FIXED_ANNUAL_HOLIDAYS: List[Tuple[int, int, str]] = [
    (1, 1, "Tết Dương lịch"),       # 01/01
    (4, 30, "30/4-1/5"),            # 30/04
    (5, 1, "30/4-1/5"),             # 01/05
    (9, 2, "Quốc khánh"),          # 02/09
]


# ──────────────────────────────────────────────────────────────────────
# Ngày CAO ĐIỂM (peak_day) — không nghỉ chính thức
# ──────────────────────────────────────────────────────────────────────
_PEAK_DAYS: List[Tuple[int, int, str]] = [
    (12, 24, "Giáng sinh"),         # 24/12
    (12, 25, "Giáng sinh"),         # 25/12
    (10, 20, "Ngày Phụ nữ Việt Nam"),  # 20/10
]


# ──────────────────────────────────────────────────────────────────────
# Hàm tính mùng 1 Tết (dùng cho năm chưa có trong _OFFICIAL_HOLIDAY_RANGES)
# ──────────────────────────────────────────────────────────────────────
def _tet_mung1(year: int) -> date:
    """Trả về ngày dương lịch của Mùng 1 Tết Nguyên đán năm ``year``."""
    if year in _TET_MUNG1_OVERRIDE:
        return _TET_MUNG1_OVERRIDE[year]
    lunar = Lunar(year, 1, 1, False)
    solar = Converter.Lunar2Solar(lunar)
    return date(solar.year, solar.month, solar.day)


def _gio_to(year: int) -> date:
    """Trả về ngày dương lịch của Giỗ Tổ Hùng Vương (10/3 âm lịch) năm ``year``."""
    if year in _GIO_TO_OVERRIDE:
        return _GIO_TO_OVERRIDE[year]
    lunar = Lunar(year, 3, 10, False)
    solar = Converter.Lunar2Solar(lunar)
    return date(solar.year, solar.month, solar.day)


# ──────────────────────────────────────────────────────────────────────
# Hàm chính: ngày lễ CHÍNH THỨC (is_holiday)
# ──────────────────────────────────────────────────────────────────────
def get_official_holidays(year: int) -> Dict[date, str]:
    """
    Trả về dict {ngày: tên_lễ} cho ``year``.
    Chỉ bao gồm ngày lễ chính thức và nghỉ bù (is_holiday = TRUE).
    KHÔNG bao gồm ngày cao điểm (Giáng sinh, 20/10).

    Ưu tiên khoảng nghỉ hardcode (từ công văn) nếu có.
    Nếu năm chưa có hardcode → dùng ngày cố định + tính âm lịch.
    """
    holidays: Dict[date, str] = {}

    # Tìm khoảng nghỉ hardcode cho năm này
    year_ranges = [r for r in _OFFICIAL_HOLIDAY_RANGES
                   if r[0].year == year or r[1].year == year]

    if year_ranges:
        # Có hardcode → dùng khoảng nghỉ chính xác
        for start, end, name, _source in year_ranges:
            d = start
            while d <= end:
                if d.year == year or d in holidays:
                    holidays[d] = name
                d += timedelta(days=1)
    else:
        # Năm chưa có hardcode → fallback sang tính toán
        # Tết Dương lịch
        holidays[date(year, 1, 1)] = "Tết Dương lịch"

        # Tết Nguyên đán (29/30 tháng Chạp → mùng 5)
        mung1 = _tet_mung1(year)
        tet_start = mung1 - timedelta(days=2)
        tet_end = mung1 + timedelta(days=4)
        d = tet_start
        while d <= tet_end:
            holidays[d] = "Tết Nguyên đán"
            d += timedelta(days=1)

        # Giỗ Tổ
        holidays[_gio_to(year)] = "Giỗ Tổ Hùng Vương"

        # 30/4 – 1/5
        for month, day, name in _FIXED_ANNUAL_HOLIDAYS:
            holidays[date(year, month, day)] = name

        # Quốc khánh
        holidays[date(year, 9, 2)] = "Quốc khánh"

    return holidays


# ──────────────────────────────────────────────────────────────────────
# Hàm chính: ngày CAO ĐIỂM (is_peak_day)
# ──────────────────────────────────────────────────────────────────────
def get_peak_days(year: int) -> Dict[date, str]:
    """
    Trả về dict {ngày: tên_sự_kiện} cho ``year``.
    Ngày cao điểm thương mại (rạp đông), KHÔNG phải nghỉ chính thức.
    """
    return {date(year, m, d): name for m, d, name in _PEAK_DAYS}


# ──────────────────────────────────────────────────────────────────────
# Hàm tiện ích: gộp tất cả (is_holiday + is_peak_day)
# ──────────────────────────────────────────────────────────────────────
def get_all_special_days(year: int) -> Dict[date, str]:
    """Trả về dict gộp ngày lễ chính thức + ngày cao điểm."""
    merged: Dict[date, str] = {}
    merged.update(get_official_holidays(year))
    merged.update(get_peak_days(year))
    return merged


def get_holidays_range(start_year: int, end_year: int) -> Dict[date, str]:
    """
    Trả về dict ngày lễ chính thức gộp từ ``start_year`` đến ``end_year`` (bao gồm).
    Chỉ trả is_holiday (không bao gồm peak_day).
    """
    merged: Dict[date, str] = {}
    for y in range(start_year, end_year + 1):
        merged.update(get_official_holidays(y))
    return merged


def get_peak_days_range(start_year: int, end_year: int) -> Dict[date, str]:
    """
    Trả về dict ngày cao điểm gộp từ ``start_year`` đến ``end_year`` (bao gồm).
    """
    merged: Dict[date, str] = {}
    for y in range(start_year, end_year + 1):
        merged.update(get_peak_days(y))
    return merged


# ──────────────────────────────────────────────────────────────────────
# CLI: in danh sách ngày lễ khi chạy trực tiếp
# ──────────────────────────────────────────────────────────────────────
def print_holidays(start_year: int = 2025, end_year: int = 2027) -> None:
    """In danh sách ngày lễ ra console để duyệt."""
    print(f"{'=' * 70}")
    print(f"  DANH SÁCH NGÀY LỄ & NGÀY CAO ĐIỂM {start_year}–{end_year}")
    print(f"{'=' * 70}")

    for y in range(start_year, end_year + 1):
        holidays = get_official_holidays(y)
        peaks = get_peak_days(y)

        print(f"\n── Năm {y} ──")
        print(f"  📅 Ngày lễ chính thức ({len(holidays)} ngày):")
        for d in sorted(holidays.keys()):
            dow = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][d.weekday()]
            print(f"    {d.strftime('%Y-%m-%d')} ({dow})  {holidays[d]}")

        print(f"\n  🔥 Ngày cao điểm ({len(peaks)} ngày):")
        for d in sorted(peaks.keys()):
            dow = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][d.weekday()]
            print(f"    {d.strftime('%Y-%m-%d')} ({dow})  {peaks[d]}")

    # In chi tiết khoảng nghỉ hardcode
    print(f"\n{'─' * 70}")
    print("  CHI TIẾT KHOẢNG NGHỈ (từ công văn):")
    for start, end, name, source in _OFFICIAL_HOLIDAY_RANGES:
        days = (end - start).days + 1
        dow_s = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][start.weekday()]
        dow_e = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][end.weekday()]
        print(f"  {name:25s} {start} ({dow_s}) → {end} ({dow_e})  "
              f"({days} ngày)  [{source}]")

    print(f"{'=' * 70}")


if __name__ == "__main__":
    print_holidays(2025, 2027)

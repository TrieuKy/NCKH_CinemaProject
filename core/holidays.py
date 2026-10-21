"""
core/holidays.py
-----------------
Nguồn duy nhất (single source of truth) cho danh sách ngày lễ Việt Nam.
Cả scripts/generate_mock_data.py và etl/extend_dim_date.py đều import từ đây.

Danh sách ngày lễ:
  - Tết Dương lịch (01/01)
  - Tết Nguyên đán (khoảng nghỉ: 29 tháng Chạp → mùng 5 Tết)
  - Giỗ Tổ Hùng Vương (10/3 âm lịch)
  - 30/4 – 1/5 (Giải phóng miền Nam & Quốc tế Lao động)
  - Quốc khánh 02/9
  - Giáng sinh 24–25/12 (ngày cao điểm, không phải lễ chính thức)
  - 20/10 Ngày Phụ nữ Việt Nam (ngày cao điểm, không phải lễ chính thức)

Ghi chú về lunarcalendar:
  - Dùng để tính ngày âm → dương cho Tết Nguyên đán và Giỗ Tổ Hùng Vương.
  - Một số năm lunarcalendar tính sai Giỗ Tổ → cho phép override thủ công.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict

from lunarcalendar import Converter, Lunar


# ──────────────────────────────────────────────────────────────────────
# Override thủ công cho các ngày lunarcalendar tính sai hoặc cần cố định
# ──────────────────────────────────────────────────────────────────────
_GIO_TO_OVERRIDE: dict[int, date] = {
    2025: date(2025, 4, 7),     # 10/3 Ất Tỵ
    2026: date(2026, 4, 26),    # 10/3 Bính Ngọ
}

_TET_MUNG1_OVERRIDE: dict[int, date] = {
    2026: date(2026, 2, 17),    # Mùng 1 Tết Bính Ngọ = 17/02/2026
}


# ──────────────────────────────────────────────────────────────────────
# Hàm tính mùng 1 Tết
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
# Hàm chính: lấy danh sách ngày lễ cho 1 năm
# ──────────────────────────────────────────────────────────────────────
def get_holidays_for_year(year: int) -> Dict[date, str]:
    """
    Trả về dict {ngày: tên_lễ} cho ``year``.

    Bao gồm cả ngày lễ chính thức và ngày cao điểm (Giáng sinh, 20/10).
    Khoảng nghỉ Tết: 29 hoặc 30 tháng Chạp (tùy năm) → mùng 5 Tết
    (= mùng 1 − 2 ngày → mùng 1 + 4 ngày, tổng 7 ngày).
    """
    holidays: Dict[date, str] = {}

    # ── 1. Tết Dương lịch ──
    holidays[date(year, 1, 1)] = "Tết Dương lịch"

    # ── 2. Tết Nguyên đán ──
    mung1 = _tet_mung1(year)
    # Khoảng nghỉ: 29/30 tháng Chạp (= mùng1 − 2 ngày) → mùng 5 (= mùng1 + 4 ngày)
    tet_start = mung1 - timedelta(days=2)   # ~ 29 tháng Chạp
    tet_end   = mung1 + timedelta(days=4)   # mùng 5 Tết
    d = tet_start
    while d <= tet_end:
        holidays[d] = "Tết Nguyên đán"
        d += timedelta(days=1)

    # ── 3. Giỗ Tổ Hùng Vương ──
    holidays[_gio_to(year)] = "Giỗ Tổ Hùng Vương"

    # ── 4. 30/4 – 1/5 ──
    holidays[date(year, 4, 30)] = "30/4-1/5"
    holidays[date(year, 5, 1)]  = "30/4-1/5"

    # ── 5. Quốc khánh 02/9 ──
    holidays[date(year, 9, 2)] = "Quốc khánh"

    # ── 6. Ngày cao điểm: 20/10 Phụ nữ Việt Nam ──
    holidays[date(year, 10, 20)] = "Ngày Phụ nữ Việt Nam"

    # ── 7. Ngày cao điểm: Giáng sinh 24–25/12 ──
    holidays[date(year, 12, 24)] = "Giáng sinh"
    holidays[date(year, 12, 25)] = "Giáng sinh"

    return holidays


def get_holidays_range(start_year: int, end_year: int) -> Dict[date, str]:
    """
    Trả về dict ngày lễ gộp từ ``start_year`` đến ``end_year`` (bao gồm).
    """
    merged: Dict[date, str] = {}
    for y in range(start_year, end_year + 1):
        merged.update(get_holidays_for_year(y))
    return merged


# ──────────────────────────────────────────────────────────────────────
# CLI: in danh sách ngày lễ khi chạy trực tiếp
# ──────────────────────────────────────────────────────────────────────
def print_holidays(start_year: int = 2025, end_year: int = 2026) -> None:
    """In danh sách ngày lễ ra console để duyệt."""
    print(f"{'=' * 60}")
    print(f"  DANH SÁCH NGÀY LỄ {start_year}–{end_year}")
    print(f"{'=' * 60}")

    for y in range(start_year, end_year + 1):
        holidays = get_holidays_for_year(y)
        print(f"\n── Năm {y} ({len(holidays)} ngày) {'─' * 35}")
        for d in sorted(holidays.keys()):
            dow = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][d.weekday()]
            print(f"  {d.strftime('%Y-%m-%d')} ({dow})  {holidays[d]}")

    # In khoảng Tết để dễ duyệt
    print(f"\n{'─' * 60}")
    print("  CHI TIẾT KHOẢNG NGHỈ TẾT NGUYÊN ĐÁN:")
    for y in range(start_year, end_year + 1):
        mung1 = _tet_mung1(y)
        tet_start = mung1 - timedelta(days=2)
        tet_end   = mung1 + timedelta(days=4)
        print(f"  Năm {y}: Mùng 1 = {mung1} → Nghỉ {tet_start} đến {tet_end} (7 ngày)")

    print(f"\n  CHI TIẾT GIỖ TỔ HÙNG VƯƠNG (10/3 ÂM LỊCH):")
    for y in range(start_year, end_year + 1):
        print(f"  Năm {y}: {_gio_to(y)}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    print_holidays(2025, 2026)

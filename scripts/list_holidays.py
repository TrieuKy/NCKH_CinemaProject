from datetime import date, timedelta
from lunarcalendar import Converter, Solar, Lunar
import sys

def get_holidays_for_year(year):
    holidays = []
    # Dương lịch cố định
    holidays.append((date(year, 1, 1), 'Tết Dương Lịch'))
    holidays.append((date(year, 4, 30), 'Giải Phóng Miền Nam'))
    holidays.append((date(year, 5, 1), 'Quốc Tế Lao Động'))
    holidays.append((date(year, 9, 2), 'Quốc Khánh'))

    # Âm lịch: Tết (30/12 đến mùng 5 Tết)
    try:
        # Giỗ tổ Hùng Vương (10/3 âm lịch)
        lunar_gio_to = Lunar(year, 3, 10, False)
        solar_gio_to = Converter.Lunar2Solar(lunar_gio_to)
        holidays.append((date(solar_gio_to.year, solar_gio_to.month, solar_gio_to.day), 'Giỗ Tổ Hùng Vương'))

        # Tết nguyên đán: 29/12 hoặc 30/12 âm đến 5/1 âm
        # Vì lunarcalendar tính toán phức tạp tháng thiếu đủ, tính lùi từ 1/1 âm lịch
        lunar_mung1 = Lunar(year, 1, 1, False)
        solar_mung1 = Converter.Lunar2Solar(lunar_mung1)
        d_mung1 = date(solar_mung1.year, solar_mung1.month, solar_mung1.day)
        holidays.append((d_mung1 - timedelta(days=2), 'Nghỉ Tết Nguyên Đán'))
        holidays.append((d_mung1 - timedelta(days=1), 'Nghỉ Tết Nguyên Đán'))
        for i in range(0, 6):
            holidays.append((d_mung1 + timedelta(days=i), 'Nghỉ Tết Nguyên Đán'))
    except Exception as e:
        print(f'Lỗi tính lịch âm năm {year}: {e}')
    
    return sorted(holidays, key=lambda x: x[0])

print('=== DANH SÁCH NGÀY LỄ 2026 - 2030 ===')
for y in range(2026, 2031):
    h = get_holidays_for_year(y)
    for d, name in h:
        print(f"{d.strftime('%Y-%m-%d')}: {name}")

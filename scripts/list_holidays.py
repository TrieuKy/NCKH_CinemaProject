"""
scripts/list_holidays.py
------------------------
Liệt kê danh sách ngày lễ và ngày cao điểm từ core.holidays (single source of truth).
"""
import sys
import os

# Thêm thư mục gốc dự án vào sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.holidays import print_holidays

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print_holidays(2025, 2027)

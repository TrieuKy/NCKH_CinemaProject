"""
scripts/run_etl.py
------------------
ETL Pipeline Orchestrator — Điểm khởi động (Entrypoint) chính thức cho toàn bộ pipeline ETL.

Quy trình tuần tự (10 bước):
  1. Kiểm tra kết nối database và cấu hình môi trường (.env).
  2. Khởi tạo schema kho dữ liệu (sql/01_schema.sql).
  3. Khởi tạo hạ tầng hỗ trợ ETL (sql/01b_etl_support.sql).
  4. Nạp dữ liệu staging từ CSV (etl/load_staging.py).
  5. Nạp dữ liệu các bảng dimension (etl/seed_dims.py -> sql/02_seed_dims.sql).
  6. Mở rộng chiều lịch dim_date (etl/extend_dim_date.py).
  7. Nạp các bảng Fact bằng SQL Set-based nguyên tử (etl/load_facts.py -> sql/03_load_facts.sql).
  8. Khởi tạo / cập nhật các View phân tích (sql/03_views.sql).
  9. Cập nhật bảng tổng hợp summary (etl/aggregate_summary.py -> sql/05_app_tables.sql).
  10. Đối soát dữ liệu và kiểm tra chất lượng (etl/reconcile.py).

Các chế độ chạy:
  - Mặc định (Full Pipeline):    python scripts/run_etl.py
  - Chỉ đối soát QC:             python scripts/run_etl.py --reconcile-only
  - Preflight kiểm tra (Dry run): python scripts/run_etl.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

# Đảm bảo đường dẫn gốc dự án có trong sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.engine import Engine

from etl.db import get_engine, logger, test_connection
from etl.load_staging import load_staging
from etl.seed_dims import seed_dims
from etl.extend_dim_date import extend_dim_date
from etl.load_facts import load_facts
from etl.aggregate_summary import run_aggregation
from etl.reconcile import reconcile, print_log

# Đường dẫn các file SQL cần thiết
SQL_SCHEMA      = ROOT / "sql" / "01_schema.sql"
SQL_ETL_SUPPORT = ROOT / "sql" / "01b_etl_support.sql"
SQL_VIEWS       = ROOT / "sql" / "03_views.sql"

# Đường dẫn các file CSV staging
CSV_TICKET      = ROOT / "data" / "staging_ticket.csv"
CSV_FNB         = ROOT / "data" / "staging_fnb.csv"


def _split_sql_statements(sql: str) -> List[str]:
    """Tách file SQL thành từng câu lệnh riêng biệt dựa trên dấu ';', bỏ comment rỗng."""
    parts = sql.split(";")
    stmts: List[str] = []
    for part in parts:
        stripped = part.strip()
        no_comments = re.sub(r"--[^\n]*", "", stripped).strip()
        if no_comments:
            stmts.append(stripped)
    return stmts


def execute_sql_file(engine: Engine, file_path: Path, step_name: str) -> None:
    """Thực thi toàn bộ các câu lệnh trong file SQL trong một transaction an toàn."""
    if not file_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file SQL: {file_path}")

    sql_content = file_path.read_text(encoding="utf-8")
    statements = _split_sql_statements(sql_content)

    logger.info("  Thực thi %s (%d câu lệnh)...", file_path.name, len(statements))
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def run_dry_run() -> bool:
    """
    Thực hiện kiểm tra tính sẵn sàng trước khi chạy (Preflight Checklist).
    Tuyệt đối KHÔNG ghi dữ liệu, KHÔNG TRUNCATE, KHÔNG tạo bảng hoặc sửa đổi CSDL.
    """
    print("\n" + "=" * 80)
    print(" " * 24 + "PREFLIGHT CHECKLIST (DRY RUN)")
    print("=" * 80)

    checks: List[Tuple[str, bool, str]] = []

    # 1. Kiểm tra biến môi trường
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    env_keys = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER"]
    missing_env = [k for k in env_keys if not os.getenv(k)]
    if missing_env:
        checks.append(("Biến môi trường (.env)", False, f"Thiếu: {', '.join(missing_env)}"))
    else:
        checks.append(("Biến môi trường (.env)", True, f"Host: {os.getenv('DB_HOST')}, DB: {os.getenv('DB_NAME')}"))

    # 2. Kiểm tra kết nối Database
    engine = None
    try:
        engine = get_engine()
        db_ok = test_connection(engine)
        checks.append(("Kết nối PostgreSQL", db_ok, "Kết nối thành công qua SQLAlchemy Engine" if db_ok else "Không thể kết nối"))
    except Exception as exc:
        checks.append(("Kết nối PostgreSQL", False, f"Lỗi khởi tạo engine: {exc}"))

    # 3. Kiểm tra file CSV đầu vào
    for name, p in [("CSV Vé (staging_ticket.csv)", CSV_TICKET), ("CSV F&B (staging_fnb.csv)", CSV_FNB)]:
        if p.exists() and p.stat().st_size > 0:
            checks.append((name, True, f"{p.name} ({p.stat().st_size / (1024*1024):.1f} MB)"))
        else:
            checks.append((name, False, f"Không tìm thấy hoặc rỗng: {p}"))

    # 4. Kiểm tra các file SQL quan trọng
    sql_files = [
        ("01_schema.sql", SQL_SCHEMA),
        ("01b_etl_support.sql", SQL_ETL_SUPPORT),
        ("02_seed_dims.sql", ROOT / "sql" / "02_seed_dims.sql"),
        ("03_load_facts.sql", ROOT / "sql" / "03_load_facts.sql"),
        ("03_views.sql", SQL_VIEWS),
        ("05_app_tables.sql", ROOT / "sql" / "05_app_tables.sql"),
    ]
    for name, p in sql_files:
        checks.append((f"SQL {name}", p.exists(), f"Tồn tại ({p.stat().st_size:,} bytes)" if p.exists() else "Thiếu file"))

    # 5. Kiểm tra quyền truy cập schema (nếu DB kết nối được)
    if engine:
        try:
            with engine.connect() as conn:
                res = conn.execute(text("""
                    SELECT schema_name FROM information_schema.schemata 
                    WHERE schema_name IN ('cinema_dw', 'staging', 'public')
                """)).fetchall()
                found_schemas = [r[0] for r in res]
                checks.append(("Kiểm tra Schemas hiện có", True, f"Tìm thấy: {', '.join(found_schemas)}"))
        except Exception as exc:
            checks.append(("Kiểm tra Schemas hiện có", False, f"Lỗi truy vấn: {exc}"))

    # In kết quả kiểm tra
    all_passed = True
    for name, passed, note in checks:
        icon = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_passed = False
        print(f"[{icon}] {name:<35} | {note}")

    print("=" * 80)
    if all_passed:
        print("🎉 KẾT QUẢ: TẤT CẢ ĐIỀU KIỆN TIỀN KIỂM ĐÃ SẴN SÀNG ĐỂ CHẠY PIPELINE.")
    else:
        print("⚠️ KẾT QUẢ: MỘT SỐ ĐIỀU KIỆN CHƯA ĐẠT. VUI LÒNG KHẮC PHỤC TRƯỚC KHI CHẠY CHÍNH THỨC.")
    print("=" * 80 + "\n")
    return all_passed


def run_pipeline() -> bool:
    """
    Thực thi toàn bộ pipeline ETL 10 bước theo thứ tự chuẩn.
    Dừng ngay lập tức nếu bất kỳ bước nào gặp lỗi.
    """
    run_id = str(uuid.uuid4())
    total_start = time.perf_counter()

    print("\n" + "=" * 90)
    print(f"{'CINEMA DATA WAREHOUSE — ETL PIPELINE ORCHESTRATOR':^90}")
    print(f"{'run_id: ' + run_id:^90}")
    print("=" * 90)

    engine = get_engine()
    step_records: List[Tuple[int, str, str, float]] = []

    def record_step(idx: int, name: str, status: str, elapsed: float):
        step_records.append((idx, name, status, elapsed))
        icon = "✅ OK" if status == "OK" else "❌ FAIL"
        print(f"  [{icon}] Bước {idx:02d}: {name:<42} ({elapsed:.2f}s)")

    steps = [
        (1, "Kiểm tra kết nối Database & Cấu hình", lambda: test_connection(engine)),
        (2, "Khởi tạo Schema CSDL (01_schema.sql)", lambda: execute_sql_file(engine, SQL_SCHEMA, "schema")),
        (3, "Khởi tạo Hạ tầng ETL (01b_etl_support.sql)", lambda: execute_sql_file(engine, SQL_ETL_SUPPORT, "etl_support")),
        (4, "Nạp Staging CSV (etl.load_staging)", lambda: load_staging(run_id=run_id)),
        (5, "Nạp Dimensions (etl.seed_dims)", lambda: seed_dims(run_id=run_id)),
        (6, "Mở rộng Lịch dim_date (etl.extend_dim_date)", lambda: extend_dim_date(run_id=run_id)),
        (7, "Nạp Fact Tables SQL Set-based (etl.load_facts)", lambda: load_facts(engine=engine, run_id=run_id)),
        (8, "Khởi tạo / Cập nhật Views (03_views.sql)", lambda: execute_sql_file(engine, SQL_VIEWS, "views")),
        (9, "Tổng hợp Summary Tables (etl.aggregate_summary)", lambda: run_aggregation(run_id=run_id)),
        (10, "Đối soát QC Toàn diện (etl.reconcile)", lambda: reconcile(run_id=run_id)),
    ]

    pipeline_success = True
    failed_step_name = ""

    for idx, name, func in steps:
        t0 = time.perf_counter()
        logger.info(">>> [BƯỚC %02d/10] %s...", idx, name)
        try:
            res = func()
            elapsed = time.perf_counter() - t0
            # Nếu hàm trả về False (như reconcile hoặc seed_dims thất bại)
            if res is False:
                record_step(idx, name, "FAIL", elapsed)
                pipeline_success = False
                failed_step_name = name
                break
            record_step(idx, name, "OK", elapsed)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            logger.error("Lỗi tại bước %d (%s): %s", idx, name, exc)
            record_step(idx, name, "FAIL", elapsed)
            pipeline_success = False
            failed_step_name = f"{name} (Lỗi: {exc})"
            break

    total_elapsed = time.perf_counter() - total_start

    print("\n" + "=" * 90)
    print(f"{'TỔNG KẾT THỰC THI PIPELINE':^90}")
    print("=" * 90)
    for idx, name, status, el in step_records:
        st_icon = "✅ THÀNH CÔNG" if status == "OK" else "❌ THẤT BẠI"
        print(f"  Bước {idx:02d} | {name:<50} | {st_icon:<14} | {el:>6.2f}s")
    print("-" * 90)
    print(f"  Tổng thời gian thực thi: {total_elapsed:.2f} giây")

    # In bảng log đối soát chi tiết của lần chạy này
    try:
        print_log(run_id=run_id)
    except Exception as exc:
        logger.warning("Không thể in bảng etl_log: %s", exc)

    if pipeline_success:
        print("\n🎉 PIPELINE ETL HOÀN TẤT THÀNH CÔNG 10/10 BƯỚC. DỮ LIỆU ĐÃ SẴN SÀNG CHO ANALYTICS & APP.\n")
        return True
    else:
        print(f"\n❌ PIPELINE DỪNG LẠI TẠI BƯỚC THẤT BẠI: {failed_step_name}\n")
        return False


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Cinema DW - ETL Pipeline Orchestrator")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Kiểm tra điều kiện môi trường, tệp tin và kết nối DB mà KHÔNG ghi dữ liệu hoặc sửa đổi CSDL.",
    )
    parser.add_argument(
        "--reconcile-only",
        action="store_true",
        help="Chỉ thực thi đối soát và QC dữ liệu hiện có trong Database, không nạp lại hoặc TRUNCATE.",
    )

    args = parser.parse_args()

    if args.dry_run:
        ok = run_dry_run()
        sys.exit(0 if ok else 1)

    if args.reconcile_only:
        print("\n" + "=" * 80)
        print(" " * 22 + "CHẾ ĐỘ CHỈ ĐỐI SOÁT (--reconcile-only)")
        print("=" * 80)
        run_id = str(uuid.uuid4())
        ok = reconcile(run_id=run_id)
        print_log(run_id=run_id)
        sys.exit(0 if ok else 1)

    # Chạy pipeline toàn bộ
    success = run_pipeline()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

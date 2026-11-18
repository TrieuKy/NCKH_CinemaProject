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
  - Mặc định (Full Pipeline):     python scripts/run_etl.py
  - Chỉ đối soát QC:              python scripts/run_etl.py --reconcile-only
  - Preflight kiểm tra (Dry run): python scripts/run_etl.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from contextlib import contextmanager
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
from etl.sql_utils import split_sql_statements
from etl.check_schema import validate_schema, SchemaDriftError

# Đường dẫn các file SQL cần thiết
SQL_SCHEMA      = ROOT / "sql" / "01_schema.sql"
SQL_ETL_SUPPORT = ROOT / "sql" / "01b_etl_support.sql"
SQL_VIEWS       = ROOT / "sql" / "03_views.sql"
SQL_SEED_DIMS   = ROOT / "sql" / "02_seed_dims.sql"
SQL_LOAD_FACTS  = ROOT / "sql" / "03_load_facts.sql"
SQL_APP_TABLES  = ROOT / "sql" / "05_app_tables.sql"

# Đường dẫn các file CSV staging
CSV_TICKET      = ROOT / "data" / "staging_ticket.csv"
CSV_FNB         = ROOT / "data" / "staging_fnb.csv"

# ID khóa Advisory Lock cố định cho hệ thống Cinema ETL
ETL_ADVISORY_LOCK_ID = 8492019283748291


@contextmanager
def acquire_pipeline_lock(engine: Engine, lock_id: int = ETL_ADVISORY_LOCK_ID, shared: bool = False):
    """
    Giữ khóa PostgreSQL advisory lock ở cấp session (session-level) trong suốt phiên chạy.
    - Full pipeline: Khóa độc quyền (exclusive) qua pg_try_advisory_lock.
    - Reconcile-only: Khóa chia sẻ (shared) qua pg_try_advisory_lock_shared.
    Tự động giải phóng khi pipeline hoàn tất, gặp lỗi hoặc người dùng ngắt (Ctrl+C).
    """
    lock_fn = "pg_try_advisory_lock_shared" if shared else "pg_try_advisory_lock"
    unlock_fn = "pg_advisory_unlock_shared" if shared else "pg_advisory_unlock"

    conn = engine.connect()
    acquired = False
    try:
        res = conn.execute(text(f"SELECT {lock_fn}(:lock_id)"), {"lock_id": lock_id}).scalar()
        if not res:
            mode_desc = "chia sẻ (QC)" if shared else "độc quyền (Full Pipeline)"
            raise RuntimeError(
                f"Một phiên ETL khác đang chạy trong hệ thống (không thể lấy khóa {mode_desc}). "
                "Vui lòng đợi phiên trước hoàn thành hoặc kiểm tra tiến trình đang chạy."
            )
        acquired = True
        logger.info("Đã xác lập PostgreSQL advisory lock (%s, ID: %s)", lock_fn, lock_id)
        yield conn
    finally:
        if acquired:
            try:
                conn.execute(text(f"SELECT {unlock_fn}(:lock_id)"), {"lock_id": lock_id})
                logger.info("Đã giải phóng PostgreSQL advisory lock (%s, ID: %s)", unlock_fn, lock_id)
            except Exception as unlock_err:
                logger.warning("Lỗi khi gửi lệnh giải phóng advisory lock: %s", unlock_err)
        try:
            conn.close()
        except Exception:
            pass


def execute_sql_file(engine: Engine, file_path: Path, step_name: str) -> bool:
    """Thực thi toàn bộ các câu lệnh trong file SQL trong một transaction an toàn."""
    if not file_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file SQL: {file_path}")

    sql_content = file_path.read_text(encoding="utf-8")
    statements = split_sql_statements(sql_content)

    logger.info("  Thực thi %s (%d câu lệnh)...", file_path.name, len(statements))
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    return True


def run_dry_run() -> bool:
    """
    Thực hiện kiểm tra tính sẵn sàng trước khi chạy (Preflight Checklist).
    Tuyệt đối KHÔNG ghi dữ liệu, KHÔNG TRUNCATE, KHÔNG tạo bảng hoặc sửa đổi CSDL.
    """
    print("\n" + "=" * 85)
    print(" " * 27 + "PREFLIGHT CHECKLIST (DRY RUN)")
    print("=" * 85)

    checks: List[Tuple[str, bool, str]] = []

    # 1. Kiểm tra cấu hình môi trường dựa trên đúng logic của etl/db.py
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)

    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "cinema_dw")
    db_user = os.getenv("DB_USER", "postgres")
    has_pwd = bool(os.getenv("DB_PASSWORD", ""))

    env_note = f"Host: {db_host}:{db_port}, DB: {db_name}, User: {db_user} (Mật khẩu: {'Đã đặt' if has_pwd else 'Trống/Mặc định'})"
    checks.append(("Cấu hình môi trường (etl/db.py)", True, env_note))

    # 2. Kiểm tra kết nối Database
    engine = None
    db_ok = False
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
        ("02_seed_dims.sql", SQL_SEED_DIMS),
        ("03_load_facts.sql", SQL_LOAD_FACTS),
        ("03_views.sql", SQL_VIEWS),
        ("05_app_tables.sql", SQL_APP_TABLES),
    ]
    for name, p in sql_files:
        checks.append((f"SQL {name}", p.exists(), f"Tồn tại ({p.stat().st_size:,} bytes)" if p.exists() else "Thiếu file"))

    # 5. Kiểm tra phân biệt trạng thái Database và Quyền truy cập (chỉ đọc)
    if engine and db_ok:
        try:
            with engine.connect() as conn:
                # Kiểm tra quyền DB cấp cao
                priv_row = conn.execute(text("""
                    SELECT 
                        has_database_privilege(current_user, current_database(), 'CONNECT'),
                        has_database_privilege(current_user, current_database(), 'CREATE')
                """)).fetchone()
                can_create_db = priv_row[1] if priv_row else False

                # Kiểm tra schemas hiện có
                res = conn.execute(text("""
                    SELECT schema_name FROM information_schema.schemata 
                    WHERE schema_name IN ('cinema_dw', 'staging', 'public')
                """)).fetchall()
                found_schemas = [r[0] for r in res]

                if "cinema_dw" not in found_schemas and "staging" not in found_schemas:
                    # Database chưa được khởi tạo
                    if can_create_db:
                        checks.append(("Trạng thái CSDL", True, "Database CHƯA KHỞI TẠO (Quyền CREATE schema: ĐẠT — Sẵn sàng khởi tạo)"))
                    else:
                        checks.append(("Trạng thái CSDL", False, "Database CHƯA KHỞI TẠO và người dùng THIẾU quyền CREATE schema trên DB"))
                else:
                    # Database đã được khởi tạo
                    # Kiểm tra quyền USAGE và CREATE trên các schema hiện có
                    schema_priv_ok = True
                    schema_priv_notes = []
                    for s in ["cinema_dw", "staging"]:
                        if s in found_schemas:
                            s_row = conn.execute(text("""
                                SELECT 
                                    has_schema_privilege(current_user, :s, 'USAGE'),
                                    has_schema_privilege(current_user, :s, 'CREATE')
                            """), {"s": s}).fetchone()
                            u_ok, c_ok = (s_row[0], s_row[1]) if s_row else (False, False)
                            if not (u_ok and c_ok):
                                schema_priv_ok = False
                                schema_priv_notes.append(f"{s}: thiếu {'USAGE ' if not u_ok else ''}{'CREATE' if not c_ok else ''}")
                            else:
                                schema_priv_notes.append(f"{s}: Đủ quyền USAGE+CREATE")

                    if schema_priv_ok:
                        checks.append(("Quyền Schema CSDL", True, f"Database ĐÃ KHỞI TẠO ({', '.join(schema_priv_notes)})"))
                    else:
                        checks.append(("Quyền Schema CSDL", False, f"Database ĐÃ KHỞI TẠO nhưng THIẾU QUYỀN ({', '.join(schema_priv_notes)})"))

                    # Kiểm tra Schema Drift
                    is_valid, drift_errs = validate_schema(engine)
                    if is_valid:
                        checks.append(("Kiểm tra Schema Drift", True, "Cấu trúc Fact, Dimension và Constraints khớp 100% Star Schema"))
                    else:
                        checks.append(("Kiểm tra Schema Drift", False, f"Phát hiện sai lệch ({len(drift_errs)} lỗi: {drift_errs[0]})"))

        except Exception as exc:
            checks.append(("Trạng thái CSDL & Quyền", False, f"Lỗi truy vấn metadata: {exc}"))

    # In kết quả kiểm tra
    all_passed = True
    for name, passed, note in checks:
        icon = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_passed = False
        print(f"[{icon}] {name:<35} | {note}")

    print("=" * 85)
    if all_passed:
        print("🎉 KẾT QUẢ: TẤT CẢ ĐIỀU KIỆN TIỀN KIỂM ĐÃ SẴN SÀNG ĐỂ CHẠY PIPELINE.")
    else:
        print("⚠️ KẾT QUẢ: MỘT SỐ ĐIỀU KIỆN CHƯA ĐẠT. VUI LÒNG KHẮC PHỤC TRƯỚC KHI CHẠY CHÍNH THỨC.")
    print("=" * 85 + "\n")
    return all_passed


def run_pipeline() -> bool:
    """
    Thực thi toàn bộ pipeline ETL 10 bước theo thứ tự chuẩn.
    Dừng ngay lập tức nếu bất kỳ bước nào gặp lỗi.
    Đồng bộ một run_id duy nhất và khóa advisory lock trong suốt quá trình.
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
        print(f"  [{icon}] Bước {idx:02d}: {name:<45} ({elapsed:.2f}s)")

    pipeline_success = True
    failed_step_name = ""

    # Quản lý khóa advisory lock độc quyền cho full pipeline
    try:
        with acquire_pipeline_lock(engine, lock_id=ETL_ADVISORY_LOCK_ID, shared=False):
            steps = [
                (1, "Kiểm tra kết nối Database & Cấu hình", lambda: test_connection(engine)),
                (2, "Khởi tạo Schema CSDL (01_schema.sql)", lambda: execute_sql_file(engine, SQL_SCHEMA, "schema")),
                (3, "Khởi tạo Hạ tầng ETL (01b_etl_support.sql)", lambda: execute_sql_file(engine, SQL_ETL_SUPPORT, "etl_support")),
                (4, "Nạp Staging CSV (etl.load_staging)", lambda: load_staging(run_id=run_id, engine=engine)),
                (5, "Nạp Dimensions (etl.seed_dims)", lambda: seed_dims(run_id=run_id, engine=engine)),
                (6, "Mở rộng Lịch dim_date (etl.extend_dim_date)", lambda: extend_dim_date(run_id=run_id, engine=engine)),
                (7, "Nạp Fact Tables SQL Set-based (etl.load_facts)", lambda: load_facts(engine=engine, run_id=run_id)),
                (8, "Khởi tạo / Cập nhật Views (03_views.sql)", lambda: execute_sql_file(engine, SQL_VIEWS, "views")),
                (9, "Tổng hợp Summary Tables (etl.aggregate_summary)", lambda: run_aggregation(run_id=run_id, engine=engine)),
                (10, "Đối soát QC Toàn diện (etl.reconcile)", lambda: reconcile(run_id=run_id, engine=engine)),
            ]

            for idx, name, func in steps:
                t0 = time.perf_counter()
                logger.info(">>> [BƯỚC %02d/10] %s...", idx, name)

                # Sau bước 3 (etl_log đã được tạo), ghi nhận audit log cho các bước DDL 2 & 3
                if idx == 4:
                    try:
                        with engine.begin() as conn_ddl_log:
                            conn_ddl_log.execute(text("""
                                INSERT INTO cinema_dw.etl_log (run_id, step, status, message)
                                VALUES 
                                    (:run_id, 'init_schema', 'OK', 'Khởi tạo schema CSDL (01_schema.sql) thành công'),
                                    (:run_id, 'init_etl_support', 'OK', 'Khởi tạo hạ tầng ETL (01b_etl_support.sql) thành công')
                            """), {"run_id": run_id})
                    except Exception as ddl_log_err:
                        logger.warning("Không thể ghi log DDL ban đầu: %s", ddl_log_err)

                # Kiểm tra Schema Drift trước khi nạp Fact (trước bước 7)
                if idx == 7:
                    is_valid, drift_errs = validate_schema(engine)
                    if not is_valid:
                        err_msg = f"Phát hiện Schema Drift trước khi nạp Fact: {'; '.join(drift_errs)}"
                        logger.error(err_msg)
                        elapsed = time.perf_counter() - t0
                        record_step(idx, name, "FAIL", elapsed)
                        pipeline_success = False
                        failed_step_name = f"{name} ({err_msg})"
                        break

                try:
                    res = func()
                    elapsed = time.perf_counter() - t0
                    # Nếu hàm trả về False hoặc vi phạm hợp đồng (None)
                    if res is False or res is None:
                        record_step(idx, name, "FAIL", elapsed)
                        pipeline_success = False
                        failed_step_name = name
                        break
                    record_step(idx, name, "OK", elapsed)

                    # Ghi nhận log DDL cho bước 8 (Views) sau khi hoàn tất
                    if idx == 8:
                        try:
                            with engine.begin() as conn_v_log:
                                conn_v_log.execute(text("""
                                    INSERT INTO cinema_dw.etl_log (run_id, step, status, message)
                                    VALUES (:run_id, 'init_views', 'OK', 'Khởi tạo / cập nhật Views phân tích (03_views.sql) thành công')
                                """), {"run_id": run_id})
                        except Exception as v_log_err:
                            logger.warning("Không thể ghi log Views: %s", v_log_err)

                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    logger.error("Lỗi tại bước %d (%s): %s", idx, name, exc)
                    record_step(idx, name, "FAIL", elapsed)
                    pipeline_success = False
                    failed_step_name = f"{name} (Lỗi: {exc})"
                    break

            # Ghi nhận trạng thái tổng thể của run_id vào cinema_dw.etl_log
            try:
                with engine.begin() as conn_overall:
                    conn_overall.execute(text("""
                        INSERT INTO cinema_dw.etl_log (run_id, step, status, message)
                        VALUES (:run_id, 'pipeline_summary', :status, :msg)
                    """), {
                        "run_id": run_id,
                        "status": "OK" if pipeline_success else "FAIL",
                        "msg": "Pipeline hoàn tất thành công 10/10 bước" if pipeline_success else f"Pipeline dừng tại bước: {failed_step_name}"
                    })
            except Exception as overall_err:
                logger.warning("Không thể ghi log tổng kết pipeline: %s", overall_err)

    except Exception as lock_exc:
        logger.error("Không thể khởi động pipeline: %s", lock_exc)
        print(f"\n❌ LỖI VẬN HÀNH: {lock_exc}\n")
        return False
    finally:
        engine.dispose()

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
        engine = get_engine()
        try:
            with acquire_pipeline_lock(engine, lock_id=ETL_ADVISORY_LOCK_ID, shared=True):
                ok = reconcile(run_id=run_id, engine=engine)
                print_log(run_id=run_id)
                sys.exit(0 if ok else 1)
        except Exception as exc:
            logger.error("Lỗi khi chạy đối soát: %s", exc)
            print(f"\n❌ LỖI ĐỐI SOÁT: {exc}\n")
            sys.exit(1)
        finally:
            engine.dispose()

    # Chạy pipeline toàn bộ
    success = run_pipeline()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

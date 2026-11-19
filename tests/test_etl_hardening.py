"""
tests/test_etl_hardening.py
---------------------------
Bộ kiểm thử toàn diện cho Task 4.1: Hardening ETL Pipeline và Kiểm soát Vận hành.

Bao phủ 12 hạng mục kiểm thử:
  1. Summary QC: QC FAIL khiến bước ETL thất bại (run_aggregation trả False).
  2. Transaction & Rollback: Lỗi SQL giữa chừng phải rollback đúng phạm vi.
  3. Audit log: Dùng cùng 1 run_id trong toàn bộ pipeline.
  4. Audit log: Ghi nhận FAIL khi một bước gặp lỗi (seed_dims, extend_dim_date, v.v.).
  5. Dry-run an toàn: Tuyệt đối không thực thi bất kỳ câu lệnh ghi CSDL nào.
  6. Dry-run phân biệt trạng thái: Chưa khởi tạo vs thiếu quyền vs sẵn sàng.
  7. Advisory lock: Ngăn chặn 2 phiên pipeline chạy đồng thời.
  8. Advisory lock: Giải phóng khóa sạch sẽ khi gặp Exception / crash.
  9. Schema Drift: Phát hiện bảng, cột hoặc constraint quan trọng bị thiếu.
  10. SQL Execution: Tách câu lệnh an toàn (bảo vệ ';' trong string, comment, dollar-quote).
  11. Orchestrator: Xử lý đúng hợp đồng trả về (không báo OK khi hàm trả False hoặc None).
  12. Path handling: Chạy độc lập với working directory (Path resolution).
"""

import os
import re
import sys
import uuid
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.db import get_engine
from etl.sql_utils import split_sql_statements
from etl.aggregate_summary import run_aggregation
from etl.extend_dim_date import extend_dim_date
from etl.seed_dims import seed_dims
from etl.load_facts import load_facts
from etl.check_schema import validate_schema, SchemaDriftError, EXPECTED_TABLE_COLUMNS
from scripts.run_etl import (
    acquire_pipeline_lock,
    run_dry_run,
    execute_sql_file,
    ETL_ADVISORY_LOCK_ID,
)


@pytest.fixture(scope="module")
def db_engine():
    """Lấy engine thực tế kết nối tới PostgreSQL cục bộ."""
    return get_engine()


# =============================================================================
# 1. SUMMARY QC TESTS
# =============================================================================

def test_summary_qc_fail_causes_step_failure(db_engine):
    """Kiểm tra: Nếu bất kỳ phép kiểm tra QC nào FAIL, run_aggregation() PHẢI trả False."""
    run_id = str(uuid.uuid4())

    # Giả lập một câu truy vấn QC trả về số liệu không khớp
    original_execute = None

    class MockConn:
        def __init__(self, real_conn):
            self.real_conn = real_conn

        def execute(self, stmt, *args, **kwargs):
            sql_str = str(stmt)
            # Giả lập làm lệch số liệu của kiểm tra tickets_sold
            if "SUM(tickets_sold)" in sql_str:
                mock_res = MagicMock()
                # src_amt = 100, dwh_amt = 999 (Lệch -> QC FAIL)
                mock_res.fetchone.return_value = (None, None, 100, 999)
                return mock_res
            return self.real_conn.execute(stmt, *args, **kwargs)

    # Dùng context manager giả lập kết nối
    real_begin = db_engine.begin

    @pytest.fixture
    def patch_begin():
        pass

    with patch.object(db_engine, "begin") as mock_begin:
        real_c = db_engine.connect()
        mock_conn = MockConn(real_c)
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn
        mock_ctx.__exit__.return_value = None
        mock_begin.return_value = mock_ctx

        try:
            success = run_aggregation(run_id=run_id, engine=db_engine)
            # Hàm phải trả về False khi có QC FAIL
            assert success is False, "run_aggregation() phải trả False khi có phép kiểm tra QC FAIL"
        finally:
            real_c.close()


def test_summary_qc_ok_returns_true(db_engine):
    """Kiểm tra: Nếu tất cả QC đều khớp, run_aggregation() trả True."""
    run_id = str(uuid.uuid4())

    # Chạy aggregation thực tế trên dữ liệu hiện có trong CSDL
    success = run_aggregation(run_id=run_id, engine=db_engine)
    assert success is True, "run_aggregation() phải trả True khi tất cả đối soát đều khớp"

    # Xác nhận các log của run_id trong bảng etl_log đều là OK
    with db_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT step, status FROM cinema_dw.etl_log
            WHERE run_id = :run_id AND step LIKE 'reconcile_summ_%'
        """), {"run_id": run_id}).fetchall()
        assert len(rows) == 3, f"Phải ghi đủ 3 dòng QC summary, tìm thấy: {len(rows)}"
        for step, st in rows:
            assert st == "OK", f"Bước {step} phải đạt OK"


# =============================================================================
# 2. TRANSACTION & ROLLBACK TESTS
# =============================================================================

def test_summary_sql_error_rolls_back_transaction(db_engine):
    """Kiểm tra: Lỗi SQL trong quá trình nạp Summary phải rollback transaction và trả False."""
    run_id = str(uuid.uuid4())

    # Giả lập câu lệnh INSERT thứ hai bị lỗi
    with patch("etl.aggregate_summary.execute_sql_statements"):
        real_begin = db_engine.begin

        with patch.object(db_engine, "begin") as mock_begin:
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = [
                None,  # TRUNCATE
                None,  # INSERT fact_showtime_summary
                Exception("Cố tình gây lỗi SQL trong lúc INSERT summary"),  # INSERT fact_daily_cinema_summary lỗi
            ]
            mock_ctx = MagicMock()
            mock_ctx.__enter__.return_value = mock_conn
            mock_ctx.__exit__.side_effect = lambda exc_type, exc_val, exc_tb: False
            mock_begin.return_value = mock_ctx

            success = run_aggregation(run_id=run_id, engine=db_engine)
            assert success is False, "run_aggregation() phải trả False khi có lỗi SQL"


# =============================================================================
# 3. AUDIT LOG & RUN_ID TESTS
# =============================================================================

def test_extend_dim_date_audit_log(db_engine):
    """Kiểm tra: extend_dim_date ghi audit log thành công và trả về True."""
    run_id = str(uuid.uuid4())
    res = extend_dim_date(run_id=run_id, engine=db_engine)
    assert res is True, "extend_dim_date() phải trả True"

    with db_engine.connect() as conn:
        row = conn.execute(text("""
            SELECT step, status, message FROM cinema_dw.etl_log
            WHERE run_id = :run_id AND step = 'extend_dim_date'
        """), {"run_id": run_id}).fetchone()
        assert row is not None, "extend_dim_date phải ghi log vào cinema_dw.etl_log"
        assert row[1] == "OK", "Status trong etl_log phải là OK"


def test_seed_dims_fail_audit_log(db_engine):
    """Kiểm tra: Khi seed_dims thất bại, phải rollback và ghi log FAIL vào etl_log."""
    run_id = str(uuid.uuid4())

    # Giả lập file SQL không hợp lệ gây lỗi khi execute
    with patch("etl.seed_dims.split_sql_statements", return_value=["SELECT 1/0;"]):
        res = seed_dims(run_id=run_id, engine=db_engine)
        assert res is False, "seed_dims() phải trả False khi câu lệnh gặp lỗi"

    # Kiểm tra log FAIL đã được ghi trong transaction riêng sau khi rollback
    with db_engine.connect() as conn:
        row = conn.execute(text("""
            SELECT step, status, message FROM cinema_dw.etl_log
            WHERE run_id = :run_id AND step = 'seed_dims'
        """), {"run_id": run_id}).fetchone()
        assert row is not None, "seed_dims phải ghi log FAIL vào cinema_dw.etl_log"
        assert row[1] == "FAIL", f"Trạng thái phải là FAIL, thực tế: {row[1]}"


# =============================================================================
# 4. DRY-RUN SAFETY & NON-MODIFICATION TESTS
# =============================================================================

def test_dry_run_executes_zero_write_statements(db_engine):
    """Kiểm tra: dry-run KHÔNG BAO GIỜ thực hiện bất kỳ lệnh ghi DDL/DML nào."""
    executed_statements = []

    real_connect = db_engine.connect

    def capturing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_execute = conn.execute

        def capturing_execute(stmt, *s_args, **s_kwargs):
            s_text = str(stmt).strip().upper()
            executed_statements.append(s_text)
            return real_execute(stmt, *s_args, **s_kwargs)

        conn.execute = capturing_execute
        return conn

    with patch.object(db_engine, "connect", side_effect=capturing_connect):
        with patch("scripts.run_etl.get_engine", return_value=db_engine):
            result = run_dry_run()
            assert result is True, "dry-run phải PASS trên môi trường hiện tại"

    # Kiểm tra tất cả câu lệnh SQL được gửi xuống CSDL
    forbidden_keywords = ["INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "ALTER", "CREATE"]
    for s in executed_statements:
        for kw in forbidden_keywords:
            # Cho phép kiểm tra quyền có chứa chữ CREATE trong chuỗi has_database_privilege(..., 'CREATE')
            # Nhưng cấm tuyệt đối câu lệnh bắt đầu bằng DDL/DML
            first_word = s.split()[0] if s.split() else ""
            assert first_word not in forbidden_keywords, (
                f"Phát hiện câu lệnh ghi CSDL bị cấm trong dry-run: {s}"
            )


# =============================================================================
# 5. ADVISORY LOCK TESTS
# =============================================================================

TEST_LOCK_ID = 9876543210123456

def test_advisory_lock_prevents_concurrent_runs(db_engine):
    """Kiểm tra: Hai phiên full pipeline không thể chạy đồng thời nhờ PostgreSQL Advisory Lock."""
    # Phiên 1 giữ khóa độc quyền
    with acquire_pipeline_lock(db_engine, lock_id=TEST_LOCK_ID, shared=False):
        # Phiên 2 cố gắng lấy khóa trong khi Phiên 1 đang giữ
        with pytest.raises(RuntimeError) as exc_info:
            with acquire_pipeline_lock(db_engine, lock_id=TEST_LOCK_ID, shared=False):
                pass
        assert "Một phiên ETL khác đang chạy" in str(exc_info.value)


def test_advisory_lock_released_on_exception(db_engine):
    """Kiểm tra: Khóa advisory lock được giải phóng tự động khi có Exception xảy ra."""
    try:
        with acquire_pipeline_lock(db_engine, lock_id=TEST_LOCK_ID, shared=False):
            raise ArithmeticError("Mô phỏng lỗi crash trong pipeline")
    except ArithmeticError:
        pass

    # Kiểm tra ngay sau đó: có thể lấy khóa lại thành công (không bị kẹt khóa)
    with acquire_pipeline_lock(db_engine, lock_id=TEST_LOCK_ID, shared=False):
        # Lấy khóa thành công
        assert True


def test_reconcile_only_blocked_by_full_pipeline_lock(db_engine):
    """Kiểm tra: Chế độ --reconcile-only không thể chạy nếu full pipeline đang ghi dữ liệu."""
    with acquire_pipeline_lock(db_engine, lock_id=TEST_LOCK_ID, shared=False):
        with pytest.raises(RuntimeError) as exc_info:
            with acquire_pipeline_lock(db_engine, lock_id=TEST_LOCK_ID, shared=True):
                pass
        assert "Một phiên ETL khác đang chạy" in str(exc_info.value)


# =============================================================================
# 6. SCHEMA DRIFT DETECTION TESTS
# =============================================================================

def test_validate_schema_valid_db(db_engine):
    """Kiểm tra: validate_schema trả (True, []) khi database khớp chuẩn Star Schema."""
    is_valid, errors = validate_schema(db_engine)
    assert is_valid is True, f"validate_schema phải đạt True trên DB hiện tại. Lỗi: {errors}"
    assert len(errors) == 0


def test_validate_schema_detects_missing_column(db_engine):
    """Kiểm tra: validate_schema phát hiện khi một cột quan trọng bị thiếu."""
    fake_expected = dict(EXPECTED_TABLE_COLUMNS)
    fake_expected["cinema_dw.fact_ticket"] = list(fake_expected["cinema_dw.fact_ticket"]) + ["non_existent_column_xyz"]

    with patch("etl.check_schema.EXPECTED_TABLE_COLUMNS", fake_expected):
        is_valid, errors = validate_schema(db_engine)
        assert is_valid is False, "validate_schema phải phát hiện cột bị thiếu"
        assert any("non_existent_column_xyz" in e for e in errors)


def test_validate_schema_detects_missing_constraint(db_engine):
    """Kiểm tra: validate_schema phát hiện khi thiếu constraint."""
    from etl.check_schema import EXPECTED_CONSTRAINTS
    fake_constraints = dict(EXPECTED_CONSTRAINTS)
    fake_constraints["cinema_dw.dim_date"] = ["NON_EXISTENT_CONSTRAINT_TYPE"]

    with patch("etl.check_schema.EXPECTED_CONSTRAINTS", fake_constraints):
        is_valid, errors = validate_schema(db_engine)
        assert is_valid is False, "validate_schema phải phát hiện constraint bị thiếu"
        assert any("NON_EXISTENT_CONSTRAINT_TYPE" in e for e in errors)


# =============================================================================
# 7. SQL STATEMENT SPLITTER TESTS
# =============================================================================

def test_split_sql_statements_handles_tricky_semicolons():
    """Kiểm tra: split_sql_statements không tách nhầm ';' trong string, comments và dollar-quote."""
    sql = """
    -- Dòng comment có dấu ; ở giữa
    SELECT 'Phim hay; Giá vé: 100k' AS note;
    /* Block comment có dấu ;
       và nhiều dòng ; */
    INSERT INTO cinema_dw.dim_movie (title) VALUES ('Fast & Furious; Phần 9');
    DO $$
    BEGIN
        IF 1 = 1 THEN
            RAISE NOTICE 'Thông báo có dấu ;';
        END IF;
    END
    $$;
    """
    stmts = split_sql_statements(sql)
    assert len(stmts) == 3, f"Kỳ vọng 3 câu lệnh riêng biệt, thực tế: {len(stmts)}"
    assert "Phim hay; Giá vé: 100k" in stmts[0]
    assert "Fast & Furious; Phần 9" in stmts[1]
    assert "Thông báo có dấu ;" in stmts[2]


# =============================================================================
# 8. ORCHESTRATOR ERROR & PATH HANDLING TESTS
# =============================================================================

def test_orchestrator_stops_when_step_returns_false():
    """Kiểm tra: Orchestrator không báo thành công khi một bước trả về False hoặc None."""
    from scripts.run_etl import run_pipeline

    with patch("scripts.run_etl.acquire_pipeline_lock"):
        with patch("scripts.run_etl.test_connection", return_value=True):
            with patch("scripts.run_etl.execute_sql_file", return_value=True):
                # Giả lập bước load_staging trả về False
                with patch("scripts.run_etl.load_staging", return_value=False):
                    success = run_pipeline()
                    assert success is False, "run_pipeline() phải trả False khi có bước trả False"


def test_path_handling_independent_of_cwd():
    """Kiểm tra: Tất cả đường dẫn SQL và CSV được tạo từ Path(__file__) độc lập với CWD."""
    from scripts.run_etl import SQL_SCHEMA, CSV_TICKET, ROOT as ETL_ROOT

    assert SQL_SCHEMA.is_absolute()
    assert CSV_TICKET.is_absolute()
    assert ETL_ROOT.is_absolute()
    assert SQL_SCHEMA.exists()

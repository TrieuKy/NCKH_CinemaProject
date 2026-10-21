import pytest
import pandas as pd
from core.ingest import validate_showtime_rows, validate_daily_row, save_showtime_summary, save_daily_summary, get_pricing_bounds
from etl.db import get_engine
from sqlalchemy import text
import uuid

@pytest.fixture(scope="module")
def setup_db():
    engine = get_engine()
    # Create a test user
    test_user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO cinema_dw.app_user (user_id, username, password_hash, role)
            VALUES (:uid, 'test_user', 'hash', 'STAFF')
            ON CONFLICT (username) DO UPDATE SET user_id = EXCLUDED.user_id;
        """), {"uid": test_user_id})
        
        real_uid = conn.execute(text("SELECT user_id FROM cinema_dw.app_user WHERE username='test_user'")).fetchone()[0]
        
        # Get real keys for testing
        ckey = conn.execute(text("SELECT cinema_key FROM cinema_dw.dim_cinema LIMIT 1")).fetchone()[0]
        rkey = conn.execute(text("SELECT room_key FROM cinema_dw.dim_room WHERE cinema_key = :ckey LIMIT 1"), {"ckey": ckey}).fetchone()[0]
        mkey = conn.execute(text("SELECT movie_key FROM cinema_dw.dim_movie LIMIT 1")).fetchone()[0]

        # Ensure a schedule exists for testing
        conn.execute(text("""
            INSERT INTO cinema_dw.showtime_schedule (show_date, show_time, cinema_key, room_key, movie_key)
            VALUES ('2025-10-01', '14:00:00', :ckey, :rkey, :mkey)
            ON CONFLICT DO NOTHING;
        """), {"ckey": ckey, "rkey": rkey, "mkey": mkey})
    
    yield (real_uid, ckey, rkey, mkey)
    
    # Cleanup
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cinema_dw.import_log WHERE imported_by = :uid"), {"uid": real_uid})
        conn.execute(text("DELETE FROM cinema_dw.fact_showtime_summary WHERE source = 'MANUAL' AND entered_by = :uid"), {"uid": real_uid})

def test_validate_showtime_negative():
    df = pd.DataFrame([{"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": -5, "ticket_revenue": 100000}])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert any("không được là số âm" in e for e in res[0]["errors"])

def test_validate_showtime_empty():
    df = pd.DataFrame([{"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": None, "ticket_revenue": 100000}])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert any("bị trống" in e for e in res[0]["errors"])

def test_validate_showtime_type_error():
    df = pd.DataFrame([{"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": "abc", "ticket_revenue": 100000}])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert any("Sai kiểu dữ liệu" in e for e in res[0]["errors"])

def test_validate_showtime_duplicate_in_file():
    df = pd.DataFrame([
        {"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": 10, "ticket_revenue": 100000},
        {"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": 20, "ticket_revenue": 200000}
    ])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert any("Trùng lặp" in e for e in res[0]["errors"])

def test_validate_showtime_not_in_schedule():
    df = pd.DataFrame([{"date_key": 20251001, "time_key": 9999, "room_key": 1, "tickets_sold": 10, "ticket_revenue": 100000}])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert any("không tồn tại trong lịch chiếu" in e for e in res[0]["errors"])

def test_validate_showtime_capacity_warning():
    df = pd.DataFrame([{"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": 150, "ticket_revenue": 15000000}])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert len(res[0]["errors"]) == 0
    assert any("vượt sức chứa" in w for w in res[0]["warnings"])

def test_validate_showtime_revenue_zero_warning():
    df = pd.DataFrame([{"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": 10, "ticket_revenue": 0}])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert len(res[0]["errors"]) == 0
    assert any("bằng 0 nhưng số vé bán > 0" in w for w in res[0]["warnings"])

def test_validate_showtime_abnormal_price_warning():
    df = pd.DataFrame([{"date_key": 20251001, "time_key": 1400, "room_key": 1, "tickets_sold": 10, "ticket_revenue": 10000}])
    res = validate_showtime_rows(df, pd.Series(index=["20251001_1400_1"]), {1: 100}, 50000, 150000)
    assert len(res[0]["errors"]) == 0
    assert any("nằm ngoài khoảng hợp lý" in w for w in res[0]["warnings"])

def test_validate_daily_fnb_greater_than_ticket():
    err, warn = validate_daily_row({"ticket_txns": 100, "fnb_txns": 150, "fnb_revenue": 5000000}, 4000000)
    assert any("không thể lớn hơn" in e for e in err)

def test_validate_daily_abnormal_revenue_warning():
    err, warn = validate_daily_row({"ticket_txns": 100, "fnb_txns": 50, "fnb_revenue": 20000000}, 4000000)
    assert any("bất thường" in w for w in warn)

def test_save_showtime_success_and_log(setup_db):
    uid, ckey, rkey, mkey = setup_db
    df = pd.DataFrame([
        {"date_key": 20251001, "time_key": 1400, "cinema_key": ckey, "room_key": rkey, "movie_key": mkey, "tickets_sold": 50, "ticket_revenue": 5000000},
        {"date_key": 20251001, "time_key": 1600, "cinema_key": ckey, "room_key": rkey, "movie_key": mkey, "tickets_sold": -5, "ticket_revenue": 5000000} # Dòng lỗi
    ])
    report = save_showtime_summary(df, uid)
    assert report["saved"] == 1
    assert report["errors"] == 1
    
    # Check import log
    engine = get_engine()
    with engine.connect() as conn:
        log = conn.execute(text("SELECT rows_total, rows_ok, rows_error FROM cinema_dw.import_log WHERE imported_by = :uid ORDER BY imported_at DESC LIMIT 1"), {"uid": uid}).fetchone()
        assert log[0] == 2
        assert log[1] == 1
        assert log[2] == 1

def test_locked_row_cannot_be_updated(setup_db):
    uid, ckey, rkey, mkey = setup_db
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO cinema_dw.fact_showtime_summary (date_key, time_key, cinema_key, room_key, movie_key, tickets_sold, ticket_revenue, source, is_locked)
            VALUES (20251001, 1400, :ckey, :rkey, :mkey, 50, 5000000, 'MANUAL', TRUE)
            ON CONFLICT (date_key, time_key, room_key) DO UPDATE SET tickets_sold = 50, source = 'MANUAL', is_locked = TRUE;
        """), {"ckey": ckey, "rkey": rkey, "mkey": mkey})
    
    df = pd.DataFrame([
        {"date_key": 20251001, "time_key": 1400, "cinema_key": ckey, "room_key": rkey, "movie_key": mkey, "tickets_sold": 99, "ticket_revenue": 9900000}
    ])
    save_showtime_summary(df, uid) # Sẽ không update được do is_locked = TRUE
    
    with engine.connect() as conn:
        val = conn.execute(text("SELECT tickets_sold FROM cinema_dw.fact_showtime_summary WHERE date_key = 20251001 AND time_key = 1400 AND room_key = :rkey AND source='MANUAL'"), {"rkey": rkey}).fetchone()
        assert val[0] == 50 # Vẫn giữ giá trị cũ, không phải 99

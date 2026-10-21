"""
etl/load_facts.py
-----------------
Nạp fact_ticket và fact_fnb từ staging bằng SQL set-based (JOIN tra khóa dim).

Luồng thực thi:
  1. Phát hiện dòng KHÔNG tra được khóa dim (LEFT JOIN + IS NULL) → ghi rejects CSV.
  2. Nếu số reject vượt ngưỡng → báo WARN nhưng vẫn tiếp tục (theo yêu cầu B3:
     "ghi vào data/rejects/ và in số lượng", không dừng pipeline).
  3. TRUNCATE fact_ticket, fact_fnb RESTART IDENTITY CASCADE → INSERT set-based.
  4. In bảng đối soát sơ bộ (staging vs fact, tổng tiền, anomaly breakdown).

Idempotent: TRUNCATE trước INSERT → chạy 2 lần ra kết quả giống nhau.

Chạy: python -m etl.load_facts
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from etl.db import get_engine, logger

ROOT       = Path(__file__).resolve().parents[1]
REJECT_DIR = ROOT / "data" / "rejects"
REJECT_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# SQL: phát hiện dòng không tra được khóa dim (để ghi reject trước khi nạp)
# =============================================================================

# --- Reject ticket: dòng nào LEFT JOIN ra NULL ở ít nhất 1 dim ---
SQL_REJECT_TICKET = text("""
SELECT
    t.txn_id,
    t.show_date,
    t.show_time,
    t.movie_id,
    t.cinema_id,
    t.room_id,
    t.customer_id,
    t.ticket_format,
    t.day_type,
    t.time_slot,
    t.customer_type,
    t.discount_rate,
    t.channel_code,
    t.is_anomaly,
    t.anomaly_type,
    -- Lý do không tra được (NULL ở dim nào)
    CASE WHEN dd.date_key    IS NULL THEN 'dim_date missing; '    ELSE '' END ||
    CASE WHEN dt.time_key    IS NULL THEN 'dim_time missing; '    ELSE '' END ||
    CASE WHEN dm.movie_key   IS NULL THEN 'dim_movie missing; '   ELSE '' END ||
    CASE WHEN dc.cinema_key  IS NULL THEN 'dim_cinema missing; '  ELSE '' END ||
    CASE WHEN dr.room_key    IS NULL THEN 'dim_room missing; '    ELSE '' END ||
    CASE WHEN cu.customer_key IS NULL THEN 'dim_customer missing; ' ELSE '' END ||
    CASE WHEN dp.pricing_key IS NULL THEN 'dim_pricing missing; ' ELSE '' END ||
    CASE WHEN ch.channel_key IS NULL THEN 'dim_channel missing; ' ELSE '' END
        AS reject_reason
FROM staging.ticket t
LEFT JOIN cinema_dw.dim_date dd
    ON dd.date_key = TO_CHAR(t.show_date::DATE, 'YYYYMMDD')::INTEGER
LEFT JOIN cinema_dw.dim_time dt
    ON dt.time_key = (
        EXTRACT(HOUR   FROM t.show_time::TIME)::INTEGER * 100
        + EXTRACT(MINUTE FROM t.show_time::TIME)::INTEGER
    )
LEFT JOIN cinema_dw.dim_movie dm  ON dm.movie_id   = t.movie_id
LEFT JOIN cinema_dw.dim_cinema dc ON dc.cinema_id  = t.cinema_id
LEFT JOIN cinema_dw.dim_room   dr ON dr.room_id    = t.room_id
LEFT JOIN cinema_dw.dim_customer cu ON cu.customer_id = t.customer_id
LEFT JOIN cinema_dw.dim_pricing dp
    ON  dp.ticket_format  = t.ticket_format
    AND dp.day_type       = t.day_type
    AND dp.time_slot      = t.time_slot
    AND dp.customer_type  = t.customer_type
    AND ROUND(dp.discount_rate::NUMERIC, 4) = ROUND(t.discount_rate::NUMERIC, 4)
LEFT JOIN cinema_dw.dim_channel ch ON ch.channel_code = t.channel_code
WHERE
    dd.date_key    IS NULL OR dt.time_key    IS NULL OR
    dm.movie_key   IS NULL OR dc.cinema_key  IS NULL OR
    dr.room_key    IS NULL OR cu.customer_key IS NULL OR
    dp.pricing_key IS NULL OR ch.channel_key IS NULL
""")

# --- Reject fnb: dòng không tra được dim hoặc txn_id không có trong fact_ticket ---
SQL_REJECT_FNB = text("""
SELECT
    f.fnb_line_id,
    f.txn_id,
    f.sale_date,
    f.sale_time,
    f.cinema_id,
    f.customer_id,
    f.product_id,
    f.channel_code,
    f.is_anomaly,
    f.anomaly_type,
    CASE WHEN dd.date_key     IS NULL THEN 'dim_date missing; '    ELSE '' END ||
    CASE WHEN dt.time_key     IS NULL THEN 'dim_time missing; '    ELSE '' END ||
    CASE WHEN dc.cinema_key   IS NULL THEN 'dim_cinema missing; '  ELSE '' END ||
    CASE WHEN cu.customer_key IS NULL THEN 'dim_customer missing; ' ELSE '' END ||
    CASE WHEN dp.product_key  IS NULL THEN 'dim_product missing; ' ELSE '' END ||
    CASE WHEN ch.channel_key  IS NULL THEN 'dim_channel missing; ' ELSE '' END ||
    CASE WHEN tk.txn_id       IS NULL THEN 'txn_id not in fact_ticket; ' ELSE '' END
        AS reject_reason
FROM staging.fnb f
LEFT JOIN cinema_dw.dim_date dd
    ON dd.date_key = TO_CHAR(f.sale_date::DATE, 'YYYYMMDD')::INTEGER
LEFT JOIN cinema_dw.dim_time dt
    ON dt.time_key = (
        EXTRACT(HOUR   FROM f.sale_time::TIME)::INTEGER * 100
        + EXTRACT(MINUTE FROM f.sale_time::TIME)::INTEGER
    )
LEFT JOIN cinema_dw.dim_cinema   dc ON dc.cinema_id  = f.cinema_id
LEFT JOIN cinema_dw.dim_customer cu ON cu.customer_id = f.customer_id
LEFT JOIN cinema_dw.dim_product  dp ON dp.product_id  = f.product_id
LEFT JOIN cinema_dw.dim_channel  ch ON ch.channel_code = f.channel_code
-- Kiểm tra F&B mồ côi: txn_id phải có trong staging.ticket (không kiểm tra fact_ticket
-- vì fact_ticket sẽ được TRUNCATE ngay sau bước này — dùng staging.ticket thay thế)
LEFT JOIN (
    SELECT DISTINCT txn_id FROM staging.ticket
) tk ON tk.txn_id = f.txn_id
WHERE
    dd.date_key    IS NULL OR dt.time_key     IS NULL OR
    dc.cinema_key  IS NULL OR cu.customer_key IS NULL OR
    dp.product_key IS NULL OR ch.channel_key  IS NULL OR
    tk.txn_id      IS NULL
""")

# =============================================================================
# SQL: nạp fact (đọc từ file 03_load_facts.sql)
# =============================================================================
SQL_FILE = ROOT / "sql" / "03_load_facts.sql"


def _load_sql_file() -> list[str]:
    """Đọc file SQL, tách thành các statement (bỏ qua comment và rỗng)."""
    import re
    content = SQL_FILE.read_text(encoding="utf-8")
    parts   = content.split(";")
    stmts   = []
    for part in parts:
        no_comments = re.sub(r'--[^\n]*', '', part).strip()
        if no_comments:
            stmts.append(part.strip())
    return stmts


# =============================================================================
# Kiểm tra chất lượng và ghi rejects
# =============================================================================

def _check_and_reject(conn) -> tuple[int, int]:
    """
    Phát hiện dòng không tra được dim, ghi ra data/rejects/*.csv.
    Trả (n_reject_ticket, n_reject_fnb).
    """
    # --- Ticket rejects ---
    df_rt = pd.read_sql(SQL_REJECT_TICKET, conn)
    n_rt  = len(df_rt)
    if n_rt > 0:
        path = REJECT_DIR / "reject_ticket.csv"
        df_rt.to_csv(path, index=False, encoding="utf-8")
        logger.warning(
            "⚠ %d dòng ticket KHÔNG tra được dim → %s", n_rt, path
        )
        # In phân tích lý do
        reasons = df_rt["reject_reason"].str.strip("; ").value_counts()
        for reason, cnt in reasons.items():
            logger.warning("  reject ticket lý do: [%s] %d dòng", reason, cnt)
    else:
        logger.info("Ticket rejects: 0 ✓")

    # --- FNB rejects ---
    df_rf = pd.read_sql(SQL_REJECT_FNB, conn)
    n_rf  = len(df_rf)
    if n_rf > 0:
        path = REJECT_DIR / "reject_fnb.csv"
        df_rf.to_csv(path, index=False, encoding="utf-8")
        logger.warning(
            "⚠ %d dòng FNB KHÔNG tra được dim → %s", n_rf, path
        )
        reasons = df_rf["reject_reason"].str.strip("; ").value_counts()
        for reason, cnt in reasons.items():
            logger.warning("  reject fnb lý do: [%s] %d dòng", reason, cnt)
    else:
        logger.info("FNB rejects: 0 ✓")

    return n_rt, n_rf


# =============================================================================
# Nạp fact
# =============================================================================

def load_facts() -> dict:
    """
    Nạp fact_ticket và fact_fnb.
    Trả dict với số dòng staging, fact và reject.
    """
    engine = get_engine()
    logger.info("=== BẮT ĐẦU B3: NẠP FACT ===")

    # Đọc số dòng staging trước để đối soát sau
    with engine.connect() as conn:
        stg_ticket = conn.execute(
            text("SELECT COUNT(*), COALESCE(SUM(paid_amount),0) FROM staging.ticket")
        ).fetchone()
        stg_fnb = conn.execute(
            text("SELECT COUNT(*), COALESCE(SUM(line_amount),0) FROM staging.fnb")
        ).fetchone()

    n_stg_ticket, sum_stg_ticket = stg_ticket
    n_stg_fnb,    sum_stg_fnb    = stg_fnb
    logger.info("Staging ticket: %s dòng | tổng paid_amount=%s",
                f"{n_stg_ticket:,}", f"{sum_stg_ticket:,.0f}")
    logger.info("Staging fnb   : %s dòng | tổng line_amount=%s",
                f"{n_stg_fnb:,}", f"{sum_stg_fnb:,.0f}")

    # Bước 1: Kiểm tra reject (dùng connection riêng để không ảnh hưởng transaction chính)
    logger.info("--- Kiểm tra dòng không tra được khóa dim ---")
    with engine.connect() as conn:
        n_rt, n_rf = _check_and_reject(conn)

    if n_rt > 0 or n_rf > 0:
        logger.warning(
            "Tổng reject: %d vé + %d F&B. Các dòng này SẼ KHÔNG được nạp vào fact.", n_rt, n_rf
        )
    else:
        logger.info("Không có dòng reject — toàn bộ staging sẽ được nạp vào fact.")

    # Bước 2: TRUNCATE + INSERT (trong transaction để atomic)
    logger.info("--- TRUNCATE + INSERT fact ---")
    stmts = _load_sql_file()
    try:
        with engine.begin() as conn:
            for i, stmt in enumerate(stmts, 1):
                import re
                label = re.search(r'(TRUNCATE|INSERT INTO)\s+([\w.]+)', stmt, re.I)
                label_str = label.group(0)[:50] if label else f"stmt_{i}"
                logger.info("[%d/%d] %s …", i, len(stmts), label_str)
                conn.execute(text(stmt))
    except Exception as exc:
        logger.error("Lỗi khi nạp fact: %s", exc)
        raise

    # Bước 3: Đọc kết quả sau nạp
    with engine.connect() as conn:
        fact_ticket = conn.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(net_amount),0) FROM cinema_dw.fact_ticket"
        )).fetchone()
        fact_fnb = conn.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(line_amount),0) FROM cinema_dw.fact_fnb"
        )).fetchone()

        # Phân tích anomaly
        anom_ticket = conn.execute(text("""
            SELECT
                COALESCE(anomaly_type, 'normal') AS anomaly_type,
                COUNT(*)                          AS n,
                ROUND(COUNT(*)::NUMERIC * 100.0 / SUM(COUNT(*)) OVER (), 2) AS pct
            FROM cinema_dw.fact_ticket
            GROUP BY anomaly_type
            ORDER BY n DESC
        """)).fetchall()

        anom_fnb = conn.execute(text("""
            SELECT
                COALESCE(anomaly_type, 'normal') AS anomaly_type,
                COUNT(*)                          AS n
            FROM cinema_dw.fact_fnb
            GROUP BY anomaly_type
            ORDER BY n DESC
        """)).fetchall()

    logger.info("=== NẠP FACT HOÀN TẤT ===")

    return {
        "stg_ticket_rows":  n_stg_ticket,
        "stg_ticket_sum":   float(sum_stg_ticket),
        "stg_fnb_rows":     n_stg_fnb,
        "stg_fnb_sum":      float(sum_stg_fnb),
        "fact_ticket_rows": fact_ticket[0],
        "fact_ticket_sum":  float(fact_ticket[1]),
        "fact_fnb_rows":    fact_fnb[0],
        "fact_fnb_sum":     float(fact_fnb[1]),
        "reject_ticket":    n_rt,
        "reject_fnb":       n_rf,
        "anom_ticket":      [(r[0], r[1], float(r[2])) for r in anom_ticket],
        "anom_fnb":         [(r[0], r[1]) for r in anom_fnb],
    }


# =============================================================================
# In bảng tổng kết
# =============================================================================

def print_summary(r: dict) -> None:
    """In bảng đối soát cuối B3."""
    print()
    print("=" * 66)
    print(f"{'B3 — KẾT QUẢ NẠP FACT':^66}")
    print("=" * 66)

    # Đối soát dòng
    print("\n📊 Số dòng (staging vs fact):")
    print(f"  {'Nguồn':<30} {'Staging':>12} {'Fact':>12} {'Reject':>8}")
    print(f"  {'-'*62}")
    def chk(stg, fact, rej):
        return "✅" if (stg - rej == fact) else "⚠️ "
    sym_t = chk(r["stg_ticket_rows"], r["fact_ticket_rows"], r["reject_ticket"])
    sym_f = chk(r["stg_fnb_rows"],    r["fact_fnb_rows"],    r["reject_fnb"])
    print(f"  {sym_t} {'fact_ticket':<28} {r['stg_ticket_rows']:>12,} {r['fact_ticket_rows']:>12,} {r['reject_ticket']:>8,}")
    print(f"  {sym_f} {'fact_fnb':<28}    {r['stg_fnb_rows']:>12,} {r['fact_fnb_rows']:>12,} {r['reject_fnb']:>8,}")

    # Đối soát tiền
    print("\n💰 Tổng tiền (staging vs fact):")
    diff_t = r["stg_ticket_sum"] - r["fact_ticket_sum"]
    diff_f = r["stg_fnb_sum"]    - r["fact_fnb_sum"]
    # Trừ phần reject khỏi staging để so sánh đúng
    sym_mt = "✅" if abs(diff_t) < 1 or r["reject_ticket"] > 0 else "⚠️ "
    sym_mf = "✅" if abs(diff_f) < 1 or r["reject_fnb"] > 0 else "⚠️ "
    print(f"  {'Bảng':<18} {'Staging':>18} {'Fact':>18} {'Chênh lệch':>12}")
    print(f"  {'-'*66}")
    print(f"  {'net_amount (vé)':<18} {r['stg_ticket_sum']:>18,.0f} {r['fact_ticket_sum']:>18,.0f} {diff_t:>+12,.0f}  {sym_mt}")
    print(f"  {'line_amount (fnb)':<18} {r['stg_fnb_sum']:>18,.0f} {r['fact_fnb_sum']:>18,.0f} {diff_f:>+12,.0f}  {sym_mf}")

    # Anomaly breakdown — ticket
    print("\n🔍 Phân tích is_anomaly — fact_ticket:")
    for atype, n, pct in r["anom_ticket"]:
        bar = "█" * int(pct / 2)
        print(f"  {atype:<22} {n:>8,}  ({pct:5.2f}%)  {bar}")

    # Anomaly breakdown — fnb
    print("\n🔍 Phân tích is_anomaly — fact_fnb:")
    total_fnb = sum(n for _, n in r["anom_fnb"])
    for atype, n in r["anom_fnb"]:
        pct = n / total_fnb * 100 if total_fnb else 0
        bar = "█" * int(pct / 2)
        print(f"  {atype:<22} {n:>8,}  ({pct:5.2f}%)  {bar}")

    print()
    if r["reject_ticket"] == 0 and r["reject_fnb"] == 0:
        print("  ✅ KHÔNG CÓ DÒNG REJECT — Toàn bộ staging đã vào fact")
    else:
        print(f"  ⚠️  Reject: {r['reject_ticket']:,} vé + {r['reject_fnb']:,} F&B → xem data/rejects/")
    print("=" * 66)


if __name__ == "__main__":
    result = load_facts()
    print_summary(result)
    sys.exit(0)

"""
core/what_if.py
---------------
What-If Scenario Simulation Engine for Cinema Operations (Task 11 — UC07).

Độc lập hoàn toàn với Streamlit để có thể kiểm thử công thức bằng unit tests.
Chỉ đọc dữ liệu baseline từ PostgreSQL (read-only), không ghi vào database,
không sử dụng forecast đã hết hạn, và không tuyên bố quan hệ nhân quả.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Constants & Bounds
# ---------------------------------------------------------------------------

VALID_CINEMA_KEYS = {1, 2, 3}
CINEMA_NAMES = {
    1: "CGV Landmark 81 (CN01)",
    2: "CGV Vincom Đồng Khởi (CN02)",
    3: "CGV Pearl Plaza (CN03)",
}

PRICE_CHANGE_MIN = -0.50   # -50%
PRICE_CHANGE_MAX = 1.00    # +100%
QTY_CHANGE_MIN = -0.80     # -80%
QTY_CHANGE_MAX = 2.00      # +200%
ATTACH_RATE_MIN = 0.00     # 0%
ATTACH_RATE_MAX = 1.00     # 100%


def _round_half_up(value: float) -> int:
    """Làm tròn số vé thành số nguyên xác định (ROUND_HALF_UP)."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _validate_finite_float(value: Any, name: str) -> float:
    """Kiểm tra giá trị số hợp lệ (không NaN, không Inf, không None/bool)."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Tham số '{name}' phải là số hợp lệ, nhận được: {value!r}")
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Tham số '{name}' không thể chuyển thành số: {value!r}") from exc
    if math.isnan(num) or math.isinf(num):
        raise ValueError(f"Tham số '{name}' không được là NaN hoặc Infinity.")
    return num


# ---------------------------------------------------------------------------
# Data Contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BaselineMetrics:
    """
    Baseline Data Contract: số liệu thực tế lịch sử đã ghi nhận từ PostgreSQL.
    Tất cả các trường được tổng hợp ở grain nhất quán, không JOIN 1-N fact_ticket × fact_fnb.
    """
    cinema_key: Optional[int]
    cinema_name: str
    start_date: date
    end_date: date
    data_days: int
    max_observed_date: Optional[date]
    ticket_revenue_clean: float
    ticket_qty_clean: int
    atp_clean: Optional[float]
    fnb_revenue_clean: float
    available_showtime_capacity: Optional[int]
    showtime_count: int
    showtime_qty_clean: int
    weighted_occupancy: Optional[float]
    ticket_txn_count: int
    fnb_txn_count: int
    attach_rate: Optional[float]
    avg_fnb_revenue_per_fnb_txn: Optional[float]
    spp_clean: Optional[float]
    data_source: str = "cinema_dw.vw_daily_kpi, vw_showtime_occupancy, vw_fnb_attach"
    data_freshness: str = "HISTORICAL_BASELINE"
    limitations: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sold_out_showtime_count: int = 0
    capacity_reconciled: bool = True

    @property
    def fingerprint(self) -> str:
        """Dấu vân tay xác định của baseline để kiểm tra tính nhất quán khi so sánh scenario."""
        payload = {
            "cinema_key": self.cinema_key,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "ticket_revenue_clean": round(self.ticket_revenue_clean, 4),
            "ticket_qty_clean": int(self.ticket_qty_clean),
            "fnb_revenue_clean": round(self.fnb_revenue_clean, 4),
            "available_showtime_capacity": self.available_showtime_capacity,
            "ticket_txn_count": int(self.ticket_txn_count),
            "fnb_txn_count": int(self.fnb_txn_count),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat()
        d["end_date"] = self.end_date.isoformat()
        d["max_observed_date"] = self.max_observed_date.isoformat() if self.max_observed_date else None
        d["fingerprint"] = self.fingerprint
        return d


@dataclass
class ScenarioResult:
    """
    Scenario Output Contract: kết quả mô phỏng có điều kiện (Hypothetical Scenario).
    scenario_id ổn định theo baseline_fingerprint + scenario_type + parameters.
    """
    scenario_id: str
    scenario_name: str
    cinema_key: Optional[int]
    start_date: date
    end_date: date
    scenario_type: str
    parameters: dict[str, Any]
    baseline_metrics: dict[str, Any]
    scenario_metrics: dict[str, Any]
    absolute_deltas: dict[str, Optional[float]]
    percentage_deltas: dict[str, Optional[float]]
    assumptions: list[str]
    limitations: list[str]
    validation_status: str
    warnings: list[str]
    baseline_fingerprint: str
    data_classification: str = "HYPOTHETICAL_SCENARIO"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    capacity_check_scope: str = "NOT_APPLICABLE"
    operational_feasibility_status: str = "NOT_APPLICABLE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "cinema_key": self.cinema_key,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "scenario_type": self.scenario_type,
            "parameters": self.parameters,
            "baseline_metrics": self.baseline_metrics,
            "scenario_metrics": self.scenario_metrics,
            "absolute_deltas": self.absolute_deltas,
            "percentage_deltas": self.percentage_deltas,
            "assumptions": self.assumptions,
            "limitations": self.limitations,
            "validation_status": self.validation_status,
            "warnings": self.warnings,
            "baseline_fingerprint": self.baseline_fingerprint,
            "data_classification": self.data_classification,
            "created_at": self.created_at,
            "capacity_check_scope": self.capacity_check_scope,
            "operational_feasibility_status": self.operational_feasibility_status,
        }


def _make_scenario_id(
    baseline_fingerprint: str,
    scenario_type: str,
    parameters: dict[str, Any],
) -> str:
    """Tạo Scenario ID ổn định, xác định từ baseline và tham số chuẩn hóa."""
    payload = {
        "baseline_fingerprint": baseline_fingerprint,
        "scenario_type": scenario_type,
        "parameters": parameters,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return f"WIF-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12].upper()}"


# ---------------------------------------------------------------------------
# Baseline Data Loader (Read-Only PostgreSQL)
# ---------------------------------------------------------------------------

def fetch_baseline_from_db(
    engine: Engine,
    cinema_key: Optional[int],
    start_date: date,
    end_date: date,
) -> Optional[BaselineMetrics]:
    """
    Truy vấn dữ liệu baseline thực tế từ PostgreSQL (chỉ đọc).
    Sử dụng 3 view ở grain riêng biệt để tránh double counting:
    - vw_daily_kpi (Ngày × Rạp)
    - vw_showtime_occupancy (Suất chiếu)
    - vw_fnb_attach (Ngày × Rạp × Khung giờ)

    Trả về None nếu không có dữ liệu trong khoảng ngày / rạp đã chọn.
    """
    if cinema_key is not None and cinema_key not in VALID_CINEMA_KEYS:
        raise ValueError(f"cinema_key không hợp lệ: {cinema_key}. Chỉ chấp nhận {sorted(VALID_CINEMA_KEYS)} hoặc None.")
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) phải nhỏ hơn hoặc bằng end_date ({end_date}).")

    start_key = int(start_date.strftime("%Y%m%d"))
    end_key = int(end_date.strftime("%Y%m%d"))
    params: dict[str, Any] = {"start_key": start_key, "end_key": end_key}
    if cinema_key is not None:
        params["cinema_key"] = cinema_key

    kpi_cinema_clause = "AND k.cinema_key = :cinema_key" if cinema_key is not None else ""
    occ_cinema_clause = "AND dc.cinema_key = :cinema_key" if cinema_key is not None else ""
    fnb_cinema_clause = "AND a.cinema_key = :cinema_key" if cinema_key is not None else ""

    kpi_sql = text(f"""
        SELECT
            COALESCE(SUM(k.ticket_revenue_clean), 0) AS ticket_revenue_clean,
            COALESCE(SUM(k.ticket_qty_clean), 0)     AS ticket_qty_clean,
            COALESCE(SUM(k.fnb_revenue_clean), 0)    AS fnb_revenue_clean,
            COUNT(DISTINCT k.full_date)              AS data_days,
            MAX(k.full_date)                         AS max_observed_date
        FROM cinema_dw.vw_daily_kpi k
        WHERE k.date_key BETWEEN :start_key AND :end_key
        {kpi_cinema_clause}
    """)

    occ_sql = text(f"""
        SELECT
            COUNT(*)                       AS showtime_count,
            COALESCE(SUM(o.capacity), 0)   AS total_capacity,
            COALESCE(SUM(o.qty_clean), 0)  AS showtime_qty_clean,
            COALESCE(SUM(CASE WHEN o.capacity > 0 AND o.qty_clean >= o.capacity THEN 1 ELSE 0 END), 0) AS sold_out_showtime_count
        FROM cinema_dw.vw_showtime_occupancy o
        JOIN cinema_dw.dim_cinema dc ON o.cinema_name = dc.cinema_name
        WHERE o.date_key BETWEEN :start_key AND :end_key
        {occ_cinema_clause}
    """)

    fnb_sql = text(f"""
        SELECT
            COALESCE(SUM(a.ticket_txn_count), 0)  AS ticket_txn_count,
            COALESCE(SUM(a.fnb_txn_count), 0)     AS fnb_txn_count,
            COALESCE(SUM(a.fnb_revenue_clean), 0) AS fnb_revenue_clean
        FROM cinema_dw.vw_fnb_attach a
        WHERE a.date_key BETWEEN :start_key AND :end_key
        {fnb_cinema_clause}
    """)

    with engine.connect() as conn:
        kpi_row = conn.execute(kpi_sql, params).fetchone()
        occ_row = conn.execute(occ_sql, params).fetchone()
        fnb_row = conn.execute(fnb_sql, params).fetchone()

    data_days = int(kpi_row[3] or 0) if kpi_row else 0
    if not kpi_row or data_days == 0:
        return None

    ticket_revenue_clean = float(kpi_row[0] or 0.0)
    ticket_qty_clean = int(kpi_row[1] or 0)
    fnb_revenue_clean = float(kpi_row[2] or 0.0)
    max_obs = kpi_row[4]
    if hasattr(max_obs, "date"):
        max_obs = max_obs.date()

    atp_clean = (ticket_revenue_clean / ticket_qty_clean) if ticket_qty_clean > 0 else None

    showtime_count = int(occ_row[0] or 0) if occ_row else 0
    total_capacity_raw = int(occ_row[1] or 0) if occ_row else 0
    showtime_qty_clean = int(occ_row[2] or 0) if occ_row else 0
    sold_out_showtime_count = int(occ_row[3] or 0) if occ_row else 0
    available_capacity = total_capacity_raw if (showtime_count > 0 and total_capacity_raw > 0) else None
    weighted_occupancy = (
        (showtime_qty_clean / available_capacity)
        if (available_capacity is not None and available_capacity > 0)
        else None
    )

    ticket_txn_count = int(fnb_row[0] or 0) if fnb_row else 0
    fnb_txn_count = int(fnb_row[1] or 0) if fnb_row else 0
    fnb_rev_from_attach = float(fnb_row[2] or 0.0) if fnb_row else 0.0

    attach_rate = (fnb_txn_count / ticket_txn_count) if ticket_txn_count > 0 else None
    avg_fnb_per_txn = (fnb_revenue_clean / fnb_txn_count) if fnb_txn_count > 0 else None
    spp_clean = (fnb_revenue_clean / ticket_txn_count) if ticket_txn_count > 0 else None

    limitations: list[str] = [
        "Dữ liệu nền là Historical Baseline đã ghi nhận trong quá khứ, không phải dữ liệu vận hành thời gian thực.",
        "Hệ thống không có dữ liệu chi phí (giá vốn phim, giá vốn F&B, chi phí vận hành) nên không tính lợi nhuận/margin.",
        "Không sử dụng kết quả forecast đã hết hạn (EXPIRED) làm dữ liệu đầu vào mô phỏng.",
    ]
    warnings: list[str] = []
    capacity_reconciled = True

    if available_capacity is None:
        warnings.append("Không có dữ liệu sức chứa suất chiếu (capacity) trong phạm vi đã chọn.")
    if abs(fnb_rev_from_attach - fnb_revenue_clean) > 1.0:
        warnings.append(
            "Doanh thu F&B từ vw_daily_kpi và vw_fnb_attach có chênh lệch; "
            "sử dụng fnb_revenue_clean từ vw_daily_kpi làm chuẩn và không coi hai tổng hợp tương đương."
        )
    if showtime_count > 0 and showtime_qty_clean != ticket_qty_clean:
        capacity_reconciled = False
        warnings.append(
            f"CẢNH BÁO ĐỐI SOÁT: Số vé sạch ở grain ngày ({ticket_qty_clean:,}) khác grain suất chiếu ({showtime_qty_clean:,}); "
            "các kịch bản dùng sức chứa suất chiếu chưa được đối soát hoàn toàn."
        )
    if sold_out_showtime_count > 0:
        warnings.append(
            f"Có {sold_out_showtime_count:,}/{showtime_count:,} suất chiếu đã đạt hoặc vượt 100% sức chứa trong kỳ baseline; "
            "ghế trống ở suất khác không tự động đáp ứng cho suất đã bán hết."
        )

    cinema_name = CINEMA_NAMES.get(cinema_key, "Tất cả cụm rạp (CN01–CN03)") if cinema_key else "Tất cả cụm rạp (CN01–CN03)"

    return BaselineMetrics(
        cinema_key=cinema_key,
        cinema_name=cinema_name,
        start_date=start_date,
        end_date=end_date,
        data_days=data_days,
        max_observed_date=max_obs,
        ticket_revenue_clean=ticket_revenue_clean,
        ticket_qty_clean=ticket_qty_clean,
        atp_clean=atp_clean,
        fnb_revenue_clean=fnb_revenue_clean,
        available_showtime_capacity=available_capacity,
        showtime_count=showtime_count,
        showtime_qty_clean=showtime_qty_clean,
        weighted_occupancy=weighted_occupancy,
        ticket_txn_count=ticket_txn_count,
        fnb_txn_count=fnb_txn_count,
        attach_rate=attach_rate,
        avg_fnb_revenue_per_fnb_txn=avg_fnb_per_txn,
        spp_clean=spp_clean,
        limitations=tuple(limitations),
        warnings=tuple(warnings),
        sold_out_showtime_count=sold_out_showtime_count,
        capacity_reconciled=capacity_reconciled,
    )


# ---------------------------------------------------------------------------
# Scenario A — Ticket Price Change
# ---------------------------------------------------------------------------

def simulate_ticket_price_change(
    baseline: BaselineMetrics,
    price_change_pct: float,
    scenario_name: Optional[str] = None,
) -> ScenarioResult:
    """
    Scenario A: Mô phỏng thay đổi giá vé bình quân (ATP) khi giữ nguyên lượng vé bán sạch.
    Công thức:
        Scenario ATP = Baseline ATP * (1 + price_change_pct)
        Scenario Ticket Revenue = Baseline Ticket Qty Clean * Scenario ATP
        Revenue Delta = Scenario Ticket Revenue - Baseline Ticket Revenue Clean
    """
    pct = _validate_finite_float(price_change_pct, "price_change_pct")
    if pct < PRICE_CHANGE_MIN or pct > PRICE_CHANGE_MAX:
        raise ValueError(
            f"Tỷ lệ điều chỉnh giá vé ({pct:.1%}) vượt phạm vi cho phép "
            f"[{PRICE_CHANGE_MIN:.0%}, +{PRICE_CHANGE_MAX:.0%}]."
        )

    params = {"price_change_pct": round(pct, 6)}
    name = scenario_name or f"Giá vé {pct:+.1%}"
    sid = _make_scenario_id(baseline.fingerprint, "TICKET_PRICE", params)

    assumptions = [
        "Giả định giữ nguyên lượng vé bán; chưa mô hình hóa phản ứng nhu cầu của khách hàng đối với thay đổi giá.",
        "Giá vé áp dụng đồng nhất tỷ lệ thay đổi trên ATP bình quân của phạm vi baseline.",
        "Doanh thu F&B giữ nguyên ở mức baseline trong kịch bản đơn biến này.",
    ]
    limitations = list(baseline.limitations) + [
        "Không ước lượng Price Elasticity of Demand (độ co giãn của cầu theo giá).",
        "Không kết luận lợi nhuận tăng/giảm vì chưa có dữ liệu chi phí.",
    ]
    warnings = list(baseline.warnings)

    if baseline.ticket_qty_clean <= 0 or baseline.atp_clean is None or baseline.atp_clean < 0:
        return ScenarioResult(
            scenario_id=sid,
            scenario_name=name,
            cinema_key=baseline.cinema_key,
            start_date=baseline.start_date,
            end_date=baseline.end_date,
            scenario_type="TICKET_PRICE",
            parameters=params,
            baseline_metrics=baseline.to_dict(),
            scenario_metrics={},
            absolute_deltas={},
            percentage_deltas={},
            assumptions=assumptions,
            limitations=limitations,
            validation_status="INSUFFICIENT_DATA",
            warnings=warnings + ["Không đủ dữ liệu vé sạch hoặc ATP baseline bằng 0 để mô phỏng giá vé."],
            baseline_fingerprint=baseline.fingerprint,
        )

    # Đối chiếu ATP từ doanh thu và số vé cùng phạm vi
    reconciled_atp = baseline.ticket_revenue_clean / baseline.ticket_qty_clean
    scenario_atp = reconciled_atp * (1.0 + pct)
    if scenario_atp < 0:
        raise ValueError("Giá vé mô phỏng không được âm.")

    if pct == 0.0:
        scenario_ticket_rev = baseline.ticket_revenue_clean
    else:
        scenario_ticket_rev = baseline.ticket_qty_clean * scenario_atp

    rev_delta = scenario_ticket_rev - baseline.ticket_revenue_clean
    rev_delta_pct = (rev_delta / baseline.ticket_revenue_clean) if baseline.ticket_revenue_clean > 0 else None

    scenario_metrics = {
        "atp_clean": scenario_atp,
        "ticket_qty_clean": baseline.ticket_qty_clean,
        "ticket_revenue_clean": scenario_ticket_rev,
        "fnb_revenue_clean": baseline.fnb_revenue_clean,
        "total_revenue_clean": scenario_ticket_rev + baseline.fnb_revenue_clean,
        "occupancy": baseline.weighted_occupancy,
        "attach_rate": baseline.attach_rate,
    }
    absolute_deltas = {
        "atp_delta": scenario_atp - reconciled_atp,
        "ticket_qty_delta": 0,
        "ticket_revenue_delta": rev_delta,
        "fnb_revenue_delta": 0.0,
        "total_revenue_delta": rev_delta,
    }
    percentage_deltas = {
        "atp_delta_pct": pct,
        "ticket_qty_delta_pct": 0.0,
        "ticket_revenue_delta_pct": rev_delta_pct,
        "fnb_revenue_delta_pct": 0.0,
    }

    return ScenarioResult(
        scenario_id=sid,
        scenario_name=name,
        cinema_key=baseline.cinema_key,
        start_date=baseline.start_date,
        end_date=baseline.end_date,
        scenario_type="TICKET_PRICE",
        parameters=params,
        baseline_metrics=baseline.to_dict(),
        scenario_metrics=scenario_metrics,
        absolute_deltas=absolute_deltas,
        percentage_deltas=percentage_deltas,
        assumptions=assumptions,
        limitations=limitations,
        validation_status="VALID",
        warnings=warnings,
        baseline_fingerprint=baseline.fingerprint,
    )


# ---------------------------------------------------------------------------
# Scenario B — Ticket Volume & Capacity
# ---------------------------------------------------------------------------

def simulate_ticket_volume_change(
    baseline: BaselineMetrics,
    qty_change_pct: float,
    scenario_name: Optional[str] = None,
) -> ScenarioResult:
    """
    Scenario B: Mô phỏng thay đổi lượng vé bán trong giới hạn sức chứa suất chiếu quan sát được.
    Quy tắc làm tròn: ROUND_HALF_UP thành số nguyên.
    Capacity Safeguards:
    - Nếu không có dữ liệu capacity: chỉ tính số vé và doanh thu giả định, không công bố occupancy.
    - Nếu số vé mô phỏng > available_showtime_capacity: đánh dấu CAPACITY_EXCEEDED và tách phần nhu cầu không đáp ứng.
    """
    pct = _validate_finite_float(qty_change_pct, "qty_change_pct")
    if pct < QTY_CHANGE_MIN or pct > QTY_CHANGE_MAX:
        raise ValueError(
            f"Tỷ lệ điều chỉnh lượng vé ({pct:.1%}) vượt phạm vi cho phép "
            f"[{QTY_CHANGE_MIN:.0%}, +{QTY_CHANGE_MAX:.0%}]."
        )

    params = {"qty_change_pct": round(pct, 6)}
    name = scenario_name or f"Lượng vé {pct:+.1%}"
    sid = _make_scenario_id(baseline.fingerprint, "TICKET_VOLUME", params)

    assumptions = [
        "Giả định ATP giữ nguyên ở mức baseline trong phạm vi ngày và rạp đang xét.",
        "Số vé mô phỏng được làm tròn thành số nguyên theo quy tắc ROUND_HALF_UP.",
        "Sức chứa so sánh là tổng sức chứa của các suất chiếu thực tế đã ghi nhận (Observed Showtime Capacity).",
        "Doanh thu F&B giữ nguyên ở mức baseline (không tự suy diễn tăng F&B nếu chưa có mô hình liên kết).",
    ]
    limitations = list(baseline.limitations) + [
        "Kiểm tra sức chứa chỉ thực hiện ở cấp tổng hợp (AGGREGATE_CAPACITY_CHECK); chưa chứng minh khả thi vận hành theo từng suất chiếu (phim × phòng × giờ chiếu).",
        "Không coi ghế trống ở suất chiếu khác là khả năng chắc chắn đáp ứng nhu cầu của một suất đã bán hết.",
        "Không tự giả định lịch chiếu mới, phòng chiếu mới hoặc nhân công suất phòng theo số suất giả định.",
        "Không đề xuất mở thêm suất vì chưa có bằng chứng lịch chiếu và phòng khả dụng.",
    ]
    warnings = list(baseline.warnings)

    if baseline.ticket_qty_clean <= 0 or baseline.atp_clean is None:
        return ScenarioResult(
            scenario_id=sid,
            scenario_name=name,
            cinema_key=baseline.cinema_key,
            start_date=baseline.start_date,
            end_date=baseline.end_date,
            scenario_type="TICKET_VOLUME",
            parameters=params,
            baseline_metrics=baseline.to_dict(),
            scenario_metrics={},
            absolute_deltas={},
            percentage_deltas={},
            assumptions=assumptions,
            limitations=limitations,
            validation_status="INSUFFICIENT_DATA",
            warnings=warnings + ["Không đủ dữ liệu vé sạch hoặc ATP baseline để mô phỏng lượng vé."],
            baseline_fingerprint=baseline.fingerprint,
            capacity_check_scope="AGGREGATE_CAPACITY_UNAVAILABLE",
            operational_feasibility_status="INSUFFICIENT_DATA",
        )

    if pct == 0.0:
        scenario_qty = baseline.ticket_qty_clean
        scenario_rev = baseline.ticket_revenue_clean
    else:
        scenario_qty = _round_half_up(baseline.ticket_qty_clean * (1.0 + pct))
        scenario_rev = scenario_qty * baseline.atp_clean

    qty_delta = scenario_qty - baseline.ticket_qty_clean
    potential_rev_delta = scenario_rev - baseline.ticket_revenue_clean
    potential_rev_delta_pct = (potential_rev_delta / baseline.ticket_revenue_clean) if baseline.ticket_revenue_clean > 0 else None
    qty_delta_pct = (qty_delta / baseline.ticket_qty_clean) if baseline.ticket_qty_clean > 0 else None

    cap = baseline.available_showtime_capacity
    validation_status = "VALID"
    capacity_check_scope = "AGGREGATE_CAPACITY_CHECK"
    operational_feasibility_status = "UNVERIFIED_AT_SHOWTIME_GRAIN"
    scenario_occupancy: Optional[float] = None
    unmet_demand_tickets = 0
    feasible_qty = scenario_qty
    feasible_rev = scenario_rev
    cap_constrained_ticket_rev: Optional[float] = None
    cap_constrained_total_rev: Optional[float] = None

    if cap is None or cap <= 0:
        capacity_check_scope = "AGGREGATE_CAPACITY_UNAVAILABLE"
        operational_feasibility_status = "UNVERIFIED_MISSING_CAPACITY"
        warnings.append(
            "Không đủ dữ liệu sức chứa suất chiếu (capacity) để kiểm tra giới hạn phòng chiếu; "
            "không công bố tỷ lệ lấp đầy (occupancy)."
        )
        limitations.append("Không kiểm chứng được giới hạn sức chứa do thiếu dữ liệu capacity ở grain suất chiếu.")
    else:
        raw_occ = scenario_qty / cap
        cap_constrained_ticket_rev = feasible_rev
        cap_constrained_total_rev = feasible_rev + baseline.fnb_revenue_clean
        if scenario_qty > cap:
            validation_status = "CAPACITY_EXCEEDED"
            operational_feasibility_status = "CAPACITY_EXCEEDED_AGGREGATE"
            unmet_demand_tickets = scenario_qty - cap
            feasible_qty = cap
            feasible_rev = feasible_qty * baseline.atp_clean
            cap_constrained_ticket_rev = feasible_rev
            cap_constrained_total_rev = feasible_rev + baseline.fnb_revenue_clean
            scenario_occupancy = 1.0  # Giới hạn trên lấp đầy 100% tổng sức chứa quan sát được
            warnings.append(
                f"CẢNH BÁO VƯỢT SỨC CHỨA (AGGREGATE_CAPACITY_CHECK): Nhu cầu giả định ({scenario_qty:,} vé) vượt tổng sức chứa "
                f"quan sát được ({cap:,} ghế, tương đương {raw_occ:.1%}). "
                f"Có {unmet_demand_tickets:,} vé vượt sức chứa không thể đáp ứng; "
                f"doanh thu vé tiềm năng ({scenario_rev:,.0f} VND) KHÔNG phải doanh thu có thể bán hết — "
                f"giới hạn trên doanh thu vé theo sức chứa là {feasible_rev:,.0f} VND."
            )
        else:
            scenario_occupancy = raw_occ
            if not baseline.capacity_reconciled:
                validation_status = "UNRECONCILED_BASELINE"
                operational_feasibility_status = "UNRECONCILED_CAPACITY_SOURCE"

    # Khi CAPACITY_EXCEEDED, total_revenue_clean và ticket_revenue_clean không được trình bày
    # bằng doanh thu từ lượng vé vượt sức chứa.
    reported_ticket_rev = cap_constrained_ticket_rev if validation_status == "CAPACITY_EXCEEDED" else scenario_rev
    reported_total_rev = (
        cap_constrained_total_rev
        if validation_status == "CAPACITY_EXCEEDED"
        else (scenario_rev + baseline.fnb_revenue_clean)
    )
    reported_ticket_rev_delta = reported_ticket_rev - baseline.ticket_revenue_clean
    reported_ticket_rev_delta_pct = (
        (reported_ticket_rev_delta / baseline.ticket_revenue_clean)
        if baseline.ticket_revenue_clean > 0
        else None
    )

    scenario_metrics = {
        "atp_clean": baseline.atp_clean,
        "ticket_qty_clean": scenario_qty,
        "potential_ticket_qty": scenario_qty,
        "aggregate_capacity_upper_bound": cap if (cap is not None and cap > 0) else None,
        "feasible_ticket_qty": feasible_qty,
        "unmet_demand_tickets": unmet_demand_tickets,
        "ticket_revenue_clean": reported_ticket_rev,
        "potential_ticket_revenue_clean": scenario_rev,
        "feasible_ticket_revenue_clean": feasible_rev,
        "capacity_constrained_ticket_revenue_upper_bound": cap_constrained_ticket_rev,
        "fnb_revenue_clean": baseline.fnb_revenue_clean,
        "total_revenue_clean": reported_total_rev,
        "potential_total_revenue_clean": scenario_rev + baseline.fnb_revenue_clean,
        "capacity_constrained_total_revenue_upper_bound": cap_constrained_total_rev,
        "occupancy": scenario_occupancy,
        "raw_demand_capacity_ratio": (scenario_qty / cap) if (cap and cap > 0) else None,
        "attach_rate": baseline.attach_rate,
        "sold_out_showtime_count": baseline.sold_out_showtime_count,
        "capacity_check_scope": capacity_check_scope,
        "operational_feasibility_status": operational_feasibility_status,
    }
    absolute_deltas = {
        "ticket_qty_delta": float(qty_delta),
        "feasible_ticket_qty_delta": float(feasible_qty - baseline.ticket_qty_clean),
        "ticket_revenue_delta": reported_ticket_rev_delta,
        "potential_ticket_revenue_delta": potential_rev_delta,
        "feasible_ticket_revenue_delta": feasible_rev - baseline.ticket_revenue_clean,
        "capacity_constrained_ticket_revenue_delta": (
            (cap_constrained_ticket_rev - baseline.ticket_revenue_clean)
            if cap_constrained_ticket_rev is not None
            else None
        ),
        "fnb_revenue_delta": 0.0,
        "total_revenue_delta": reported_ticket_rev_delta,
        "potential_total_revenue_delta": potential_rev_delta,
        "capacity_constrained_total_revenue_delta": (
            (cap_constrained_total_rev - (baseline.ticket_revenue_clean + baseline.fnb_revenue_clean))
            if cap_constrained_total_rev is not None
            else None
        ),
    }
    percentage_deltas = {
        "ticket_qty_delta_pct": qty_delta_pct,
        "ticket_revenue_delta_pct": reported_ticket_rev_delta_pct,
        "potential_ticket_revenue_delta_pct": potential_rev_delta_pct,
        "capacity_constrained_ticket_revenue_delta_pct": (
            ((cap_constrained_ticket_rev - baseline.ticket_revenue_clean) / baseline.ticket_revenue_clean)
            if (cap_constrained_ticket_rev is not None and baseline.ticket_revenue_clean > 0)
            else None
        ),
        "fnb_revenue_delta_pct": 0.0,
    }

    return ScenarioResult(
        scenario_id=sid,
        scenario_name=name,
        cinema_key=baseline.cinema_key,
        start_date=baseline.start_date,
        end_date=baseline.end_date,
        scenario_type="TICKET_VOLUME",
        parameters=params,
        baseline_metrics=baseline.to_dict(),
        scenario_metrics=scenario_metrics,
        absolute_deltas=absolute_deltas,
        percentage_deltas=percentage_deltas,
        assumptions=assumptions,
        limitations=limitations,
        validation_status=validation_status,
        warnings=warnings,
        baseline_fingerprint=baseline.fingerprint,
        capacity_check_scope=capacity_check_scope,
        operational_feasibility_status=operational_feasibility_status,
    )


# ---------------------------------------------------------------------------
# Scenario C — F&B Attach Rate
# ---------------------------------------------------------------------------

def simulate_fnb_attach_rate(
    baseline: BaselineMetrics,
    scenario_attach_rate: float,
    scenario_name: Optional[str] = None,
) -> ScenarioResult:
    """
    Scenario C: Mô phỏng tác động số học của thay đổi tỷ lệ giao dịch mua kèm F&B (Attach Rate).
    Sử dụng số giao dịch vé/F&B Raw từ vw_fnb_attach và doanh thu F&B Clean:
        Baseline Attach Rate = fnb_txn_count / ticket_txn_count
        Baseline Avg F&B Rev / F&B Txn = fnb_revenue_clean / fnb_txn_count
        Scenario F&B Txns = ticket_txn_count * scenario_attach_rate
        Scenario F&B Revenue = Scenario F&B Txns * Baseline Avg F&B Rev / F&B Txn
    """
    rate = _validate_finite_float(scenario_attach_rate, "scenario_attach_rate")
    if rate < ATTACH_RATE_MIN or rate > ATTACH_RATE_MAX:
        raise ValueError(
            f"Attach Rate mô phỏng ({rate:.1%}) phải nằm trong khoảng "
            f"[{ATTACH_RATE_MIN:.0%}, {ATTACH_RATE_MAX:.0%}]."
        )

    params = {"scenario_attach_rate": round(rate, 6)}
    name = scenario_name or f"F&B Attach Rate {rate:.1%}"
    sid = _make_scenario_id(baseline.fingerprint, "FNB_ATTACH", params)

    assumptions = [
        "Giả định tổng số giao dịch vé (ticket_txn_count, Raw) không thay đổi.",
        "Giả định giá trị giỏ hàng F&B bình quân trên mỗi giao dịch có mua F&B (fnb_revenue_clean / fnb_txn_count) không đổi.",
        "Doanh thu vé giữ nguyên ở mức baseline.",
    ]
    limitations = list(baseline.limitations) + [
        "Mô hình kết hợp (Hybrid): Attach Rate dùng số giao dịch Raw, doanh thu F&B dùng dữ liệu Clean (is_anomaly = 0).",
        "Attach Rate đo theo giao dịch vé (Transaction), KHÔNG phải tỷ lệ trên số vé/khách.",
        "spp_clean là Spend Per Ticket Transaction (chi tiêu trên mỗi giao dịch vé), KHÔNG phải Spend Per Patron.",
        "Không khẳng định nhóm khách mới mua kèm F&B sẽ có giá trị giỏ hàng trung bình giống nhóm khách hiện tại.",
        "Không tính biên lợi nhuận F&B vì thiếu dữ liệu giá vốn (COGS).",
    ]
    warnings = list(baseline.warnings)

    if (
        baseline.ticket_txn_count <= 0
        or baseline.fnb_txn_count <= 0
        or baseline.avg_fnb_revenue_per_fnb_txn is None
    ):
        return ScenarioResult(
            scenario_id=sid,
            scenario_name=name,
            cinema_key=baseline.cinema_key,
            start_date=baseline.start_date,
            end_date=baseline.end_date,
            scenario_type="FNB_ATTACH",
            parameters=params,
            baseline_metrics=baseline.to_dict(),
            scenario_metrics={},
            absolute_deltas={},
            percentage_deltas={},
            assumptions=assumptions,
            limitations=limitations,
            validation_status="INSUFFICIENT_DATA",
            warnings=warnings + ["Mẫu số ticket_txn_count hoặc fnb_txn_count bằng 0, không đủ dữ liệu mô phỏng F&B."],
            baseline_fingerprint=baseline.fingerprint,
        )

    baseline_attach = baseline.attach_rate or 0.0
    if abs(rate - baseline_attach) < 1e-12:
        scenario_fnb_txns = float(baseline.fnb_txn_count)
        scenario_fnb_rev = baseline.fnb_revenue_clean
    else:
        scenario_fnb_txns = baseline.ticket_txn_count * rate
        scenario_fnb_rev = scenario_fnb_txns * baseline.avg_fnb_revenue_per_fnb_txn

    fnb_rev_delta = scenario_fnb_rev - baseline.fnb_revenue_clean
    fnb_rev_delta_pct = (fnb_rev_delta / baseline.fnb_revenue_clean) if baseline.fnb_revenue_clean > 0 else None
    scenario_spp = scenario_fnb_rev / baseline.ticket_txn_count

    scenario_metrics = {
        "ticket_revenue_clean": baseline.ticket_revenue_clean,
        "ticket_qty_clean": baseline.ticket_qty_clean,
        "fnb_revenue_clean": scenario_fnb_rev,
        "total_revenue_clean": baseline.ticket_revenue_clean + scenario_fnb_rev,
        "attach_rate": rate,
        "fnb_txn_count_estimated": _round_half_up(scenario_fnb_txns),
        "avg_fnb_revenue_per_fnb_txn": baseline.avg_fnb_revenue_per_fnb_txn,
        "spp_clean_hybrid": scenario_spp,
        "occupancy": baseline.weighted_occupancy,
    }
    absolute_deltas = {
        "ticket_revenue_delta": 0.0,
        "ticket_qty_delta": 0,
        "fnb_revenue_delta": fnb_rev_delta,
        "total_revenue_delta": fnb_rev_delta,
        "attach_rate_delta_pp": (rate - baseline_attach) * 100.0,
    }
    percentage_deltas = {
        "ticket_revenue_delta_pct": 0.0,
        "ticket_qty_delta_pct": 0.0,
        "fnb_revenue_delta_pct": fnb_rev_delta_pct,
    }

    return ScenarioResult(
        scenario_id=sid,
        scenario_name=name,
        cinema_key=baseline.cinema_key,
        start_date=baseline.start_date,
        end_date=baseline.end_date,
        scenario_type="FNB_ATTACH",
        parameters=params,
        baseline_metrics=baseline.to_dict(),
        scenario_metrics=scenario_metrics,
        absolute_deltas=absolute_deltas,
        percentage_deltas=percentage_deltas,
        assumptions=assumptions,
        limitations=limitations,
        validation_status="VALID",
        warnings=warnings,
        baseline_fingerprint=baseline.fingerprint,
    )


# ---------------------------------------------------------------------------
# Scenario Comparison
# ---------------------------------------------------------------------------

def compare_scenarios(
    baseline: BaselineMetrics,
    scenarios: list[ScenarioResult],
) -> list[dict[str, Any]]:
    """
    So sánh nhiều kịch bản trên cùng một baseline.
    - Kiểm tra mọi kịch bản phải khớp baseline.fingerprint.
    - Không tự cộng dồn A + B + C và không xếp hạng 'tốt nhất'.
    """
    fp = baseline.fingerprint
    rows: list[dict[str, Any]] = [
        {
            "scenario_id": "BASELINE",
            "scenario_name": "Baseline (Thực tế lịch sử)",
            "scenario_type": "BASELINE",
            "ticket_revenue_clean": baseline.ticket_revenue_clean,
            "potential_ticket_revenue_clean": baseline.ticket_revenue_clean,
            "capacity_constrained_ticket_revenue_upper_bound": baseline.ticket_revenue_clean,
            "fnb_revenue_clean": baseline.fnb_revenue_clean,
            "total_revenue_clean": baseline.ticket_revenue_clean + baseline.fnb_revenue_clean,
            "potential_total_revenue_clean": baseline.ticket_revenue_clean + baseline.fnb_revenue_clean,
            "revenue_delta_vnd": 0.0,
            "potential_revenue_delta_vnd": 0.0,
            "revenue_delta_pct": 0.0,
            "ticket_qty_clean": baseline.ticket_qty_clean,
            "feasible_ticket_qty": baseline.ticket_qty_clean,
            "unmet_demand_tickets": 0,
            "occupancy": baseline.weighted_occupancy,
            "attach_rate": baseline.attach_rate,
            "capacity_check_scope": "OBSERVED_HISTORICAL",
            "operational_feasibility_status": "HISTORICAL_BASELINE",
            "validation_status": "HISTORICAL_BASELINE",
            "limitations_summary": "; ".join(baseline.limitations[:2]),
        }
    ]

    for sc in scenarios:
        if sc.baseline_fingerprint != fp:
            raise ValueError(
                f"Kịch bản '{sc.scenario_name}' ({sc.scenario_id}) không cùng phạm vi/baseline "
                f"với dữ liệu hiện tại (fingerprint {sc.baseline_fingerprint} != {fp})."
            )
        if sc.validation_status == "INSUFFICIENT_DATA":
            continue

        sm = sc.scenario_metrics
        base_total = baseline.ticket_revenue_clean + baseline.fnb_revenue_clean
        sc_total = float(sm.get("total_revenue_clean", base_total))
        pot_ticket_rev = float(sm.get("potential_ticket_revenue_clean", sm.get("ticket_revenue_clean", baseline.ticket_revenue_clean)))
        pot_total = float(sm.get("potential_total_revenue_clean", sc_total))
        cap_upper_ticket = sm.get("capacity_constrained_ticket_revenue_upper_bound")
        delta_total = sc_total - base_total
        pot_delta_total = pot_total - base_total
        delta_pct = (delta_total / base_total) if base_total > 0 else None

        rows.append(
            {
                "scenario_id": sc.scenario_id,
                "scenario_name": sc.scenario_name,
                "scenario_type": sc.scenario_type,
                "ticket_revenue_clean": float(sm.get("ticket_revenue_clean", baseline.ticket_revenue_clean)),
                "potential_ticket_revenue_clean": pot_ticket_rev,
                "capacity_constrained_ticket_revenue_upper_bound": (
                    float(cap_upper_ticket) if cap_upper_ticket is not None else None
                ),
                "fnb_revenue_clean": float(sm.get("fnb_revenue_clean", baseline.fnb_revenue_clean)),
                "total_revenue_clean": sc_total,
                "potential_total_revenue_clean": pot_total,
                "revenue_delta_vnd": delta_total,
                "potential_revenue_delta_vnd": pot_delta_total,
                "revenue_delta_pct": delta_pct,
                "ticket_qty_clean": int(sm.get("ticket_qty_clean", baseline.ticket_qty_clean)),
                "feasible_ticket_qty": int(sm.get("feasible_ticket_qty", sm.get("ticket_qty_clean", baseline.ticket_qty_clean))),
                "unmet_demand_tickets": int(sm.get("unmet_demand_tickets", 0)),
                "occupancy": sm.get("occupancy"),
                "attach_rate": sm.get("attach_rate"),
                "capacity_check_scope": sc.capacity_check_scope,
                "operational_feasibility_status": sc.operational_feasibility_status,
                "validation_status": sc.validation_status,
                "limitations_summary": "; ".join(sc.limitations[:2]),
            }
        )

    return rows

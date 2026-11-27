"""
core/dss.py
-----------
Decision Support System (DSS) Engine — Deterministic, Explainable, Evidence-Backed.

Tổng hợp dữ liệu sạch từ 4 nguồn:
  1. KPI vận hành (`vw_daily_kpi`, `vw_showtime_occupancy`, `vw_fnb_attach`, `vw_anomaly_showtime`)
  2. Phân khúc khách hàng RFM (`rfm_cluster_profile`, `model_metrics`)
  3. Dự báo nhu cầu vé 14 ngày (`forecast_result`, `model_metrics`, `dim_room`)
  4. Khai phá đánh giá khách hàng (`review_raw`, `review_nlp`, tái sử dụng `reviews/insights.py`)

Nguyên tắc khoa học:
  - Deterministic: cùng đầu vào -> cùng decision_id và danh sách quyết định.
  - Explainable: mọi quyết định có `explanation` (gì xảy ra, rule nào, vì sao, quản lý cần kiểm tra gì).
  - Evidence-backed: mọi quyết định gắn `evidence` truy vết được về số liệu/bản ghi gốc.
  - Safe: không tự thực thi hành động kinh doanh (`status = "PROPOSED"`), không lưu PII,
    không suy rộng review CN01 sang rạp khác, không dùng dữ liệu bất thường cho KPI sạch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from reviews.insights import generate_operational_insights

logger = logging.getLogger("dss")

# ---------------------------------------------------------------------------
# Hằng số & Ngưỡng Rule Catalog
# ---------------------------------------------------------------------------
VALID_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

VALID_CATEGORIES = (
    "REVENUE_KPI",
    "RETENTION_RFM",
    "CAPACITY_FORECAST",
    "SERVICE_QUALITY_REVIEW",
)

# Ngưỡng mặc định cho từng rule
DEFAULT_THRESHOLDS: Dict[str, Any] = {
    "kpi_rev_drop_high_pct": -15.0,       # Giảm >= 15% so với kỳ trước -> HIGH
    "kpi_rev_drop_critical_pct": -25.0,   # Giảm >= 25% so với kỳ trước -> CRITICAL
    "kpi_occ_low_medium": 0.35,           # Weighted occupancy < 35% -> MEDIUM
    "kpi_occ_low_high": 0.25,             # Weighted occupancy < 25% -> HIGH
    "kpi_fnb_attach_min": 0.35,           # Attach rate < 35% -> MEDIUM
    "kpi_fnb_attach_drop_pp": -5.0,       # Giảm >= 5 điểm % -> HIGH
    "kpi_anom_showtime_pct_high": 30.0,   # >= 30% suất có giao dịch bất thường -> HIGH
    "rfm_expected_business_k": 6,         # 6 nhóm nghiệp vụ tham chiếu
    "rfm_churn_share_pct": 10.0,          # Nhóm Có nguy cơ rời bỏ >= 10% -> HIGH
    "rfm_vip_min_count": 1,               # Có nhóm VIP -> LOW/MEDIUM retention check
    "fc_high_demand_ratio": 0.80,         # yhat >= 80% sức chứa theo lịch chiếu (date x cinema x slot) -> HIGH
    "fc_spike_vs_hist_ratio": 1.25,       # yhat >= 1.25x trung bình lịch sử cùng rạp & khung giờ -> MEDIUM (Demand Surge)
    "fc_horizon_days": 14,                # Horizon dự báo yêu cầu: 14 ngày tới (> reference_date)
    "rev_min_sample": 10,                 # Tối thiểu 10 reviews mới sinh insight
    "max_stale_days": 30,                 # Quá 30 ngày so với ref_date -> dữ liệu quá hạn
}

VALID_MODES = ("operational", "historical_audit")

RULE_CATALOG: List[Dict[str, str]] = [
    {
        "rule_id": "KPI-REV-DROP",
        "category": "REVENUE_KPI",
        "objective": "Phát hiện sụt giảm doanh thu sạch (vé + F&B) theo rạp so với kỳ liền trước (đủ 7 ngày/kỳ).",
        "trigger": "pct_change <= -15.0% (HIGH), <= -25.0% (CRITICAL)",
        "source_model": "kpi_dw",
    },
    {
        "rule_id": "KPI-DATA-GAP",
        "category": "REVENUE_KPI",
        "objective": "Cảnh báo thiếu ngày lịch, trùng full_date hoặc NULL/NaN trong cửa sổ so sánh KPI 7 ngày.",
        "trigger": "unique_days < 7 hoặc duplicate full_date hoặc NULL/NaN KPI (MEDIUM)",
        "source_model": "kpi_dw",
    },
    {
        "rule_id": "KPI-OCC-LOW",
        "category": "REVENUE_KPI",
        "objective": "Phát hiện rạp có tỷ lệ lấp đầy sạch (gia quyền theo sức chứa) thấp.",
        "trigger": "weighted_occupancy < 0.35 (MEDIUM), < 0.25 (HIGH)",
        "source_model": "kpi_dw",
    },
    {
        "rule_id": "KPI-FNB-DROP",
        "category": "REVENUE_KPI",
        "objective": "Phát hiện suy giảm hiệu quả bán kèm F&B (attach_rate).",
        "trigger": "attach_rate < 0.35 (MEDIUM) hoặc giảm >= 5 điểm % (HIGH)",
        "source_model": "kpi_dw",
    },
    {
        "rule_id": "KPI-ANOM-HIGH",
        "category": "REVENUE_KPI",
        "objective": "Cảnh báo tỷ lệ suất chiếu có giao dịch bất thường cao, cần kiểm toán dữ liệu.",
        "trigger": "anomaly_showtime_pct >= 30.0% (HIGH)",
        "source_model": "kpi_dw",
    },
    {
        "rule_id": "RFM-K-MISMATCH",
        "category": "RETENTION_RFM",
        "objective": "Cảnh báo lệch số cụm giữa mô hình RFM (k chọn theo Silhouette) và 6 nhóm nghiệp vụ.",
        "trigger": "actual_k != 6 (MEDIUM)",
        "source_model": "rfm_kmeans",
    },
    {
        "rule_id": "RFM-CHURN-RISK",
        "category": "RETENTION_RFM",
        "objective": "Đề xuất rà soát chiến dịch giữ chân cho nhóm 'Có nguy cơ rời bỏ'.",
        "trigger": "cluster 'Có nguy cơ rời bỏ' chiếm >= 10.0% hội viên (HIGH)",
        "source_model": "rfm_kmeans",
    },
    {
        "rule_id": "RFM-VIP-RETENTION",
        "category": "RETENTION_RFM",
        "objective": "Đề xuất duy trì quyền lợi và cá nhân hóa trải nghiệm cho nhóm 'VIP'.",
        "trigger": "cluster 'VIP' tồn tại với >= 1 hội viên (LOW)",
        "source_model": "rfm_kmeans",
    },
    {
        "rule_id": "FC-PEAK-DEMAND",
        "category": "CAPACITY_FORECAST",
        "objective": "Capacity Pressure Alert: cảnh báo áp lực sức chứa thực sự khi đã có lịch chiếu xác nhận ở grain ngày × rạp × khung giờ.",
        "trigger": "có lịch chiếu và yhat >= 0.80 * SUM(capacity suất chiếu hợp lệ) (HIGH)",
        "source_model": "forecast",
    },
    {
        "rule_id": "FC-DEMAND-SURGE",
        "category": "CAPACITY_FORECAST",
        "objective": "Demand Surge Alert: cảnh báo nhu cầu dự báo tăng tương đối so với trung bình lịch sử cùng rạp & khung giờ khi chưa có lịch chiếu.",
        "trigger": "chưa có lịch chiếu và yhat >= 1.25 * hist_slot_mean (MEDIUM)",
        "source_model": "forecast",
    },
    {
        "rule_id": "FC-DATA-GAP",
        "category": "CAPACITY_FORECAST",
        "objective": "Cảnh báo thiếu dữ liệu dự báo, thiếu một phần horizon 14 ngày, hoặc toàn bộ dự báo đã hết hạn.",
        "trigger": "future_days < 14 hoặc all target_date <= reference_date (MEDIUM)",
        "source_model": "forecast",
    },
    {
        "rule_id": "REV-TOPIC-ALERT",
        "category": "SERVICE_QUALITY_REVIEW",
        "objective": "Chuyển kết quả khai phá review theo chủ đề thành khuyến nghị cải thiện vận hành.",
        "trigger": "n >= 10 và priority in ('TRUNG BÌNH', 'ƯU TIÊN CAO', 'KHẨN CẤP')",
        "source_model": "review_nlp",
    },
]


# ---------------------------------------------------------------------------
# Decision Output Contract
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    """Cấu trúc đầu ra thống nhất cho mọi khuyến nghị DSS."""

    decision_id: str
    rule_id: str
    cinema_key: Optional[int]
    time_scope: str
    category: str
    severity: str
    title: str
    recommendation: str
    explanation: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    source_model: str
    as_of_date: str
    limitations: List[str] = field(default_factory=list)
    status: str = "PROPOSED"

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity '{self.severity}'. Must be one of {VALID_SEVERITIES}.")
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{self.category}'. Must be one of {VALID_CATEGORIES}.")
        if self.status != "PROPOSED":
            raise ValueError("DSS decisions must remain 'PROPOSED' and never auto-execute.")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_decision_id(rule_id: str, cinema_key: Optional[int], time_scope: str, sub_key: str = "") -> str:
    """Sinh định danh duy nhất, tất định (deterministic) cho mỗi quyết định."""
    raw = f"{rule_id}|{cinema_key}|{time_scope}|{sub_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"DSS-{rule_id}-{digest}"


def _to_iso_date(val: Any) -> Optional[str]:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    if isinstance(val, pd.Timestamp):
        return val.date().isoformat()
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val)[:10]


def _is_stale(as_of_str: Optional[str], reference_date: date, max_stale_days: int) -> Optional[int]:
    """Trả về số ngày trễ nếu dữ liệu cũ hơn `max_stale_days`, ngược lại None."""
    if not as_of_str:
        return None
    try:
        d = date.fromisoformat(as_of_str[:10])
    except ValueError:
        return None
    age = (reference_date - d).days
    return age if age > max_stale_days else None


def _downgrade_severity(severity: str) -> str:
    """Giảm cấp severity 1 bậc khi dữ liệu nguồn đã quá hạn (không cho phép hành động khẩn cấp trên dữ liệu cũ)."""
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    idx = order.index(severity)
    return order[max(0, idx - 1)]


def _apply_freshness_policy(
    severity: str,
    title: str,
    source_date_str: Optional[str],
    reference_date: date,
    max_stale_days: int,
    source_label: str,
    mode: str = "operational",
    is_actionable: bool = True,
) -> Tuple[str, str, Dict[str, Any], List[str]]:
    """
    Áp dụng chính sách Data Freshness nhất quán cho mọi nguồn DSS:
      - Tính số ngày trễ (`age_days`) so với `reference_date`.
      - Nếu `mode == "historical_audit"`: gắn nhãn [LỊCH SỬ / AUDIT], giảm cấp nếu là khuyến nghị hành động.
      - Nếu quá hạn (`age_days > max_stale_days`) và `is_actionable=True`:
        giảm cấp severity 1 bậc + gắn nhãn `[DỮ LIỆU QUÁ HẠN - PHÂN TÍCH LỊCH SỬ]` để không phát khuyến nghị hành động ngay trên dữ liệu cũ.
    """
    stale_days = _is_stale(source_date_str, reference_date, max_stale_days)
    age_days: Optional[int] = None
    if source_date_str:
        try:
            age_days = (reference_date - date.fromisoformat(source_date_str[:10])).days
        except ValueError:
            age_days = None

    notes: List[str] = []
    out_sev = severity
    out_title = title
    decision_mode = "HISTORICAL_AUDIT" if mode == "historical_audit" else "OPERATIONAL"

    if mode == "historical_audit":
        if is_actionable:
            out_sev = _downgrade_severity(severity)
        out_title = f"[LỊCH SỬ / AUDIT] {title}"
        notes.append(
            f"Chế độ đánh giá lịch sử (historical_audit) tại reference_date={reference_date.isoformat()}; không phải khuyến nghị vận hành hiện tại."
        )
    elif stale_days is not None:
        decision_mode = "HISTORICAL_ANALYSIS_ONLY"
        if is_actionable:
            out_sev = _downgrade_severity(severity)
            out_title = f"[DỮ LIỆU QUÁ HẠN] {title}"
            notes.append(
                f"Dữ liệu {source_label} đã cũ {stale_days} ngày (as_of={source_date_str}, reference_date={reference_date.isoformat()}, ngưỡng={max_stale_days} ngày); đã giảm cấp mức độ từ {severity} xuống {out_sev} (chỉ có giá trị phân tích lịch sử, không dùng để hành động vận hành ngay)."
            )
        else:
            notes.append(
                f"Dữ liệu {source_label} đã cũ {stale_days} ngày so với ngày tham chiếu {reference_date.isoformat()}."
            )

    freshness = {
        "source_label": source_label,
        "source_as_of_date": source_date_str,
        "reference_date": reference_date.isoformat(),
        "age_days": age_days,
        "max_stale_days": max_stale_days,
        "is_stale": stale_days is not None,
        "decision_mode": decision_mode,
        "original_severity": severity,
        "effective_severity": out_sev,
    }
    return out_sev, out_title, freshness, notes


# ---------------------------------------------------------------------------
# 1. Đánh giá KPI Doanh thu & Hiệu quả Vận hành (D4)
# ---------------------------------------------------------------------------
def _validate_7d_revenue_window(
    grp: pd.DataFrame,
    curr_start: pd.Timestamp,
    max_date: date,
    prev_start: pd.Timestamp,
    prev_end: pd.Timestamp,
) -> Dict[str, Any]:
    """
    Kiểm tra tính đầy đủ và sạch của cửa sổ so sánh 7 ngày vs 7 ngày cho một rạp:
      - Đủ 7 ngày lịch duy nhất ở cả hai kỳ.
      - Không trùng lặp `full_date`.
      - Không có giá trị NULL/NaN trong `ticket_revenue_clean` (hoặc `fnb_revenue_clean` nếu cột tồn tại).
    """
    win = grp[(grp["full_date"] >= prev_start) & (grp["full_date"] <= pd.Timestamp(max_date))].copy()
    win["_d"] = win["full_date"].dt.date

    dup_dates = sorted([d.isoformat() for d, cnt in win["_d"].value_counts().items() if cnt > 1])

    rev_cols = ["ticket_revenue_clean"]
    if "fnb_revenue_clean" in win.columns:
        rev_cols.append("fnb_revenue_clean")
    null_mask = pd.Series(False, index=win.index)
    for col in rev_cols:
        null_mask = null_mask | pd.to_numeric(win[col], errors="coerce").isna()
    null_count = int(null_mask.sum())

    curr_g = win[win["full_date"] >= curr_start]
    prev_g = win[(win["full_date"] >= prev_start) & (win["full_date"] <= prev_end)]

    expected_curr = {(curr_start.date() + timedelta(days=i)) for i in range(7)}
    expected_prev = {(prev_start.date() + timedelta(days=i)) for i in range(7)}
    actual_curr = set(curr_g["_d"].unique())
    actual_prev = set(prev_g["_d"].unique())

    missing_curr = sorted(d.isoformat() for d in (expected_curr - actual_curr))
    missing_prev = sorted(d.isoformat() for d in (expected_prev - actual_prev))

    reasons: List[str] = []
    if dup_dates:
        reasons.append("duplicate_full_date")
    if null_count > 0:
        reasons.append("null_or_nan_kpi")
    if len(actual_curr) != 7 or len(actual_prev) != 7:
        reasons.append("incomplete_7d_window")

    return {
        "is_valid": len(reasons) == 0,
        "reasons": reasons,
        "curr_g": curr_g,
        "prev_g": prev_g,
        "days_current": int(len(actual_curr)),
        "days_previous": int(len(actual_prev)),
        "missing_dates_current": missing_curr,
        "missing_dates_previous": missing_prev,
        "duplicate_dates": dup_dates,
        "null_rows": null_count,
    }


def evaluate_kpi_rules(
    daily_kpi_df: Optional[pd.DataFrame],
    occupancy_df: Optional[pd.DataFrame] = None,
    fnb_attach_df: Optional[pd.DataFrame] = None,
    anomaly_df: Optional[pd.DataFrame] = None,
    reference_date: Optional[date] = None,
    thresholds: Optional[Dict[str, Any]] = None,
    mode: str = "operational",
) -> List[Decision]:
    """
    Đánh giá các rule KPI từ dữ liệu sạch (`*_clean`).
    So sánh cửa sổ 7 ngày gần nhất với 7 ngày liền trước theo từng `cinema_key`.
    Bắt buộc đủ 7 ngày lịch duy nhất cho mỗi kỳ, không trùng ngày và không NULL/NaN.
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decisions: List[Decision] = []
    ref_date = reference_date or date.today()

    if daily_kpi_df is None or daily_kpi_df.empty:
        return decisions

    df = daily_kpi_df.copy()
    df["full_date"] = pd.to_datetime(df["full_date"], errors="coerce")
    df = df.dropna(subset=["full_date", "cinema_key"])
    if mode == "historical_audit":
        df = df[df["full_date"] <= pd.Timestamp(ref_date)]
    if df.empty:
        return decisions

    max_date = df["full_date"].max().date()

    curr_start = pd.Timestamp(max_date) - pd.Timedelta(days=6)
    prev_start = curr_start - pd.Timedelta(days=7)
    prev_end = curr_start - pd.Timedelta(days=1)
    time_scope = f"{curr_start.date().isoformat()}..{max_date.isoformat()}"

    for cinema_key, grp in df.groupby("cinema_key"):
        ck = int(cinema_key)
        cinema_name = str(grp["cinema_name"].iloc[0]) if "cinema_name" in grp.columns else f"CN{ck:02d}"

        check = _validate_7d_revenue_window(grp, curr_start, max_date, prev_start, prev_end)
        if not check["is_valid"]:
            # Nếu hoàn toàn không có dữ liệu ở một kỳ (0 ngày) hoặc thiếu ngày / trùng ngày / NULL -> cảnh báo kiểm toán
            if check["days_current"] > 0 or check["days_previous"] > 0:
                gap_title = f"[{cinema_name}] Dữ liệu KPI doanh thu 7 ngày không đủ điều kiện so sánh ({','.join(check['reasons'])})"
                eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                    "MEDIUM",
                    gap_title,
                    max_date.isoformat(),
                    ref_date,
                    cfg["max_stale_days"],
                    "KPI (vw_daily_kpi)",
                    mode=mode,
                    is_actionable=False,
                )
                decisions.append(
                    Decision(
                        decision_id=make_decision_id("KPI-DATA-GAP", ck, time_scope, sub_key="rev_7d"),
                        rule_id="KPI-DATA-GAP",
                        cinema_key=ck,
                        time_scope=time_scope,
                        category="REVENUE_KPI",
                        severity=eff_sev,
                        title=eff_title,
                        recommendation="Kiểm tra dữ liệu nguồn fact_daily_cinema_summary / vw_daily_kpi trước khi kết luận biến động doanh thu.",
                        explanation={
                            "decision_mode": freshness["decision_mode"],
                            "what_happened": (
                                f"Cửa sổ so sánh 7 ngày tại {cinema_name} không hợp lệ: "
                                f"kỳ hiện tại {check['days_current']}/7 ngày, kỳ trước {check['days_previous']}/7 ngày, "
                                f"trùng ngày={len(check['duplicate_dates'])}, NULL/NaN={check['null_rows']}."
                            ),
                            "why_triggered": f"Rule KPI-DATA-GAP kích hoạt do {', '.join(check['reasons'])}.",
                            "why_recommended": "Không được so sánh tổng doanh thu 3 ngày với 7 ngày hoặc tự điền 0 cho ngày thiếu.",
                            "manager_checks": [
                                "Kiểm tra nhật ký ETL cho các ngày bị thiếu hoặc trùng lặp.",
                                "Xác nhận ngày thiếu là mất dữ liệu hay rạp ngừng hoạt động thực tế.",
                            ],
                        },
                        evidence=[
                            {
                                "metric": "kpi_revenue_window_completeness",
                                "reasons": check["reasons"],
                                "days_current": check["days_current"],
                                "days_previous": check["days_previous"],
                                "missing_dates_current": check["missing_dates_current"],
                                "missing_dates_previous": check["missing_dates_previous"],
                                "duplicate_dates": check["duplicate_dates"],
                                "null_rows": check["null_rows"],
                                "freshness": freshness,
                            }
                        ],
                        source_model="kpi_dw",
                        as_of_date=max_date.isoformat(),
                        limitations=[
                            "Không tự điền doanh thu thiếu bằng 0; bỏ qua KPI-REV-DROP để tránh cảnh báo sụt giảm giả.",
                            *stale_notes,
                        ],
                    )
                )
            continue

        curr_g = check["curr_g"]
        prev_g = check["prev_g"]

        # Chỉ dùng doanh thu sạch (Clean Data Only)
        curr_rev = float(
            pd.to_numeric(curr_g["ticket_revenue_clean"]).sum()
            + (pd.to_numeric(curr_g["fnb_revenue_clean"]).sum() if "fnb_revenue_clean" in curr_g.columns else 0.0)
        )
        prev_rev = float(
            pd.to_numeric(prev_g["ticket_revenue_clean"]).sum()
            + (pd.to_numeric(prev_g["fnb_revenue_clean"]).sum() if "fnb_revenue_clean" in prev_g.columns else 0.0)
        )

        if prev_rev > 0:
            rev_pct_change = ((curr_rev - prev_rev) / prev_rev) * 100.0
            if rev_pct_change <= cfg["kpi_rev_drop_high_pct"]:
                raw_sev = "CRITICAL" if rev_pct_change <= cfg["kpi_rev_drop_critical_pct"] else "HIGH"
                base_title = f"[{cinema_name}] Doanh thu sạch 7 ngày giảm {abs(rev_pct_change):.1f}%"
                eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                    raw_sev,
                    base_title,
                    max_date.isoformat(),
                    ref_date,
                    cfg["max_stale_days"],
                    "KPI (vw_daily_kpi)",
                    mode=mode,
                    is_actionable=True,
                )
                limitations = [
                    "Biến động doanh thu 7 ngày chỉ phản ánh tương quan thời điểm, chưa kiểm soát lịch phát hành phim mới hoặc ngày lễ.",
                    *stale_notes,
                ]

                decisions.append(
                    Decision(
                        decision_id=make_decision_id("KPI-REV-DROP", ck, time_scope),
                        rule_id="KPI-REV-DROP",
                        cinema_key=ck,
                        time_scope=time_scope,
                        category="REVENUE_KPI",
                        severity=eff_sev,
                        title=eff_title,
                        recommendation="Rà soát cơ cấu suất chiếu, lịch phim đang chiếu và hiệu quả kênh bán vé tại rạp.",
                        explanation={
                            "decision_mode": freshness["decision_mode"],
                            "what_happened": f"Tổng doanh thu sạch (vé + F&B) tại {cinema_name} đạt {curr_rev:,.0f} đ, giảm {abs(rev_pct_change):.1f}% so với 7 ngày trước ({prev_rev:,.0f} đ).",
                            "why_triggered": f"Rule KPI-REV-DROP kích hoạt khi mức giảm <= {cfg['kpi_rev_drop_high_pct']}% (đủ 7/7 ngày mỗi kỳ).",
                            "why_recommended": "Cần kiểm tra xem sụt giảm đến từ lượng khách (vé), giá vé bình quân (ATP), hay lịch phim trước khi điều chỉnh chính sách.",
                            "manager_checks": [
                                "Kiểm tra số suất chiếu và phim mới phát hành trong tuần tại rạp.",
                                "Đối chiếu tỷ lệ giao dịch bất thường (is_anomaly=1) xem có phải do lọc dữ liệu lỗi.",
                            ],
                        },
                        evidence=[
                            {
                                "metric": "clean_total_revenue_7d",
                                "current_value": round(curr_rev, 2),
                                "previous_value": round(prev_rev, 2),
                                "pct_change": round(rev_pct_change, 2),
                                "days_current": 7,
                                "days_previous": 7,
                                "freshness": freshness,
                            }
                        ],
                        source_model="kpi_dw",
                        as_of_date=max_date.isoformat(),
                        limitations=limitations,
                    )
                )

    # Rule KPI-OCC-LOW: dùng occupancy_clean gia quyền theo capacity
    if occupancy_df is not None and not occupancy_df.empty:
        occ = occupancy_df.copy()
        occ["full_date"] = pd.to_datetime(occ["full_date"], errors="coerce")
        occ = occ.dropna(subset=["full_date", "cinema_key", "qty_clean", "capacity"])
        occ = occ[occ["capacity"] > 0]
        if mode == "historical_audit":
            occ = occ[occ["full_date"] <= pd.Timestamp(ref_date)]
        if not occ.empty:
            occ_max = occ["full_date"].max().date()
            occ_start = pd.Timestamp(occ_max) - pd.Timedelta(days=6)
            occ_scope = f"{occ_start.date().isoformat()}..{occ_max.isoformat()}"
            recent_occ = occ[occ["full_date"] >= occ_start]
            for cinema_key, grp in recent_occ.groupby("cinema_key"):
                ck = int(cinema_key)
                cinema_name = str(grp["cinema_name"].iloc[0]) if "cinema_name" in grp.columns else f"CN{ck:02d}"
                total_qty = float(grp["qty_clean"].sum())
                total_cap = float(grp["capacity"].sum())
                if total_cap <= 0:
                    continue
                weighted_occ = total_qty / total_cap
                if weighted_occ < cfg["kpi_occ_low_medium"]:
                    raw_sev = "HIGH" if weighted_occ < cfg["kpi_occ_low_high"] else "MEDIUM"
                    base_title = f"[{cinema_name}] Tỷ lệ lấp đầy sạch thấp ({weighted_occ * 100:.1f}%)"
                    eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                        raw_sev,
                        base_title,
                        occ_max.isoformat(),
                        ref_date,
                        cfg["max_stale_days"],
                        "Occupancy (vw_showtime_occupancy)",
                        mode=mode,
                        is_actionable=True,
                    )
                    decisions.append(
                        Decision(
                            decision_id=make_decision_id("KPI-OCC-LOW", ck, occ_scope),
                            rule_id="KPI-OCC-LOW",
                            cinema_key=ck,
                            time_scope=occ_scope,
                            category="REVENUE_KPI",
                            severity=eff_sev,
                            title=eff_title,
                            recommendation="Rà soát phân bổ suất chiếu các khung giờ thấp điểm (<30%) và cân nhắc tối ưu lịch phòng.",
                            explanation={
                                "decision_mode": freshness["decision_mode"],
                                "what_happened": f"Tỷ lệ lấp đầy sạch gia quyền theo sức chứa 7 ngày gần nhất tại {cinema_name} đạt {weighted_occ * 100:.1f}% ({int(total_qty):,}/{int(total_cap):,} ghế).",
                                "why_triggered": f"Rule KPI-OCC-LOW kích hoạt khi weighted_occupancy < {cfg['kpi_occ_low_medium'] * 100:.0f}%.",
                                "why_recommended": "Lấp đầy thấp làm tăng chi phí vận hành trên mỗi vé bán; cần xem xét lại khung giờ hoặc phòng chiếu lớn.",
                                "manager_checks": [
                                    "Kiểm tra danh sách suất chiếu có occupancy_clean < 30% trong 7 ngày qua.",
                                    "Đánh giá xem rạp có đang xếp phim cũ vào phòng công suất lớn hay không.",
                                ],
                            },
                            evidence=[
                                {
                                    "metric": "weighted_occupancy_clean_7d",
                                    "value": round(weighted_occ, 4),
                                    "qty_clean": int(total_qty),
                                    "total_capacity": int(total_cap),
                                    "showtimes": int(len(grp)),
                                    "freshness": freshness,
                                }
                            ],
                            source_model="kpi_dw",
                            as_of_date=occ_max.isoformat(),
                            limitations=[
                                "Không tính trung bình cộng giản đơn occupancy; đã gia quyền theo sức chứa phòng.",
                                *stale_notes,
                            ],
                        )
                    )

    # Rule KPI-FNB-DROP: dùng attach_rate (Raw) theo docs/kpi_dictionary.md, kiểm tra đủ 7 ngày lịch
    if fnb_attach_df is not None and not fnb_attach_df.empty:
        fnb = fnb_attach_df.copy()
        fnb["full_date"] = pd.to_datetime(fnb["full_date"], errors="coerce")
        fnb = fnb.dropna(subset=["full_date", "cinema_key", "ticket_txn_count", "fnb_txn_count"])
        if mode == "historical_audit":
            fnb = fnb[fnb["full_date"] <= pd.Timestamp(ref_date)]
        if not fnb.empty:
            fnb_max = fnb["full_date"].max().date()
            fnb_curr_start = pd.Timestamp(fnb_max) - pd.Timedelta(days=6)
            fnb_prev_start = fnb_curr_start - pd.Timedelta(days=7)
            fnb_prev_end = fnb_curr_start - pd.Timedelta(days=1)
            fnb_scope = f"{fnb_curr_start.date().isoformat()}..{fnb_max.isoformat()}"

            for cinema_key, grp in fnb.groupby("cinema_key"):
                ck = int(cinema_key)
                cinema_name = str(grp["cinema_name"].iloc[0]) if "cinema_name" in grp.columns else f"CN{ck:02d}"
                c_g = grp[grp["full_date"] >= fnb_curr_start]
                p_g = grp[(grp["full_date"] >= fnb_prev_start) & (grp["full_date"] <= fnb_prev_end)]

                c_days = int(c_g["full_date"].dt.date.nunique())
                p_days = int(p_g["full_date"].dt.date.nunique())
                if c_days < 7:
                    eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                        "MEDIUM",
                        f"[{cinema_name}] Dữ liệu F&B attach_rate 7 ngày chưa đủ ({c_days}/7 ngày)",
                        fnb_max.isoformat(),
                        ref_date,
                        cfg["max_stale_days"],
                        "F&B Attach (vw_fnb_attach)",
                        mode=mode,
                        is_actionable=False,
                    )
                    decisions.append(
                        Decision(
                            decision_id=make_decision_id("KPI-DATA-GAP", ck, fnb_scope, sub_key="fnb_7d"),
                            rule_id="KPI-DATA-GAP",
                            cinema_key=ck,
                            time_scope=fnb_scope,
                            category="REVENUE_KPI",
                            severity=eff_sev,
                            title=eff_title,
                            recommendation="Kiểm tra dữ liệu vw_fnb_attach trước khi đánh giá suy giảm bán kèm F&B.",
                            explanation={
                                "decision_mode": freshness["decision_mode"],
                                "what_happened": f"Cửa sổ hiện tại của F&B tại {cinema_name} chỉ có {c_days}/7 ngày lịch duy nhất.",
                                "why_triggered": "Rule KPI-DATA-GAP kích hoạt khi cửa sổ F&B 7 ngày bị thiếu ngày.",
                                "why_recommended": "Tránh kết luận sai về attach_rate trên cửa sổ thiếu ngày.",
                                "manager_checks": ["Kiểm tra dữ liệu fact_fnb và fact_ticket theo ngày."],
                            },
                            evidence=[{"metric": "fnb_window_completeness", "days_current": c_days, "days_previous": p_days, "freshness": freshness}],
                            source_model="kpi_dw",
                            as_of_date=fnb_max.isoformat(),
                            limitations=["Cửa sổ F&B hiện tại không đủ 7 ngày.", *stale_notes],
                        )
                    )
                    continue

                c_tkt = float(c_g["ticket_txn_count"].sum())
                c_fnb = float(c_g["fnb_txn_count"].sum())
                if c_tkt <= 0:
                    continue
                curr_attach = c_fnb / c_tkt

                prev_attach = None
                delta_pp = None
                fnb_extra_notes: List[str] = []
                if p_days == 7:
                    p_tkt = float(p_g["ticket_txn_count"].sum())
                    p_fnb = float(p_g["fnb_txn_count"].sum())
                    prev_attach = (p_fnb / p_tkt) if p_tkt > 0 else None
                    delta_pp = ((curr_attach - prev_attach) * 100.0) if prev_attach is not None else None
                else:
                    fnb_extra_notes.append(f"Kỳ trước chỉ có {p_days}/7 ngày nên không tính delta_pp so với kỳ trước.")

                triggered = (curr_attach < cfg["kpi_fnb_attach_min"]) or (
                    delta_pp is not None and delta_pp <= cfg["kpi_fnb_attach_drop_pp"]
                )
                if triggered:
                    raw_sev = "HIGH" if (delta_pp is not None and delta_pp <= cfg["kpi_fnb_attach_drop_pp"]) else "MEDIUM"
                    base_title = f"[{cinema_name}] Tỷ lệ gắn kèm F&B cần chú ý ({curr_attach * 100:.1f}%)"
                    eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                        raw_sev,
                        base_title,
                        fnb_max.isoformat(),
                        ref_date,
                        cfg["max_stale_days"],
                        "F&B Attach (vw_fnb_attach)",
                        mode=mode,
                        is_actionable=True,
                    )
                    decisions.append(
                        Decision(
                            decision_id=make_decision_id("KPI-FNB-DROP", ck, fnb_scope),
                            rule_id="KPI-FNB-DROP",
                            cinema_key=ck,
                            time_scope=fnb_scope,
                            category="REVENUE_KPI",
                            severity=eff_sev,
                            title=eff_title,
                            recommendation="Kiểm tra trưng bày quầy bắp nước, tốc độ phục vụ giờ cao điểm và đề xuất combo kèm vé.",
                            explanation={
                                "decision_mode": freshness["decision_mode"],
                                "what_happened": f"F&B attach_rate 7 ngày qua tại {cinema_name} đạt {curr_attach * 100:.1f}%.",
                                "why_triggered": "attach_rate dưới ngưỡng 35% hoặc giảm >= 5 điểm % so với 7 ngày trước.",
                                "why_recommended": "Bán chéo F&B đóng góp lớn vào biên lợi nhuận rạp; cần kiểm tra nguyên nhân tại quầy hoặc kênh APP/WEB.",
                                "manager_checks": [
                                    "Kiểm tra tồn kho combo và thời gian chờ quầy F&B giờ cao điểm.",
                                    "Kiểm tra tỷ lệ khách đặt online (APP/WEB) có chọn mua kèm combo hay không.",
                                ],
                            },
                            evidence=[
                                {
                                    "metric": "attach_rate_7d",
                                    "current_value": round(curr_attach, 4),
                                    "previous_value": round(prev_attach, 4) if prev_attach is not None else None,
                                    "delta_pp": round(delta_pp, 2) if delta_pp is not None else None,
                                    "days_current": c_days,
                                    "days_previous": p_days,
                                    "ticket_txn_count": int(c_tkt),
                                    "fnb_txn_count": int(c_fnb),
                                    "freshness": freshness,
                                }
                            ],
                            source_model="kpi_dw",
                            as_of_date=fnb_max.isoformat(),
                            limitations=[
                                "attach_rate thuộc Nhóm 2 (Raw) theo docs/kpi_dictionary.md; spp_clean là chi tiêu trên giao dịch vé (SPT), không phải trên từng khách.",
                                *fnb_extra_notes,
                                *stale_notes,
                            ],
                        )
                    )

    # Rule KPI-ANOM-HIGH: cảnh báo chất lượng dữ liệu nếu tỷ lệ suất bất thường cao
    if anomaly_df is not None and not anomaly_df.empty:
        anom = anomaly_df.copy()
        if mode == "historical_audit" and "full_date" in anom.columns:
            anom["full_date"] = pd.to_datetime(anom["full_date"], errors="coerce")
            anom = anom[anom["full_date"] <= pd.Timestamp(ref_date)]
        anom = anom.dropna(subset=["cinema_key", "is_anomaly_showtime"])
        if not anom.empty:
            a_max = _to_iso_date(anom["full_date"].max()) if "full_date" in anom.columns else max_date.isoformat()
            for cinema_key, grp in anom.groupby("cinema_key"):
                ck = int(cinema_key)
                cinema_name = str(grp["cinema_name"].iloc[0]) if "cinema_name" in grp.columns else f"CN{ck:02d}"
                total_st = len(grp)
                anom_st = int(grp["is_anomaly_showtime"].astype(bool).sum())
                if total_st == 0:
                    continue
                anom_pct = (anom_st / total_st) * 100.0
                if anom_pct >= cfg["kpi_anom_showtime_pct_high"]:
                    eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                        "HIGH",
                        f"[{cinema_name}] {anom_pct:.1f}% suất chiếu có giao dịch bất thường",
                        a_max or max_date.isoformat(),
                        ref_date,
                        cfg["max_stale_days"],
                        "Anomaly Showtime (vw_anomaly_showtime)",
                        mode=mode,
                        is_actionable=False,
                    )
                    decisions.append(
                        Decision(
                            decision_id=make_decision_id("KPI-ANOM-HIGH", ck, f"as_of_{a_max}"),
                            rule_id="KPI-ANOM-HIGH",
                            cinema_key=ck,
                            time_scope=f"as_of_{a_max}",
                            category="REVENUE_KPI",
                            severity=eff_sev,
                            title=eff_title,
                            recommendation="Thực hiện kiểm toán đối soát dữ liệu POS/vé (price_zero, qty_spike) trước khi ra quyết định kinh doanh lớn.",
                            explanation={
                                "decision_mode": freshness["decision_mode"],
                                "what_happened": f"Có {anom_st}/{total_st} suất chiếu ({anom_pct:.1f}%) tại {cinema_name} ghi nhận cờ is_anomaly_showtime=TRUE.",
                                "why_triggered": f"Rule KPI-ANOM-HIGH kích hoạt khi tỷ lệ suất bất thường >= {cfg['kpi_anom_showtime_pct_high']}%.",
                                "why_recommended": "Mức nhiễm bẩn dữ liệu cao có thể làm sai lệch các chỉ số Raw/Hybrid nếu không tách bạch dữ liệu sạch.",
                                "manager_checks": [
                                    "Kiểm tra chi tiết các suất trong vw_anomaly_showtime theo anomaly_types.",
                                    "Xác nhận các chỉ số ra quyết định đang dùng cột *_clean.",
                                ],
                            },
                            evidence=[
                                {
                                    "metric": "anomaly_showtime_pct",
                                    "value": round(anom_pct, 2),
                                    "anomaly_showtimes": anom_st,
                                    "total_showtimes": total_st,
                                    "freshness": freshness,
                                }
                            ],
                            source_model="kpi_dw",
                            as_of_date=a_max or max_date.isoformat(),
                            limitations=[
                                "Cờ bất thường phản ánh quy tắc kiểm soát ETL (qty_spike, price_zero), cần đối chiếu chứng từ POS.",
                                *stale_notes,
                            ],
                        )
                    )

    return decisions


# ---------------------------------------------------------------------------
# 2. Đánh giá Customer Retention — RFM (D1)
# ---------------------------------------------------------------------------
def evaluate_rfm_rules(
    profile_df: Optional[pd.DataFrame],
    rfm_metrics_df: Optional[pd.DataFrame] = None,
    as_of_date: Optional[str] = None,
    reference_date: Optional[date] = None,
    member_last_txn_date: Optional[str] = None,
    thresholds: Optional[Dict[str, Any]] = None,
    mode: str = "operational",
) -> List[Decision]:
    """
    Đánh giá phân khúc RFM thực tế.
    Kiểm tra tính tương thích giữa số cụm k thực tế của mô hình và 6 nhóm nghiệp vụ tham chiếu.
    Phân biệt thời điểm chạy mô hình (`updated_at`) và ngày cuối dữ liệu hội viên (`member_last_txn_date`).
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decisions: List[Decision] = []

    if profile_df is None or profile_df.empty:
        return decisions

    prof = profile_df.copy()
    ref_date = reference_date or date.today()
    actual_k = int(prof["cluster_id"].nunique()) if "cluster_id" in prof.columns else len(prof)
    model_run_date = as_of_date or (
        _to_iso_date(prof["updated_at"].max()) if "updated_at" in prof.columns else ref_date.isoformat()
    )
    # Ngày dữ liệu thực tế: ưu tiên ngày giao dịch hội viên cuối cùng nếu có, để phát hiện dữ liệu gốc quá cũ
    effective_data_date = member_last_txn_date or model_run_date
    time_scope = f"rfm_as_of_{model_run_date}"

    audit_notes: List[str] = []
    if mode == "historical_audit":
        audit_notes.append(
            "Bảng rfm_cluster_profile chỉ lưu snapshot hiện tại (không có lịch sử theo ngày); "
            "không thể tái lập phân cụm tại thời điểm quá khứ và không phải kết quả historical backtest chống data leakage."
        )
        if model_run_date and model_run_date > ref_date.isoformat():
            audit_notes.append(
                f"CẢNH BÁO LOOK-AHEAD LEAKAGE: Mô hình RFM được cập nhật lúc {model_run_date} "
                f"(sau reference_date={ref_date.isoformat()}); kết quả này chưa tồn tại tại thời điểm replay."
            )

    # Rule RFM-K-MISMATCH: cảnh báo tích hợp mô hình (không phải khuyến nghị chiến dịch tức thời)
    if actual_k != cfg["rfm_expected_business_k"]:
        present_names = sorted(prof["cluster_name"].astype(str).unique().tolist())
        expected_names = ["VIP", "Trung thành", "Thỉnh thoảng", "Mới", "Có nguy cơ rời bỏ", "Ngủ đông"]
        missing_names = [n for n in expected_names if n not in present_names]
        eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
            "MEDIUM",
            f"Phân khúc RFM thực tế dùng k={actual_k} (khác {cfg['rfm_expected_business_k']} nhóm nghiệp vụ)",
            effective_data_date,
            ref_date,
            cfg["max_stale_days"],
            f"RFM (model_run={model_run_date}, member_last_txn={member_last_txn_date or 'N/A'})",
            mode=mode,
            is_actionable=False,
        )

        decisions.append(
            Decision(
                decision_id=make_decision_id("RFM-K-MISMATCH", None, time_scope),
                rule_id="RFM-K-MISMATCH",
                cinema_key=None,
                time_scope=time_scope,
                category="RETENTION_RFM",
                severity=eff_sev,
                title=eff_title,
                recommendation="Chỉ áp dụng chính sách chăm sóc cho các cụm thực tế hiện có; không gán cứng chính sách cho các nhóm bị gộp.",
                explanation={
                    "decision_mode": freshness["decision_mode"],
                    "what_happened": f"Pipeline RFM (core/rfm.py) chọn k={actual_k} theo Silhouette tối ưu nên chỉ sinh {present_names}, thiếu {missing_names}.",
                    "why_triggered": f"actual_k ({actual_k}) != expected_business_k ({cfg['rfm_expected_business_k']}).",
                    "why_recommended": "Gán nhãn/chính sách cho nhóm không tồn tại trong rfm_result sẽ gây sai lệch đối tượng nhận ưu đãi.",
                    "manager_checks": [
                        "Xác nhận danh sách cluster_name trong rfm_cluster_profile trước khi lập chiến dịch.",
                        "Nếu nghiệp vụ bắt buộc 6 nhóm riêng biệt, cần đánh giá lại đánh đổi giữa Silhouette và tính phân tách nghiệp vụ.",
                    ],
                },
                evidence=[
                    {
                        "actual_k": actual_k,
                        "expected_business_k": cfg["rfm_expected_business_k"],
                        "present_clusters": present_names,
                        "missing_business_clusters": missing_names,
                        "model_updated_at": model_run_date,
                        "member_last_txn_date": member_last_txn_date,
                        "freshness": freshness,
                    }
                ],
                source_model="rfm_kmeans",
                as_of_date=model_run_date or "",
                limitations=[
                    "Khách vãng lai (GUEST, ~70% giao dịch) không nằm trong mô hình RFM.",
                    *audit_notes,
                    *stale_notes,
                ],
            )
        )

    # Rule RFM-CHURN-RISK
    churn_rows = prof[prof["cluster_name"] == "Có nguy cơ rời bỏ"]
    if not churn_rows.empty:
        row = churn_rows.iloc[0]
        pct_cust = float(row["pct_customers"])
        cust_cnt = int(row["customer_count"])
        avg_r = float(row["avg_recency"])
        if pct_cust >= cfg["rfm_churn_share_pct"]:
            eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                "HIGH",
                f"Nhóm 'Có nguy cơ rời bỏ' chiếm {pct_cust:.1f}% hội viên ({cust_cnt:,} khách)",
                effective_data_date,
                ref_date,
                cfg["max_stale_days"],
                f"RFM (model_run={model_run_date}, member_last_txn={member_last_txn_date or 'N/A'})",
                mode=mode,
                is_actionable=True,
            )
            decisions.append(
                Decision(
                    decision_id=make_decision_id("RFM-CHURN-RISK", None, time_scope),
                    rule_id="RFM-CHURN-RISK",
                    cinema_key=None,
                    time_scope=time_scope,
                    category="RETENTION_RFM",
                    severity=eff_sev,
                    title=eff_title,
                    recommendation="Thiết kế thử nghiệm A/B chiến dịch tái kích hoạt (gợi ý phim theo top_genre, ưu đãi combo) cho một nhóm mẫu trước khi nhân rộng.",
                    explanation={
                        "decision_mode": freshness["decision_mode"],
                        "what_happened": f"Cụm 'Có nguy cơ rời bỏ' có {cust_cnt:,} hội viên ({pct_cust:.1f}%), Recency trung bình {avg_r:.1f} ngày.",
                        "why_triggered": f"Tỷ lệ hội viên nhóm rủi ro >= {cfg['rfm_churn_share_pct']}%.",
                        "why_recommended": "Recency tăng cao cho thấy tần suất quay lại giảm; cần tiếp cận sớm trước khi chuyển sang trạng thái ngủ đông.",
                        "manager_checks": [
                            "Không gửi voucher hàng loạt khi chưa có ngân sách và nhóm đối chứng (A/B test).",
                            "Kiểm tra kênh liên lạc hợp lệ và lịch phim sắp chiếu phù hợp top_genre của cụm.",
                        ],
                    },
                    evidence=[
                        {
                            "cluster_name": "Có nguy cơ rời bỏ",
                            "customer_count": cust_cnt,
                            "pct_customers": pct_cust,
                            "avg_recency": avg_r,
                            "avg_frequency": float(row["avg_frequency"]),
                            "avg_monetary": float(row["avg_monetary"]),
                            "top_genre": str(row.get("top_genre", "N/A")),
                            "combo_rate": float(row.get("combo_rate", 0.0)),
                            "model_updated_at": model_run_date,
                            "member_last_txn_date": member_last_txn_date,
                            "freshness": freshness,
                        }
                    ],
                    source_model="rfm_kmeans",
                    as_of_date=model_run_date or "",
                    limitations=[
                        "Chưa có thử nghiệm A/B thực tế nên không khẳng định chiến dịch chắc chắn làm tăng doanh thu.",
                        *audit_notes,
                        *stale_notes,
                    ],
                )
            )

    # Rule RFM-VIP-RETENTION
    vip_rows = prof[prof["cluster_name"] == "VIP"]
    if not vip_rows.empty:
        row = vip_rows.iloc[0]
        cust_cnt = int(row["customer_count"])
        if cust_cnt >= cfg["rfm_vip_min_count"]:
            eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                "LOW",
                f"Duy trì trải nghiệm nhóm VIP ({cust_cnt:,} hội viên, {float(row['pct_customers']):.1f}%)",
                effective_data_date,
                ref_date,
                cfg["max_stale_days"],
                f"RFM (model_run={model_run_date}, member_last_txn={member_last_txn_date or 'N/A'})",
                mode=mode,
                is_actionable=True,
            )
            decisions.append(
                Decision(
                    decision_id=make_decision_id("RFM-VIP-RETENTION", None, time_scope),
                    rule_id="RFM-VIP-RETENTION",
                    cinema_key=None,
                    time_scope=time_scope,
                    category="RETENTION_RFM",
                    severity=eff_sev,
                    title=eff_title,
                    recommendation="Ưu tiên quyền lợi giữ chân (suất chiếu sớm, ưu đãi phòng đặc biệt/combo) thay vì giảm giá vé sâu.",
                    explanation={
                        "decision_mode": freshness["decision_mode"],
                        "what_happened": f"Nhóm VIP có {cust_cnt:,} hội viên với chi tiêu trung bình {float(row['avg_monetary']):,.0f} đ và tần suất {float(row['avg_frequency']):.1f} giao dịch.",
                        "why_triggered": "Nhóm VIP hiện diện trong hồ sơ phân cụm RFM.",
                        "why_recommended": "Giữ chân khách hàng giá trị cao giúp ổn định doanh thu định kỳ.",
                        "manager_checks": [
                            "Theo dõi mức độ hài lòng của khách VIP qua phản hồi dịch vụ và thời gian chờ quầy.",
                        ],
                    },
                    evidence=[
                        {
                            "cluster_name": "VIP",
                            "customer_count": cust_cnt,
                            "pct_customers": float(row["pct_customers"]),
                            "avg_recency": float(row["avg_recency"]),
                            "avg_frequency": float(row["avg_frequency"]),
                            "avg_monetary": float(row["avg_monetary"]),
                            "top_genre": str(row.get("top_genre", "N/A")),
                            "combo_rate": float(row.get("combo_rate", 0.0)),
                            "model_updated_at": model_run_date,
                            "member_last_txn_date": member_last_txn_date,
                            "freshness": freshness,
                        }
                    ],
                    source_model="rfm_kmeans",
                    as_of_date=model_run_date or "",
                    limitations=[
                        "Tác động tài chính của quyền lợi VIP cần được đo lường qua giữ chân dài hạn.",
                        *audit_notes,
                        *stale_notes,
                    ],
                )
            )

    return decisions


# ---------------------------------------------------------------------------
# 3. Đánh giá Capacity & Staffing — Forecasting (D2)
# ---------------------------------------------------------------------------
def _build_scheduled_slot_capacity(schedule_df: Optional[pd.DataFrame]) -> Dict[Tuple[date, int, str], float]:
    """
    Tính sức chứa thực sự theo lịch chiếu xác nhận tại đúng grain dự báo (target_date × cinema_key × time_slot):
        SUM(capacity của từng suất chiếu hợp lệ).
    Không nhân công suất đơn lẻ bằng số suất giả định.
    """
    if schedule_df is None or schedule_df.empty:
        return {}
    sched = schedule_df.copy()
    date_col = "show_date" if "show_date" in sched.columns else "target_date"
    if date_col not in sched.columns or "cinema_key" not in sched.columns or "time_slot" not in sched.columns or "capacity" not in sched.columns:
        return {}
    if "is_valid" in sched.columns:
        sched = sched[sched["is_valid"] == True]  # noqa: E712
    sched["_d"] = pd.to_datetime(sched[date_col], errors="coerce").dt.date
    sched["capacity"] = pd.to_numeric(sched["capacity"], errors="coerce")
    sched = sched.dropna(subset=["_d", "cinema_key", "time_slot", "capacity"])
    sched = sched[sched["capacity"] > 0]
    if sched.empty:
        return {}
    grouped = sched.groupby(["_d", "cinema_key", "time_slot"])["capacity"].sum()
    return {(d, int(ck), str(slot)): float(cap) for (d, ck, slot), cap in grouped.items()}


def _build_hist_slot_mean(
    hist_slot_df: Optional[pd.DataFrame],
    max_date: Optional[date] = None,
) -> Dict[Tuple[int, str], float]:
    """
    Tính trung bình số vé sạch lịch sử theo (cinema_key, time_slot) để đánh giá Demand Surge tương đối.
    Nếu `max_date` được cung cấp và bảng có cột ngày (`full_date` hoặc `show_date`),
    chỉ sử dụng quan sát `<= max_date` để chống look-ahead leakage.
    """
    if hist_slot_df is None or hist_slot_df.empty:
        return {}
    hist = hist_slot_df.copy()
    if max_date is not None:
        date_col = "full_date" if "full_date" in hist.columns else ("show_date" if "show_date" in hist.columns else None)
        if date_col is not None:
            hist["_hdate"] = pd.to_datetime(hist[date_col], errors="coerce").dt.date
            hist = hist[hist["_hdate"].notna() & (hist["_hdate"] <= max_date)]
    val_col = "hist_slot_mean" if "hist_slot_mean" in hist.columns else "qty_clean"
    if val_col not in hist.columns or "cinema_key" not in hist.columns or "time_slot" not in hist.columns:
        return {}
    hist[val_col] = pd.to_numeric(hist[val_col], errors="coerce")
    hist = hist.dropna(subset=["cinema_key", "time_slot", val_col])
    hist = hist[hist[val_col] > 0]
    if hist.empty:
        return {}
    grouped = hist.groupby(["cinema_key", "time_slot"])[val_col].mean()
    return {(int(ck), str(slot)): float(val) for (ck, slot), val in grouped.items()}


def evaluate_forecast_rules(
    forecast_df: Optional[pd.DataFrame],
    cinema_capacity_map: Optional[Dict[int, int]] = None,
    cinema_name_map: Optional[Dict[int, str]] = None,
    expected_cinema_keys: Optional[Sequence[int]] = None,
    reference_date: Optional[date] = None,
    schedule_df: Optional[pd.DataFrame] = None,
    hist_slot_df: Optional[pd.DataFrame] = None,
    observed_last_date: Optional[str] = None,
    thresholds: Optional[Dict[str, Any]] = None,
    mode: str = "operational",
) -> List[Decision]:
    """
    Đánh giá dự báo 14 ngày (`is_test = False`) với Temporal Validity & Capacity Semantics:
      1. Temporal Validity: Trong chế độ `operational`, chỉ sử dụng dự báo có `target_date > reference_date`.
         Không dùng `created_at` mới để coi `target_date` quá khứ là còn hiệu lực.
      2. Capacity Semantics: Chỉ tính occupancy/áp lực công suất (`FC-PEAK-DEMAND`) khi có lịch chiếu xác nhận
         tại grain `date × cinema × time_slot`. Nếu chưa có lịch chiếu, chỉ phát cảnh báo nhu cầu tương đối
         (`FC-DEMAND-SURGE`) nếu có dữ liệu lịch sử hợp lệ.
      3. Forecast Completeness: Kiểm tra đủ horizon 14 ngày (`reference_date + 1 .. reference_date + 14`) theo từng rạp.
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decisions: List[Decision] = []
    name_map = cinema_name_map or {}
    ref_date = reference_date or date.today()
    horizon_days = int(cfg["fc_horizon_days"])
    expected_horizon_dates = {ref_date + timedelta(days=i) for i in range(1, horizon_days + 1)}
    horizon_scope = f"{(ref_date + timedelta(days=1)).isoformat()}..{(ref_date + timedelta(days=horizon_days)).isoformat()}"

    if forecast_df is None or forecast_df.empty:
        for ck in (expected_cinema_keys or [None]):
            decisions.append(
                Decision(
                    decision_id=make_decision_id("FC-DATA-GAP", ck, horizon_scope, sub_key="empty"),
                    rule_id="FC-DATA-GAP",
                    cinema_key=ck,
                    time_scope=horizon_scope,
                    category="CAPACITY_FORECAST",
                    severity="MEDIUM",
                    title=f"[{name_map.get(ck, f'CN{ck:02d}') if ck else 'Hệ thống'}] Thiếu dữ liệu dự báo 14 ngày",
                    recommendation="Chạy lại pipeline dự báo (scripts/run_forecast.py) trước khi lập kế hoạch vận hành.",
                    explanation={
                        "decision_mode": "OPERATIONAL" if mode == "operational" else "HISTORICAL_AUDIT",
                        "what_happened": "Không tìm thấy bản ghi dự báo tương lai (is_test=FALSE).",
                        "why_triggered": "Rule FC-DATA-GAP kích hoạt khi bảng forecast_result rỗng.",
                        "why_recommended": "Không có đầu vào hợp lệ thì không thể đưa khuyến nghị công suất.",
                        "manager_checks": ["Kiểm tra log chạy scripts/run_forecast.py và dữ liệu fact_showtime_summary."],
                    },
                    evidence=[
                        {
                            "reason": "empty_forecast_result",
                            "future_rows": 0,
                            "reference_date": ref_date.isoformat(),
                            "observed_last_date": observed_last_date,
                        }
                    ],
                    source_model="forecast",
                    as_of_date=ref_date.isoformat(),
                    limitations=["Thiếu hoàn toàn đầu ra mô hình dự báo."],
                )
            )
        return decisions

    df = forecast_df.copy()
    if "is_test" in df.columns:
        df = df[df["is_test"] == False]  # noqa: E712
    df["target_date"] = pd.to_datetime(df["target_date"], errors="coerce")
    df = df.dropna(subset=["target_date", "cinema_key", "time_slot", "yhat"])
    if df.empty:
        return decisions

    df["_tdate"] = df["target_date"].dt.date
    created_at_str = _to_iso_date(df["created_at"].max()) if "created_at" in df.columns else None
    horizon_start = ref_date + timedelta(days=1)
    horizon_end = ref_date + timedelta(days=horizon_days)

    if mode == "operational":
        valid_df = df[(df["_tdate"] >= horizon_start) & (df["_tdate"] <= horizon_end)].copy()
        expired_df = df[df["_tdate"] < horizon_start].copy()
        beyond_horizon_df = df[df["_tdate"] > horizon_end].copy()
    else:
        valid_df = df.copy()
        expired_df = pd.DataFrame(columns=df.columns)
        beyond_horizon_df = pd.DataFrame(columns=df.columns)

    sched_cap_map = _build_scheduled_slot_capacity(schedule_df)
    hist_mean_map = _build_hist_slot_mean(hist_slot_df, max_date=ref_date)

    obs_stale_days = _is_stale(observed_last_date, ref_date, cfg["max_stale_days"]) if observed_last_date else None
    obs_missing = not observed_last_date
    obs_unusable_in_op = (mode == "operational") and (obs_missing or (obs_stale_days is not None))

    audit_notes: List[str] = []
    lookahead_risk = False
    if mode == "historical_audit":
        created_after_ref = bool(created_at_str and created_at_str > ref_date.isoformat())
        observed_after_ref = bool(observed_last_date and observed_last_date > ref_date.isoformat())
        lookahead_risk = created_after_ref or observed_after_ref
        audit_notes.append(
            "Chế độ HISTORICAL_AUDIT chỉ phục vụ kiểm toán hậu kiểm; không phải kết quả scientific backtesting chính thức đã kiểm định chống data leakage."
        )
        if lookahead_risk:
            audit_notes.append(
                f"CẢNH BÁO LOOK-AHEAD LEAKAGE: created_at={created_at_str}, observed_last_date={observed_last_date} "
                f"có mốc sau reference_date={ref_date.isoformat()}; kết quả không phản ánh thông tin sẵn có tại thời điểm replay."
            )

    all_cks = sorted(set(expected_cinema_keys or []) | set(df["cinema_key"].astype(int).unique()))
    for ck in all_cks:
        cinema_name = name_map.get(ck, f"CN{ck:02d}")
        ck_valid = valid_df[valid_df["cinema_key"] == ck]
        ck_expired = expired_df[expired_df["cinema_key"] == ck]
        ck_beyond = beyond_horizon_df[beyond_horizon_df["cinema_key"] == ck]

        if mode == "operational" and ck_valid.empty:
            if not ck_expired.empty and ck_beyond.empty:
                reason = "all_forecasts_expired"
            elif not ck_beyond.empty and ck_expired.empty:
                reason = "all_forecasts_beyond_horizon"
            elif not ck_expired.empty and not ck_beyond.empty:
                reason = "no_forecasts_in_horizon"
            else:
                reason = "missing_cinema_forecast"

            exp_min = ck_expired["_tdate"].min().isoformat() if not ck_expired.empty else None
            exp_max = ck_expired["_tdate"].max().isoformat() if not ck_expired.empty else None
            beyond_min = ck_beyond["_tdate"].min().isoformat() if not ck_beyond.empty else None
            beyond_max = ck_beyond["_tdate"].max().isoformat() if not ck_beyond.empty else None

            if reason == "all_forecasts_expired":
                gap_title = f"[{cinema_name}] Toàn bộ dự báo đã hết hạn tại {ref_date.isoformat()} (target_date <= reference_date)"
                gap_what = (
                    f"Tìm thấy {len(ck_expired)} dòng dự báo cho {cinema_name} ({exp_min}..{exp_max}, created_at={created_at_str}), "
                    f"nhưng toàn bộ có target_date <= reference_date ({ref_date.isoformat()}) nên không còn hiệu lực vận hành."
                )
                gap_why = f"Rule FC-DATA-GAP kích hoạt ({reason}); không dùng created_at mới để coi target_date quá khứ là còn hiệu lực."
            elif reason == "all_forecasts_beyond_horizon":
                gap_title = f"[{cinema_name}] Dự báo chỉ nằm ngoài horizon 14 ngày ({horizon_scope})"
                gap_what = (
                    f"Tìm thấy {len(ck_beyond)} dòng dự báo cho {cinema_name} ({beyond_min}..{beyond_max}), "
                    f"nhưng toàn bộ nằm ngoài horizon 14 ngày ({horizon_scope}) và thiếu hoàn toàn ngày trong horizon."
                )
                gap_why = f"Rule FC-DATA-GAP kích hoạt ({reason}); dự báo từ ngày thứ 15 trở đi không được dùng cho horizon 14 ngày."
            elif reason == "no_forecasts_in_horizon":
                gap_title = f"[{cinema_name}] Không có dự báo hợp lệ trong horizon 14 ngày ({horizon_scope})"
                gap_what = (
                    f"Rạp {cinema_name} có {len(ck_expired)} dòng quá hạn và {len(ck_beyond)} dòng ngoài horizon 14 ngày, "
                    f"nhưng 0 dòng trong cửa sổ {horizon_scope}."
                )
                gap_why = f"Rule FC-DATA-GAP kích hoạt ({reason}); không dùng dữ liệu ngoài horizon để lấp chỗ trống."
            else:
                gap_title = f"[{cinema_name}] Thiếu dữ liệu dự báo 14 ngày"
                gap_what = f"Không có bản ghi dự báo hợp lệ cho rạp cinema_key={ck} trong horizon {horizon_scope}."
                gap_why = f"Rule FC-DATA-GAP kích hoạt ({reason})."

            decisions.append(
                Decision(
                    decision_id=make_decision_id("FC-DATA-GAP", ck, horizon_scope, sub_key=reason),
                    rule_id="FC-DATA-GAP",
                    cinema_key=ck,
                    time_scope=horizon_scope,
                    category="CAPACITY_FORECAST",
                    severity="MEDIUM",
                    title=gap_title,
                    recommendation="Cập nhật dữ liệu lịch sử mới nhất và chạy lại pipeline dự báo (scripts/run_forecast.py) cho horizon 14 ngày tới.",
                    explanation={
                        "decision_mode": "OPERATIONAL",
                        "what_happened": gap_what,
                        "why_triggered": gap_why,
                        "why_recommended": "Không được phát cảnh báo công suất hoặc khuyến nghị mở suất từ dự báo đã hết hạn hoặc ngoài horizon.",
                        "manager_checks": [
                            "Kiểm tra ngày dữ liệu quan sát cuối cùng (fact_showtime_summary) và chạy lại dự báo cho 14 ngày tới."
                        ],
                    },
                    evidence=[
                        {
                            "reason": reason,
                            "cinema_key": ck,
                            "future_rows": 0,
                            "expired_rows": int(len(ck_expired)),
                            "expired_target_range": f"{exp_min}..{exp_max}" if exp_min else None,
                            "beyond_horizon_rows": int(len(ck_beyond)),
                            "beyond_horizon_range": f"{beyond_min}..{beyond_max}" if beyond_min else None,
                            "model_created_at": created_at_str,
                            "observed_last_date": observed_last_date,
                            "observed_stale_days": obs_stale_days,
                            "reference_date": ref_date.isoformat(),
                        }
                    ],
                    source_model="forecast",
                    as_of_date=observed_last_date or created_at_str or ref_date.isoformat(),
                    limitations=[
                        "Dự báo cũ hơn ngày tham chiếu hoặc ngoài horizon 14 ngày bị chặn khỏi chế độ vận hành; muốn xem lại chỉ sử dụng chế độ historical_audit."
                    ],
                )
            )
            continue

        # Kiểm tra độ đầy đủ của horizon 14 ngày trong chế độ operational
        if mode == "operational":
            covered_dates = set(ck_valid["_tdate"].unique()) & expected_horizon_dates
            if len(covered_dates) < horizon_days:
                missing_dates = sorted(d.isoformat() for d in (expected_horizon_dates - covered_dates))
                decisions.append(
                    Decision(
                        decision_id=make_decision_id("FC-DATA-GAP", ck, horizon_scope, sub_key="partial_horizon"),
                        rule_id="FC-DATA-GAP",
                        cinema_key=ck,
                        time_scope=horizon_scope,
                        category="CAPACITY_FORECAST",
                        severity="MEDIUM",
                        title=f"[{cinema_name}] Dự báo thiếu một phần horizon 14 ngày ({len(covered_dates)}/{horizon_days} ngày)",
                        recommendation="Kiểm tra các ngày bị thiếu trong forecast_result trước khi lập kế hoạch cho cả tuần/2 tuần.",
                        explanation={
                            "decision_mode": "OPERATIONAL",
                            "what_happened": f"Rạp {cinema_name} chỉ có dự báo cho {len(covered_dates)}/{horizon_days} ngày trong cửa sổ {horizon_scope}.",
                            "why_triggered": "Rule FC-DATA-GAP kích hoạt khi số ngày dự báo tương lai < 14 ngày (không dùng ngày thứ 15+ để lấp ngày thiếu).",
                            "why_recommended": "Thiếu ngày trong horizon có thể bỏ sót điểm cao điểm cục bộ.",
                            "manager_checks": ["Đối chiếu danh sách missing_dates trong evidence với lịch chạy scripts/run_forecast.py."],
                        },
                        evidence=[
                            {
                                "reason": "partial_horizon_gap",
                                "cinema_key": ck,
                                "covered_days": int(len(covered_dates)),
                                "expected_days": horizon_days,
                                "missing_dates": missing_dates,
                                "expired_rows_ignored": int(len(ck_expired)),
                                "beyond_horizon_rows_ignored": int(len(ck_beyond)),
                                "model_created_at": created_at_str,
                                "observed_last_date": observed_last_date,
                                "reference_date": ref_date.isoformat(),
                            }
                        ],
                        source_model="forecast",
                        as_of_date=observed_last_date or created_at_str or ref_date.isoformat(),
                        limitations=[
                            "Ngày không có suất theo lịch chiếu không bị tính là lỗi, nhưng ngày thiếu hoàn toàn trong forecast_result là thiếu dữ liệu dự báo."
                        ],
                    )
                )

        if ck_valid.empty:
            continue

        cap_pressure_rows = []
        demand_surge_rows = []
        for _, row in ck_valid.iterrows():
            tdate = row["_tdate"]
            slot = str(row["time_slot"])
            yhat = float(row["yhat"])
            sched_cap = sched_cap_map.get((tdate, ck, slot))

            if sched_cap is not None and sched_cap > 0:
                occ_ratio = yhat / sched_cap
                if occ_ratio >= cfg["fc_high_demand_ratio"]:
                    cap_pressure_rows.append((row, sched_cap, occ_ratio))
            else:
                hist_mean = hist_mean_map.get((ck, slot))
                if hist_mean is not None and hist_mean > 0:
                    surge_ratio = yhat / hist_mean
                    if surge_ratio >= cfg["fc_spike_vs_hist_ratio"]:
                        demand_surge_rows.append((row, hist_mean, surge_ratio))

        if obs_unusable_in_op:
            blocked_rules = []
            if cap_pressure_rows:
                blocked_rules.append("FC-PEAK-DEMAND")
            if demand_surge_rows:
                blocked_rules.append("FC-DEMAND-SURGE")

            gap_sub_key = "missing_observed_date" if obs_missing else "stale_observed_source"
            gap_title = (
                f"[{cinema_name}] Thiếu observed_last_date để xác minh độ mới dữ liệu nguồn dự báo"
                if obs_missing
                else f"[{cinema_name}] Dữ liệu quan sát nguồn dự báo đã quá hạn ({obs_stale_days} ngày)"
            )
            gap_what = (
                f"Dự báo cho {cinema_name} có {len(ck_valid)} dòng trong horizon {horizon_scope} (created_at={created_at_str}), "
                f"nhưng thiếu mốc observed_last_date nên không thể xác minh độ mới dữ liệu quan sát gốc."
                if obs_missing
                else f"Dự báo cho {cinema_name} (created_at={created_at_str}) được xây trên dữ liệu quan sát kết thúc ngày "
                f"{observed_last_date} (cũ {obs_stale_days} ngày > ngưỡng {cfg['max_stale_days']} ngày tại {ref_date.isoformat()})."
            )
            decisions.append(
                Decision(
                    decision_id=make_decision_id("FC-DATA-GAP", ck, horizon_scope, sub_key=gap_sub_key),
                    rule_id="FC-DATA-GAP",
                    cinema_key=ck,
                    time_scope=horizon_scope,
                    category="CAPACITY_FORECAST",
                    severity="MEDIUM",
                    title=gap_title,
                    recommendation="Cập nhật dữ liệu quan sát mới nhất vào fact_showtime_summary và chạy lại pipeline dự báo trước khi ra quyết định vận hành.",
                    explanation={
                        "decision_mode": "HISTORICAL_ANALYSIS_ONLY" if obs_stale_days is not None else "OPERATIONAL",
                        "what_happened": gap_what,
                        "why_triggered": "Rule FC-DATA-GAP kích hoạt để tách cảnh báo độ mới dữ liệu nguồn khỏi cảnh báo nhu cầu; không dùng created_at thay thế observed_last_date.",
                        "why_recommended": "Chặn phát khuyến nghị Capacity Pressure / Demand Surge hiện hành khi dữ liệu quan sát gốc quá hạn hoặc chưa được xác minh.",
                        "manager_checks": [
                            "Kiểm tra MAX(full_date) trong fact_showtime_summary và nhật ký ETL.",
                            "Chỉ mở khóa khuyến nghị dự báo sau khi dữ liệu quan sát được cập nhật và re-train/re-predict.",
                        ],
                    },
                    evidence=[
                        {
                            "reason": gap_sub_key,
                            "cinema_key": ck,
                            "observed_last_date": observed_last_date,
                            "observed_stale_days": obs_stale_days,
                            "model_created_at": created_at_str,
                            "reference_date": ref_date.isoformat(),
                            "blocked_rules": blocked_rules,
                            "blocked_cap_pressure_slots": len(cap_pressure_rows),
                            "blocked_demand_surge_slots": len(demand_surge_rows),
                        }
                    ],
                    source_model="forecast",
                    as_of_date=observed_last_date or created_at_str or ref_date.isoformat(),
                    limitations=[
                        "Đã chặn FC-PEAK-DEMAND và FC-DEMAND-SURGE trong chế độ vận hành do dữ liệu quan sát nguồn quá hạn hoặc thiếu mốc kiểm chứng.",
                    ],
                )
            )
            continue

        date_min = ck_valid["_tdate"].min().isoformat()
        date_max = ck_valid["_tdate"].max().isoformat()
        scope = f"{date_min}..{date_max}"
        freshness_source_date = observed_last_date or created_at_str or ref_date.isoformat()
        created_stale_note: List[str] = []
        if created_at_str:
            c_stale = _is_stale(created_at_str, ref_date, cfg["max_stale_days"])
            if c_stale is not None:
                created_stale_note.append(
                    f"Lưu ý metadata: model created_at={created_at_str} đã cũ {c_stale} ngày (dù observed_last_date={observed_last_date} còn mới)."
                )
            elif observed_last_date and created_at_str < observed_last_date:
                created_stale_note.append(
                    f"Lưu ý metadata: model created_at={created_at_str} sớm hơn observed_last_date={observed_last_date}."
                )

        # 1. Capacity Pressure Alert (chỉ khi có lịch chiếu xác nhận tại đúng grain)
        if cap_pressure_rows:
            top_row, top_sched_cap, top_ratio = max(cap_pressure_rows, key=lambda x: x[2])
            t_date = top_row["_tdate"].isoformat()
            slot = str(top_row["time_slot"])
            yhat_val = float(top_row["yhat"])
            yhat_lo = float(top_row["yhat_lower"]) if pd.notna(top_row.get("yhat_lower")) else None
            yhat_hi = float(top_row["yhat_upper"]) if pd.notna(top_row.get("yhat_upper")) else None
            model_name = str(top_row.get("model_name", "unknown"))

            base_title = f"[{cinema_name}] Áp lực sức chứa theo lịch chiếu ở {len(cap_pressure_rows)} khung giờ (đỉnh {t_date} - {slot}: {top_ratio*100:.0f}%)"
            eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                "HIGH",
                base_title,
                freshness_source_date,
                ref_date,
                cfg["max_stale_days"],
                f"Forecast (observed_last={observed_last_date or 'N/A'}, created_at={created_at_str})",
                mode=mode,
                is_actionable=True,
            )

            decisions.append(
                Decision(
                    decision_id=make_decision_id("FC-PEAK-DEMAND", ck, scope),
                    rule_id="FC-PEAK-DEMAND",
                    cinema_key=ck,
                    time_scope=scope,
                    category="CAPACITY_FORECAST",
                    severity=eff_sev,
                    title=eff_title,
                    recommendation=(
                        "Rà soát lịch trực quầy vé/bắp nước và phân bổ phòng chiếu cho khung giờ có tỷ lệ dự báo/sức chứa lịch chiếu cao."
                    ),
                    explanation={
                        "decision_mode": freshness["decision_mode"],
                        "what_happened": (
                            f"Mô hình {model_name} dự báo {len(cap_pressure_rows)} khung giờ tại {cinema_name} có lượng vé dự báo "
                            f"đạt >= {cfg['fc_high_demand_ratio']*100:.0f}% tổng sức chứa lịch chiếu xác nhận, đỉnh {t_date} khung {slot}: "
                            f"yhat={yhat_val:.0f} vé / {int(top_sched_cap)} ghế khả dụng ({top_ratio*100:.1f}%)."
                        ),
                        "why_triggered": "Rule FC-PEAK-DEMAND (Capacity Pressure Alert) kích hoạt khi yhat >= 0.80 * SUM(capacity suất chiếu hợp lệ theo ngày × rạp × khung giờ).",
                        "why_recommended": "Áp lực lấp đầy thực sự so với ghế đã xếp lịch có thể gây ùn ứ quầy vé/F&B.",
                        "manager_checks": [
                            "Kiểm tra khả năng chuyển sang phòng lớn hơn hoặc điều phối nhân sự quầy.",
                            "Xác định định mức nhân sự thực tế theo ca (hệ thống không tự tính số nhân viên).",
                        ],
                    },
                    evidence=[
                        {
                            "alert_type": "Capacity Pressure Alert",
                            "model_name": model_name,
                            "peak_date": t_date,
                            "peak_time_slot": slot,
                            "yhat_tickets": round(yhat_val, 2),
                            "yhat_lower": round(yhat_lo, 2) if yhat_lo is not None else None,
                            "yhat_upper": round(yhat_hi, 2) if yhat_hi is not None else None,
                            "scheduled_slot_capacity": int(top_sched_cap),
                            "projected_occupancy_ratio": round(top_ratio, 4),
                            "high_pressure_slots_count": len(cap_pressure_rows),
                            "model_created_at": created_at_str,
                            "observed_last_date": observed_last_date,
                            "lookahead_leakage_risk": lookahead_risk,
                            "freshness": freshness,
                        }
                    ],
                    source_model="forecast",
                    as_of_date=freshness_source_date,
                    limitations=[
                        "Khoảng [yhat_lower, yhat_upper] là khoảng heuristic từ độ lệch chuẩn phần dư out-of-sample (±1.96*std), không phải bảo đảm xác suất 95%.",
                        "yhat là số vé dự báo, khác với occupancy; occupancy chỉ được tính khi có tổng sức chứa từ lịch chiếu xác nhận.",
                        *created_stale_note,
                        *audit_notes,
                        *stale_notes,
                    ],
                )
            )

        # 2. Demand Surge Alert (khi chưa có lịch chiếu xác nhận, chỉ cảnh báo tăng tương đối so với lịch sử)
        if demand_surge_rows:
            top_row, top_hist_mean, top_surge = max(demand_surge_rows, key=lambda x: x[2])
            t_date = top_row["_tdate"].isoformat()
            slot = str(top_row["time_slot"])
            yhat_val = float(top_row["yhat"])
            yhat_lo = float(top_row["yhat_lower"]) if pd.notna(top_row.get("yhat_lower")) else None
            yhat_hi = float(top_row["yhat_upper"]) if pd.notna(top_row.get("yhat_upper")) else None
            model_name = str(top_row.get("model_name", "unknown"))

            base_title = f"[{cinema_name}] Nhu cầu dự báo tăng tương đối ở {len(demand_surge_rows)} khung giờ (đỉnh {t_date} - {slot}: {top_surge:.2f}x lịch sử)"
            eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                "MEDIUM",
                base_title,
                freshness_source_date,
                ref_date,
                cfg["max_stale_days"],
                f"Forecast (observed_last={observed_last_date or 'N/A'}, created_at={created_at_str})",
                mode=mode,
                is_actionable=True,
            )

            decisions.append(
                Decision(
                    decision_id=make_decision_id("FC-DEMAND-SURGE", ck, scope),
                    rule_id="FC-DEMAND-SURGE",
                    cinema_key=ck,
                    time_scope=scope,
                    category="CAPACITY_FORECAST",
                    severity=eff_sev,
                    title=eff_title,
                    recommendation="Kiểm tra lịch chiếu dự kiến và năng lực phòng chiếu thực tế trước khi lập kế hoạch nhân sự hoặc xếp suất.",
                    explanation={
                        "decision_mode": freshness["decision_mode"],
                        "what_happened": (
                            f"Chưa có lịch chiếu xác nhận cho {len(demand_surge_rows)} khung giờ tại {cinema_name}, nhưng {model_name} "
                            f"dự báo đỉnh {t_date} khung {slot} đạt yhat={yhat_val:.0f} vé (gấp {top_surge:.2f}x trung bình lịch sử {top_hist_mean:.1f} vé)."
                        ),
                        "why_triggered": f"Rule FC-DEMAND-SURGE (Demand Surge Alert) kích hoạt khi chưa có lịch chiếu và yhat >= {cfg['fc_spike_vs_hist_ratio']}x trung bình lịch sử cùng rạp & khung giờ.",
                        "why_recommended": "Cảnh báo sớm mức tăng nhu cầu tương đối; không kết luận tỷ lệ lấp đầy (occupancy) và không đề xuất mở thêm suất khi chưa kiểm tra lịch phòng.",
                        "manager_checks": [
                            "Kiểm tra showtime_schedule và số phòng khả dụng cho khung giờ này trước khi quyết định xếp suất.",
                        ],
                    },
                    evidence=[
                        {
                            "alert_type": "Demand Surge Alert",
                            "schedule_available": False,
                            "model_name": model_name,
                            "peak_date": t_date,
                            "peak_time_slot": slot,
                            "yhat_tickets": round(yhat_val, 2),
                            "yhat_lower": round(yhat_lo, 2) if yhat_lo is not None else None,
                            "yhat_upper": round(yhat_hi, 2) if yhat_hi is not None else None,
                            "hist_slot_mean": round(top_hist_mean, 2),
                            "demand_vs_hist_ratio": round(top_surge, 4),
                            "surge_slots_count": len(demand_surge_rows),
                            "model_created_at": created_at_str,
                            "observed_last_date": observed_last_date,
                            "lookahead_leakage_risk": lookahead_risk,
                            "freshness": freshness,
                        }
                    ],
                    source_model="forecast",
                    as_of_date=freshness_source_date,
                    limitations=[
                        "Chưa có lịch chiếu xác nhận (showtime_schedule) nên không tính được occupancy và không kết luận rạp gần đầy.",
                        "Không khuyến nghị mở thêm suất khi chưa kiểm tra năng lực phòng chiếu.",
                        "Khoảng [yhat_lower, yhat_upper] là khoảng heuristic (±1.96*std).",
                        *created_stale_note,
                        *audit_notes,
                        *stale_notes,
                    ],
                )
            )

    return decisions


# ---------------------------------------------------------------------------
# 4. Đánh giá Service Quality — Review Mining (D3)
# ---------------------------------------------------------------------------
_REVIEW_PRIORITY_MAP = {
    "KHẨN CẤP": "CRITICAL",
    "ƯU TIÊN CAO": "HIGH",
    "TRUNG BÌNH": "MEDIUM",
    "DUY TRÌ": "LOW",
}


def evaluate_review_rules(
    reviews_df: Optional[pd.DataFrame],
    prev_reviews_df: Optional[pd.DataFrame] = None,
    cinema_name_map: Optional[Dict[int, str]] = None,
    min_sample: int = 10,
    reference_date: Optional[date] = None,
    thresholds: Optional[Dict[str, Any]] = None,
    mode: str = "operational",
) -> List[Decision]:
    """
    Đánh giá chất lượng dịch vụ từ review theo TỪNG rạp (`cinema_key`).
    Tuyệt đối không suy rộng kết quả rạp CN01 sang CN02/CN03.
    Tái sử dụng `reviews.insights.generate_operational_insights`.
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decisions: List[Decision] = []
    if reviews_df is None or reviews_df.empty or "cinema_key" not in reviews_df.columns:
        return decisions

    ref_date = reference_date or date.today()
    name_map = cinema_name_map or {}

    rev_df = reviews_df.copy()
    prev_df = prev_reviews_df.copy() if prev_reviews_df is not None else None
    audit_notes: List[str] = []
    if mode == "historical_audit":
        if "published_date" in rev_df.columns:
            rev_df["_pdate"] = pd.to_datetime(rev_df["published_date"], errors="coerce").dt.date
            rev_df = rev_df[rev_df["_pdate"].notna() & (rev_df["_pdate"] <= ref_date)]
        if prev_df is not None and not prev_df.empty and "published_date" in prev_df.columns:
            prev_df["_pdate"] = pd.to_datetime(prev_df["published_date"], errors="coerce").dt.date
            prev_df = prev_df[prev_df["_pdate"].notna() & (prev_df["_pdate"] <= ref_date)]
        audit_notes.append(
            "Chế độ HISTORICAL_AUDIT chỉ phục vụ kiểm toán hậu kiểm; không phải kết quả historical backtest chính thức."
        )
    if rev_df.empty:
        return decisions

    for cinema_key, grp in rev_df.groupby("cinema_key"):
        if pd.isna(cinema_key):
            continue
        ck = int(cinema_key)
        cinema_name = name_map.get(ck, f"CN{ck:02d}")

        if len(grp) < min_sample:
            continue

        prev_grp = None
        if prev_df is not None and not prev_df.empty and "cinema_key" in prev_df.columns:
            p_sub = prev_df[prev_df["cinema_key"] == ck]
            if len(p_sub) >= min_sample:
                prev_grp = p_sub

        insights = generate_operational_insights(grp, prev_df=prev_grp, min_sample=min_sample)
        as_of = (
            _to_iso_date(pd.to_datetime(grp["published_date"], errors="coerce").max())
            if "published_date" in grp.columns
            else ref_date.isoformat()
        )
        time_scope = f"reviews_as_of_{as_of}"

        for ins in insights:
            raw_sev = _REVIEW_PRIORITY_MAP.get(ins["priority"], "LOW")
            if raw_sev == "LOW":
                # Chỉ phát hành cảnh báo hành động khi đạt ngưỡng từ TRUNG BÌNH trở lên
                continue

            topic = str(ins["topic"])
            eff_sev, eff_title, freshness, stale_notes = _apply_freshness_policy(
                raw_sev,
                f"[{cinema_name}] Phản hồi tiêu cực chủ đề '{topic}' ({ins['pct_negative']}%)",
                as_of,
                ref_date,
                cfg["max_stale_days"],
                "Reviews (published_date)",
                mode=mode,
                is_actionable=True,
            )
            decisions.append(
                Decision(
                    decision_id=make_decision_id("REV-TOPIC-ALERT", ck, time_scope, sub_key=topic),
                    rule_id="REV-TOPIC-ALERT",
                    cinema_key=ck,
                    time_scope=time_scope,
                    category="SERVICE_QUALITY_REVIEW",
                    severity=eff_sev,
                    title=eff_title,
                    recommendation=f"Quick win: {ins['quick_win']} | Mục tiêu: {ins['kpi_target']}",
                    explanation={
                        "decision_mode": freshness["decision_mode"],
                        "what_happened": (
                            f"Trong {ins['n']} lượt nhắc chủ đề '{topic}' tại {cinema_name}, có {ins['negative_count']} lượt "
                            f"được phân loại NEGATIVE ({ins['pct_negative']}%)."
                        ),
                        "why_triggered": f"Rule REV-TOPIC-ALERT kích hoạt ở mức '{ins['priority']}' (n={ins['n']}, pct_negative={ins['pct_negative']}%).",
                        "why_recommended": f"Giả thuyết nguyên nhân từ template vận hành (không phải kết luận nhân quả từ NLP): {ins['root_causes']}",
                        "manager_checks": [
                            "Đọc trực tiếp các review gốc thuộc chủ đề này trước khi triển khai hành động.",
                            "Không suy rộng kết quả của rạp này sang các rạp khác chưa có dữ liệu review.",
                        ],
                    },
                    evidence=[
                        {
                            "evidence_type": "nlp_aggregated_topic_stats",
                            "cinema_key": ck,
                            "topic": topic,
                            "mentions_n": ins["n"],
                            "negative_count": ins["negative_count"],
                            "pct_negative": ins["pct_negative"],
                            "prev_pct_negative": ins["prev_pct_negative"],
                            "delta_pct_negative": ins["delta_pct_negative"],
                            "avg_rating": ins["avg_rating"],
                            "total_cinema_reviews": int(len(grp)),
                            "freshness": freshness,
                        }
                    ],
                    source_model="review_nlp",
                    as_of_date=as_of or ref_date.isoformat(),
                    limitations=[
                        "Sentiment và topic được ước lượng bằng thuật toán lexicon/rating (Task 8 chưa hoàn tất Human Ground Truth chính thức).",
                        "Mô hình phân loại cấp độ toàn review (chưa phải ABSA theo từng khía cạnh).",
                        "root_causes là giả thuyết từ từ điển vận hành, cần kiểm chứng bằng nội dung review gốc.",
                        *audit_notes,
                        *stale_notes,
                    ],
                )
            )

    return decisions


# ---------------------------------------------------------------------------
# 5. Deduplication & Conflict Resolution (Section C2)
# ---------------------------------------------------------------------------
def resolve_conflicts_and_deduplicate(decisions: List[Decision]) -> List[Decision]:
    """
    1. Loại bỏ trùng lặp theo khóa `(rule_id, cinema_key, time_scope, title)` giữ mức severity cao nhất.
    2. Xử lý xung đột/tương tác:
       - Nếu cùng cinema_key có `KPI-ANOM-HIGH`, gắn cảnh báo vào `KPI-REV-DROP` / `KPI-OCC-LOW`.
       - Nếu cùng cinema_key có cả `KPI-OCC-LOW` và `FC-PEAK-DEMAND`, ghi chú rõ khác biệt khung thời gian
         (lịch sử 7 ngày vs dự báo 14 ngày tới).
    3. Sắp xếp theo severity giảm dần, sau đó rule_id, cinema_key.
    """
    dedup: Dict[tuple, Decision] = {}
    for d in decisions:
        key = (d.rule_id, d.cinema_key, d.time_scope, d.title)
        existing = dedup.get(key)
        if existing is None or SEVERITY_RANK[d.severity] > SEVERITY_RANK[existing.severity]:
            dedup[key] = d

    result = list(dedup.values())

    anom_cinemas = {d.cinema_key for d in result if d.rule_id == "KPI-ANOM-HIGH" and d.cinema_key is not None}
    occ_low_cinemas = {d.cinema_key for d in result if d.rule_id == "KPI-OCC-LOW" and d.cinema_key is not None}
    fc_peak_cinemas = {d.cinema_key for d in result if d.rule_id == "FC-PEAK-DEMAND" and d.cinema_key is not None}

    for d in result:
        if d.cinema_key in anom_cinemas and d.rule_id in ("KPI-REV-DROP", "KPI-OCC-LOW"):
            note = "Rạp đang có cảnh báo KPI-ANOM-HIGH (>=30% suất bất thường); cần kiểm toán dữ liệu gốc song song."
            if note not in d.limitations:
                d.limitations.append(note)

        if d.cinema_key in (occ_low_cinemas & fc_peak_cinemas) and d.rule_id in ("KPI-OCC-LOW", "FC-PEAK-DEMAND"):
            note = "Lưu ý đồng thời có KPI-OCC-LOW (7 ngày lịch sử) và FC-PEAK-DEMAND (14 ngày tới theo khung giờ)."
            if note not in d.limitations:
                d.limitations.append(note)

    result.sort(
        key=lambda d: (
            -SEVERITY_RANK[d.severity],
            d.cinema_key if d.cinema_key is not None else 0,
            d.rule_id,
            d.decision_id,
        )
    )
    return result


# ---------------------------------------------------------------------------
# 6. Orchestrator & DB Loader
# ---------------------------------------------------------------------------
def run_dss_from_dataframes(
    daily_kpi_df: Optional[pd.DataFrame] = None,
    occupancy_df: Optional[pd.DataFrame] = None,
    fnb_attach_df: Optional[pd.DataFrame] = None,
    anomaly_df: Optional[pd.DataFrame] = None,
    rfm_profile_df: Optional[pd.DataFrame] = None,
    forecast_df: Optional[pd.DataFrame] = None,
    reviews_df: Optional[pd.DataFrame] = None,
    prev_reviews_df: Optional[pd.DataFrame] = None,
    schedule_df: Optional[pd.DataFrame] = None,
    hist_slot_df: Optional[pd.DataFrame] = None,
    cinema_capacity_map: Optional[Dict[int, int]] = None,
    cinema_name_map: Optional[Dict[int, str]] = None,
    expected_cinema_keys: Optional[Sequence[int]] = None,
    observed_last_date: Optional[str] = None,
    member_last_txn_date: Optional[str] = None,
    reference_date: Optional[date] = None,
    thresholds: Optional[Dict[str, Any]] = None,
    mode: str = "operational",
) -> List[Decision]:
    """Chạy toàn bộ DSS Decision Engine trên các DataFrame đầu vào (không cần DB)."""
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Must be one of {VALID_MODES}.")

    effective_ref_date = reference_date or date.today()
    raw_decisions: List[Decision] = []

    raw_decisions.extend(
        evaluate_kpi_rules(
            daily_kpi_df=daily_kpi_df,
            occupancy_df=occupancy_df,
            fnb_attach_df=fnb_attach_df,
            anomaly_df=anomaly_df,
            reference_date=effective_ref_date,
            thresholds=thresholds,
            mode=mode,
        )
    )
    raw_decisions.extend(
        evaluate_rfm_rules(
            profile_df=rfm_profile_df,
            reference_date=effective_ref_date,
            member_last_txn_date=member_last_txn_date,
            thresholds=thresholds,
            mode=mode,
        )
    )
    raw_decisions.extend(
        evaluate_forecast_rules(
            forecast_df=forecast_df,
            cinema_capacity_map=cinema_capacity_map,
            cinema_name_map=cinema_name_map,
            expected_cinema_keys=expected_cinema_keys,
            reference_date=effective_ref_date,
            schedule_df=schedule_df,
            hist_slot_df=hist_slot_df,
            observed_last_date=observed_last_date,
            thresholds=thresholds,
            mode=mode,
        )
    )
    raw_decisions.extend(
        evaluate_review_rules(
            reviews_df=reviews_df,
            prev_reviews_df=prev_reviews_df,
            cinema_name_map=cinema_name_map,
            min_sample=(thresholds or {}).get("rev_min_sample", DEFAULT_THRESHOLDS["rev_min_sample"]),
            reference_date=effective_ref_date,
            thresholds=thresholds,
            mode=mode,
        )
    )

    return resolve_conflicts_and_deduplicate(raw_decisions)


def load_dss_inputs_from_db(engine: Engine, cinema_key: Optional[int] = None) -> Dict[str, Any]:
    """Tải toàn bộ dữ liệu đầu vào sạch từ PostgreSQL `cinema_dw` (chỉ đọc)."""
    cinema_filter = "WHERE cinema_key = :cinema_key" if cinema_key is not None else ""
    params = {"cinema_key": cinema_key} if cinema_key is not None else {}

    with engine.connect() as conn:
        dim_cinema = pd.read_sql(
            text(f"""
                SELECT c.cinema_key, c.cinema_name, COALESCE(SUM(r.capacity), 0) AS total_capacity
                FROM cinema_dw.dim_cinema c
                LEFT JOIN cinema_dw.dim_room r ON c.cinema_key = r.cinema_key
                {"WHERE c.cinema_key = :cinema_key" if cinema_key is not None else ""}
                GROUP BY c.cinema_key, c.cinema_name
                ORDER BY c.cinema_key
            """),
            conn,
            params=params,
        )

        daily_kpi = pd.read_sql(
            text(f"SELECT * FROM cinema_dw.vw_daily_kpi {cinema_filter}"),
            conn,
            params=params,
        )

        occupancy = pd.read_sql(
            text(f"""
                SELECT o.date_key, d.full_date, r.cinema_key, o.cinema_name,
                       o.qty_clean, o.capacity, o.occupancy_clean, o.is_low_demand
                FROM cinema_dw.vw_showtime_occupancy o
                JOIN cinema_dw.dim_date d ON o.date_key = d.date_key
                JOIN cinema_dw.dim_room r ON o.room_key = r.room_key
                {"WHERE r.cinema_key = :cinema_key" if cinema_key is not None else ""}
            """),
            conn,
            params=params,
        )

        fnb_attach = pd.read_sql(
            text(f"SELECT * FROM cinema_dw.vw_fnb_attach {cinema_filter}"),
            conn,
            params=params,
        )

        anomaly = pd.read_sql(
            text(f"""
                SELECT a.full_date, r.cinema_key, a.cinema_name, a.is_anomaly_showtime, a.anom_txn_rate_pct
                FROM cinema_dw.vw_anomaly_showtime a
                JOIN cinema_dw.dim_room r ON a.room_key = r.room_key
                {"WHERE r.cinema_key = :cinema_key" if cinema_key is not None else ""}
            """),
            conn,
            params=params,
        )

        rfm_profile = pd.read_sql(
            text("SELECT * FROM cinema_dw.rfm_cluster_profile ORDER BY cluster_id"),
            conn,
        )

        member_last_txn_row = conn.execute(
            text("""
                SELECT MAX(d.full_date)
                FROM cinema_dw.fact_ticket ft
                JOIN cinema_dw.dim_date d ON ft.date_key = d.date_key
                JOIN cinema_dw.dim_customer dc ON ft.customer_key = dc.customer_key
                WHERE dc.customer_id <> 'GUEST'
            """)
        ).scalar()

        forecast = pd.read_sql(
            text(f"""
                SELECT target_date, cinema_key, time_slot, model_name, yhat, yhat_lower, yhat_upper, is_test, created_at
                FROM cinema_dw.forecast_result
                WHERE is_test = FALSE
                {"AND cinema_key = :cinema_key" if cinema_key is not None else ""}
                ORDER BY target_date, cinema_key, time_slot
            """),
            conn,
            params=params,
        )

        schedule_df = pd.read_sql(
            text(f"""
                SELECT s.show_date, s.cinema_key, s.room_key, r.capacity, t.time_slot
                FROM cinema_dw.showtime_schedule s
                JOIN cinema_dw.dim_room r ON s.room_key = r.room_key
                LEFT JOIN cinema_dw.dim_time t
                  ON EXTRACT(HOUR FROM s.show_time) = t.hour_no
                 AND EXTRACT(MINUTE FROM s.show_time) = t.minute_no
                {"WHERE s.cinema_key = :cinema_key" if cinema_key is not None else ""}
            """),
            conn,
            params=params,
        )

        hist_slot_df = pd.read_sql(
            text(f"""
                SELECT d.full_date, f.cinema_key, t.time_slot, SUM(f.tickets_sold_clean) AS qty_clean
                FROM cinema_dw.fact_showtime_summary f
                JOIN cinema_dw.dim_date d ON f.date_key = d.date_key
                JOIN cinema_dw.dim_time t ON f.time_key = t.time_key
                {"WHERE f.cinema_key = :cinema_key" if cinema_key is not None else ""}
                GROUP BY d.full_date, f.cinema_key, t.time_slot
            """),
            conn,
            params=params,
        )

        observed_last_row = conn.execute(
            text("""
                SELECT MAX(d.full_date)
                FROM cinema_dw.fact_showtime_summary f
                JOIN cinema_dw.dim_date d ON f.date_key = d.date_key
            """)
        ).scalar()

        reviews = pd.read_sql(
            text(f"""
                SELECT rr.review_id, rr.cinema_key, rr.rating, rr.published_date,
                       nl.sentiment_final, nl.topics
                FROM cinema_dw.review_raw rr
                JOIN cinema_dw.review_nlp nl USING (review_id)
                {"WHERE rr.cinema_key = :cinema_key" if cinema_key is not None else ""}
                ORDER BY rr.published_date DESC
            """),
            conn,
            params=params,
        )

    cap_map = {int(r["cinema_key"]): int(r["total_capacity"]) for _, r in dim_cinema.iterrows()}
    name_map = {int(r["cinema_key"]): str(r["cinema_name"]) for _, r in dim_cinema.iterrows()}
    expected_cks = [int(r["cinema_key"]) for _, r in dim_cinema.iterrows()]

    return {
        "daily_kpi_df": daily_kpi,
        "occupancy_df": occupancy,
        "fnb_attach_df": fnb_attach,
        "anomaly_df": anomaly,
        "rfm_profile_df": rfm_profile,
        "forecast_df": forecast,
        "reviews_df": reviews,
        "schedule_df": schedule_df,
        "hist_slot_df": hist_slot_df,
        "cinema_capacity_map": cap_map,
        "cinema_name_map": name_map,
        "expected_cinema_keys": expected_cks,
        "observed_last_date": _to_iso_date(observed_last_row),
        "member_last_txn_date": _to_iso_date(member_last_txn_row),
    }


def run_dss(
    engine: Engine,
    cinema_key: Optional[int] = None,
    reference_date: Optional[date] = None,
    mode: str = "operational",
) -> List[Decision]:
    """Chạy DSS Engine trực tiếp trên PostgreSQL `cinema_dw` (read-only)."""
    effective_ref_date = reference_date or date.today()
    inputs = load_dss_inputs_from_db(engine, cinema_key=cinema_key)
    return run_dss_from_dataframes(**inputs, reference_date=effective_ref_date, mode=mode)

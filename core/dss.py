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
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

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
    "fc_high_demand_ratio": 0.80,         # yhat >= 80% sức chứa rạp/khung giờ -> HIGH
    "fc_spike_vs_hist_ratio": 1.25,       # hoặc yhat >= 1.25x trung bình lịch sử -> MEDIUM
    "rev_min_sample": 10,                 # Tối thiểu 10 reviews mới sinh insight
    "max_stale_days": 30,                 # Quá 30 ngày so với ref_date -> cảnh báo dữ liệu cũ
}

RULE_CATALOG: List[Dict[str, str]] = [
    {
        "rule_id": "KPI-REV-DROP",
        "category": "REVENUE_KPI",
        "objective": "Phát hiện sụt giảm doanh thu sạch (vé + F&B) theo rạp so với kỳ liền trước.",
        "trigger": "pct_change <= -15.0% (HIGH), <= -25.0% (CRITICAL)",
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
        "objective": "Cảnh báo khung giờ dự báo nhu cầu cao trong 14 ngày tới để rà soát nhân sự & lịch chiếu.",
        "trigger": "yhat >= 0.80 * cinema_capacity (HIGH) hoặc >= 1.25 * hist_slot_mean (MEDIUM)",
        "source_model": "forecast",
    },
    {
        "rule_id": "FC-DATA-GAP",
        "category": "CAPACITY_FORECAST",
        "objective": "Cảnh báo thiếu dữ liệu dự báo cho rạp để tránh ra quyết định công suất thiếu căn cứ.",
        "trigger": "future forecast rows == 0 (MEDIUM)",
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


# ---------------------------------------------------------------------------
# 1. Đánh giá KPI Doanh thu & Hiệu quả Vận hành (D4)
# ---------------------------------------------------------------------------
def evaluate_kpi_rules(
    daily_kpi_df: Optional[pd.DataFrame],
    occupancy_df: Optional[pd.DataFrame] = None,
    fnb_attach_df: Optional[pd.DataFrame] = None,
    anomaly_df: Optional[pd.DataFrame] = None,
    reference_date: Optional[date] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[Decision]:
    """
    Đánh giá các rule KPI từ dữ liệu sạch (`*_clean`).
    So sánh cửa sổ 7 ngày gần nhất với 7 ngày liền trước theo từng `cinema_key`.
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decisions: List[Decision] = []

    if daily_kpi_df is None or daily_kpi_df.empty:
        return decisions

    df = daily_kpi_df.copy()
    df["full_date"] = pd.to_datetime(df["full_date"], errors="coerce")
    df = df.dropna(subset=["full_date", "cinema_key"])
    if df.empty:
        return decisions

    max_date = df["full_date"].max().date()
    ref_date = reference_date or max_date
    stale_days = _is_stale(max_date.isoformat(), ref_date, cfg["max_stale_days"])

    curr_start = pd.Timestamp(max_date) - pd.Timedelta(days=6)
    prev_start = curr_start - pd.Timedelta(days=7)
    prev_end = curr_start - pd.Timedelta(days=1)
    time_scope = f"{curr_start.date().isoformat()}..{max_date.isoformat()}"

    for cinema_key, grp in df.groupby("cinema_key"):
        ck = int(cinema_key)
        cinema_name = str(grp["cinema_name"].iloc[0]) if "cinema_name" in grp.columns else f"CN{ck:02d}"

        curr_g = grp[(grp["full_date"] >= curr_start) & (grp["full_date"] <= pd.Timestamp(max_date))]
        prev_g = grp[(grp["full_date"] >= prev_start) & (grp["full_date"] <= prev_end)]

        if len(curr_g) < 3 or len(prev_g) < 3:
            continue

        # Chỉ dùng doanh thu sạch (Clean Data Only)
        curr_rev = float(
            pd.to_numeric(curr_g["ticket_revenue_clean"], errors="coerce").fillna(0).sum()
            + pd.to_numeric(curr_g.get("fnb_revenue_clean", 0), errors="coerce").fillna(0).sum()
        )
        prev_rev = float(
            pd.to_numeric(prev_g["ticket_revenue_clean"], errors="coerce").fillna(0).sum()
            + pd.to_numeric(prev_g.get("fnb_revenue_clean", 0), errors="coerce").fillna(0).sum()
        )

        if prev_rev > 0:
            rev_pct_change = ((curr_rev - prev_rev) / prev_rev) * 100.0
            if rev_pct_change <= cfg["kpi_rev_drop_high_pct"]:
                severity = "CRITICAL" if rev_pct_change <= cfg["kpi_rev_drop_critical_pct"] else "HIGH"
                limitations = [
                    "Biến động doanh thu 7 ngày chỉ phản ánh tương quan thời điểm, chưa kiểm soát lịch phát hành phim mới hoặc ngày lễ."
                ]
                if stale_days is not None:
                    limitations.append(f"Dữ liệu KPI đã cũ {stale_days} ngày so với ngày tham chiếu {ref_date.isoformat()}.")

                decisions.append(
                    Decision(
                        decision_id=make_decision_id("KPI-REV-DROP", ck, time_scope),
                        rule_id="KPI-REV-DROP",
                        cinema_key=ck,
                        time_scope=time_scope,
                        category="REVENUE_KPI",
                        severity=severity,
                        title=f"[{cinema_name}] Doanh thu sạch 7 ngày giảm {abs(rev_pct_change):.1f}%",
                        recommendation="Rà soát cơ cấu suất chiếu, lịch phim đang chiếu và hiệu quả kênh bán vé tại rạp.",
                        explanation={
                            "what_happened": f"Tổng doanh thu sạch (vé + F&B) tại {cinema_name} đạt {curr_rev:,.0f} đ, giảm {abs(rev_pct_change):.1f}% so với 7 ngày trước ({prev_rev:,.0f} đ).",
                            "why_triggered": f"Rule KPI-REV-DROP kích hoạt khi mức giảm <= {cfg['kpi_rev_drop_high_pct']}%.",
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
                                "days_current": int(len(curr_g)),
                                "days_previous": int(len(prev_g)),
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
                    severity = "HIGH" if weighted_occ < cfg["kpi_occ_low_high"] else "MEDIUM"
                    decisions.append(
                        Decision(
                            decision_id=make_decision_id("KPI-OCC-LOW", ck, occ_scope),
                            rule_id="KPI-OCC-LOW",
                            cinema_key=ck,
                            time_scope=occ_scope,
                            category="REVENUE_KPI",
                            severity=severity,
                            title=f"[{cinema_name}] Tỷ lệ lấp đầy sạch thấp ({weighted_occ * 100:.1f}%)",
                            recommendation="Rà soát phân bổ suất chiếu các khung giờ thấp điểm (<30%) và cân nhắc tối ưu lịch phòng.",
                            explanation={
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
                                }
                            ],
                            source_model="kpi_dw",
                            as_of_date=occ_max.isoformat(),
                            limitations=["Không tính trung bình cộng giản đơn occupancy; đã gia quyền theo sức chứa phòng."],
                        )
                    )

    # Rule KPI-FNB-DROP: dùng attach_rate (Raw) theo docs/kpi_dictionary.md
    if fnb_attach_df is not None and not fnb_attach_df.empty:
        fnb = fnb_attach_df.copy()
        fnb["full_date"] = pd.to_datetime(fnb["full_date"], errors="coerce")
        fnb = fnb.dropna(subset=["full_date", "cinema_key", "ticket_txn_count", "fnb_txn_count"])
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

                c_tkt = float(c_g["ticket_txn_count"].sum())
                c_fnb = float(c_g["fnb_txn_count"].sum())
                if c_tkt <= 0:
                    continue
                curr_attach = c_fnb / c_tkt

                p_tkt = float(p_g["ticket_txn_count"].sum())
                p_fnb = float(p_g["fnb_txn_count"].sum())
                prev_attach = (p_fnb / p_tkt) if p_tkt > 0 else None
                delta_pp = ((curr_attach - prev_attach) * 100.0) if prev_attach is not None else None

                triggered = (curr_attach < cfg["kpi_fnb_attach_min"]) or (
                    delta_pp is not None and delta_pp <= cfg["kpi_fnb_attach_drop_pp"]
                )
                if triggered:
                    severity = "HIGH" if (delta_pp is not None and delta_pp <= cfg["kpi_fnb_attach_drop_pp"]) else "MEDIUM"
                    decisions.append(
                        Decision(
                            decision_id=make_decision_id("KPI-FNB-DROP", ck, fnb_scope),
                            rule_id="KPI-FNB-DROP",
                            cinema_key=ck,
                            time_scope=fnb_scope,
                            category="REVENUE_KPI",
                            severity=severity,
                            title=f"[{cinema_name}] Tỷ lệ gắn kèm F&B cần chú ý ({curr_attach * 100:.1f}%)",
                            recommendation="Kiểm tra trưng bày quầy bắp nước, tốc độ phục vụ giờ cao điểm và đề xuất combo kèm vé.",
                            explanation={
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
                                    "ticket_txn_count": int(c_tkt),
                                    "fnb_txn_count": int(c_fnb),
                                }
                            ],
                            source_model="kpi_dw",
                            as_of_date=fnb_max.isoformat(),
                            limitations=[
                                "attach_rate thuộc Nhóm 2 (Raw) theo docs/kpi_dictionary.md; spp_clean là chi tiêu trên giao dịch vé (SPT), không phải trên từng khách."
                            ],
                        )
                    )

    # Rule KPI-ANOM-HIGH: cảnh báo chất lượng dữ liệu nếu tỷ lệ suất bất thường cao
    if anomaly_df is not None and not anomaly_df.empty:
        anom = anomaly_df.copy()
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
                    decisions.append(
                        Decision(
                            decision_id=make_decision_id("KPI-ANOM-HIGH", ck, f"as_of_{a_max}"),
                            rule_id="KPI-ANOM-HIGH",
                            cinema_key=ck,
                            time_scope=f"as_of_{a_max}",
                            category="REVENUE_KPI",
                            severity="HIGH",
                            title=f"[{cinema_name}] {anom_pct:.1f}% suất chiếu có giao dịch bất thường",
                            recommendation="Thực hiện kiểm toán đối soát dữ liệu POS/vé (price_zero, qty_spike) trước khi ra quyết định kinh doanh lớn.",
                            explanation={
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
                                }
                            ],
                            source_model="kpi_dw",
                            as_of_date=a_max or max_date.isoformat(),
                            limitations=["Cờ bất thường phản ánh quy tắc kiểm soát ETL (qty_spike, price_zero), cần đối chiếu chứng từ POS."],
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
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[Decision]:
    """
    Đánh giá phân khúc RFM thực tế.
    Kiểm tra tính tương thích giữa số cụm k thực tế của mô hình và 6 nhóm nghiệp vụ tham chiếu.
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decisions: List[Decision] = []

    if profile_df is None or profile_df.empty:
        return decisions

    prof = profile_df.copy()
    actual_k = int(prof["cluster_id"].nunique()) if "cluster_id" in prof.columns else len(prof)
    data_date = as_of_date or (
        _to_iso_date(prof["updated_at"].max()) if "updated_at" in prof.columns else date.today().isoformat()
    )
    time_scope = f"rfm_as_of_{data_date}"

    stale_days = _is_stale(data_date, reference_date, cfg["max_stale_days"]) if reference_date else None
    stale_note = [f"Mô hình RFM chạy cách đây {stale_days} ngày."] if stale_days is not None else []

    # Rule RFM-K-MISMATCH: Không tự ý đổi k, nhưng phải đánh dấu vấn đề tích hợp
    if actual_k != cfg["rfm_expected_business_k"]:
        present_names = sorted(prof["cluster_name"].astype(str).unique().tolist())
        expected_names = ["VIP", "Trung thành", "Thỉnh thoảng", "Mới", "Có nguy cơ rời bỏ", "Ngủ đông"]
        missing_names = [n for n in expected_names if n not in present_names]

        decisions.append(
            Decision(
                decision_id=make_decision_id("RFM-K-MISMATCH", None, time_scope),
                rule_id="RFM-K-MISMATCH",
                cinema_key=None,
                time_scope=time_scope,
                category="RETENTION_RFM",
                severity="MEDIUM",
                title=f"Phân khúc RFM thực tế dùng k={actual_k} (khác {cfg['rfm_expected_business_k']} nhóm nghiệp vụ)",
                recommendation="Chỉ áp dụng chính sách chăm sóc cho các cụm thực tế hiện có; không gán cứng chính sách cho các nhóm bị gộp.",
                explanation={
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
                    }
                ],
                source_model="rfm_kmeans",
                as_of_date=data_date or "",
                limitations=[
                    "Khách vãng lai (GUEST, ~70% giao dịch) không nằm trong mô hình RFM.",
                    *stale_note,
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
            decisions.append(
                Decision(
                    decision_id=make_decision_id("RFM-CHURN-RISK", None, time_scope),
                    rule_id="RFM-CHURN-RISK",
                    cinema_key=None,
                    time_scope=time_scope,
                    category="RETENTION_RFM",
                    severity="HIGH",
                    title=f"Nhóm 'Có nguy cơ rời bỏ' chiếm {pct_cust:.1f}% hội viên ({cust_cnt:,} khách)",
                    recommendation="Thiết kế thử nghiệm A/B chiến dịch tái kích hoạt (gợi ý phim theo top_genre, ưu đãi combo) cho một nhóm mẫu trước khi nhân rộng.",
                    explanation={
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
                        }
                    ],
                    source_model="rfm_kmeans",
                    as_of_date=data_date or "",
                    limitations=[
                        "Chưa có thử nghiệm A/B thực tế nên không khẳng định chiến dịch chắc chắn làm tăng doanh thu.",
                        *stale_note,
                    ],
                )
            )

    # Rule RFM-VIP-RETENTION
    vip_rows = prof[prof["cluster_name"] == "VIP"]
    if not vip_rows.empty:
        row = vip_rows.iloc[0]
        cust_cnt = int(row["customer_count"])
        if cust_cnt >= cfg["rfm_vip_min_count"]:
            decisions.append(
                Decision(
                    decision_id=make_decision_id("RFM-VIP-RETENTION", None, time_scope),
                    rule_id="RFM-VIP-RETENTION",
                    cinema_key=None,
                    time_scope=time_scope,
                    category="RETENTION_RFM",
                    severity="LOW",
                    title=f"Duy trì trải nghiệm nhóm VIP ({cust_cnt:,} hội viên, {float(row['pct_customers']):.1f}%)",
                    recommendation="Ưu tiên quyền lợi giữ chân (suất chiếu sớm, ưu đãi phòng đặc biệt/combo) thay vì giảm giá vé sâu.",
                    explanation={
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
                        }
                    ],
                    source_model="rfm_kmeans",
                    as_of_date=data_date or "",
                    limitations=[
                        "Tác động tài chính của quyền lợi VIP cần được đo lường qua giữ chân dài hạn.",
                        *stale_note,
                    ],
                )
            )

    return decisions


# ---------------------------------------------------------------------------
# 3. Đánh giá Capacity & Staffing — Forecasting (D2)
# ---------------------------------------------------------------------------
def evaluate_forecast_rules(
    forecast_df: Optional[pd.DataFrame],
    cinema_capacity_map: Optional[Dict[int, int]] = None,
    cinema_name_map: Optional[Dict[int, str]] = None,
    expected_cinema_keys: Optional[Sequence[int]] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[Decision]:
    """
    Đánh giá dự báo 14 ngày (`is_test = False`).
    Lưu ý: Không tự bịa định mức nhân sự/chi phí; chỉ cảnh báo nhu cầu cao và đề xuất rà soát.
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decisions: List[Decision] = []
    cap_map = cinema_capacity_map or {}
    name_map = cinema_name_map or {}

    if forecast_df is None or forecast_df.empty:
        for ck in (expected_cinema_keys or [None]):
            decisions.append(
                Decision(
                    decision_id=make_decision_id("FC-DATA-GAP", ck, "future_14d"),
                    rule_id="FC-DATA-GAP",
                    cinema_key=ck,
                    time_scope="future_14d",
                    category="CAPACITY_FORECAST",
                    severity="MEDIUM",
                    title=f"[{name_map.get(ck, f'CN{ck:02d}') if ck else 'Hệ thống'}] Thiếu dữ liệu dự báo 14 ngày",
                    recommendation="Chạy lại pipeline dự báo (scripts/run_forecast.py) trước khi lập kế hoạch vận hành.",
                    explanation={
                        "what_happened": "Không tìm thấy bản ghi dự báo tương lai (is_test=FALSE).",
                        "why_triggered": "Rule FC-DATA-GAP kích hoạt khi bảng forecast_result rỗng.",
                        "why_recommended": "Không có đầu vào hợp lệ thì không thể đưa khuyến nghị công suất.",
                        "manager_checks": ["Kiểm tra log chạy scripts/run_forecast.py và dữ liệu fact_showtime_summary."],
                    },
                    evidence=[{"future_rows": 0}],
                    source_model="forecast",
                    as_of_date=date.today().isoformat(),
                    limitations=["Thiếu hoàn toàn đầu ra mô hình dự báo."],
                )
            )
        return decisions

    df = forecast_df.copy()
    if "is_test" in df.columns:
        df = df[df["is_test"] == False]  # noqa: E712
    df["target_date"] = pd.to_datetime(df["target_date"], errors="coerce")
    df = df.dropna(subset=["target_date", "cinema_key", "time_slot", "yhat"])

    if expected_cinema_keys:
        present_cks = set(df["cinema_key"].astype(int).unique()) if not df.empty else set()
        for ck in expected_cinema_keys:
            if ck not in present_cks:
                decisions.append(
                    Decision(
                        decision_id=make_decision_id("FC-DATA-GAP", ck, "future_14d"),
                        rule_id="FC-DATA-GAP",
                        cinema_key=ck,
                        time_scope="future_14d",
                        category="CAPACITY_FORECAST",
                        severity="MEDIUM",
                        title=f"[{name_map.get(ck, f'CN{ck:02d}')}] Thiếu dữ liệu dự báo 14 ngày",
                        recommendation="Kiểm tra dữ liệu lịch sử và chạy lại mô hình dự báo cho rạp này.",
                        explanation={
                            "what_happened": f"Không có bản ghi dự báo tương lai cho rạp cinema_key={ck}.",
                            "why_triggered": "Rule FC-DATA-GAP kích hoạt khi thiếu dự báo theo rạp.",
                            "why_recommended": "Tránh đưa quyết định mở suất hoặc bố trí ca làm việc khi thiếu dự báo.",
                            "manager_checks": ["Kiểm tra dữ liệu nguồn fact_showtime_summary của rạp."],
                        },
                        evidence=[{"cinema_key": ck, "future_rows": 0}],
                        source_model="forecast",
                        as_of_date=date.today().isoformat(),
                        limitations=["Không có dữ liệu dự báo cho rạp."],
                    )
                )

    if df.empty:
        return decisions

    as_of = _to_iso_date(df["created_at"].max()) if "created_at" in df.columns else date.today().isoformat()

    for cinema_key, grp in df.groupby("cinema_key"):
        ck = int(cinema_key)
        cinema_name = name_map.get(ck, f"CN{ck:02d}")
        total_cap = cap_map.get(ck)

        # Tổng hợp theo ngày × khung giờ, tìm các điểm đỉnh nhu cầu
        high_rows = []
        for _, row in grp.iterrows():
            yhat = float(row["yhat"])
            if total_cap and total_cap > 0 and yhat >= cfg["fc_high_demand_ratio"] * total_cap:
                high_rows.append((row, yhat / total_cap))

        if high_rows:
            # Chọn ngày/khung giờ có áp lực cao nhất làm đại diện cho rạp trong cửa sổ 14 ngày (tránh spam nhiều cảnh báo trùng)
            top_row, top_ratio = max(high_rows, key=lambda x: x[1])
            t_date = top_row["target_date"].date().isoformat()
            slot = str(top_row["time_slot"])
            yhat_val = float(top_row["yhat"])
            yhat_lo = float(top_row["yhat_lower"]) if pd.notna(top_row.get("yhat_lower")) else None
            yhat_hi = float(top_row["yhat_upper"]) if pd.notna(top_row.get("yhat_upper")) else None
            model_name = str(top_row.get("model_name", "unknown"))

            date_min = grp["target_date"].min().date().isoformat()
            date_max = grp["target_date"].max().date().isoformat()
            scope = f"{date_min}..{date_max}"

            decisions.append(
                Decision(
                    decision_id=make_decision_id("FC-PEAK-DEMAND", ck, scope),
                    rule_id="FC-PEAK-DEMAND",
                    cinema_key=ck,
                    time_scope=scope,
                    category="CAPACITY_FORECAST",
                    severity="HIGH",
                    title=f"[{cinema_name}] Dự báo nhu cầu cao ở {len(high_rows)} khung giờ (đỉnh {t_date} - {slot})",
                    recommendation=(
                        "Rà soát lịch trực quầy vé/bắp nước và kiểm tra khả năng đáp ứng của số phòng/suất chiếu "
                        "hiện có trước khi cân nhắc điều phối thêm suất."
                    ),
                    explanation={
                        "what_happened": (
                            f"Mô hình {model_name} dự báo {len(high_rows)} khung giờ tại {cinema_name} đạt >= {cfg['fc_high_demand_ratio']*100:.0f}% "
                            f"tổng sức chứa 1 lượt phòng ({total_cap} ghế), đỉnh điểm ngày {t_date} khung {slot} với yhat={yhat_val:.0f} vé."
                        ),
                        "why_triggered": f"Rule FC-PEAK-DEMAND kích hoạt khi yhat >= {cfg['fc_high_demand_ratio']} * tổng sức chứa phòng.",
                        "why_recommended": "Nhu cầu vé cao có thể kéo theo ùn ứ tại quầy F&B và soát vé nếu không chuẩn bị trước.",
                        "manager_checks": [
                            "Đối chiếu lịch chiếu dự kiến (số suất và phân bổ phòng) trong ngày cao điểm.",
                            "Xác định định mức nhân sự thực tế theo ca tại rạp (hệ thống không tự tính số nhân viên).",
                        ],
                    },
                    evidence=[
                        {
                            "model_name": model_name,
                            "peak_date": t_date,
                            "peak_time_slot": slot,
                            "yhat": round(yhat_val, 2),
                            "yhat_lower": round(yhat_lo, 2) if yhat_lo is not None else None,
                            "yhat_upper": round(yhat_hi, 2) if yhat_hi is not None else None,
                            "cinema_single_turn_capacity": total_cap,
                            "demand_to_capacity_ratio": round(top_ratio, 4),
                            "high_demand_slots_count": len(high_rows),
                        }
                    ],
                    source_model="forecast",
                    as_of_date=as_of or date.today().isoformat(),
                    limitations=[
                        "Khoảng [yhat_lower, yhat_upper] là khoảng heuristic từ độ lệch chuẩn phần dư out-of-sample (±1.96*std), không phải bảo đảm xác suất 95%.",
                        "Dự báo theo grain ngày × rạp × khung giờ; một khung giờ có thể gồm nhiều suất chiếu nên cần đối chiếu lịch chiếu thực tế.",
                        "Hệ thống không tạo định mức nhân sự hoặc chi phí giả.",
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
) -> List[Decision]:
    """
    Đánh giá chất lượng dịch vụ từ review theo TỪNG rạp (`cinema_key`).
    Tuyệt đối không suy rộng kết quả rạp CN01 sang CN02/CN03.
    Tái sử dụng `reviews.insights.generate_operational_insights`.
    """
    decisions: List[Decision] = []
    if reviews_df is None or reviews_df.empty or "cinema_key" not in reviews_df.columns:
        return decisions

    name_map = cinema_name_map or {}

    for cinema_key, grp in reviews_df.groupby("cinema_key"):
        if pd.isna(cinema_key):
            continue
        ck = int(cinema_key)
        cinema_name = name_map.get(ck, f"CN{ck:02d}")

        if len(grp) < min_sample:
            continue

        prev_grp = None
        if prev_reviews_df is not None and not prev_reviews_df.empty and "cinema_key" in prev_reviews_df.columns:
            p_sub = prev_reviews_df[prev_reviews_df["cinema_key"] == ck]
            if len(p_sub) >= min_sample:
                prev_grp = p_sub

        insights = generate_operational_insights(grp, prev_df=prev_grp, min_sample=min_sample)
        as_of = (
            _to_iso_date(pd.to_datetime(grp["published_date"], errors="coerce").max())
            if "published_date" in grp.columns
            else date.today().isoformat()
        )
        time_scope = f"reviews_as_of_{as_of}"

        for ins in insights:
            sev = _REVIEW_PRIORITY_MAP.get(ins["priority"], "LOW")
            if sev == "LOW":
                # Chỉ phát hành cảnh báo hành động khi đạt ngưỡng từ TRUNG BÌNH trở lên
                continue

            topic = str(ins["topic"])
            decisions.append(
                Decision(
                    decision_id=make_decision_id("REV-TOPIC-ALERT", ck, time_scope, sub_key=topic),
                    rule_id="REV-TOPIC-ALERT",
                    cinema_key=ck,
                    time_scope=time_scope,
                    category="SERVICE_QUALITY_REVIEW",
                    severity=sev,
                    title=f"[{cinema_name}] Phản hồi tiêu cực chủ đề '{topic}' ({ins['pct_negative']}%)",
                    recommendation=f"Quick win: {ins['quick_win']} | Mục tiêu: {ins['kpi_target']}",
                    explanation={
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
                        }
                    ],
                    source_model="review_nlp",
                    as_of_date=as_of or date.today().isoformat(),
                    limitations=[
                        "Sentiment và topic được ước lượng bằng thuật toán lexicon/rating (Task 8 chưa hoàn tất Human Ground Truth chính thức).",
                        "Mô hình phân loại cấp độ toàn review (chưa phải ABSA theo từng khía cạnh).",
                        "root_causes là giả thuyết từ từ điển vận hành, cần kiểm chứng bằng nội dung review gốc.",
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
    cinema_capacity_map: Optional[Dict[int, int]] = None,
    cinema_name_map: Optional[Dict[int, str]] = None,
    expected_cinema_keys: Optional[Sequence[int]] = None,
    reference_date: Optional[date] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[Decision]:
    """Chạy toàn bộ DSS Decision Engine trên các DataFrame đầu vào (không cần DB)."""
    raw_decisions: List[Decision] = []

    raw_decisions.extend(
        evaluate_kpi_rules(
            daily_kpi_df=daily_kpi_df,
            occupancy_df=occupancy_df,
            fnb_attach_df=fnb_attach_df,
            anomaly_df=anomaly_df,
            reference_date=reference_date,
            thresholds=thresholds,
        )
    )
    raw_decisions.extend(
        evaluate_rfm_rules(
            profile_df=rfm_profile_df,
            reference_date=reference_date,
            thresholds=thresholds,
        )
    )
    raw_decisions.extend(
        evaluate_forecast_rules(
            forecast_df=forecast_df,
            cinema_capacity_map=cinema_capacity_map,
            cinema_name_map=cinema_name_map,
            expected_cinema_keys=expected_cinema_keys,
            thresholds=thresholds,
        )
    )
    raw_decisions.extend(
        evaluate_review_rules(
            reviews_df=reviews_df,
            prev_reviews_df=prev_reviews_df,
            cinema_name_map=cinema_name_map,
            min_sample=(thresholds or {}).get("rev_min_sample", DEFAULT_THRESHOLDS["rev_min_sample"]),
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
        "cinema_capacity_map": cap_map,
        "cinema_name_map": name_map,
        "expected_cinema_keys": expected_cks,
    }


def run_dss(
    engine: Engine,
    cinema_key: Optional[int] = None,
    reference_date: Optional[date] = None,
) -> List[Decision]:
    """Chạy DSS Engine trực tiếp trên PostgreSQL `cinema_dw` (read-only)."""
    inputs = load_dss_inputs_from_db(engine, cinema_key=cinema_key)
    return run_dss_from_dataframes(**inputs, reference_date=reference_date)

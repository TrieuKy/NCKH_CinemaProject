"""
Sinh dữ liệu thô (staging) cho Data Warehouse rạp chiếu.

Quy luật: docs/assumptions.md
Đầu ra: data/staging_ticket.csv , data/staging_fnb.csv

Chạy:
    python scripts/generate_mock_data.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.holidays import get_holidays_range, get_peak_days_range

SEED = 42
WALK_IN_RATE = 0.70
ANOMALY_TXN_RATE = 0.015
ANOMALY_FNB_RATE = 0.015
VOLUME_SCALE = 1.0
START = date(2025, 10, 1)
END = date(2026, 9, 30)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

RNG = np.random.default_rng(SEED)

CINEMAS = [
    {
        "cinema_id": "CN01",
        "cinema_name": "CGV Landmark 81",
        "district": "Bình Thạnh",
        "city": "TP. Hồ Chí Minh",
        "cinema_segment": "premium_destination",
        "occ_mult": 1.08,
    },
    {
        "cinema_id": "CN02",
        "cinema_name": "CGV Vincom Đồng Khởi",
        "district": "Quận 1",
        "city": "TP. Hồ Chí Minh",
        "cinema_segment": "urban_cbd",
        "occ_mult": 1.02,
    },
    {
        "cinema_id": "CN03",
        "cinema_name": "CGV Pearl Plaza",
        "district": "Bình Thạnh",
        "city": "TP. Hồ Chí Minh",
        "cinema_segment": "family_mall",
        "occ_mult": 0.95,
    },
]

ROOMS = [
    {"room_id": "R101", "cinema_id": "CN01", "room_name": "Phòng 1", "room_type": "2D", "capacity": 120},
    {"room_id": "R102", "cinema_id": "CN01", "room_name": "Phòng 2", "room_type": "3D", "capacity": 100},
    {"room_id": "R103", "cinema_id": "CN01", "room_name": "Phòng IMAX", "room_type": "IMAX", "capacity": 180},
    {"room_id": "R201", "cinema_id": "CN02", "room_name": "Phòng 1", "room_type": "2D", "capacity": 140},
    {"room_id": "R202", "cinema_id": "CN02", "room_name": "Phòng 2", "room_type": "3D", "capacity": 110},
    {"room_id": "R203", "cinema_id": "CN02", "room_name": "Phòng 3", "room_type": "2D", "capacity": 90},
    {"room_id": "R301", "cinema_id": "CN03", "room_name": "Phòng 1", "room_type": "2D", "capacity": 130},
    {"room_id": "R302", "cinema_id": "CN03", "room_name": "Phòng 2", "room_type": "3D", "capacity": 95},
]

MOVIES = [
    {"movie_id": "MV01", "title": "Lật Mặt 8", "genre": "Hành động", "language": "Tiếng Việt", "duration_min": 132, "rating": "T16", "release_date": date(2025, 4, 5), "country": "Việt Nam"},
    {"movie_id": "MV02", "title": "Mai", "genre": "Tâm lý", "language": "Tiếng Việt", "duration_min": 131, "rating": "T18", "release_date": date(2025, 4, 18), "country": "Việt Nam"},
    {"movie_id": "MV03", "title": "Minecraft Movie", "genre": "Gia đình", "language": "Tiếng Anh", "duration_min": 101, "rating": "P", "release_date": date(2025, 4, 4), "country": "Mỹ"},
    {"movie_id": "MV04", "title": "Thunderbolts", "genre": "Hành động", "language": "Tiếng Anh", "duration_min": 126, "rating": "T13", "release_date": date(2025, 5, 2), "country": "Mỹ"},
    {"movie_id": "MV05", "title": "Làm Giàu Với Ma 2", "genre": "Hài", "language": "Tiếng Việt", "duration_min": 118, "rating": "T13", "release_date": date(2025, 5, 23), "country": "Việt Nam"},
    {"movie_id": "MV06", "title": "How to Train Your Dragon", "genre": "Gia đình", "language": "Tiếng Anh", "duration_min": 125, "rating": "P", "release_date": date(2025, 6, 13), "country": "Mỹ"},
    {"movie_id": "MV07", "title": "F1 The Movie", "genre": "Hành động", "language": "Tiếng Anh", "duration_min": 155, "rating": "T13", "release_date": date(2025, 6, 27), "country": "Mỹ"},
    {"movie_id": "MV08", "title": "Superman", "genre": "Hành động", "language": "Tiếng Anh", "duration_min": 130, "rating": "T13", "release_date": date(2025, 7, 11), "country": "Mỹ"},
    {"movie_id": "MV09", "title": "Fantastic Four", "genre": "Hành động", "language": "Tiếng Anh", "duration_min": 128, "rating": "T13", "release_date": date(2025, 7, 25), "country": "Mỹ"},
    {"movie_id": "MV10", "title": "Bộ Tứ Báo Thủ", "genre": "Hài", "language": "Tiếng Việt", "duration_min": 112, "rating": "T13", "release_date": date(2025, 8, 8), "country": "Việt Nam"},
    {"movie_id": "MV11", "title": "The Conjuring Last Rites", "genre": "Kinh dị", "language": "Tiếng Anh", "duration_min": 120, "rating": "T18", "release_date": date(2025, 9, 5), "country": "Mỹ"},
    {"movie_id": "MV12", "title": "Trạng Quỳnh", "genre": "Hài", "language": "Tiếng Việt", "duration_min": 108, "rating": "P", "release_date": date(2025, 9, 19), "country": "Việt Nam"},
    {"movie_id": "MV13", "title": "Avatar Fire and Ash", "genre": "Khoa học viễn tưởng", "language": "Tiếng Anh", "duration_min": 190, "rating": "T13", "release_date": date(2025, 12, 19), "country": "Mỹ"},
    {"movie_id": "MV14", "title": "Zootopia 2", "genre": "Gia đình", "language": "Tiếng Anh", "duration_min": 108, "rating": "P", "release_date": date(2025, 11, 26), "country": "Mỹ"},
    {"movie_id": "MV15", "title": "Tết Ở Làng Địa Ngục", "genre": "Kinh dị", "language": "Tiếng Việt", "duration_min": 110, "rating": "T16", "release_date": date(2026, 2, 6), "country": "Việt Nam"},
    {"movie_id": "MV16", "title": "Nhà Bà Nữ 2", "genre": "Tâm lý", "language": "Tiếng Việt", "duration_min": 124, "rating": "T16", "release_date": date(2026, 1, 16), "country": "Việt Nam"},
    {"movie_id": "MV17", "title": "The Batman 2", "genre": "Hành động", "language": "Tiếng Anh", "duration_min": 150, "rating": "T16", "release_date": date(2026, 3, 6), "country": "Mỹ"},
    {"movie_id": "MV18", "title": "Spider-Man Brand New Day", "genre": "Hành động", "language": "Tiếng Anh", "duration_min": 135, "rating": "T13", "release_date": date(2026, 7, 10), "country": "Mỹ"},
    {"movie_id": "MV19", "title": "Dune Messiah", "genre": "Khoa học viễn tưởng", "language": "Tiếng Anh", "duration_min": 165, "rating": "T13", "release_date": date(2026, 5, 15), "country": "Mỹ"},
    {"movie_id": "MV20", "title": "Cám", "genre": "Kinh dị", "language": "Tiếng Việt", "duration_min": 98, "rating": "T18", "release_date": date(2025, 10, 17), "country": "Việt Nam"},
    {"movie_id": "MV21", "title": "Inside Out 3", "genre": "Gia đình", "language": "Tiếng Anh", "duration_min": 102, "rating": "P", "release_date": date(2026, 6, 12), "country": "Mỹ"},
    {"movie_id": "MV22", "title": "Yêu Nhầm Bạn Thân", "genre": "Tình cảm", "language": "Tiếng Việt", "duration_min": 115, "rating": "T13", "release_date": date(2026, 4, 10), "country": "Việt Nam"},
    {"movie_id": "MV23", "title": "Mission Impossible 9", "genre": "Hành động", "language": "Tiếng Anh", "duration_min": 148, "rating": "T13", "release_date": date(2026, 8, 21), "country": "Mỹ"},
    {"movie_id": "MV24", "title": "Đào, Phở và Piano", "genre": "Lịch sử", "language": "Tiếng Việt", "duration_min": 100, "rating": "T13", "release_date": date(2025, 12, 5), "country": "Việt Nam"},
]

PRODUCTS = [
    {"product_id": "P01", "product_name": "Bắp rang nhỏ", "product_category": "snack", "is_combo": False, "unit_price": 45000, "weight": 0.12},
    {"product_id": "P02", "product_name": "Bắp rang vừa", "product_category": "snack", "is_combo": False, "unit_price": 65000, "weight": 0.18},
    {"product_id": "P03", "product_name": "Bắp rang lớn", "product_category": "snack", "is_combo": False, "unit_price": 79000, "weight": 0.12},
    {"product_id": "P04", "product_name": "Nước ngọt", "product_category": "drink", "is_combo": False, "unit_price": 39000, "weight": 0.16},
    {"product_id": "P05", "product_name": "Nước ngọt lớn", "product_category": "drink", "is_combo": False, "unit_price": 49000, "weight": 0.10},
    {"product_id": "P06", "product_name": "Combo 1 (bắp vừa + nước)", "product_category": "combo", "is_combo": True, "unit_price": 99000, "weight": 0.18},
    {"product_id": "P07", "product_name": "Combo 2 (bắp lớn + 2 nước)", "product_category": "combo", "is_combo": True, "unit_price": 145000, "weight": 0.08},
    {"product_id": "P08", "product_name": "Nachos phô mai", "product_category": "snack", "is_combo": False, "unit_price": 69000, "weight": 0.06},
]

LIST_PRICE = {
    ("2D", "weekday"): 75000,
    ("2D", "weekend"): 95000,
    ("2D", "holiday"): 105000,
    ("3D", "weekday"): 95000,
    ("3D", "weekend"): 115000,
    ("3D", "holiday"): 125000,
    ("IMAX", "weekday"): 140000,
    ("IMAX", "weekend"): 165000,
    ("IMAX", "holiday"): 180000,
}

OCC_BASE = {
    ("weekday", "Sáng"): 0.12,
    ("weekday", "Chiều"): 0.32,
    ("weekday", "Tối"): 0.58,
    ("weekday", "Đêm"): 0.40,
    ("friday", "Sáng"): 0.16,
    ("friday", "Chiều"): 0.40,
    ("friday", "Tối"): 0.72,
    ("friday", "Đêm"): 0.55,
    ("weekend", "Sáng"): 0.38,
    ("weekend", "Chiều"): 0.68,
    ("weekend", "Tối"): 0.88,
    ("weekend", "Đêm"): 0.70,
    ("holiday", "Sáng"): 0.50,
    ("holiday", "Chiều"): 0.80,
    ("holiday", "Tối"): 0.94,
    ("holiday", "Đêm"): 0.78,
}

SHOW_TIMES = [time(9, 30), time(11, 15), time(14, 0), time(16, 30), time(19, 0), time(21, 15)]
PARTY_SIZES = np.array([1, 2, 3, 4])
PARTY_PROBS = np.array([0.45, 0.35, 0.15, 0.05])
TIERS = np.array(["Standard", "Silver", "Gold"])
TIER_PROBS = np.array([0.60, 0.30, 0.10])
GENDERS = np.array(["Nam", "Nữ", "Khác"])
AGE_GROUPS = np.array(["13-17", "18-24", "25-34", "35-44", "45-54", "55+"])
AGE_PROBS = np.array([0.08, 0.28, 0.30, 0.18, 0.10, 0.06])


HOLIDAYS: dict[date, str] = {}
PEAK_DAYS: dict[date, str] = {}


def build_holidays() -> None:
    """Nạp ngày lễ và ngày cao điểm từ core.holidays — nguồn duy nhất."""
    HOLIDAYS.clear()
    HOLIDAYS.update(get_holidays_range(START.year, END.year))
    PEAK_DAYS.clear()
    PEAK_DAYS.update(get_peak_days_range(START.year, END.year))


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def time_slot_of(t: time) -> str:
    if t < time(12, 0):
        return "Sáng"
    if t < time(17, 0):
        return "Chiều"
    if t < time(21, 30):
        return "Tối"
    return "Đêm"


def day_bucket(d: date) -> tuple[str, str, bool, str]:
    """Trả (day_type giá, occ_bucket, is_weekend, holiday_name)."""
    is_weekend = d.weekday() >= 5
    if d in HOLIDAYS:
        return "holiday", "holiday", is_weekend, HOLIDAYS[d]
    if d in PEAK_DAYS:
        # Ngày cao điểm: áp giá holiday, đông khách, nhưng không phải lễ chính thức
        return "holiday", "holiday", is_weekend, ""
    if is_weekend:
        return "weekend", "weekend", True, ""
    if d.weekday() == 4:
        return "weekday", "friday", False, ""
    return "weekday", "weekday", False, ""


def movie_age_mult(show_day: date, release: date) -> float:
    age = (show_day - release).days
    if age < 0:
        return 0.0
    if age <= 13:
        return 1.35
    if age <= 41:
        return 1.10
    if age <= 69:
        return 0.85
    return 0.60


def movies_on(show_day: date, room_type: str) -> list[dict]:
    eligible = [m for m in MOVIES if m["release_date"] <= show_day <= m["release_date"] + timedelta(days=90)]
    if not eligible:
        eligible = [m for m in MOVIES if m["release_date"] <= show_day]
    if room_type == "IMAX":
        imax_like = [m for m in eligible if m["genre"] in {"Hành động", "Khoa học viễn tưởng", "Gia đình"}]
        if imax_like:
            return imax_like
    return eligible


def occupancy(occ_bucket: str, slot: str, cinema_occ_mult: float, age_mult: float) -> float:
    base = OCC_BASE[(occ_bucket, slot)]
    noise = float(RNG.uniform(-0.08, 0.08))
    value = (base + noise) * cinema_occ_mult * age_mult
    return float(np.clip(value, 0.05, 0.98))


def showtimes_for(d: date, room: dict, is_holiday: bool, is_weekend: bool) -> list[time]:
    """Ít suất ngày thường, đủ suất cuối tuần/lễ/cao điểm — vẫn thể hiện quy luật hành vi."""
    busy = is_holiday or is_weekend or (d in PEAK_DAYS)
    if room["room_type"] == "IMAX":
        return SHOW_TIMES if busy else [time(14, 0), time(16, 30), time(19, 0), time(21, 15)]
    if busy:
        return [time(9, 30), time(14, 0), time(16, 30), time(19, 0), time(21, 15)]
    if d.weekday() == 4:  # Thứ 6
        return [time(14, 0), time(16, 30), time(19, 0), time(21, 15)]
    return [time(14, 0), time(19, 0), time(21, 15)]


def member_discount(tier: str) -> float:
    return {"Standard": 0.05, "Silver": 0.08, "Gold": 0.12}[tier]


def ticket_money(list_price: int, slot: str, customer_type: str, tier: str | None) -> tuple[float, int, int]:
    rate = 0.20 if slot == "Sáng" else 0.0
    if customer_type == "MEMBER" and tier:
        rate += member_discount(tier)
    rate = min(rate, 0.35)
    discount_amount = int(round(list_price * rate))
    paid = list_price - discount_amount
    return rate, discount_amount, paid


def build_members(n: int = 2500) -> list[dict]:
    members = []
    for i in range(1, n + 1):
        members.append(
            {
                "customer_id": f"CUS{i:06d}",
                "customer_type": "MEMBER",
                "gender": str(RNG.choice(GENDERS, p=[0.48, 0.50, 0.02])),
                "age_group": str(RNG.choice(AGE_GROUPS, p=AGE_PROBS)),
                "membership_tier": str(RNG.choice(TIERS, p=TIER_PROBS)),
            }
        )
    return members


def channel_of(customer_type: str) -> str:
    if customer_type == "WALK_IN":
        return str(RNG.choice(["POS", "APP", "WEB"], p=[0.80, 0.15, 0.05]))
    return str(RNG.choice(["POS", "APP", "WEB"], p=[0.25, 0.50, 0.25]))


def fnb_buy_prob(slot: str, is_weekend: bool, is_holiday: bool) -> float:
    base = {"Sáng": 0.22, "Chiều": 0.38, "Tối": 0.52, "Đêm": 0.52}[slot]
    if is_weekend or is_holiday:
        base += 0.10
    return min(base, 0.70)


def pick_products(n_lines: int, slot: str, party: int) -> list[dict]:
    weights = np.array([p["weight"] for p in PRODUCTS], dtype=float)
    if slot in {"Tối", "Đêm"} or party >= 2:
        weights = weights.copy()
        for i, p in enumerate(PRODUCTS):
            if p["is_combo"]:
                weights[i] *= 1.6
    weights = weights / weights.sum()
    chosen_idx = RNG.choice(len(PRODUCTS), size=n_lines, replace=False, p=weights)
    return [PRODUCTS[i] for i in chosen_idx]


def generate() -> tuple[pd.DataFrame, pd.DataFrame]:
    build_holidays()
    members = build_members()
    cinema_by_id = {c["cinema_id"]: c for c in CINEMAS}

    ticket_rows: list[dict] = []
    txn_meta: list[dict] = []

    txn_seq = 1

    print("Đang sinh suất chiếu và giao dịch vé...")
    for show_day in daterange(START, END):
        day_type, occ_bucket, is_weekend, holiday_name = day_bucket(show_day)
        is_holiday = bool(holiday_name)
        for room in ROOMS:
            cinema = cinema_by_id[room["cinema_id"]]
            for show_t in showtimes_for(show_day, room, is_holiday, is_weekend):
                slot = time_slot_of(show_t)
                catalog = movies_on(show_day, room["room_type"])
                if not catalog:
                    continue
                weights = np.array([movie_age_mult(show_day, m["release_date"]) for m in catalog], dtype=float)
                if weights.sum() <= 0:
                    continue
                weights = weights / weights.sum()
                movie = catalog[int(RNG.choice(len(catalog), p=weights))]
                age_m = movie_age_mult(show_day, movie["release_date"])
                occ = occupancy(occ_bucket, slot, cinema["occ_mult"], age_m)
                n_sold = int(round(occ * room["capacity"] * VOLUME_SCALE))
                n_sold = max(3, min(n_sold, room["capacity"]))

                remaining = n_sold
                list_price = LIST_PRICE[(room["room_type"], day_type)]

                while remaining > 0:
                    party = int(min(remaining, RNG.choice(PARTY_SIZES, p=PARTY_PROBS)))
                    is_walk_in = RNG.random() < WALK_IN_RATE
                    if is_walk_in:
                        cust = {
                            "customer_id": "GUEST",
                            "customer_type": "WALK_IN",
                            "gender": "",
                            "age_group": "",
                            "membership_tier": "",
                        }
                    else:
                        cust = members[int(RNG.integers(0, len(members)))]
                    channel = channel_of(cust["customer_type"])
                    rate, disc, paid = ticket_money(
                        list_price, slot, cust["customer_type"], cust["membership_tier"] or None
                    )
                    txn_id = f"TXN{txn_seq:08d}"
                    txn_seq += 1
                    txn_meta.append(
                        {
                            "txn_id": txn_id,
                            "show_date": show_day,
                            "show_time": show_t,
                            "slot": slot,
                            "cinema_id": cinema["cinema_id"],
                            "customer_id": cust["customer_id"],
                            "customer_type": cust["customer_type"],
                            "channel_code": channel,
                            "party": party,
                            "is_weekend": is_weekend,
                            "is_holiday": is_holiday,
                        }
                    )
                    ticket_rows.append(
                        {
                            "txn_id": txn_id,
                            "show_date": show_day.isoformat(),
                            "show_time": show_t.strftime("%H:%M:%S"),
                            "cinema_id": cinema["cinema_id"],
                            "cinema_name": cinema["cinema_name"],
                            "district": cinema["district"],
                            "city": cinema["city"],
                            "room_id": room["room_id"],
                            "room_name": room["room_name"],
                            "room_type": room["room_type"],
                            "capacity": room["capacity"],
                            "movie_id": movie["movie_id"],
                            "movie_title": movie["title"],
                            "genre": movie["genre"],
                            "duration_min": movie["duration_min"],
                            "rating": movie["rating"],
                            "release_date": movie["release_date"].isoformat(),
                            "language": movie["language"],
                            "country": movie["country"],
                            "customer_id": cust["customer_id"],
                            "customer_type": cust["customer_type"],
                            "gender": cust["gender"],
                            "age_group": cust["age_group"],
                            "membership_tier": cust["membership_tier"],
                            "channel_code": channel,
                            "ticket_format": room["room_type"],
                            "day_type": day_type,
                            "time_slot": slot,
                            "list_price": list_price,
                            "discount_rate": round(rate, 4),
                            "discount_amount": disc * party,
                            "paid_amount": paid * party,
                            "qty": party,
                            "is_holiday": int(is_holiday),
                            "holiday_name": holiday_name,
                            "is_anomaly": 0,
                            "anomaly_type": "",
                        }
                    )
                    remaining -= party

    tickets = pd.DataFrame(ticket_rows)
    print(f"  Giao dịch vé bình thường: {len(tickets):,} dòng | Tổng vé: {tickets['qty'].sum():,}")

    tickets = inject_ticket_anomalies(tickets)
    fnb = generate_fnb(txn_meta)
    fnb = inject_fnb_anomalies(fnb)
    return tickets, fnb


def inject_ticket_anomalies(tickets: pd.DataFrame) -> pd.DataFrame:
    """1–2% dòng giao dịch vé: giá 0đ, doanh thu lệch, hoặc vượt capacity."""
    print("Đang gieo nhãn bất thường trên vé...")
    txn_ids = tickets["txn_id"].unique()
    n_anom_txn = max(1, int(round(len(txn_ids) * ANOMALY_TXN_RATE)))
    chosen = RNG.choice(txn_ids, size=n_anom_txn, replace=False)
    kinds = RNG.choice(
        np.array(["price_zero", "revenue_mismatch", "qty_spike"]),
        size=n_anom_txn,
        p=[0.45, 0.45, 0.10],
    )
    kind_of = pd.Series(kinds, index=chosen)
    mapped = tickets["txn_id"].map(kind_of)

    zero_mask = mapped.eq("price_zero")
    mismatch_mask = mapped.eq("revenue_mismatch")

    tickets.loc[zero_mask, "paid_amount"] = 0
    tickets.loc[zero_mask, "is_anomaly"] = 1
    tickets.loc[zero_mask, "anomaly_type"] = "price_zero"

    mismatch_idx = tickets.index[mismatch_mask].to_numpy()
    if len(mismatch_idx):
        half = RNG.random(len(mismatch_idx)) < 0.5
        # Nửa đầu: thu theo giá niêm yết nguyên giá (bỏ qua chiết khấu)
        tickets.loc[mismatch_idx[half], "paid_amount"] = (
            tickets.loc[mismatch_idx[half], "list_price"].to_numpy() * tickets.loc[mismatch_idx[half], "qty"].to_numpy()
        )
        # Nửa sau: thu gấp đôi
        tickets.loc[mismatch_idx[~half], "paid_amount"] = (
            tickets.loc[mismatch_idx[~half], "paid_amount"].to_numpy() * 2
        )
        tickets.loc[mismatch_mask, "is_anomaly"] = 1
        tickets.loc[mismatch_mask, "anomaly_type"] = "revenue_mismatch"

    spike_ids = kind_of.index[kind_of.eq("qty_spike")].tolist()
    extra_rows: list[dict] = []
    if spike_ids:
        templates = tickets[tickets["txn_id"].isin(spike_ids)].drop_duplicates(
            ["txn_id"], keep="first"
        )
        show_totals = tickets.groupby(["room_id", "show_date", "show_time"])["qty"].sum()
        seq = 1
        for _, rec in templates.iterrows():
            already = int(show_totals.loc[(rec["room_id"], rec["show_date"], rec["show_time"])])
            cap = int(rec["capacity"])
            # Số vé overflow để tổng số vé trong suất vượt sức chứa phòng
            overflow = max(5, cap - already + int(round(cap * 0.08)))
            extra = rec.to_dict()
            extra["txn_id"] = f"TXN9{seq:07d}"
            extra["qty"] = overflow
            unit_paid = int(rec["paid_amount"]) // int(rec["qty"])
            unit_disc = int(rec["discount_amount"]) // int(rec["qty"])
            extra["paid_amount"] = unit_paid * overflow
            extra["discount_amount"] = unit_disc * overflow
            extra["is_anomaly"] = 1
            extra["anomaly_type"] = "qty_spike"
            extra_rows.append(extra)
            seq += 1

    if extra_rows:
        tickets = pd.concat([tickets, pd.DataFrame(extra_rows)], ignore_index=True)
    return tickets


def generate_fnb(txn_meta: list[dict]) -> pd.DataFrame:
    print("Đang sinh bắp nước...")
    rows: list[dict] = []
    line_seq = 1
    for meta in txn_meta:
        if RNG.random() > fnb_buy_prob(meta["slot"], meta["is_weekend"], meta["is_holiday"]):
            continue
        n_lines = int(RNG.choice([1, 2, 3], p=[0.55, 0.35, 0.10]))
        products = pick_products(n_lines, meta["slot"], meta["party"])
        for prod in products:
            qty = int(RNG.choice([1, 2], p=[0.78, 0.22]))
            unit = int(prod["unit_price"])
            rows.append(
                {
                    "txn_id": meta["txn_id"],
                    "fnb_line_id": f"FNB{line_seq:08d}",
                    "sale_date": meta["show_date"].isoformat(),
                    "sale_time": meta["show_time"].strftime("%H:%M:%S"),
                    "cinema_id": meta["cinema_id"],
                    "customer_id": meta["customer_id"],
                    "customer_type": meta["customer_type"],
                    "channel_code": meta["channel_code"],
                    "product_id": prod["product_id"],
                    "product_name": prod["product_name"],
                    "product_category": prod["product_category"],
                    "is_combo": int(prod["is_combo"]),
                    "qty": qty,
                    "unit_price": unit,
                    "line_amount": qty * unit,
                    "is_anomaly": 0,
                    "anomaly_type": "",
                }
            )
            line_seq += 1
    return pd.DataFrame(rows)


def inject_fnb_anomalies(fnb: pd.DataFrame) -> pd.DataFrame:
    if fnb.empty:
        return fnb
    print("Đang gieo nhãn bất thường trên F&B...")
    n = max(1, int(round(len(fnb) * ANOMALY_FNB_RATE)))
    idx = RNG.choice(fnb.index.to_numpy(), size=min(n, len(fnb)), replace=False)
    kinds = RNG.choice(np.array(["price_zero", "revenue_mismatch"]), size=len(idx), p=[0.5, 0.5])
    for i, kind in zip(idx, kinds):
        if kind == "price_zero":
            fnb.at[i, "unit_price"] = 0
            fnb.at[i, "line_amount"] = 0
            fnb.at[i, "is_anomaly"] = 1
            fnb.at[i, "anomaly_type"] = "price_zero"
        else:
            fnb.at[i, "line_amount"] = int(fnb.at[i, "line_amount"]) + 15000
            fnb.at[i, "is_anomaly"] = 1
            fnb.at[i, "anomaly_type"] = "revenue_mismatch"
    return fnb


def validate(tickets: pd.DataFrame, fnb: pd.DataFrame) -> None:
    print("\n=== Kiểm tra giả định ===")
    n_t = len(tickets)
    n_f = len(fnb)
    walk_in_txn = tickets.drop_duplicates("txn_id")
    walk_rate = (walk_in_txn["customer_type"] == "WALK_IN").mean()
    anom_t = (tickets["is_anomaly"] == 1).mean()
    anom_f = (fnb["is_anomaly"] == 1).mean() if n_f else 0
    orphan = ~fnb["txn_id"].isin(tickets["txn_id"]) if n_f else pd.Series(dtype=bool)

    # Kiểm tra vượt sức chứa ngoài các suất được chủ đích gieo qty_spike
    spike_mask = tickets["anomaly_type"].eq("qty_spike")
    non_spike = tickets[~spike_mask]
    sold_vs_cap = (
        non_spike.groupby(["room_id", "show_date", "show_time", "capacity"], as_index=False)["qty"]
        .sum()
        .rename(columns={"qty": "legal_sold"})
    )
    over = sold_vs_cap[sold_vs_cap["legal_sold"] > sold_vs_cap["capacity"]]

    print(f"Dòng giao dịch vé: {n_t:,} | Tổng vé: {tickets['qty'].sum():,} | F&B: {n_f:,}")
    print(f"Tỷ lệ GUEST/WALK_IN theo giao dịch: {walk_rate:.2%}")
    print(f"Anomaly vé (theo dòng giao dịch): {anom_t:.2%} | Anomaly F&B: {anom_f:.2%}")
    print(f"F&B mồ côi (txn không có vé): {int(orphan.sum())}")
    print(f"Suất hợp lệ vượt capacity: {len(over)}")
    print(f"Khoảng ngày: {tickets['show_date'].min()} → {tickets['show_date'].max()}")
    print(f"Số rạp: {tickets['cinema_id'].nunique()} | Số phim: {tickets['movie_id'].nunique()}")

    assert tickets["cinema_id"].nunique() == 3
    assert 0.008 <= anom_t <= 0.025
    assert n_f == 0 or 0.008 <= anom_f <= 0.025
    assert orphan.sum() == 0
    assert over.empty
    assert 0.62 <= walk_rate <= 0.78
    print("Tất cả kiểm tra đã đạt.\n")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tickets, fnb = generate()
    validate(tickets, fnb)

    ticket_path = DATA_DIR / "staging_ticket.csv"
    fnb_path = DATA_DIR / "staging_fnb.csv"
    tickets.to_csv(ticket_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    fnb.to_csv(fnb_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    print(f"Đã ghi {ticket_path} ({ticket_path.stat().st_size / 1e6:.1f} MB)")
    print(f"Đã ghi {fnb_path} ({fnb_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())

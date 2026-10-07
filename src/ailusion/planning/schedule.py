"""Graph B — xếp 3 phương án, tính chi phí, kiểm tra ràng buộc, mô phỏng rủi ro. Code tất định.

Ba phương án dùng chung dữ liệu (Pool) và chung chiến lược của nhóm người dùng (persona.py),
rồi lệch nhau một nấc quanh chiến lược đó:
- Cân bằng: đúng chiến lược.
- Tiết kiệm: chỗ ở rẻ hơn, ăn rẻ hơn, ngại vé đắt hơn.
- Trải nghiệm: chỗ ở tốt hơn, ăn ngon hơn, thêm một điểm mỗi ngày.

`tighten` (0-2) là mức siết khi bước kiểm tra thấy vượt ngân sách cứng: hạ hạng phòng, bớt
điểm có vé, ăn rẻ hơn. Bước kiểm tra tăng mức siết rồi xếp lại (vòng 07 -> 06 trong sơ đồ).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .candidates import Hotel, Place, Pool, interest_match, quality
from .persona import default_strategy
from .request import MOBILITY_STOPS, PlanRequest, busy_minutes, to_minutes

WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
DEFAULT_HOURS = (7 * 60 + 30, 17 * 60 + 30)  # chưa có giờ mở cửa thì giả định 07:30–17:30
ROAD_FACTOR = 1.35  # đường thật dài hơn đường chim bay
HOTEL_RADIUS_KM = 15

# Độ lệch của từng phương án so với chiến lược của nhóm.
PROFILES: dict[str, dict[str, Any]] = {
    "canbang": {
        "name": "Cân bằng",
        "desc": "Đúng chiến lược của nhóm: hài hoà chi phí, trải nghiệm và nhịp độ",
        "tier_delta": 0.0,
        "meal_mult": 1.0,
        "ticket_delta": 0.0,
        "slots": 0,
        "lunch_min": 60,
    },
    "tietkiem": {
        "name": "Tiết kiệm",
        "desc": "Chỗ ở và bữa ăn rẻ hơn, ưu tiên điểm miễn phí",
        "tier_delta": -0.35,
        "meal_mult": 0.6,
        "ticket_delta": 0.35,
        "slots": 0,
        "lunch_min": 45,
    },
    "trainghiem": {
        "name": "Trải nghiệm",
        "desc": "Chỗ ở và bữa ăn tốt hơn, thêm hoạt động đặc sắc",
        "tier_delta": 0.3,
        "meal_mult": 1.6,
        "ticket_delta": -0.3,
        "slots": 1,
        "lunch_min": 75,
    },
}
SLOTS = MOBILITY_STOPS  # trần số điểm/ngày theo nhịp độ người dùng chọn trong form
MODES = {  # km/h trong thành phố, cách tính tiền
    "xemay": {"kmh": 25, "per_km": 2_000, "per_day": 130_000, "per_vehicle": 2},
    "taxi": {"kmh": 22, "per_km": 14_000, "per_day": 0, "per_vehicle": 4},
    "xerieng": {"kmh": 22, "per_km": 0, "per_day": 1_300_000, "per_vehicle": 6},
    "congcong": {"kmh": 15, "per_km": 7_000, "per_day": 0, "per_vehicle": 1},
}
# Bộ trọng số chọn phương án (khớp prototype) + chi phí và rủi ro.
WEIGHT_SETS: dict[str, dict[str, float]] = {
    "can_bang": {
        "interest": 28,
        "experience": 16,
        "budget": 20,
        "pace": 16,
        "weather": 8,
        "travel": 12,
        "cost_vnd": 10,
        "p_break": 10,
    },
    "tiet_kiem": {
        "interest": 22,
        "experience": 10,
        "budget": 38,
        "pace": 12,
        "weather": 6,
        "travel": 12,
        "cost_vnd": 25,
        "p_break": 8,
    },
    "trai_nghiem": {
        "interest": 30,
        "experience": 30,
        "budget": 10,
        "pace": 12,
        "weather": 8,
        "travel": 10,
        "cost_vnd": 5,
        "p_break": 8,
    },
}


@dataclass
class Slot:
    type: str
    start: int
    end: int
    name: str
    place: Place | None = None
    km: float = 0.0
    travel: int = 0
    price: int = 0
    note: str | None = None
    why: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DayPlan:
    index: int
    window: tuple[int, int]
    slots: list[Slot]
    rain: dict[str, int]
    back_km: float = 0.0
    back_min: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class OptionPlan:
    key: str
    hotel: Hotel | None
    days: list[DayPlan]
    cost: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    hard_ok: bool = True
    risk: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class Style:
    """Tham số xếp lịch của một phương án = chiến lược của nhóm + độ lệch của phương án."""

    slots: int
    buffer: int
    tier: float
    food_vnd: int
    ticket_w: float
    quality_w: float
    max_travel: int
    rest: int
    avoid_crowd: bool
    focus: tuple[str, ...]
    lunch_min: int


def style_for(
    key: str, req: PlanRequest, strategy: dict[str, Any] | None = None, tighten: int = 0
) -> Style:
    s = strategy or default_strategy(req.persona).model_dump()
    prof = PROFILES[key]
    # Nhịp độ trong form là ràng buộc của người dùng: chiến lược không được vượt trần đó.
    cap = SLOTS[req.mobility] + prof["slots"]
    return Style(
        slots=max(1, min(s["stops_per_day"] + prof["slots"], cap) - (1 if tighten >= 2 else 0)),
        buffer=s["buffer_min"],
        tier=s["hotel_tier"] + prof["tier_delta"] - 0.4 * tighten,
        food_vnd=int(s["meal_vnd"] * prof["meal_mult"] * (1 - 0.25 * tighten)),
        ticket_w=(s["ticket_sensitivity"] - 0.3) + prof["ticket_delta"] + 0.35 * tighten,
        quality_w=s["quality_over_quantity"],
        max_travel=s["max_travel_min"],
        rest=s["rest_break_min"],
        avoid_crowd=bool(s["avoid_crowd"] or req.interests.avoid_crowd),
        focus=tuple(s.get("focus_tags") or ()),
        lunch_min=prof["lunch_min"],
    )


# ---------------------------------------------------------------- tiện ích
def hhmm(minutes: int) -> str:
    minutes = max(0, min(int(round(minutes)), 24 * 60 - 1))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lat1, lng1), (lat2, lng2) = a, b
    p = math.pi / 180
    h = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h)) * ROAD_FACTOR


def travel_min(km: float, mode: str) -> int:
    return max(3, round(km / MODES[mode]["kmh"] * 60) + 4)  # +4 phút gọi xe, gửi xe


def rain_blocks(day: dict[str, Any] | None) -> dict[str, int]:
    """Khả năng mưa (%) theo buổi từ dự báo theo giờ: sáng 6-9h, chiều 12-15h, tối 18-21h."""
    if not day:
        return {"sang": 30, "chieu": 30, "toi": 30}
    hours = {h["time"]: h.get("chance_of_rain_pct", 0) for h in day.get("hours", [])}
    overall = day.get("chance_of_rain_pct", 30)

    def avg(*keys: str) -> int:
        vals = [hours[k] for k in keys if k in hours]
        return round(sum(vals) / len(vals)) if vals else overall

    return {
        "sang": avg("06:00", "09:00"),
        "chieu": avg("12:00", "15:00"),
        "toi": avg("18:00", "21:00"),
    }


def block_of(minute: int) -> str:
    return "sang" if minute < 12 * 60 else "chieu" if minute < 18 * 60 else "toi"


def hours_of(p: Place, day: int) -> tuple[int, int] | None:
    """Giờ mở cửa ngày `day`; None = đóng cửa; chưa có dữ liệu = giờ mặc định."""
    return p.hours[day] if day in p.hours else DEFAULT_HOURS


def payers(req: PlanRequest, child_share: float) -> float:
    b = req.basic
    return b.adults + b.seniors + b.children * child_share


# ---------------------------------------------------------------- xếp lịch
def build_option(
    key: str,
    pool: Pool,
    req: PlanRequest,
    tighten: int = 0,
    strategy: dict[str, Any] | None = None,
) -> OptionPlan:
    style = style_for(key, req, strategy, tighten)
    center = pool.center or _centroid(pool)
    hotel = pick_hotel(pool.hotels, style.tier, req, center)
    base = (hotel.lat, hotel.lng) if hotel else center
    used: set[str] = set()
    groups: dict[str, int] = {}  # nhóm địa điểm đã xếp trong cả chuyến, để tránh lặp
    days = [_plan_day(d, pool, req, base, used, groups, style) for d in range(req.basic.days)]
    plan = OptionPlan(key=key, hotel=hotel, days=days)
    if tighten:
        plan.repairs.append(
            f"Siết mức {tighten}: hạ hạng phòng, bớt điểm có vé, bữa ăn rẻ hơn để giữ ngân sách"
        )
    return plan


def pick_hotel(
    hotels: list[Hotel], tier: float, req: PlanRequest, center: tuple[float, float]
) -> Hotel | None:
    """tier 0 = rẻ nhất đạt chuẩn, 1 = tốt nhất; danh sách đã sắp theo giá.

    search_hotels đôi khi trả lẫn chỗ ở tỉnh khác (tên trùng) -> chỉ xét chỗ ở cách trung tâm
    điểm đến không quá HOTEL_RADIUS_KM.
    """
    near = [h for h in hotels if km_between(center, (h.lat, h.lng)) <= HOTEL_RADIUS_KM]
    good = [h for h in near if h.rating >= 3.8] or near
    if not good:
        return None
    if req.access.wheelchair or req.basic.seniors:
        good = sorted(good, key=lambda h: (-h.rating, h.price_night))[: max(3, len(good) // 2)]
        good.sort(key=lambda h: h.price_night)
    tier = max(0.0, min(1.0, tier))
    return good[round(tier * (len(good) - 1))]


def _centroid(pool: Pool) -> tuple[float, float]:
    pts = [(p.lat, p.lng) for p in pool.places.values()] or [(16.0, 108.0)]
    return (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts))


def _window(d: int, req: PlanRequest) -> tuple[int, int]:
    t = req.time
    start, end = to_minutes(t.day_start), to_minutes(t.day_end)
    if d == 0:
        start = max(start, to_minutes(t.first_start))
    if d == req.basic.days - 1:
        end = min(end, to_minutes(t.last_end))
    return start, max(start + 120, end)


def poi_score(p: Place, req: PlanRequest, style: Style, rain: dict[str, int]) -> float:
    q = style.quality_w  # nhóm càng chuộng chất lượng thì rating càng nặng ký
    interest = interest_match(p, req) + (0.15 if set(p.tags) & set(style.focus) else 0)
    s = (0.55 - 0.25 * q) * interest + (0.45 + 0.25 * q) * quality(p)
    s -= style.ticket_w * min(1.0, p.ticket_vnd / 300_000)
    if style.avoid_crowd and p.crowded:
        s -= 0.25
    if not p.indoor:
        wet = max(rain["sang"], rain["chieu"]) / 100
        s -= wet * {"indoor": 0.45, "flex": 0.15, "ok": 0.0}[req.weather.rain]
    return s


def _plan_day(
    d: int,
    pool: Pool,
    req: PlanRequest,
    base: tuple[float, float],
    used: set[str],
    groups: dict[str, int],
    style: Style,
) -> DayPlan:
    mode, food_vnd = req.transport, style.food_vnd
    weather = pool.weather[d] if d < len(pool.weather) else None
    rain = rain_blocks(weather)
    start, end = _window(d, req)
    fixed = sorted(
        (to_minutes(f.start), to_minutes(f.end), f.label) for f in req.time.fixed if f.day == d + 1
    )
    hours_long = (end - start - busy_minutes(start, end, [(s, e) for s, e, _ in fixed])) / 60
    n_slots = max(1, round(style.slots * min(1.0, hours_long / 10)))

    cands = [
        p
        for p in pool.places.values()
        if p.kind in ("poi", "cafe") and p.place_id not in used and hours_of(p, d) is not None
    ]
    if req.weather.rain_hard:
        cands = [p for p in cands if p.indoor or max(rain["sang"], rain["chieu"]) < 60]
    chosen: list[Place] = []

    def value(p: Place, near: tuple[float, float] | None) -> float:
        """Điểm khi chọn: hợp ý + chất lượng, trừ lặp nhóm và trừ xa."""
        v = poi_score(p, req, style, rain) - 0.3 * groups.get(p.group, 0)
        if any(c.group == p.group for c in chosen):
            v -= 0.5
        if near:
            v -= km_between(near, (p.lat, p.lng)) / 40
        return v

    def pick(near: tuple[float, float] | None) -> Place | None:
        options = [
            p
            for p in cands
            if p.place_id not in used
            and p not in chosen
            and not (p.kind == "cafe" and any(c.kind == "cafe" for c in chosen))
            and not (near and travel_min(km_between(near, (p.lat, p.lng)), mode) > style.max_travel)
        ]
        return max(options, key=lambda p: value(p, near), default=None)

    while len(chosen) < n_slots:
        p = pick((chosen[0].lat, chosen[0].lng) if chosen else None)
        if p is None:
            break
        chosen.append(p)
        groups[p.group] = groups.get(p.group, 0) + 1

    day = DayPlan(index=d, window=(start, end), slots=[], rain=rain)
    pos, t = base, start
    remaining = _nearest_order(base, chosen)
    lunch_done = dinner_done = siesta_done = False
    # Nghỉ giữa ngày theo chiến lược; đã có nghỉ trưa 90 phút của form thì chỉ nghỉ phần dư.
    rest_extra = max(0, style.rest - 90) if req.access.siesta else style.rest
    want_dinner = end >= 19 * 60 + 30

    def go(to: tuple[float, float]) -> tuple[float, int]:
        km = km_between(pos, to)
        return km, travel_min(km, mode) + style.buffer

    def add_meal(meal: str) -> None:
        nonlocal pos, t
        rest = _pick_food(pool, req, pos, food_vnd, used)
        dur = style.lunch_min if meal == "trua" else style.lunch_min + 15
        label = "Ăn trưa" if meal == "trua" else "Ăn tối"
        if rest:
            km, mins = go((rest.lat, rest.lng))
            t += mins
            per = food_vnd * payers(req, 0.6)
            day.slots.append(
                Slot(
                    "meal",
                    t,
                    t + dur,
                    f"{label}: {rest.name}",
                    rest,
                    km,
                    mins,
                    int(per),
                    why=_why(rest, req, km, rain),
                )
            )
            used.add(rest.place_id)
            pos = (rest.lat, rest.lng)
        else:
            day.slots.append(
                Slot(
                    "meal",
                    t,
                    t + dur,
                    f"{label} gần đó",
                    None,
                    price=int(food_vnd * payers(req, 0.6)),
                    note="Chưa tìm được quán phù hợp trong dữ liệu",
                )
            )
        t += dur

    def block_fixed() -> None:
        nonlocal t
        while fixed and t + 30 >= fixed[0][0]:
            fs, fe, label = fixed.pop(0)
            if t > fs:
                day.warnings.append(f"Đến muộn {t - fs} phút cho {label}")
            day.slots.append(Slot("fixed", fs, fe, label, note="Lịch cố định, không dời"))
            t = max(t, fe)

    while True:  # mỗi nhánh dưới đây đều tiến thời gian hoặc bớt việc, nhánh cuối là break
        block_fixed()
        if not lunch_done and t >= 11 * 60 + 30:
            add_meal("trua")
            lunch_done = True
            if req.access.siesta and not siesta_done:
                day.slots.append(Slot("siesta", t, t + 90, "Nghỉ trưa"))
                t, siesta_done = t + 90, True
            if rest_extra >= 15:
                day.slots.append(
                    Slot(
                        "free",
                        t,
                        t + rest_extra,
                        "Nghỉ ngơi giữa ngày",
                        note="Theo chiến lược của nhóm người dùng",
                    )
                )
                t += rest_extra
            continue
        if want_dinner and not dinner_done and t >= 18 * 60:
            add_meal("toi")
            dinner_done = True
            continue
        if not remaining:
            # Còn giờ ban ngày thì lấp thêm một điểm gần đó thay vì để trống cả buổi chiều.
            if t + 90 < min(end, 17 * 60 + 30) and len(chosen) < n_slots + (style.slots >= 4):
                extra = pick(pos)
                if extra is not None:
                    chosen.append(extra)
                    groups[extra.group] = groups.get(extra.group, 0) + 1
                    remaining = [extra]
                    continue
            if not lunch_done and t < 14 * 60:
                t = max(t, 11 * 60 + 30)
                continue
            if want_dinner and not dinner_done and t < 20 * 60:
                day.slots.append(Slot("free", t, max(t, 18 * 60), "Tự do, nghỉ ngơi"))
                t = max(t, 18 * 60)
                continue
            break
        p = remaining.pop(0)
        km, mins = go((p.lat, p.lng))
        open_, close = hours_of(p, d) or DEFAULT_HOURS
        arrive = max(t + mins, open_)
        visit = p.visit_min
        back = travel_min(km_between((p.lat, p.lng), base), mode) + style.buffer
        if (
            arrive + visit > close
            or arrive + visit + back > end
            or (fixed and arrive + visit > fixed[0][0])
        ):
            day.warnings.append(f"Bỏ {p.name}: không vừa khung giờ")
            continue
        if req.weather.rain_hard and not p.indoor and rain[block_of(arrive)] >= 60:
            day.warnings.append(f"Bỏ {p.name}: dự báo mưa {rain[block_of(arrive)]}% lúc ghé")
            continue
        price = int(p.ticket_vnd * payers(req, 0.5))
        day.slots.append(
            Slot(
                "poi",
                arrive,
                arrive + visit,
                p.name,
                p,
                km,
                mins,
                price,
                why=_why(p, req, km, rain),
            )
        )
        used.add(p.place_id)
        pos, t = (p.lat, p.lng), arrive + visit
        remaining = _nearest_order(pos, remaining)
    block_fixed()
    day.back_km = km_between(pos, base)
    day.back_min = travel_min(day.back_km, mode) + style.buffer
    if t + day.back_min > end:
        day.warnings.append(f"Về chỗ ở lúc {hhmm(t + day.back_min)}, trễ hơn giờ hẹn {hhmm(end)}")
    return day


def _nearest_order(start: tuple[float, float], places: list[Place]) -> list[Place]:
    rest, out, pos = list(places), [], start
    while rest:
        nxt = min(rest, key=lambda p: km_between(pos, (p.lat, p.lng)))
        rest.remove(nxt)
        out.append(nxt)
        pos = (nxt.lat, nxt.lng)
    return out


def _pick_food(
    pool: Pool, req: PlanRequest, pos: tuple[float, float], food_vnd: int, used: set[str]
) -> Place | None:
    foods = [p for p in pool.places.values() if p.kind == "food" and p.place_id not in used]
    eaten = {
        _chain(pool.places[i].name)
        for i in used
        if i in pool.places and pool.places[i].kind == "food"
    }
    if req.access.veg:
        veg = [p for p in foods if p.veg or "chay" in p.text]
        foods = veg or foods
    if not foods:
        return None
    # Quán gần, ngon; ngân sách thấp thì ưu tiên quán nhiều review (thường bình dân, ổn định).
    return min(
        foods,
        key=lambda p: (
            km_between(pos, (p.lat, p.lng)) / 3
            - quality(p)
            - (0.2 if food_vnd < 100_000 and p.reviews > 1000 else 0)
            + (3.0 if _chain(p.name) in eaten else 0)
        ),
    )


def _chain(name: str) -> str:
    """Ba chữ đầu của tên quán — đủ để nhận ra cùng một chuỗi (VD "Lẩu Gà Lá É ...")."""
    return " ".join(name.lower().split()[:3])


def _why(p: Place, req: PlanRequest, km: float, rain: dict[str, int]) -> list[dict[str, Any]]:
    wet = max(rain["sang"], rain["chieu"])
    return [
        {
            "label": "Hợp sở thích",
            "value": round(interest_match(p, req), 2),
            "note": ", ".join(p.tags) or "chung",
        },
        {
            "label": "Chất lượng",
            "value": round(quality(p), 2),
            "note": f"{p.rating}★ · {p.reviews:,} đánh giá".replace(",", "."),
        },
        {
            "label": "Giá",
            "value": round(1 - min(1.0, p.ticket_vnd / 300_000), 2),
            "note": "miễn phí"
            if not p.ticket_vnd
            else f"~{p.ticket_vnd:,} đ/người".replace(",", "."),
        },
        {"label": "Quãng đường", "value": round(max(0.0, 1 - km / 20), 2), "note": f"{km:.1f} km"},
        {
            "label": "Thời tiết",
            "value": round(1.0 if p.indoor else 1 - wet / 100, 2),
            "note": "trong nhà" if p.indoor else f"ngoài trời, mưa {wet}%",
        },
    ]


# ---------------------------------------------------------------- chi phí + kiểm tra
def cost_of(plan: OptionPlan, pool: Pool, req: PlanRequest) -> dict[str, int]:
    from .planner import nights, rooms_needed

    b, mode = req.basic, MODES[req.transport]
    stay = 0
    if plan.hotel and req.budget.need_stay:
        stay = plan.hotel.price_night * nights(req) * rooms_needed(req)
    food = sum(s.price for d in plan.days for s in d.slots if s.type == "meal")
    tickets = sum(s.price for d in plan.days for s in d.slots if s.type == "poi")
    km = sum(s.km for d in plan.days for s in d.slots) + sum(d.back_km for d in plan.days)
    vehicles = math.ceil(b.people / mode["per_vehicle"])
    if req.transport == "congcong":
        move = int(km * mode["per_km"] * b.people)
    else:
        move = int(km * mode["per_km"] * vehicles + mode["per_day"] * vehicles * b.days)
    intercity = 0
    if req.budget.intercity:
        fare = pool.intercity.get("flight_round_trip_per_person")
        if fare:
            intercity = int(fare * payers(req, 0.75))
        else:
            road_km = pool.intercity.get("road", {}).get("km", 300)
            intercity = int(road_km * 1_000 * 2 * payers(req, 0.75))  # xe khách ~1.000 đ/km/người
    sub = stay + food + tickets + move + intercity
    reserve = int(sub * 0.05)
    return {
        "stay": stay,
        "food": food,
        "tickets": tickets,
        "move": move,
        "intercity": intercity,
        "reserve": reserve,
        "total": sub + reserve,
    }


def validate(plan: OptionPlan, req: PlanRequest) -> None:
    """Soát ràng buộc cứng; ghi `issues` và `hard_ok`."""
    c, bud, issues = plan.cost, req.budget, []
    if bud.hard and c["total"] > bud.total_vnd:
        issues.append(f"Vượt ngân sách cứng {c['total'] - bud.total_vnd:,} đ".replace(",", "."))
    for k, cap in (
        ("stay", bud.caps.stay),
        ("food", bud.caps.food),
        ("tickets", bud.caps.tickets),
        ("move", bud.caps.move),
    ):
        if cap is not None and c[k] > cap:
            issues.append(f"Vượt trần {k}: {c[k]:,} > {cap:,} đ".replace(",", "."))
    for d in plan.days:
        for w in d.warnings:
            if w.startswith(("Đến muộn", "Về chỗ ở")):
                issues.append(f"Ngày {d.index + 1}: {w}")
        if not any(s.type == "poi" for s in d.slots):
            issues.append(f"Ngày {d.index + 1}: không xếp được điểm tham quan nào")
    if req.access.veg and not any(
        s.place and (s.place.veg or "chay" in s.place.text)
        for d in plan.days
        for s in d.slots
        if s.type == "meal"
    ):
        issues.append("Chưa tìm được quán chay trong dữ liệu")
    plan.issues = issues
    plan.hard_ok = not issues


def over_budget(plan: OptionPlan, req: PlanRequest) -> bool:
    return any(i.startswith(("Vượt ngân sách", "Vượt trần")) for i in plan.issues)


# ---------------------------------------------------------------- rủi ro
def simulate(plan: OptionPlan, req: PlanRequest, seed: int, n: int = 1000) -> dict[str, Any]:
    """1000 kịch bản: đường kẹt (lognormal), mưa theo buổi, tham quan lâu hơn dự kiến."""
    rng = random.Random(seed)
    breaks = wets = 0
    lates: list[float] = []
    for _ in range(n):
        broke = wet = False
        late = 0.0
        for d in plan.days:
            raining = {b: rng.random() < pct / 100 for b, pct in d.rain.items()}
            t = d.window[0]
            for s in d.slots:
                tr = s.travel * math.exp(0.25 * rng.gauss(0, 1)) if s.travel else 0
                if tr and raining[block_of(int(t))]:
                    tr *= 1.2
                arrive = t + tr
                if s.type == "fixed":
                    broke |= arrive > s.start + 5
                    t = s.end
                    continue
                begin = max(arrive, s.start)
                dur = s.end - s.start
                if s.type == "poi" and s.place:
                    dur *= min(1.5, max(0.7, 1 + 0.15 * rng.gauss(0, 1)))
                    close = (hours_of(s.place, d.index) or DEFAULT_HOURS)[1]
                    broke |= begin + dur > close + 2
                    wet |= (not s.place.indoor) and raining[block_of(int(begin + dur / 2))]
                t = begin + dur
            end = t + d.back_min * math.exp(0.25 * rng.gauss(0, 1))
            late += max(0.0, end - d.window[1])
            broke |= end > d.window[1] + 20
        breaks += broke
        wets += wet
        lates.append(late / max(1, len(plan.days)))
    lates.sort()
    return {
        "simulations": n,
        "seed": seed,
        "p_break": round(breaks / n, 3),
        "p_wet": round(wets / n, 3),
        "late_p50_min": round(lates[n // 2]),
        "late_p90_min": round(lates[int(n * 0.9)]),
    }


# ---------------------------------------------------------------- điểm thành phần
def score_parts(plan: OptionPlan, req: PlanRequest, target: int | None = None) -> dict[str, float]:
    """`target`: số điểm/ngày mong muốn theo chiến lược (mặc định theo nhịp độ trong form)."""
    pois = [s for d in plan.days for s in d.slots if s.type == "poi" and s.place]
    if not pois:
        return {k: 0.0 for k in ("interest", "experience", "budget", "pace", "weather", "travel")}
    target = target or SLOTS[req.mobility]
    per_day = len(pois) / len(plan.days)
    outdoor = [s for s in pois if not s.place.indoor]  # type: ignore[union-attr]
    dry = [s for s in outdoor if _rain_at(plan, s) < 50]
    travel = sum(s.travel for d in plan.days for s in d.slots) + sum(d.back_min for d in plan.days)
    window = sum(d.window[1] - d.window[0] for d in plan.days)
    total, budget = plan.cost["total"], req.budget.total_vnd
    return {
        "interest": round(100 * sum(interest_match(s.place, req) for s in pois) / len(pois), 1),  # type: ignore[arg-type]
        "experience": round(100 * sum(quality(s.place) for s in pois) / len(pois), 1),  # type: ignore[arg-type]
        "budget": round(
            100
            * max(0.0, 1 - max(0, total - budget) / budget * 3)
            * (1 - 0.3 * min(1, total / budget)),
            1,
        ),
        "pace": round(100 * max(0.0, 1 - abs(per_day - target) / target), 1),
        "weather": round(100 * (len(dry) / len(outdoor) if outdoor else 1.0), 1),
        "travel": round(100 * max(0.0, 1 - travel / max(1, window)), 1),
    }


def _rain_at(plan: OptionPlan, slot: Slot) -> int:
    for d in plan.days:
        if slot in d.slots:
            return d.rain[block_of(slot.start)]
    return 0


def binding(plan: OptionPlan, req: PlanRequest) -> list[str]:
    """Ràng buộc đang bị chạm — giải thích vì sao phương án không tốt hơn được nữa."""
    out, c = [], plan.cost
    if req.budget.hard and c["total"] >= req.budget.total_vnd * 0.94:
        out.append(f"Ngân sách cứng đã dùng {round(100 * c['total'] / req.budget.total_vnd)}%")
    for d in plan.days:
        last = max((s.end for s in d.slots), default=d.window[0]) + d.back_min
        if last >= d.window[1] - 12:
            out.append(
                f"Giờ về chỗ ở ngày {d.index + 1}: {hhmm(last)}, giới hạn {hhmm(d.window[1])}"
            )
        for s in d.slots:
            if s.type == "poi" and s.place:
                close = (hours_of(s.place, d.index) or DEFAULT_HOURS)[1]
                if close - s.end <= 15:
                    out.append(f"{s.place.name} đóng cửa lúc {hhmm(close)}")
    for f in req.time.fixed:
        out.append(f"{f.label} {f.start}–{f.end} ngày {f.day}")
    if req.access.siesta:
        out.append("Nghỉ trưa 90 phút mỗi ngày")
    out += plan.repairs
    return out[:6]


def day_date(req: PlanRequest, d: int) -> tuple[str, str]:
    day = req.basic.start_date + timedelta(days=d)
    return day.isoformat(), WEEKDAYS[day.weekday()]

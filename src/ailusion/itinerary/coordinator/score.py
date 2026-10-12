"""Làn Coordinator — bước 7–8: chấm điểm, chọn chỗ ở, chốt danh sách đưa Planner.

- Bước 7 (`cham`): thời gian ở lại (DB; thiếu thì ước theo loại chỗ `STAY_BY_TYPE`); chọn chỗ ở:
  so 4 mặt (tổng tiền, phút ngồi xe tới các chỗ chơi, sao tin được, tiện ích), lấy tầng không bị
  trội, rồi xếp theo điều nhóm khách coi trọng.
- Bước 8 (`chot_danh_sach`): ma trận phút đi giữa chỗ ở và các chỗ đáng xét; sức chứa từng ngày
  = phút rảnh ÷ (thời gian ở lại phổ biến + thời gian đi phổ biến); lấy từ tầng không bị trội cho
  tới khi đủ, đặt mã ngắn D1, D2… (chỗ chơi), Q1, Q2… (quán) cho Planner.
"""

from __future__ import annotations

import math
from typing import Any

from ..shared.context import Ctx
from ..shared.form import to_minutes
from ..shared.math import km, median, pareto_tiers
from ..shared.models import (
    STAY,
    DayWindow,
    ItineraryState,
    Place,
    dump_places,
    form_of,
    place_from_dict,
    place_to_dict,
    places_of,
    profile_of,
    stays_of,
)
from ..shared.tools import tool_ok


class NoPlacesError(Exception):
    """Không có điểm tham quan nào ở điểm đến (DB lẫn tool tìm kiếm đều trống)."""

    def __init__(self, destination: str) -> None:
        super().__init__(f"Chưa có dữ liệu địa điểm cho {destination}")
        self.destination = destination


# Thời gian ở lại theo loại — chỉ dùng khi Google không có dòng "Mọi người thường dành …".
# Số tạm (nguồn "tu_dat"), kết quả ghi rõ "ước theo loại".
STAY_BY_TYPE: list[tuple[tuple[str, ...], int, int]] = [
    (("bảo tàng",), 60, 90),
    (("chùa", "nhà thờ", "đền", "lăng"), 30, 60),
    (("bãi biển", "biển"), 90, 180),
    (("khu vui chơi", "công viên giải trí"), 120, 240),
    (("chợ",), 45, 90),
    (("cà phê", "quán cà phê"), 45, 90),
    (("spa", "massage"), 60, 120),
    (("núi", "thác", "rừng", "hang"), 90, 180),
]
MEAL_STAY = (45, 75)
DEFAULT_STAY = (60, 90)

# Giá ước mỗi người (VND) cho chỗ chưa có giá thật sau khi đã hỏi DB và Agent Budget. Ưu tiên
# trung vị giá của các chỗ cùng loại có giá thật trong chính chuyến này (`fill_price_estimates`);
# không có thì theo bảng dưới — số tạm (nguồn "tu_dat"), kết quả ghi rõ "ước". Từ khoá so theo thứ
# tự: "công viên giải trí" phải đứng trước "công viên".
PRICE_BY_TYPE: list[tuple[tuple[str, ...], int]] = [
    (("công viên giải trí", "khu vui chơi", "sun world", "vinwonders", "công viên nước"), 700_000),
    (("spa", "massage", "mát xa"), 400_000),
    (("suối khoáng", "tắm bùn", "suối nước nóng"), 300_000),
    (("bảo tàng", "di tích", "triển lãm", "thành cổ", "lăng"), 50_000),
    (("cà phê", "cafe", "coffee"), 60_000),
    # Chỉ loại gần như luôn miễn phí. Không đoán theo "cầu", "hồ": Cầu Rồng miễn phí nhưng Cầu Vàng
    # nằm trong khu Bà Nà phải mua vé.
    (
        (
            "bãi biển",
            "bãi tắm",
            "chùa",
            "đền",
            "nhà thờ",
            "hành hương",
            "thánh địa",
            "tượng",
            "đài tưởng niệm",
            "quảng trường",
            "phố đi bộ",
            "công viên",
            "bán đảo",
            "chợ",
        ),
        0,
    ),
]
VISIT_PRICE = 100_000  # chỗ chơi không khớp loại nào
MEAL_PRICE = 150_000  # một bữa mỗi người khi không quán nào trong chuyến có giá
MIN_SAME_TYPE_PRICES = 2  # cần ít nhất ngần này chỗ cùng loại có giá thật mới lấy trung vị


def fill_price_estimates(places: dict[str, Place]) -> None:
    """Điền giá ước cho chỗ chơi / quán chưa có giá, ghi nguồn vào `price_by` ("ước …")."""
    known: dict[str, list[float]] = {}
    for p in places.values():
        if p.price is not None and not (p.price_by or "").startswith("ước"):
            known.setdefault("meal" if p.kind == "meal" else p.group, []).append(float(p.price))
    for p in places.values():
        if p.price is not None:
            continue
        same = known.get("meal" if p.kind == "meal" else p.group, [])
        if len(same) >= MIN_SAME_TYPE_PRICES:
            p.price, p.price_by = int(median(same) or 0), "ước theo chỗ cùng loại"
        elif p.kind == "meal":
            p.price, p.price_by = MEAL_PRICE, "ước theo loại"
        else:
            p.price = next(
                (v for words, v in PRICE_BY_TYPE if any(w in p.text for w in words)), VISIT_PRICE
            )
            p.price_by = "ước theo loại"


def fill_stay_estimate(place: Place) -> None:
    if place.stay_low is not None:
        return
    if place.kind == "meal":
        place.stay_low, place.stay_high = MEAL_STAY
    else:
        low, high = next(
            ((lo, hi) for words, lo, hi in STAY_BY_TYPE if any(w in place.text for w in words)),
            DEFAULT_STAY,
        )
        place.stay_low, place.stay_high = low, high
    place.stay_by = "ước theo loại"


MAX_DETAILS = 10  # get_place_details (tốn quota SerpApi) để lấy thời gian ở lại
MAX_STAYS_COMPARED = 6
# = giới hạn của tool distance_matrix (MCP `_TOI_DA_DIEM`); vượt là lỗi cả ma trận.
MAX_MATRIX_POINTS = 25
# Ước phút đi khi distance_matrix không trả được cặp nào đó: km đường chim bay × hệ số đường vòng
# ÷ tốc độ trong phố, cộng phút gửi xe. Số tạm (nguồn "tu_dat"), kết quả ghi rõ là ước.
DETOUR = 1.4
CITY_KMH = {"motorbike": 25, "car": 25, "taxi": 25, "bus": 15}
PARKING_MIN = 3


def estimate_minutes(a: Place, b: Place, travel_mode: str | None) -> float:
    speed = CITY_KMH.get(travel_mode or "", 25)
    return round(km((a.lat, a.lng), (b.lat, b.lng)) * DETOUR / speed * 60 + PARKING_MIN)


def capacity(days: list[DayWindow], typical_stay: float, typical_hop: float) -> list[int]:
    per = max(1.0, typical_stay + typical_hop)
    return [max(1, math.floor(d.free_minutes / per)) for d in days]


def place_vector(center: tuple[float, float] | None) -> Any:
    """4 mặt so chỗ (bước 8), đổi về "lớn hơn là tốt hơn": sao tin được, hợp mục đích,
    giá (âm), khoảng cách tới trung tâm (âm)."""

    def vector(p: Place) -> list[float | None]:
        return [
            p.rating_floor,
            1.0 if p.matches_purpose else 0.0,
            -p.price if p.price is not None else None,
            -km(center, (p.lat, p.lng)) if center else 0.0,
        ]

    return vector


# Loại nên thưa hơn "mỗi ngày 1 chỗ": số chỗ tối đa cả chuyến = số ngày × tỉ lệ (ít nhất 1).
# Spa: 1 chỗ mỗi 2 ngày — mục đích "nghỉ dưỡng" có từ khoá spa nên dễ kéo spa vào mọi ngày.
TYPE_SHARE = {"spa": 0.5}


def type_cap(group: str, days: int) -> int:
    """Số chỗ tối đa của một loại trong cả chuyến."""
    return max(1, math.ceil(days * TYPE_SHARE.get(group, 1.0)))


def pick_pool(
    visits: list[Place],
    meals: list[Place],
    need_visits: int,
    need_meals: int,
    center: Any,
    per_group: int,
) -> tuple[list[Place], list[list[str]]]:
    """Lấy hết tầng 1, rồi tầng 2… cho tới khi đủ số chỗ cả chuyến cần. Quán ăn chọn cùng cách.

    Chỗ chơi cùng một loại (`Place.group`: spa, chùa…) lấy tối đa `type_cap` chỗ (`per_group` =
    số ngày; spa thưa hơn): không thì "nghỉ dưỡng" kéo toàn spa vào, ngày nào cũng spa.
    """
    chosen: list[Place] = []
    tiers_out: list[list[str]] = []
    for items, need, cap in ((visits, need_visits, per_group), (meals, need_meals, None)):
        taken = 0
        per: dict[str, int] = {}
        for tier in pareto_tiers(items, place_vector(center)):
            if taken >= need:
                break
            kept = []
            for p in sorted(tier, key=lambda p: -(p.rating_floor or 0)):
                if cap is not None and per.get(p.group, 0) >= type_cap(p.group, cap):
                    continue
                per[p.group] = per.get(p.group, 0) + 1
                kept.append(p)
            chosen += kept
            taken += len(kept)
            tiers_out.append([p.place_id for p in kept])
    return chosen, tiers_out


def split_by_day(
    pool: list[Place], days: list[int], minutes: dict[str, float]
) -> dict[int, list[str]]:
    """Chia chỗ cho từng ngày TRƯỚC khi đưa Planner, để không ngày nào đi lại chỗ ngày khác.

    Chỗ chơi: mỗi ngày lấy 1 chỗ nổi tiếng nhất còn mở cửa ngày đó làm "mỏ neo"; các chỗ còn lại
    (nổi tiếng trước) vào ngày có mỏ neo gần nhất (phút đi), miễn là ngày đó mở cửa, chưa có chỗ
    cùng loại và chưa đầy. Không ngày nào nhận được thì nới luật cùng loại, rồi nới số lượng.
    Quán ăn: đủ nhiều (≥ 2 quán mỗi ngày) thì chia theo mỏ neo gần nhất; ít thì ngày nào cũng
    dùng chung. `minutes`: "id_a|id_b" -> phút đi (ma trận bước 8).
    """
    visits = sorted((p for p in pool if p.kind != "meal"), key=lambda p: -(p.reviews or 0))
    meals = sorted((p for p in pool if p.kind == "meal"), key=lambda p: -(p.reviews or 0))
    out: dict[int, list[Place]] = {d: [] for d in days}

    def is_open(p: Place, day: int) -> bool:
        return p.hours.get(day, "chưa rõ") is not None  # chưa rõ giờ thì coi như mở

    def far(p: Place, day: int) -> float:
        anchor = out[day][0] if out[day] else None
        return minutes.get(f"{anchor.place_id}|{p.place_id}", 999.0) if anchor else 0.0

    rest = list(visits)
    for day in days:  # mỏ neo
        anchor = next((p for p in rest if is_open(p, day)), None)
        if anchor:
            out[day].append(anchor)
            rest.remove(anchor)
    per_day = math.ceil(len(visits) / max(1, len(days)))
    for p in rest:
        open_days = [d for d in days if is_open(p, d)]
        for rule in ("đủ luật", "nới cùng loại", "nới số lượng"):
            fits = [
                d
                for d in open_days
                if (rule == "nới số lượng" or len(out[d]) < per_day)
                and (rule != "đủ luật" or all(q.group != p.group for q in out[d]))
            ]
            if fits:
                out[min(fits, key=lambda d: (far(p, d), len(out[d])))].append(p)
                break
    if len(meals) >= 2 * len(days):
        per_meal = math.ceil(len(meals) / len(days))
        anchors = {d: list(out[d]) for d in days}
        taken = {d: 0 for d in days}
        for m in meals:
            fits = [d for d in days if taken[d] < per_meal] or days
            day = min(fits, key=lambda d: far(m, d) if anchors[d] else 999.0)
            out[day].append(m)
            taken[day] += 1
    else:
        for d in days:
            out[d] += meals
    return {d: [p.place_id for p in ps] for d, ps in out.items()}


async def cham(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    stays = stays_of(state)
    await ctx.log("cham", "step", "Chấm sao tin được, thời gian ở lại; chọn chỗ ở")
    # Thời gian ở lại từ Google cho chỗ đáng xét nhất (tốn quota, giới hạn MAX_DETAILS).
    top = sorted(
        (p for p in places.values() if p.kind == "visit"),
        key=lambda p: (not p.matches_purpose, -(p.rating_floor or 0)),
    )[:MAX_DETAILS]
    # source="db": DB chưa lưu `time_spent` cho chỗ nào (đã đếm 28.9k chỗ), gọi SerpApi thì
    # tốn tới 10 quota mỗi lộ trình; thiếu thì ước theo loại chỗ (STAY_BY_TYPE).
    details, logs = await ctx.many(
        [("get_place_details", {"place_id": p.place_id, "source": "db"}) for p in top]
    )
    for p, data in zip(top, details, strict=True):
        stay = ((data.get("place") or {}).get("stay_min")) if tool_ok(data) else None
        if stay and stay.get("low"):
            p.stay_low, p.stay_high, p.stay_by = int(stay["low"]), int(stay["high"]), "google"
    for p in places.values():
        fill_stay_estimate(p)
    fill_price_estimates(places)

    # Chọn chỗ ở: user đã đặt thì dùng luôn; chưa thì so 4 mặt rồi xếp theo nhóm.
    center = state.get("center")
    chosen_stay = None
    if form.stay_booked:
        geo, g_log = await ctx.tool("geocode_vn", address=f"{form.stay_booked}, {form.destination}")
        logs.append(g_log)
        top_geo = (geo.get("results") or [{}])[0] if tool_ok(geo) else {}
        lat, lng = (
            top_geo.get("lat") or (center or [0, 0])[0],
            top_geo.get("lng") or (center or [0, 0])[1],
        )
        chosen_stay = Place(
            place_id="booked",
            name=form.stay_booked,
            category="Chỗ ở đã đặt",
            kind="stay",
            lat=lat,
            lng=lng,
        )
    elif stays:
        candidates = sorted(stays.values(), key=lambda s: -(s.rating_floor or 0))[
            :MAX_STAYS_COMPARED
        ]
        visits = [p for p in top] or [p for p in places.values() if p.kind == "visit"][:10]
        ride: dict[str, float | None] = {}
        if visits:
            data, m_log = await ctx.tool(
                "distance_matrix",
                origins=[f"{s.lat},{s.lng}" for s in candidates],
                destinations=[f"{p.lat},{p.lng}" for p in visits],
                vehicle=form.vehicle,
            )
            logs.append(m_log)
            per: dict[int, list[float]] = {}
            for pair in (data.get("pairs") or []) if tool_ok(data) else []:
                if pair.get("duration_min") is not None and pair["from"] < len(candidates):
                    per.setdefault(pair["from"], []).append(pair["duration_min"])
            ride = {s.name: median(per.get(i, [])) for i, s in enumerate(candidates)}
        rooms = -(-form.group_size // 2)

        def vector(s: Place) -> list[float | None]:
            total = s.price * max(1, form.num_nights) * rooms if s.price else None
            amenities = sum(1 for n in profile.nice_to_have_needs if s.need_status.get(n) == "yes")
            return [
                -total if total else None,
                -ride[s.name] if ride.get(s.name) is not None else None,
                s.rating_floor,
                float(amenities),
            ]

        tiers = pareto_tiers(candidates, vector)
        first = tiers[0] if tiers else candidates
        key = {
            "P1": lambda s: s.price or 10**9,
            "P2": lambda s: ride.get(s.name) or 10**9,
            "P3": lambda s: -(s.rating_floor or 0),
            "P4": lambda s: ride.get(s.name) or 10**9,
        }[profile.group]
        chosen_stay = sorted(first, key=key)[0]
    await ctx.log(
        "cham",
        "finding",
        f"Chỗ ở: {chosen_stay.name if chosen_stay else 'chưa chọn được'}",
    )
    return {
        "places": dump_places(places),
        "stay": place_to_dict(chosen_stay) if chosen_stay else None,
        "tool_log": logs,
    }


async def chot_danh_sach(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    center = tuple(state["center"]) if state.get("center") else None
    stay = place_from_dict(state["stay"]) if state.get("stay") else None
    visits = [p for p in places.values() if p.kind == "visit"]
    meals = [p for p in places.values() if p.kind == "meal"]
    if not visits:
        raise NoPlacesError(form.destination)
    # Ma trận phút đi giữa chỗ ở và các chỗ đáng xét (tối đa MAX_MATRIX_POINTS điểm).
    ordered_visits = sorted(visits, key=lambda p: (not p.matches_purpose, -(p.rating_floor or 0)))
    # Mỗi loại tối đa 1 chỗ mỗi ngày của chuyến; chỗ thừa cùng loại xếp xuống cuối.
    per: dict[str, int] = {}
    first, rest = [], []
    for p in ordered_visits:
        per[p.group] = per.get(p.group, 0) + 1
        (first if per[p.group] <= type_cap(p.group, len(profile.days)) else rest).append(p)
    ordered_visits = first + rest
    ordered_meals = sorted(meals, key=lambda p: -(p.rating_floor or 0))
    # Ít nhất 2 quán riêng mỗi ngày (bước chia ngày cần vậy mới không phải dùng chung quán).
    n_meal_slots = max(
        sum(1 + (to_minutes(d.day_end) >= 19 * 60) for d in profile.days), 2 * len(profile.days)
    )
    shortlist = ordered_visits[: MAX_MATRIX_POINTS - 1 - min(len(ordered_meals), n_meal_slots + 3)]
    shortlist += ordered_meals[: min(len(ordered_meals), n_meal_slots + 3)]
    points = ([stay] if stay else []) + shortlist
    data, m_log = await ctx.tool(
        "distance_matrix", origins=[f"{p.lat},{p.lng}" for p in points], vehicle=form.vehicle
    )
    ids = ([STAY] if stay else []) + [p.place_id for p in shortlist]
    matrix: dict[str, float] = {}
    for pair in (data.get("pairs") or []) if tool_ok(data) else []:
        if pair.get("duration_min") is None:
            continue
        matrix[f"{ids[pair['from']]}|{ids[pair['to']]}"] = float(pair["duration_min"])
    # Cặp tool không trả (lỗi, quá giới hạn, không có đường): ước theo đường chim bay thay vì để
    # trống — phút đi trống bị tính là 0, lịch thiếu dữ liệu lại thành lịch "ít ngồi xe nhất".
    notes = list(state.get("notes") or [])
    by_id = dict(zip(ids, points, strict=True))
    guessed = 0
    for a in ids:
        for b in ids:
            if a != b and f"{a}|{b}" not in matrix:
                matrix[f"{a}|{b}"] = float(estimate_minutes(by_id[a], by_id[b], form.travel_mode))
                guessed += 1
    if guessed:
        notes.append(
            f"Không lấy được phút đi thật cho {guessed} chặng, đang ước theo khoảng cách đường "
            "chim bay — giờ đến các chỗ có thể lệch."
        )
    hops = [v for k, v in matrix.items() if STAY not in k]
    typical_hop = median(hops) or 20.0
    typical_stay = (
        median([float(p.stay_low) for p in shortlist if p.kind == "visit" and p.stay_low]) or 60.0
    )
    caps = capacity(profile.days, typical_stay, typical_hop)
    pool, tiers = pick_pool(
        [p for p in shortlist if p.kind == "visit"],
        [p for p in shortlist if p.kind == "meal"],
        # Mỗi ngày thêm 2 chỗ dư để Planner có lựa chọn khi xếp 3 lịch khác nhau.
        sum(caps) + 2 * len(profile.days),
        n_meal_slots,
        center,
        per_group=len(profile.days),
    )
    # Đặt mã theo độ nổi tiếng (số review giảm dần): Planner hay chọn các mã đầu danh sách, nên
    # chỗ nổi tiếng (Cầu Vàng, Bà Nà…) đứng trước spa / quán nhỏ có sao cao nhưng ít người biết.
    pool = sorted(pool, key=lambda p: (p.kind == "meal", -(p.reviews or 0)))
    codes = {}
    for p in pool:
        prefix = "Q" if p.kind == "meal" else "D"
        codes[f"{prefix}{sum(c.startswith(prefix) for c in codes) + 1}"] = p.place_id
    code_of = {pid: c for c, pid in codes.items()}
    by_day = split_by_day(pool, [d.day for d in profile.days], matrix)
    day_codes = {str(d): [code_of[pid] for pid in pids] for d, pids in by_day.items()}
    await ctx.log(
        "chot_danh_sach",
        "finding",
        f"Sức chứa {caps} chỗ/ngày, đưa Planner "
        f"{sum(p.kind == 'visit' for p in pool)} chỗ chơi + "
        f"{sum(p.kind == 'meal' for p in pool)} quán",
    )
    return {
        "pool": [p.place_id for p in pool],
        "codes": codes,
        "day_codes": day_codes,
        "matrix": matrix,
        "notes": notes,
        "tool_log": [m_log],
    }

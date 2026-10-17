"""Làn Coordinator — bước 5: gọi tool tìm chỗ chơi, quán ăn, chỗ ở, lễ hội, thời tiết, thiên tai.

Dữ liệu đã lưu (Postgres qua MCP, không tốn quota) gọi trước, song song. Nguồn tốn quota chỉ gọi
khi dữ liệu đã lưu thiếu:
- `search_events` (Tavily) khi DB không có lễ hội nào trong ngày đi;
- `search_hotels` (SerpApi) khi DB có dưới `ENOUGH_PRICED_STAYS` chỗ ở có giá;
- DB và vùng quanh toạ độ vẫn dưới `ENOUGH_VISITS` chỗ chơi: tìm POI bằng Vietmap (`find_nearby`,
  không tốn quota SerpApi, không có sao / review); vẫn thiếu mới `search_places` (SerpApi).

Chỗ user gọi tên (`must_visits`) tra riêng (`find_musts`): DB trước, rồi Vietmap, cuối cùng mới
`search_places`; tìm được thì đánh dấu `Place.must` để các bước sau giữ, xếp đúng ngày / giờ.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import timedelta
from typing import Any

from ..shared.context import Ctx
from ..shared.form import (
    CAFE_REST_PRICE,
    HOTEL_REST_SHARE,
    PURPOSE_KEYWORDS,
    TripForm,
    needs_for,
    rooms_for,
)
from ..shared.math import km, median
from ..shared.models import (
    ItineraryState,
    Place,
    _plain,
    dump_places,
    form_of,
    profile_of,
    wanted_match,
    wanted_words,
)
from ..shared.tools import add_places, add_stays, tool_ok
from .score import stay_target_share

ENOUGH_VISITS = 6
NEARBY_KM = 10
ENOUGH_PRICED_STAYS = 5  # dưới số này chỗ ở có giá trong DB mới gọi search_hotels


def place_calls(
    where: dict[str, Any], purposes: list[str], interests: str, needs: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    """Các lượt tìm chỗ chơi / quán (đọc DB) cho một vùng: `where` là {"destination": …} hoặc
    {"lat", "lng", "radius_km"} (có thể kèm "sort_by"). Gồm loại chung, từ khoá của mục đích, sở
    thích, nhu cầu. Mỗi lượt lấy tới trần của tool (30 chỗ)."""
    calls: list[tuple[str, dict[str, Any]]] = [
        ("find_places", {**where, "category": "Điểm thu hút khách du lịch", "limit": 30}),
        ("find_places", {**where, "category": "Nhà hàng", "limit": 30}),
    ]
    for purpose in purposes:
        for word in PURPOSE_KEYWORDS[purpose]:
            calls.append(("find_places", {**where, "query": word, "limit": 25}))
    # Thứ user nhắc (VD "bún bò"): tìm riêng từng thứ.
    for word in wanted_words(interests)[:5]:
        calls.append(("find_places", {**where, "query": word, "limit": 30}))
    # Nhu cầu: một lượt tìm quán đáp ứng mọi nhu cầu (kể cả món chay, ghế cao cho bé), một lượt
    # tìm chỗ chơi theo nhu cầu không phải ăn uống (lối xe lăn...).
    if needs:
        calls.append(("find_places", {**where, "needs": needs, "limit": 30}))
    other = needs_for("visit", needs)
    if other and other != needs:
        calls.append(("find_places", {**where, "needs": other, "limit": 30}))
    return calls


# 3 phương án cần nhiều chỗ khác nhau (tool không có phân trang): ngoài lượt tìm theo độ đông
# khách, tìm thêm theo sao cao, và quét 4 hướng quanh trung tâm (mỗi hướng một vòng tròn). Chỉ đọc
# DB, không tốn quota.
SWEEP_KM = 6
SWEEP_RADIUS_KM = 5


def wider_wheres(dest: str, center: list[float] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"destination": dest, "sort_by": "rating", "min_reviews": 30}]
    if center:
        lat, lng = center
        dlat = SWEEP_KM / 111
        dlng = SWEEP_KM / (111 * max(0.2, math.cos(math.radians(lat))))
        out += [
            {"lat": lat + a, "lng": lng + b, "radius_km": SWEEP_RADIUS_KM}
            for a, b in ((dlat, 0), (-dlat, 0), (0, dlng), (0, -dlng))
        ]
    return out


MUST_MATCH = 0.6
VIETMAP_MUST_RADIUS_M = 50_000  # chỗ user gọi tên: tìm trong 50 km quanh điểm đến (Bà Nà ~ 40 km)
VIETMAP_FALLBACK_WORDS = ("điểm tham quan", "danh lam thắng cảnh", "bảo tàng", "quán ăn")


def vietmap_near(
    center: Any, destination: str, radius_m: int = VIETMAP_MUST_RADIUS_M
) -> dict[str, Any]:
    """Tâm tìm cho `find_nearby` (Vietmap): toạ độ điểm đến nếu có, không thì tên điểm đến."""
    if center:
        return {"lat": center[0], "lng": center[1], "radius_m": min(radius_m, 50_000)}
    return {
        "near": destination,
        "radius_m": min(radius_m, 50_000),
    }  # tỉ lệ chữ trong tên user gõ phải có trong tên chỗ tìm được


def name_match(wanted: str, name: str, place: str | None = None) -> float:
    """Tỉ lệ chữ (bỏ dấu) của tên user gõ có trong tên chỗ: "ba na hills" vs "Sun World Bà Nà
    Hills" = 1.0. Chữ của tên điểm đến `place` không tính (còn chữ khác thì thôi): "Đại Nội Huế"
    từng khớp "NGƯ PHỐ - Chả Cá Hà Nội tại Huế" 2/3 nhờ "Nội" + "Huế"."""
    want = set(re.findall(r"\w+", _plain(wanted)))
    if place:
        want = (want - set(re.findall(r"\w+", _plain(place)))) or want
    have = set(re.findall(r"\w+", _plain(name)))
    return len(want & have) / len(want) if want else 0.0


# Chữ chỉ loại chỗ: tên tìm được có thêm loại mà user không gõ là chỗ KHÁC cùng tên (user gõ
# "Làng chài Mũi Né" -> "Nhà Hàng Làng Chài Mũi Né"; "Bãi Ông Địa" -> "Ăn Vặt Bãi Đá Ông Địa").
TYPE_WORDS = (
    "nha hang",
    "quan an",
    "quan",
    "an vat",
    "restaurant",
    "cafe",
    "ca phe",
    "coffee",
    "bistro",
    "bbq",
    "khach san",
    "hotel",
    "homestay",
    "resort",
    "villa",
    "nha nghi",
    "spa",
    "cua hang",
    "shop",
    "tour",
)


def other_type(wanted: str, p: Place) -> bool:
    """Chỗ `p` thuộc loại khác chỗ user gọi tên: tên có chữ chỉ loại (nhà hàng, quán, khách
    sạn…) mà tên user gõ không có. ("Bếp Hên" là quán dù tên không có chữ "quán": vẫn khớp.)"""
    want, name = f" {_plain(wanted)} ", f" {_plain(p.name)} "
    return any(f" {w} " in name and f" {w} " not in want for w in TYPE_WORDS)


FAMOUS_SHARE = 0.3  # chỗ cùng tên có ≥ ngần này × số review của chỗ nổi tiếng nhất mới được xét


def best_match(
    wanted: str,
    found: list[Place],
    center: tuple[float, float] | None = None,
    place: str | None = None,
) -> Place | None:
    """Chỗ khớp tên nhất (đủ `MUST_MATCH`). Nhiều chỗ khớp như nhau và đều nổi tiếng thì lấy chỗ
    gần trung tâm điểm đến nhất: Google có cả ghim "Ba Na Hills" trên đỉnh núi (xe không tới,
    phút đi sai hẳn) lẫn "Sun World Bà Nà Hills" ở ga cáp treo — lấy chỗ đi tới được."""
    scored = [(name_match(wanted, p.name, place), p) for p in found if not other_type(wanted, p)]
    top = max((sc for sc, _ in scored), default=0.0)
    if top < MUST_MATCH:
        return None
    same = [p for sc, p in scored if sc == top]
    most = max(p.reviews or 0 for p in same)
    famous = [p for p in same if (p.reviews or 0) >= FAMOUS_SHARE * most]
    if center is None or len(famous) == 1:
        return max(famous, key=lambda p: p.reviews or 0)
    return min(famous, key=lambda p: km(center, (p.lat, p.lng)))


WANTED_SEARCH_MAX = 3  # số thứ user nhắc được tìm thêm ngoài DB (SerpApi tốn 1 lượt mỗi thứ)


async def find_wanted(
    form: TripForm, places: dict[str, Place], ctx: Ctx, center: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """Thứ user nhắc (VD "bún bò") mà DB không có chỗ nào khớp: tìm Vietmap (miễn phí) quanh
    điểm đến, vẫn không có thì SerpApi. Không thấy ở đâu thì ghi chú cho user."""
    dest, logs = form.destination, []

    def lacking(words: list[str]) -> list[str]:
        return [w for w in words if not any(wanted_match(p, [w]) for p in places.values())]

    missing = lacking(wanted_words(form.interests)[:WANTED_SEARCH_MAX])
    for name, args_of in (
        ("find_nearby", lambda w: {"query": w, **vietmap_near(center, dest), "limit": 10}),
        ("search_places", lambda w: {"query": f"{w} {dest}", "limit": 10}),
    ):
        if not missing:
            break
        results, more = await ctx.many([(name, args_of(w)) for w in missing])
        logs += more
        for data in results:
            add_places(places, data, name)
        missing = lacking(missing)
    return logs, [f"Không tìm thấy chỗ có “{w}” ở {dest}." for w in missing]


async def find_musts(
    form: TripForm,
    places: dict[str, Place],
    ctx: Ctx,
    center: tuple[float, float] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Tra chỗ user chỉ định, đánh dấu `must` rồi thêm vào `places`. -> (nhật ký tool, ghi chú)."""
    musts = form.must_visits
    if not musts:
        return [], []
    dest = form.destination
    calls = [("find_places", {"destination": dest, "query": m.name, "limit": 8}) for m in musts]
    results, logs = await ctx.many(calls)
    picked: dict[int, Place] = {}
    for i, (m, data) in enumerate(zip(musts, results, strict=True)):
        pool: dict[str, Place] = {}
        add_places(pool, data, "find_places", any_kind=True)
        if hit := best_match(m.name, list(pool.values()), center, dest):
            picked[i] = hit
    # DB không có: hỏi SerpApi (tốn quota), chỉ cho đúng các chỗ còn thiếu.
    # DB không có: Vietmap trước (không tốn quota SerpApi), vẫn không thấy mới hỏi SerpApi.
    for tool, args_of in (
        ("find_nearby", lambda m: {"query": m.name, **vietmap_near(center, dest), "limit": 5}),
        ("search_places", lambda m: {"query": f"{m.name} {dest}", "limit": 5}),
    ):
        left = [i for i in range(len(musts)) if i not in picked]
        if not left:
            break
        more, more_logs = await ctx.many([(tool, args_of(musts[i])) for i in left])
        logs += more_logs
        for i, data in zip(left, more, strict=True):
            pool: dict[str, Place] = {}
            add_places(pool, data, tool, any_kind=True)
            if hit := best_match(musts[i].name, list(pool.values()), center, dest):
                picked[i] = hit
    notes = []
    for i, m in enumerate(musts):
        hit = picked.get(i)
        if hit is None:
            notes.append(f"Không tìm thấy “{m.name}” ở {dest}, lộ trình chưa có chỗ này.")
            continue
        p = places.get(hit.place_id) or hit
        p.must, p.must_day, p.must_time, p.must_name = True, m.day, m.time, m.name
        places[p.place_id] = p
    return logs, notes


# Toạ độ điểm đến (geocode tên) lệch khỏi trung vị các chỗ tìm được theo tên điểm đến quá ngần này
# thì coi là geocode nhầm nơi trùng tên (VD "Hội An" ra một xã Hội An ở tỉnh khác). Số tạm.
CENTER_CHECK_KM = 40
CENTER_MIN_PLACES = 5


def checked_center(
    center: list[float] | None, places: dict[str, Place], destination: str
) -> tuple[list[float] | None, str | None]:
    """Kiểm toạ độ trung tâm điểm đến bằng chính các chỗ DB trả theo tên điểm đến: lệch xa trung
    vị của chúng thì lấy trung vị (nhiều chỗ thật ở điểm đến đáng tin hơn 1 kết quả geocode)."""
    pts = [(p.lat, p.lng) for p in places.values() if p.lat and p.lng]
    if len(pts) < CENTER_MIN_PLACES:
        return center, None
    mid = [median([a for a, _ in pts]) or 0.0, median([b for _, b in pts]) or 0.0]
    if center and km(tuple(center), tuple(mid)) <= CENTER_CHECK_KM:  # type: ignore[arg-type]
        return center, None
    return mid, (
        f"Toạ độ {destination} lấy theo vị trí các địa điểm tìm được "
        "(geocode ra nơi khác trùng tên)."
        if center
        else None
    )


# Chỗ ở theo ngân sách: tìm thêm quanh giá phòng mục tiêu (phần ngân sách dành cho chỗ ở / đêm /
# phòng), không thì "đáng tiền" chỉ ra phòng 200–400 nghìn và cả chuyến tiêu thấp hơn ngân sách.
STAY_BAND = (0.6, 1.4)


def stay_band_calls(
    form: TripForm, trip_budget: int | None, needs: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    if not trip_budget or not form.num_nights or form.stay_booked:
        return []
    needs = needs_for("stay", needs)
    rooms = rooms_for(form)
    target = trip_budget * stay_target_share(form) / form.num_nights / rooms
    return [
        (
            "find_stays",
            {
                "destination": form.destination,
                "min_price_vnd": int(target * STAY_BAND[0]),
                "max_price_vnd": int(target * STAY_BAND[1]),
                "sort_by": "rating",
                "limit": 20,
                **({"needs": needs} if needs else {}),
            },
        )
    ]


# Chỗ nghỉ trưa dọc đường (TripForm.nap): quán cà phê sao cao, hoặc khách sạn (nghỉ theo giờ).
REST_CAFES = {"category": "Quán cà phê", "sort_by": "rating", "min_reviews": 50, "limit": 30}


def add_rest_spots(
    places: dict[str, Place], cafes: dict[str, Place], stays: dict[str, Place], form: TripForm
) -> None:
    """Thêm chỗ nghỉ trưa vào `places` với kind "rest" (không vào danh sách chỗ chơi / quán ăn)."""
    for pid, q in cafes.items():
        if pid not in places:
            q.kind, q.rest_of, q.price = "rest", "cafe", CAFE_REST_PRICE
            places[pid] = q
    if form.nap == "hotel":
        rooms = rooms_for(form)
        for pid, s in stays.items():
            if s.price and pid not in places:
                rest = copy.copy(s)
                rest.kind, rest.rest_of = "rest", "hotel"
                rest.price = int(s.price * HOTEL_REST_SHARE * rooms / form.group_size)
                places[pid] = rest


async def tim_kiem(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile = form_of(state), profile_of(state)
    dest = form.destination
    start, end = form.start_date.isoformat(), form.end_date.isoformat()
    stay_needs = needs_for("stay", profile.required_needs)
    calls = []
    for where in [{"destination": dest}, *wider_wheres(dest, state.get("center"))]:
        calls += place_calls(where, profile.purposes, form.interests, profile.required_needs)
    calls += [
        (
            "find_festivals",
            {"location": dest, "start_date": start, "end_date": end, "limit": 5},
        ),
        (
            "find_stays",
            {
                "destination": dest,
                "sort_by": "value",
                "limit": 20,
                **({"needs": stay_needs} if stay_needs else {}),
            },
        ),
        *stay_band_calls(form, profile.trip_budget, profile.required_needs),
        *(
            [("find_places", {"destination": dest, **REST_CAFES})]
            if form.nap in ("auto", "cafe") and any(d.nap for d in profile.days)
            else []
        ),
        (
            "weather_forecast",
            {"start_date": start, "end_date": end, "location": dest, "hourly": True},
        ),
        ("disaster_alerts", {"location": dest, "days": 7}),
    ]
    hotel_call = None
    if not form.stay_booked and form.num_nights:
        hotel_call = (
            "search_hotels",
            {
                "destination": dest,
                "check_in_date": start,
                "check_out_date": (
                    form.end_date + timedelta(days=0 if form.num_nights else 1)
                ).isoformat(),
                "adults": max(1, form.group_size - form.kids_under_6 - form.kids_6_12),
                "children_ages": [4] * form.kids_under_6 + [9] * form.kids_6_12 or None,
                "limit": 15,
            },
        )
    await ctx.log("tim_kiem", "step", f"Gọi {len(calls)} tool tìm địa điểm, chỗ ở, sự kiện")
    results, logs = await ctx.many(calls)
    places: dict[str, Place] = {}
    stays: dict[str, Place] = {}
    weather: list[dict[str, Any]] = []
    festivals: list[str] = []
    festival_dates: list[dict[str, Any]] = []  # lễ hội có ngày, để gắn vào từng ngày đi
    alerts: list[str] = []
    rest: dict[str, Place] = {}
    for (name, args), data in zip(calls, results, strict=True):
        if name == "find_places" and args.get("category") == REST_CAFES["category"]:
            add_places(rest, data, name)
        elif name == "find_places":
            add_places(places, data, name)
        elif name in ("find_stays", "search_hotels"):
            add_stays(stays, data, name)
        elif name == "weather_forecast" and tool_ok(data):
            weather = data.get("days") or []
        elif name in ("find_festivals", "search_events") and tool_ok(data):
            for f in data.get("festivals") or data.get("events") or []:
                if f.get("name") or f.get("title"):
                    festivals.append(f.get("name") or f.get("title"))
                if f.get("name") and f.get("start_date"):
                    festival_dates.append(
                        {"name": f["name"], "start": f["start_date"], "end": f.get("end_date")}
                    )
        elif name == "disaster_alerts" and tool_ok(data):
            for e in data.get("events") or []:
                if str(e.get("alert_level", "")).lower() == "red" and e.get("is_current", True):
                    alerts.append(f"{e.get('name') or e.get('type')} (mức đỏ)")

    add_rest_spots(places, rest, stays, form)

    # Nguồn tốn quota chỉ gọi khi dữ liệu đã lưu thiếu: sự kiện (Tavily) khi DB không có lễ
    # hội nào; giá phòng theo ngày (SerpApi) khi chỗ ở trong DB chưa đủ chỗ có giá.
    paid: list[tuple[str, dict[str, Any]]] = []
    if not festivals:
        paid.append(
            (
                "search_events",
                {"location": dest, "start_date": start, "end_date": end, "max_results": 5},
            )
        )
    if hotel_call and sum(st.price is not None for st in stays.values()) < ENOUGH_PRICED_STAYS:
        paid.append(hotel_call)
    if paid:
        more, more_logs = await ctx.many(paid)
        logs += more_logs
        for (name, _args), data in zip(paid, more, strict=True):
            if name == "search_hotels":
                add_stays(stays, data, name)
            elif tool_ok(data):
                festivals += [e["title"] for e in data.get("events") or [] if e.get("title")]

    # Điểm đến không có dữ liệu riêng trong DB (VD "Hội An" nằm trong dữ liệu Đà Nẵng sau sáp
    # nhập tỉnh): chạy lại ĐỦ các lượt tìm quanh toạ độ điểm đến (miễn phí), không chỉ 2 loại
    # chung chung; vẫn thiếu thì mới search_places (tốn quota).
    center, center_note = checked_center(state.get("center"), places, dest)
    visits = sum(p.kind == "visit" for p in places.values())
    if visits < ENOUGH_VISITS and center:
        near = {"lat": center[0], "lng": center[1], "radius_km": NEARBY_KM}
        nearby = place_calls(near, profile.purposes, form.interests, profile.required_needs)
        more, more_logs = await ctx.many(nearby)
        for (name, _args), data in zip(nearby, more, strict=True):
            add_places(places, data, name)
        logs += more_logs
        visits = sum(p.kind == "visit" for p in places.values())
    if visits < ENOUGH_VISITS:
        # POI Vietmap (không tốn quota SerpApi; không có sao / review nên xếp sau chỗ có số).
        words = list(VIETMAP_FALLBACK_WORDS)
        for purpose in profile.purposes:
            words += list(PURPOSE_KEYWORDS[purpose][:2])
        calls = [
            (
                "find_nearby",
                {"query": w, **vietmap_near(center, dest, NEARBY_KM * 1000 + 5000), "limit": 10},
            )
            for w in dict.fromkeys(words)
        ]
        more, more_logs = await ctx.many(calls)
        for data in more:
            add_places(places, data, "find_nearby")
        logs += more_logs
        visits = sum(p.kind == "visit" for p in places.values())
    if visits < ENOUGH_VISITS:
        more, more_logs = await ctx.many(
            [
                ("search_places", {"query": f"điểm tham quan nổi tiếng {dest}", "limit": 20}),
                ("search_places", {"query": f"quán ăn ngon {dest}", "limit": 20}),
            ]
        )
        for data in more:
            add_places(places, data, "search_places")
        logs += more_logs
    wanted_logs, wanted_notes = await find_wanted(form, places, ctx, center)
    logs += wanted_logs
    must_logs, must_notes = await find_musts(
        form,
        places,
        ctx,
        tuple(center) if center else None,  # type: ignore[arg-type]
    )
    logs += must_logs
    n_visit = sum(p.kind == "visit" for p in places.values())
    n_meal = sum(p.kind == "meal" for p in places.values())
    musts = [p for p in places.values() if p.must]
    await ctx.log(
        "tim_kiem",
        "finding",
        f"{n_visit} chỗ chơi, {n_meal} quán, {len(stays)} chỗ ở"
        + (f"; chỗ bạn chọn: {', '.join(p.name for p in musts)}" if musts else ""),
    )
    return {
        "notes": list(state.get("notes") or [])
        + must_notes
        + wanted_notes
        + ([center_note] if center_note else []),
        "center": center,
        "places": dump_places(places),
        "stays": dump_places(stays),
        "weather": weather,
        "festivals": list(dict.fromkeys(festivals))[:8],
        "festival_dates": festival_dates[:8],
        "alerts": alerts,
        "tool_log": logs,
    }

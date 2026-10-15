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

import re
from datetime import timedelta
from typing import Any

from ..shared.context import Ctx
from ..shared.form import PURPOSE_KEYWORDS, TripForm
from ..shared.math import km, median
from ..shared.models import ItineraryState, Place, _plain, dump_places, form_of, profile_of
from ..shared.tools import add_places, add_stays, tool_ok

ENOUGH_VISITS = 6
NEARBY_KM = 10
ENOUGH_PRICED_STAYS = 5  # dưới số này chỗ ở có giá trong DB mới gọi search_hotels


def place_calls(
    where: dict[str, Any], purposes: list[str], interests: str, needs: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    """Các lượt tìm chỗ chơi / quán (đọc DB) cho một vùng: `where` là {"destination": …} hoặc
    {"lat", "lng", "radius_km"}. Gồm loại chung, từ khoá của mục đích, sở thích, nhu cầu."""
    calls: list[tuple[str, dict[str, Any]]] = [
        ("find_places", {**where, "category": "Điểm thu hút khách du lịch", "limit": 30}),
        ("find_places", {**where, "category": "Nhà hàng", "limit": 30}),
    ]
    for purpose in purposes:
        for word in PURPOSE_KEYWORDS[purpose]:
            calls.append(("find_places", {**where, "query": word, "limit": 15}))
    for word in [w.strip() for w in interests.split(",") if w.strip()][:3]:
        calls.append(("find_places", {**where, "query": word, "limit": 15}))
    if needs:
        calls.append(("find_places", {**where, "needs": needs, "limit": 30}))
    return calls


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


def name_match(wanted: str, name: str) -> float:
    """Tỉ lệ chữ (bỏ dấu) của tên user gõ có trong tên chỗ: "ba na hills" vs "Sun World Bà Nà
    Hills" = 1.0."""
    want = set(re.findall(r"\w+", _plain(wanted)))
    have = set(re.findall(r"\w+", _plain(name)))
    return len(want & have) / len(want) if want else 0.0


FAMOUS_SHARE = 0.3  # chỗ cùng tên có ≥ ngần này × số review của chỗ nổi tiếng nhất mới được xét


def best_match(
    wanted: str, found: list[Place], center: tuple[float, float] | None = None
) -> Place | None:
    """Chỗ khớp tên nhất (đủ `MUST_MATCH`). Nhiều chỗ khớp như nhau và đều nổi tiếng thì lấy chỗ
    gần trung tâm điểm đến nhất: Google có cả ghim "Ba Na Hills" trên đỉnh núi (xe không tới,
    phút đi sai hẳn) lẫn "Sun World Bà Nà Hills" ở ga cáp treo — lấy chỗ đi tới được."""
    scored = [(name_match(wanted, p.name), p) for p in found]
    top = max((sc for sc, _ in scored), default=0.0)
    if top < MUST_MATCH:
        return None
    same = [p for sc, p in scored if sc == top]
    most = max(p.reviews or 0 for p in same)
    famous = [p for p in same if (p.reviews or 0) >= FAMOUS_SHARE * most]
    if center is None or len(famous) == 1:
        return max(famous, key=lambda p: p.reviews or 0)
    return min(famous, key=lambda p: km(center, (p.lat, p.lng)))


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
        if hit := best_match(m.name, list(pool.values()), center):
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
            if hit := best_match(musts[i].name, list(pool.values()), center):
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


async def tim_kiem(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile = form_of(state), profile_of(state)
    dest = form.destination
    start, end = form.start_date.isoformat(), form.end_date.isoformat()
    calls = place_calls(
        {"destination": dest}, profile.purposes, form.interests, profile.required_needs
    )
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
                **({"needs": profile.required_needs} if profile.required_needs else {}),
            },
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
    for (name, _args), data in zip(calls, results, strict=True):
        if name == "find_places":
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

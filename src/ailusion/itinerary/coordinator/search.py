"""Làn Coordinator — bước 5: gọi tool tìm chỗ chơi, quán ăn, chỗ ở, lễ hội, thời tiết, thiên tai.

Dữ liệu đã lưu (Postgres qua MCP, không tốn quota) gọi trước, song song. Nguồn tốn quota chỉ gọi
khi dữ liệu đã lưu thiếu:
- `search_events` (Tavily) khi DB không có lễ hội nào trong ngày đi;
- `search_hotels` (SerpApi) khi DB có dưới `ENOUGH_PRICED_STAYS` chỗ ở có giá;
- `search_places` (SerpApi) khi DB và vùng quanh toạ độ vẫn dưới `ENOUGH_VISITS` chỗ chơi.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..shared.context import Ctx
from ..shared.form import PURPOSE_KEYWORDS
from ..shared.models import ItineraryState, Place, dump_places, form_of, profile_of
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
        ("browse_places", {**where, "category": "Điểm thu hút khách du lịch", "limit": 30}),
        ("browse_places", {**where, "category": "Nhà hàng", "limit": 30}),
    ]
    for purpose in purposes:
        for word in PURPOSE_KEYWORDS[purpose]:
            calls.append(("browse_places", {**where, "query": word, "limit": 15}))
    for word in [w.strip() for w in interests.split(",") if w.strip()][:3]:
        calls.append(("browse_places", {**where, "query": word, "limit": 15}))
    if needs:
        calls.append(("find_places_by_needs", {**where, "needs": needs, "limit": 30}))
    return calls


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
    alerts: list[str] = []
    for (name, _args), data in zip(calls, results, strict=True):
        if name in ("browse_places", "find_places_by_needs"):
            add_places(places, data, name)
        elif name in ("find_stays", "search_hotels"):
            add_stays(stays, data, name)
        elif name == "weather_forecast" and tool_ok(data):
            weather = data.get("days") or []
        elif name in ("find_festivals", "search_events") and tool_ok(data):
            for f in data.get("festivals") or data.get("events") or []:
                if f.get("name") or f.get("title"):
                    festivals.append(f.get("name") or f.get("title"))
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
    center = state.get("center")
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
        more, more_logs = await ctx.many(
            [
                ("search_places", {"query": f"điểm tham quan nổi tiếng {dest}", "limit": 20}),
                ("search_places", {"query": f"quán ăn ngon {dest}", "limit": 20}),
            ]
        )
        for data in more:
            add_places(places, data, "search_places")
        logs += more_logs
    n_visit = sum(p.kind == "visit" for p in places.values())
    n_meal = sum(p.kind == "meal" for p in places.values())
    await ctx.log("tim_kiem", "finding", f"{n_visit} chỗ chơi, {n_meal} quán, {len(stays)} chỗ ở")
    return {
        "places": dump_places(places),
        "stays": dump_places(stays),
        "weather": weather,
        "festivals": list(dict.fromkeys(festivals))[:8],
        "alerts": alerts,
        "tool_log": logs,
    }

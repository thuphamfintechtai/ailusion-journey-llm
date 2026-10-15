"""Làn Coordinator — bước 1, 3, 4: nhận form, phân nhóm khách bằng luật, hỏi lại.

- Bước 3 (`phan_nhom`): `profile_trip` chạy 4 phần theo thứ tự: nhu cầu theo người đi cùng → giờ
  rảnh từng ngày → mục đích → nhóm khách P1–P4 (luật B0 → B4). Không gọi LLM.
- Bước 4 (`hoi_lai`): `questions_for` gom tối đa 3 câu còn thiếu, mỗi câu kèm nút bấm; còn câu
  hỏi thì pipeline dừng, user trả lời rồi gửi lại form.
"""

from __future__ import annotations

import statistics
from typing import Any

from ..shared.context import Ctx
from ..shared.form import (
    BUSY_TRAVEL_MIN,
    CHECKIN_MIN,
    DEFAULT_ARRIVAL,
    DEFAULT_DEPARTURE,
    DEFAULT_NIGHT_RETURN,
    DEFAULT_WAKE,
    DEPARTURE_BUFFER_MIN,
    GROUP_NAMES,
    HUB_EXIT_MIN,
    HUB_TRAVEL_MIN,
    LOW_BUDGET_RATIO,
    LOW_FREE_MINUTES,
    PRIORITY_GROUP,
    SORT_ORDER,
    TABLE_A,
    TripForm,
    clock,
    daily_budget,
    hhmm,
    to_minutes,
    trip_budget,
)
from ..shared.models import DayWindow, ItineraryState, TripProfile, form_of, profile_of
from ..shared.tools import tool_ok


def base_daily_cost(stay_prices: list[int], meal_prices: list[int]) -> int | None:
    """Chi phí tối thiểu 1 người 1 ngày = giá phòng phổ biến ÷ 2 + 3 × giá 1 bữa phổ biến.

    Tiền đi lại chưa tính (chưa có nguồn); trung vị lấy từ chính dữ liệu tool của điểm đến.
    """
    if not stay_prices or not meal_prices:
        return None
    return int(statistics.median(stay_prices) / 2 + 3 * statistics.median(meal_prices))


def profile_trip(
    form: TripForm,
    stay_prices: list[int],
    meal_prices: list[int],
    travel_to: dict[str, int] | None = None,
) -> TripProfile:
    """`travel_to`: phút đi đã hỏi get_route, khoá "hub" (bến -> chỗ ở), "busy:<ngày>"."""
    travel_to = travel_to or {}
    guess: list[str] = []

    # Phần 1 — nhu cầu theo người đi cùng (bảng A)
    rows = []
    if form.kids_under_6:
        rows.append(TABLE_A[0])
    if form.kids_6_12:
        rows.append(TABLE_A[1])
    if form.seniors_65:
        rows.append(TABLE_A[2])
    if form.wheelchair_users:
        rows.append(TABLE_A[3])
    if form.group_type == "couple":
        rows.append(TABLE_A[4])
    if form.group_type in ("friends", "colleagues"):
        rows.append(TABLE_A[5])
    if form.group_type == "solo":
        rows.append(TABLE_A[6])
    needs_care = bool(form.kids_under_6 or form.seniors_65 or form.wheelchair_users)
    required = list(dict.fromkeys([*form.required_needs, *(n for r in rows for n in r[2])]))
    nice = list(dict.fromkeys(n for r in rows for n in r[3] if n not in required))
    # Giờ về buổi tối chỉ theo user điền; không tự đặt luật về sớm (kể cả đoàn có trẻ nhỏ: cột
    # "Tối về trước" của bảng A không còn áp).
    night_return = clock(form.night_return_time) or DEFAULT_NIGHT_RETURN

    # Phần 2 — giờ rảnh từng ngày
    days: list[DayWindow] = []
    hub = travel_to.get("hub", HUB_TRAVEL_MIN)
    for d in range(1, form.num_days + 1):
        day_guess: list[str] = []
        wake = clock(form.wake_time) or DEFAULT_WAKE
        if d == 1:
            arrival = clock(form.arrival_time)
            if arrival is None:
                arrival = DEFAULT_ARRIVAL
                day_guess.append(f"Ngày 1 bắt đầu {DEFAULT_ARRIVAL}")
                start = to_minutes(arrival)
            else:
                start = to_minutes(arrival) + HUB_EXIT_MIN + hub
            start = max(start, to_minutes(wake))
        else:
            start = to_minutes(wake)
        end = to_minutes(night_return) if not form.night_out else 23 * 60 + 30
        if d == form.num_days:
            if form.checkin_close_time:
                end = min(end, to_minutes(form.checkin_close_time) - hub)
            else:
                departure = clock(form.departure_time)
                if departure is None:
                    departure = DEFAULT_DEPARTURE
                    day_guess.append(f"Ngày {d} về lúc {DEFAULT_DEPARTURE}")
                end = min(end, to_minutes(departure) - CHECKIN_MIN - DEPARTURE_BUFFER_MIN - hub)
        blocked, meal_only = [], []
        busy_minutes = 0
        for slot in form.busy_slots:
            # Chưa có giờ bắt đầu thì chưa chặn được gì — bước 4 hỏi lại (H5).
            if slot.day != d or slot.one_person or not slot.busy_start:
                continue
            go = travel_to.get(f"busy:{d}", BUSY_TRAVEL_MIN)
            b_start = max(start, to_minutes(slot.busy_start) - go)
            if slot.busy_end:
                b_end = to_minutes(slot.busy_end)
                blocked.append(f"{hhmm(b_start)}–{hhmm(b_end)}")
                busy_minutes += max(0, min(b_end, end) - b_start)
            else:
                meal_only.append(f"{hhmm(b_start)}–{hhmm(end)}")
                busy_minutes += max(0, end - b_start)
        free = max(0, end - start - busy_minutes)
        days.append(
            DayWindow(
                day=d,
                day_start=hhmm(start),
                day_end=hhmm(max(start, end)),
                blocked_slots=blocked,
                meal_only_slots=meal_only,
                free_minutes=free,
                guess=day_guess,
            )
        )
        guess += day_guess

    # Phần 3 — mục đích
    purposes = list(form.purposes[:2])
    main = purposes[0] if purposes else None
    max_effort = form.walking_level if "adventure" in purposes else None
    if max_effort and needs_care:
        max_effort = "M1"

    # Phần 4 — nhóm khách (B0 → B4, dòng đúng đầu tiên)
    base = base_daily_cost(stay_prices, meal_prices)
    per_day = daily_budget(form)
    low_days = sum(d.free_minutes < LOW_FREE_MINUTES for d in days)
    if form.priority:
        group, rule = PRIORITY_GROUP[form.priority], "B0"
    elif low_days * 2 > len(days) or (form.num_days <= 2 and main not in ("relax", "adventure")):
        group, rule = "P4", "B1"
    elif needs_care:
        group, rule = "P2", "B2"
    elif per_day is not None and base is not None and per_day < LOW_BUDGET_RATIO * base:
        group, rule = "P1", "B3"
    else:
        group, rule = "P3", "B4"

    profile = TripProfile(
        needs_care=needs_care,
        required_needs=required,
        nice_to_have_needs=nice,
        night_return_time=night_return,
        days=days,
        purposes=purposes,
        main_purpose=main,
        max_effort=max_effort,
        group=group,
        rule=rule,
        sort_order=SORT_ORDER[group],
        daily_budget=per_day,
        trip_budget=trip_budget(form),
        base_daily_cost=base,
        guess=guess,
    )
    profile.questions = questions_for(form, profile)
    return profile


QUESTIONS: dict[str, dict[str, Any]] = {
    "H1": {
        "text": "Bạn đi cùng ai?",
        "field": "group_type",
        "choices": [
            ["Một mình", {"group_type": "solo"}],
            ["Cặp đôi", {"group_type": "couple"}],
            ["Bạn bè", {"group_type": "friends"}],
            ["Đồng nghiệp", {"group_type": "colleagues"}],
            ["Gia đình có trẻ nhỏ", {"group_type": "family", "kids_under_6": 1}],
            ["Có người lớn tuổi", {"group_type": "family", "seniors_65": 1}],
        ],
    },
    "H5": {"text": "Giờ bận là mấy giờ, xong lúc mấy giờ?", "field": "busy_slots", "choices": []},
    "H4": {
        "text": "Bạn đi bộ được tầm nào?",
        "field": "walking_level",
        "choices": [
            ["Dạo 1–2 tiếng", {"walking_level": "M1"}],
            ["3 tiếng, có dốc", {"walking_level": "M2"}],
            ["4–5 tiếng leo dốc", {"walking_level": "M3"}],
            ["Cả ngày", {"walking_level": "M4"}],
        ],
    },
    "H3": {
        "text": "Ngân sách khoảng bao nhiêu cho cả đoàn?",
        "field": "budget",
        "choices": [
            ["Dưới 3 triệu", {"budget": 3_000_000, "budget_unit": "group_trip"}],
            ["3–6 triệu", {"budget": 6_000_000, "budget_unit": "group_trip"}],
            ["6–10 triệu", {"budget": 10_000_000, "budget_unit": "group_trip"}],
            ["Trên 10 triệu", {"budget": 15_000_000, "budget_unit": "group_trip"}],
        ],
    },
    "H2": {
        "text": "Chuyến này bạn muốn làm gì?",
        "field": "purposes",
        "choices": [
            ["Nghỉ dưỡng", {"purposes": ["relax"]}],
            ["Ẩm thực", {"purposes": ["food"]}],
            ["Khám phá, văn hoá", {"purposes": ["culture"]}],
            ["Vui chơi, sự kiện", {"purposes": ["fun"]}],
            ["Phiêu lưu", {"purposes": ["adventure"]}],
            ["Mua sắm", {"purposes": ["shopping"]}],
        ],
    },
}


MAX_QUESTIONS = 3


def questions_for(form: TripForm, profile: TripProfile) -> list[str]:
    """Mã câu hỏi còn thiếu, theo thứ tự ưu tiên của bước 4, tối đa 3 câu."""
    out = []
    family_without_ages = form.group_type == "family" and not (
        form.kids_under_6 or form.kids_6_12 or form.seniors_65
    )
    if form.group_type is None or family_without_ages:
        out.append("H1")
    if any(not s.busy_start for s in form.busy_slots):
        out.append("H5")
    if profile.main_purpose == "adventure" and not form.walking_level:
        out.append("H4")
    if not form.budget:
        out.append("H3")
    if form.num_days >= 3 and not form.purposes and not form.interests.strip():
        out.append("H2")
    return out[:MAX_QUESTIONS]


async def nhan_form(state: ItineraryState, ctx: Ctx) -> dict:
    form = form_of(state)
    await ctx.log(
        "nhan_form",
        "step",
        f"{form.destination}, {form.num_days} ngày, {form.group_size} người",
    )
    return {"notes": [], "llm_calls": 0}


async def phan_nhom(state: ItineraryState, ctx: Ctx) -> dict:
    form = form_of(state)
    await ctx.log("phan_nhom", "step", "Phân nhóm khách bằng luật, tính giờ rảnh từng ngày")
    (geo, stays_probe, food_probe), logs = await ctx.many(
        [
            ("geocode_vn", {"address": form.destination}),
            (
                "find_stays",
                {"destination": form.destination, "sort_by": "popular", "limit": 20},
            ),
            (
                "find_places",
                {"destination": form.destination, "category": "Nhà hàng", "limit": 30},
            ),
        ]
    )
    top = (geo.get("results") or [{}])[0] if tool_ok(geo) else {}
    center = [top["lat"], top["lng"]] if top.get("lat") is not None else None
    stay_prices = [
        int(s["price_per_night_vnd"])
        for s in (stays_probe.get("stays") or [] if tool_ok(stays_probe) else [])
        if s.get("price_per_night_vnd")
    ]
    meal_prices = [
        int(p["price_vnd"]["vnd"])
        for p in (food_probe.get("places") or [] if tool_ok(food_probe) else [])
        if isinstance(p.get("price_vnd"), dict) and p["price_vnd"].get("vnd")
    ]
    profile = profile_trip(form, stay_prices, meal_prices)
    text = (
        f"Nhóm {profile.group} {GROUP_NAMES[profile.group]} (luật {profile.rule}); rảnh "
        + " / ".join(str(d.free_minutes) for d in profile.days)
        + " phút"
    )
    await ctx.log("phan_nhom", "plan", text)
    return {"profile": profile.model_dump(mode="json"), "center": center, "tool_log": logs}


def after_profile(state: ItineraryState) -> str:
    return "hoi_lai" if profile_of(state).questions else "tim_kiem"


async def hoi_lai(state: ItineraryState, ctx: Ctx) -> dict:
    await ctx.log("hoi_lai", "finding", "Còn thiếu ý quan trọng, hỏi lại user 1 lượt")
    return {}

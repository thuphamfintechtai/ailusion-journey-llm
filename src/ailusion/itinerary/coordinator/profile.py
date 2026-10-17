"""Làn Coordinator — bước 1, 3, 4: nhận form, phân nhóm khách bằng luật, hỏi lại.

- Bước 3 (`phan_nhom`): `profile_trip` chạy 4 phần theo thứ tự: nhu cầu theo người đi cùng → giờ
  rảnh từng ngày → mục đích → nhóm khách P1–P4 (luật B0 → B4). Không gọi LLM.
- Bước 4 (`hoi_lai`): `questions_for` gom tối đa 3 câu còn thiếu, mỗi câu kèm nút bấm; còn câu
  hỏi thì pipeline dừng, user trả lời rồi gửi lại form.
"""

from __future__ import annotations

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
    EVENING_RIDE_BACK,
    GROUP_NAMES,
    HUB_EXIT_MIN,
    HUB_TRAVEL_MIN,
    LOW_FREE_MINUTES,
    MUST_EVENING_STAY,
    NAP_IF_END_FROM,
    NAP_IF_START_BY,
    NAP_MIN,
    PRIORITY_GROUP,
    SORT_ORDER,
    TABLE_A,
    TripForm,
    clock,
    daily_budget,
    hhmm,
    must_window,
    to_minutes,
    trip_budget,
)
from ..shared.models import DayWindow, ItineraryState, TripProfile, form_of, profile_of
from ..shared.tools import tool_ok
from .levels import budget_level, budget_text, price_levels


def profile_trip(
    form: TripForm,
    price_levels: dict[str, dict[str, int]] | None = None,
    travel_to: dict[str, int] | None = None,
) -> TripProfile:
    """`price_levels`: mặt bằng giá của điểm đến (coordinator/levels.py). `travel_to`: phút đi đã
    hỏi get_route, khoá "hub" (bến -> chỗ ở), "busy:<ngày>"."""
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
        # Giờ về là giờ mặc định (user chưa nhập) mà user hẹn tới một chỗ lúc tối muộn hơn: kéo
        # ngày tới sau giờ hẹn đó, không thì chính chỗ user chọn thành "về quá giờ".
        if not form.night_return_time and not form.night_out:
            for m in form.must_visits:
                span = must_window(m.time) if m.day in (None, d) else None
                if span and span[0] >= end - MUST_EVENING_STAY:
                    end = max(end, min(23 * 60 + 30, span[0] + MUST_EVENING_STAY))
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
            # Bận hẳn ngoài giờ của ngày (bận 07:00–09:00 mà tới nơi buổi chiều): không chặn gì —
            # trước đây ra khung ngược "14:45–09:00" đưa Planner.
            if b_start >= end or (slot.busy_end and to_minutes(slot.busy_end) <= start):
                continue
            if slot.busy_end:
                b_end = to_minutes(slot.busy_end)
                blocked.append(f"{hhmm(b_start)}–{hhmm(b_end)}")
                busy_minutes += max(0, min(b_end, end) - b_start)
            else:
                meal_only.append(f"{hhmm(b_start)}–{hhmm(end)}")
                busy_minutes += max(0, end - b_start)
        nap = (
            form.nap != "none"
            and start <= to_minutes(NAP_IF_START_BY)
            and end >= to_minutes(NAP_IF_END_FROM)
        )
        free = max(0, end - start - busy_minutes - (NAP_MIN if nap else 0))
        days.append(
            DayWindow(
                day=d,
                day_start=hhmm(start),
                day_end=hhmm(max(start, end)),
                blocked_slots=blocked,
                meal_only_slots=meal_only,
                nap=nap,
                nap_at=form.nap,
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
    per_day = daily_budget(form)
    low_days = sum(d.free_minutes < LOW_FREE_MINUTES for d in days)
    if form.priority:
        group, rule = PRIORITY_GROUP[form.priority], "B0"
    elif low_days * 2 > len(days) or (form.num_days <= 2 and main not in ("relax", "adventure")):
        group, rule = "P4", "B1"
    elif needs_care:
        group, rule = "P2", "B2"
    # B3 (ngân sách thấp -> "tiết kiệm") đã bỏ: có ngân sách thì bước ghép bám ±5% ngân sách;
    # xếp nhóm tiết kiệm thì lịch rẻ lên đầu, cả chuyến tiêu thấp hơn ngân sách cả 35%.
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
        price_levels=price_levels,
        guess=guess,
    )
    profile.questions = questions_for(form, profile)
    return profile


def time_notes(form: TripForm, profile: TripProfile) -> list[str]:
    """Ghi chú khi giờ user nhập va nhau mà vẫn lập được: lịch bận ngày cuối kéo quá giờ phải
    rời chỗ ở để kịp chuyến về (bận 11:00–13:00 mà phải rời chỗ ở 12:30)."""
    last = profile.days[-1]
    out = []
    for slot in form.busy_slots:
        if slot.day != last.day or slot.one_person or not slot.busy_start or not slot.busy_end:
            continue
        if to_minutes(slot.busy_end) > to_minutes(last.day_end):
            out.append(
                f"Lịch bận ngày {last.day} ({slot.busy_start}–{slot.busy_end}) kết thúc sau giờ "
                f"phải rời chỗ ở ({last.day_end}) để kịp chuyến về: lịch ngày này chỉ xếp tới "
                "trước giờ bận — kiểm lại giờ bận hoặc giờ về."
            )
    return out


QUESTIONS: dict[str, dict[str, Any]] = {
    "H1": {
        "text": "Bạn đi cùng ai?",
        "field": "group_type",
        "choices": [
            ["Một mình", {"group_type": "solo"}],
            ["Cặp đôi", {"group_type": "couple"}],
            ["Bạn bè", {"group_type": "friends"}],
            ["Đồng nghiệp", {"group_type": "colleagues"}],
            ["Gia đình (người lớn)", {"group_type": "family"}],
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
            # Khoảng user chọn là khoảng ngân sách (budget_min–budget), không gán 1 số user không
            # nói rồi bám ±5% quanh nó ("Trên 10 triệu" từng thành đúng 15 triệu).
            ["Dưới 3 triệu", {"budget": 3_000_000, "budget_min": 0, "budget_unit": "group_trip"}],
            [
                "3–6 triệu",
                {"budget": 6_000_000, "budget_min": 3_000_000, "budget_unit": "group_trip"},
            ],
            [
                "6–10 triệu",
                {"budget": 10_000_000, "budget_min": 6_000_000, "budget_unit": "group_trip"},
            ],
            [
                "Trên 10 triệu",
                {"budget": None, "budget_min": 10_000_000, "budget_unit": "group_trip"},
            ],
        ],
    },
    "H6": {
        "text": "Buổi tối bạn thường đi chơi tới mấy giờ rồi mới về chỗ ở?",
        "field": "night_return_time",
        # Đi chơi tới H giờ -> về tới chỗ ở trước H giờ + EVENING_RIDE_BACK phút (đường về).
        "choices": [
            [f"Khoảng {h}h", {"night_return_time": f"{h}:{EVENING_RIDE_BACK:02d}"}]
            for h in (19, 20, 21, 22)
        ]
        + [
            ["Đi khuya, sau 23h", {"night_out": True}],
            ["Chưa biết (để 20h)", {"night_return_time": DEFAULT_NIGHT_RETURN}],
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
    # Chỉ hỏi khi chưa biết đi cùng ai. "Gia đình" mà không có trẻ em / người lớn tuổi là gia đình
    # toàn người lớn: form đã có ô số trẻ em, người lớn tuổi (để 0 = không có), hỏi lại thì user
    # chọn "Gia đình" xong vẫn bị hỏi "đi cùng ai".
    if form.group_type is None:
        out.append("H1")
    if any(not s.busy_start for s in form.busy_slots):
        out.append("H5")
    if profile.main_purpose == "adventure" and not form.walking_level:
        out.append("H4")
    if not form.budget and not form.budget_min:
        out.append("H3")
    if form.num_days >= 3 and not form.purposes and not form.interests.strip():
        out.append("H2")
    # Buổi tối đi tới mấy giờ: không đoán, hỏi user (chọn "chưa biết" thì 20:00).
    if not form.night_return_time and not form.night_out:
        out.append("H6")
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
    # Mặt bằng giá của cả điểm đến (`price_stats_vnd`, tính trước khi cắt `limit`): chỉ cần số
    # thống kê, không cần danh sách nên lấy 1 chỗ.
    (geo, stays_probe, food_probe), logs = await ctx.many(
        [
            ("geocode_vn", {"address": form.destination}),
            ("find_stays", {"destination": form.destination, "limit": 1}),
            ("find_places", {"destination": form.destination, "category": "Nhà hàng", "limit": 1}),
        ]
    )
    top = (geo.get("results") or [{}])[0] if tool_ok(geo) else {}
    center = [top["lat"], top["lng"]] if top.get("lat") is not None else None
    levels = price_levels(
        stays_probe if tool_ok(stays_probe) else {}, food_probe if tool_ok(food_probe) else {}
    )
    profile = profile_trip(form, levels)
    text = (
        f"Nhóm {profile.group} {GROUP_NAMES[profile.group]} (luật {profile.rule}); rảnh "
        + " / ".join(str(d.free_minutes) for d in profile.days)
        + " phút"
    )
    await ctx.log("phan_nhom", "plan", text)
    # Ngân sách theo "mỗi người" / có vé đã mua: ghi số cả đoàn cả chuyến máy dùng để so (bấm nhầm
    # "mỗi người mỗi ngày" thì 6 triệu thành 36 triệu, user thấy ngay khi đang chờ).
    if (form.budget_unit != "group_trip" or form.prepaid_tickets) and (b := budget_text(form)):
        await ctx.log("phan_nhom", "finding", f"Ngân sách: {b}")
    # Báo sớm (trước khi lập lịch) khi ngân sách thấp so với mặt bằng giá của điểm đến.
    level = budget_level(form, profile)
    if level and level.get("log"):
        await ctx.log("phan_nhom", "finding", level["log"])
    notes = time_notes(form, profile)
    for note in notes:
        await ctx.log("phan_nhom", "finding", note)
    return {
        "profile": profile.model_dump(mode="json"),
        "center": center,
        "tool_log": logs,
        "notes": [*(state.get("notes") or []), *notes],
    }


def after_profile(state: ItineraryState) -> str:
    return "hoi_lai" if profile_of(state).questions else "tim_kiem"


async def hoi_lai(state: ItineraryState, ctx: Ctx) -> dict:
    await ctx.log("hoi_lai", "finding", "Còn thiếu ý quan trọng, hỏi lại user 1 lượt")
    return {}

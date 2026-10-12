"""Bước 17 — kết quả trả cho user.

- `ItineraryResponse` (JSON cho web): tối đa 3 phương án, mỗi cái hơn 2 cái kia ở đâu, đang giả
  định gì. `status` "need_info" = còn câu hỏi (bước 4); "ok" = có phương án, `ask_user` khác None
  khi vượt ngân sách và cần user chọn.
- `to_response`: state cuối của pipeline -> `ItineraryResponse`.
- `request_text` / `itinerary_markdown`: form và lộ trình dạng chữ để nối vào hội thoại chat.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .coordinator.profile import QUESTIONS
from .coordinator.simulate import simulate
from .shared.context import Ctx
from .shared.form import (
    GROUP_NAMES,
    PURPOSE_NAMES,
    TripForm,
    hhmm,
    to_minutes,
)
from .shared.llm import cost_usd
from .shared.models import (
    DayPlan,
    ItineraryState,
    Place,
    Run,
    form_of,
    matrix_of,
    place_from_dict,
    places_of,
    plans_of,
    profile_of,
    weather_text,
)


class Choice(BaseModel):
    label: str
    set: dict[str, Any] = Field(description="Các ô form được điền khi bấm nút này")


class Question(BaseModel):
    id: str
    text: str
    choices: list[Choice] = Field(default_factory=list)


class Item(BaseModel):
    type: Literal["start", "visit", "meal", "back"]
    start: str
    end: str
    name: str
    place_id: str | None = None
    category: str | None = None
    lat: float | None = None
    lng: float | None = None
    price: int | None = Field(None, description="Cả đoàn, VND; None = chưa có giá")
    price_by: str | None = None
    travel_min: int = 0
    wait_min: int = 0
    rating: float | None = None
    rating_floor: float | None = None
    reviews: int = 0
    stay_by: str | None = Field(None, description="google | ước theo loại")
    matches_purpose: bool | None = None


class Day(BaseModel):
    day: int
    date: str
    weekday: str
    plan_id: str
    status: Literal["ok", "risky", "warn", "fail"]
    issues: list[str] = Field(default_factory=list)
    unchecked: list[str] = Field(default_factory=list)
    beaten_by: str | None = None
    metrics: dict[str, float | None]
    items: list[Item]
    weather: str | None = None
    maps_url: str | None = None


class Option(BaseModel):
    id: str
    name: str
    better_at: list[str] = Field(default_factory=list)
    explanation: str = ""
    total_cost: int | None = Field(None, description="Cả đoàn cả chuyến; None = chưa đủ giá")
    estimated_prices: list[str] = Field(
        default_factory=list, description="Chỗ trong phương án đang dùng giá ước (chưa có giá thật)"
    )
    days: list[Day]


class Stay(BaseModel):
    name: str
    price_per_night: int | None = None
    total: int | None = None
    rating: float | None = None
    lat: float | None = None
    lng: float | None = None
    booked: bool = False


class DayWindowOut(BaseModel):
    day: int
    day_start: str
    day_end: str
    free_minutes: int
    blocked_slots: list[str] = Field(default_factory=list)
    meal_only_slots: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    group: str
    group_name: str
    rule: str
    purposes: list[str]
    needs_care: bool
    required_needs: list[str]
    nice_to_have_needs: list[str]
    daily_budget: int | None
    trip_budget: int | None
    days: list[DayWindowOut]


class Stats(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    seconds: float = 0


class ItineraryResponse(BaseModel):
    thread_id: str
    status: Literal["ok", "need_info"]
    destination: str
    start_date: str
    days: int
    questions: list[Question] = Field(default_factory=list)
    profile: Profile | None = None
    stay: Stay | None = None
    options: list[Option] = Field(default_factory=list)
    chosen: str | None = None
    why: str = ""
    warnings: list[dict[str, str]] = Field(default_factory=list)
    ask_user: dict[str, Any] | None = None
    guesses: list[str] = Field(default_factory=list, description="Đang giả định vì user chưa nói")
    understood: list[dict[str, str]] = Field(
        default_factory=list, description="Ý lấy từ lời nhắn, kèm câu trích (bước 2)"
    )
    notes: list[str] = Field(default_factory=list, description="Chỗ bị bỏ, chưa xác minh...")
    festivals: list[str] = Field(default_factory=list)
    stats: Stats = Field(default_factory=Stats)


WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def to_response(state: dict[str, Any], thread_id: str, seconds: float) -> ItineraryResponse:
    form, profile = form_of(state), profile_of(state)
    usage = state.get("usage") or []
    tool_log = state.get("tool_log") or []
    stats = Stats(
        llm_calls=state.get("llm_calls", 0),
        tool_calls=len(tool_log),
        tool_errors=sum(not t["ok"] for t in tool_log),
        tokens_in=sum(u["in"] for u in usage),
        tokens_out=sum(u["out"] for u in usage),
        cost_usd=cost_usd(usage),
        seconds=round(seconds, 1),
    )
    prof = Profile(
        group=profile.group,
        group_name=GROUP_NAMES[profile.group],
        rule=profile.rule,
        purposes=profile.purposes,
        needs_care=profile.needs_care,
        required_needs=profile.required_needs,
        nice_to_have_needs=profile.nice_to_have_needs,
        daily_budget=profile.daily_budget,
        trip_budget=profile.trip_budget,
        days=[
            DayWindowOut(
                **d.model_dump(
                    include={
                        "day",
                        "day_start",
                        "day_end",
                        "free_minutes",
                        "blocked_slots",
                        "meal_only_slots",
                    }
                )
            )
            for d in profile.days
        ],
    )
    base = {
        "thread_id": thread_id,
        "destination": form.destination,
        "start_date": form.start_date.isoformat(),
        "days": form.num_days,
        "profile": prof,
        "guesses": profile.guess,
        "understood": state.get("understood") or [],
        "stats": stats,
    }
    if profile.questions and not state.get("trip"):
        questions = [
            Question(
                id=q,
                text=QUESTIONS[q]["text"],
                choices=[Choice(label=c[0], set=c[1]) for c in QUESTIONS[q]["choices"]],
            )
            for q in profile.questions
        ]
        return ItineraryResponse(status="need_info", questions=questions, **base)

    places = places_of(state)
    plans = plans_of(state)
    matrix = matrix_of(state)
    windows = {d.day: d for d in profile.days}
    decision = state.get("decision") or {}
    stay_d = state.get("stay")
    options = []
    for i, o in enumerate(state["trip"]["options"]):
        days = []
        for pid in o["days"]:
            p = plans[pid]
            run = simulate(
                p, places, matrix, windows[p.day], profile.required_needs, long_stay=False
            )
            days.append(_day(p, run, places, form, state, i == 0))
        options.append(
            Option(
                id=o["id"],
                name=f"Phương án {i + 1}",
                better_at=o["better_at"],
                explanation=(decision.get("explanation") or {}).get(o["id"], ""),
                total_cost=o["total_cost"],
                estimated_prices=list(
                    dict.fromkeys(
                        places[s].name
                        for pid in o["days"]
                        for s in plans[pid].stops
                        if (places[s].price_by or "").startswith("ước")
                    )
                ),
                days=days,
            )
        )
    stay = None
    if stay_d:
        s = place_from_dict(stay_d)
        stay = Stay(
            name=s.name,
            price_per_night=s.price,
            total=state["trip"].get("stay_cost"),
            rating=s.rating,
            lat=s.lat,
            lng=s.lng,
            booked=bool(form.stay_booked),
        )
    notes = list(state.get("notes") or [])
    if state.get("alerts"):
        notes.append("Cảnh báo thiên tai: " + "; ".join(state["alerts"]))
    verify = [
        f"{p.name}: {v}"
        for p in places.values()
        for v in p.to_verify
        if p.place_id
        in {s for o in options for d in o.days for s in [it.place_id for it in d.items] if s}
    ]
    notes += verify[:5]
    return ItineraryResponse(
        status="ok",
        stay=stay,
        options=options,
        chosen=decision.get("chosen"),
        why=decision.get("why", ""),
        warnings=[
            {"id": w["id"], "decision": w["decision"]} for w in decision.get("warnings") or []
        ],
        ask_user=state["trip"].get("ask_user"),
        notes=notes,
        festivals=state.get("festivals") or [],
        **base,
    )


def _day(
    plan: DayPlan,
    run: Run,
    places: dict[str, Place],
    form: TripForm,
    state: dict[str, Any],
    first_option: bool,
) -> Day:
    date = form.day_date(plan.day)
    stay_name = (state.get("stay") or {}).get("name") or "chỗ ở"
    items = [
        Item(
            type="start",
            start=plan.leave_stay,
            end=plan.leave_stay,
            name=f"Xuất phát từ {stay_name}",
        )
    ]
    for s in run.stops:
        p = places[s.place_id]
        items.append(
            Item(
                type="meal" if p.kind == "meal" else "visit",
                start=hhmm(s.start),
                end=hhmm(s.leave),
                name=p.name,
                place_id=p.place_id,
                category=p.category,
                lat=p.lat,
                lng=p.lng,
                price=p.price * form.group_size if p.price is not None else None,
                price_by=p.price_by,
                travel_min=s.travel,
                wait_min=s.wait,
                rating=p.rating,
                rating_floor=p.rating_floor,
                reviews=p.reviews,
                stay_by=p.stay_by,
                matches_purpose=p.matches_purpose,
            )
        )
    last_travel = run.back - (run.stops[-1].leave if run.stops else to_minutes(plan.leave_stay))
    items.append(
        Item(
            type="back",
            start=hhmm(run.back),
            end=hhmm(run.back),
            name="Về chỗ ở",
            travel_min=int(last_travel),
        )
    )
    return Day(
        day=plan.day,
        date=date.isoformat(),
        weekday=WEEKDAYS[date.weekday()],
        plan_id=plan.id,
        status=plan.status,
        issues=plan.issues,
        unchecked=plan.unchecked,
        beaten_by=plan.beaten_by,
        metrics=plan.metrics,
        items=items,
        weather=weather_text(state, plan.day),
        maps_url=(state.get("maps") or {}).get(plan.id),
    )


async def tra_ket_qua(state: ItineraryState, ctx: Ctx) -> dict:
    await ctx.log("tra_ket_qua", "answer", "Trả 3 phương án")
    return {}


ICONS = {"visit": "📍", "meal": "🍜"}


def request_text(form: TripForm) -> str:
    party = [f"{form.group_size} người"]
    if form.kids_under_6:
        party.append(f"{form.kids_under_6} trẻ dưới 6 tuổi")
    if form.kids_6_12:
        party.append(f"{form.kids_6_12} trẻ 6–12 tuổi")
    if form.seniors_65:
        party.append(f"{form.seniors_65} người từ 65 tuổi")
    lines = [
        f"Lập lộ trình: {form.destination}, {form.num_days} ngày "
        f"({form.start_date:%d/%m}–{form.end_date:%d/%m}), {', '.join(party)}."
    ]
    if form.budget:
        unit = {
            "group_trip": "cả đoàn cả chuyến",
            "person_trip": "mỗi người cả chuyến",
            "person_day": "mỗi người mỗi ngày",
        }[form.budget_unit]
        lines.append(f"Ngân sách {_money(form.budget)} {unit}.")
    if form.purposes:
        lines.append("Mục đích: " + ", ".join(PURPOSE_NAMES[p] for p in form.purposes) + ".")
    if form.message.strip():
        lines.append(f"Lời nhắn: {form.message.strip()}")
    return "\n".join(lines)


def itinerary_markdown(resp: ItineraryResponse) -> str:
    if resp.status == "need_info":
        return "Mình cần thêm vài thông tin: " + " ".join(q.text for q in resp.questions)
    chosen = next((o for o in resp.options if o.id == resp.chosen), resp.options[0])
    group = resp.profile.group if resp.profile else ""
    lines = [
        f"**Lộ trình {resp.destination} {resp.days} ngày** · nhóm {group} "
        f"{GROUP_NAMES.get(group, '')} · đề xuất **{chosen.name}**",
    ]
    if resp.why:
        lines += ["", resp.why]
    if resp.stay:
        lines += [
            "",
            f"🏨 **Chỗ ở:** {resp.stay.name}"
            + (f" · {_money(resp.stay.price_per_night)}/đêm" if resp.stay.price_per_night else ""),
        ]
    for day in chosen.days:
        lines += ["", f"**Ngày {day.day} · {day.weekday} {day.date[8:10]}/{day.date[5:7]}**"]
        for item in day.items:
            if item.type in ("visit", "meal"):
                price = f" · {_money(item.price)}" if item.price else ""
                lines.append(f"- {item.start} {ICONS[item.type]} {item.name}{price}")
        if day.issues:
            lines.append(f"- ⚠️ {'; '.join(day.issues)}")
    lines += [
        "",
        f"💰 **Tổng:** {_money(chosen.total_cost) if chosen.total_cost else 'chưa đủ giá'}"
        + (
            f" (gồm giá ước của {len(chosen.estimated_prices)} chỗ chưa có giá thật)"
            if chosen.estimated_prices and chosen.total_cost
            else ""
        ),
    ]
    others = [o for o in resp.options if o.id != chosen.id]
    if others:
        lines += [
            "",
            "Phương án khác: "
            + "; ".join(f"{o.name} ({', '.join(o.better_at) or 'dự phòng'})" for o in others)
            + ".",
        ]
    if resp.guesses:
        lines += ["", "Đang giả định: " + "; ".join(resp.guesses) + "."]
    return "\n".join(lines)


def _money(v: int | None) -> str:
    return f"{int(v or 0):,} đ".replace(",", ".")

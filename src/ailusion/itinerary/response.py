"""Bước 17 — kết quả trả cho user.

- `ItineraryResponse` (JSON cho web): tối đa 3 phương án, mỗi cái hơn 2 cái kia ở đâu, đang giả
  định gì. `status` "need_info" = còn câu hỏi (bước 4); "ok" = có phương án, `ask_user` khác None
  khi vượt ngân sách và cần user chọn.
- `to_response`: state cuối của pipeline -> `ItineraryResponse`. Mỗi chỗ có ảnh, mô tả ngắn và
  tối đa 3 lý do chọn (`why_of`, code ghép từ số đã tính); mỗi ngày có 1 câu về vùng đi
  (`area_note`). Không gọi thêm LLM.
- Số cho các tab (code thuần, xem `tabs.py`, `shared/lunar.py`): chặng đi của từng chỗ
  (`travel_km`, `travel_cost` kèm khoảng `travel_cost_min/max`), giá theo khoảng
  (`price_min/max`), ngày âm, lễ, mức đông, thời tiết theo buổi, chi phí từng ngày (`Day.cost`,
  có chi phí phát sinh và tổng thấp/cao) và cả chuyến (`Option.cost_breakdown` = tổng các ngày),
  hành lý (`packing`).
- `tra_ket_qua` (node bước 17): gọi `get_place_photos` 1 lượt cho các chỗ trong phương án còn
  thiếu ảnh; `get_route` cho từng lịch trong phương án để bản đồ vẽ theo đường phố thật
  (`Day.route_paths`). Tool hỏng thì vẫn trả kết quả, chỉ thiếu ảnh / bản đồ nối thẳng.
- `request_text` / `itinerary_markdown`: form và lộ trình dạng chữ để nối vào hội thoại chat.
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .coordinator.levels import budget_level
from .coordinator.profile import QUESTIONS
from .coordinator.rank import budget_note
from .coordinator.simulate import simulate
from .shared.context import Ctx
from .shared.fares import cars_for, city_of, compare, is_night
from .shared.form import (
    GROUP_NAMES,
    PURPOSE_NAMES,
    TripForm,
    hhmm,
    money,
    must_time_text,
    skipped_meals,
    to_minutes,
)
from .shared.llm import cost_usd
from .shared.lunar import crowd_of, holiday_of, to_lunar
from .shared.math import km
from .shared.models import (
    CATEGORY_GROUPS,
    EVENING_WORDS,
    STAY,
    DayPlan,
    ItineraryState,
    Place,
    Run,
    dump_places,
    form_of,
    matrix_of,
    place_from_dict,
    places_of,
    plans_of,
    profile_of,
    weather_text,
)
from .shared.tools import add_photos, nfc, tool_ok
from .tabs import (
    RAIN_PCT,
    SEATS_PER_VEHICLE,
    day_extras,
    day_weather,
    leg_cost,
    leg_km,
    leg_range,
    packing,
    price_range,
    rental_of,
    ride_alt,
    travel_alt,
    vehicles_of,
)


class Choice(BaseModel):
    label: str
    set: dict[str, Any] = Field(description="Các ô form được điền khi bấm nút này")


class Question(BaseModel):
    id: str
    text: str
    choices: list[Choice] = Field(default_factory=list)


class TravelAlt(BaseModel):
    mode: Literal["motorbike", "car", "taxi", "walk"]
    minutes: int


class Item(BaseModel):
    type: Literal["start", "visit", "meal", "rest", "back"]
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
    image: str | None = Field(None, description="Link ảnh đại diện; None = chưa có ảnh")
    summary: str | None = Field(None, description="Mô tả ngắn chỗ này có gì (≤ 220 ký tự)")
    why: list[str] = Field(default_factory=list, description="Tối đa 3 lý do chọn, code ghép")
    must: bool = Field(False, description="User tự chọn chỗ này (form `must_visits`)")
    wanted: str | None = Field(
        None, description='Thứ user nhắc mà chỗ này đáp ứng (VD "bún bò"): web ghi "Theo yêu cầu"'
    )
    must_time: str | None = Field(
        None, description='Giờ user muốn tới: "HH:MM" | morning | afternoon | evening'
    )
    travel_mode: str | None = Field(None, description="Phương tiện chặng tới đây (id form)")
    travel_km: float | None = Field(None, description="Km chặng từ điểm trước tới đây")
    travel_cost: int | None = Field(
        None, description="Tiền đi chặng này, cả đoàn, VND (ước, số giữa / trung bình các hãng)"
    )
    travel_cost_min: int | None = Field(None, description="Tiền đi chặng thấp nhất (hãng rẻ nhất)")
    travel_cost_max: int | None = Field(None, description="Tiền đi chặng cao nhất (hãng đắt nhất)")
    travel_alt: list[TravelAlt] = Field(
        default_factory=list,
        description="Phút đi chặng này bằng phương tiện khác (ước theo tốc độ phố, không có bus)",
    )
    price_min: int | None = Field(None, description="Cả nhóm; giá ước thì -25%, chưa có giá: None")
    price_max: int | None = Field(None, description="Cả nhóm; giá ước thì +25%, chưa có giá: None")
    price_pp_min: int | None = Field(None, description="Mỗi người, thấp (khoảng giá Google / ước)")
    price_pp_max: int | None = Field(None, description="Mỗi người, cao; None = không rõ mức trần")
    price_note: str | None = Field(
        None, description="Giải thích giá, VD khoảng giá mỗi người, hoặc chợ: không ước được"
    )

    @model_validator(mode="after")
    def _ranges(self) -> Item:
        """Không truyền khoảng thì suy từ số: giá theo `price_range`, tiền đi = (số, số)."""
        if self.price_min is None and self.price_max is None:
            self.price_min, self.price_max = price_range(self.price, self.price_by)
        if self.travel_cost is not None and self.travel_cost_min is None:
            self.travel_cost_min = self.travel_cost_max = self.travel_cost
        return self


class Session(BaseModel):
    part: Literal["morning", "afternoon", "evening"]
    label: Literal["Sáng", "Chiều", "Tối"]
    condition: str
    rain_chance: int | None = None
    icon: Literal["sun", "cloud", "rain", "storm", "fog"]


class ExtraItem(BaseModel):
    """Một khoản phát sinh, cả đoàn, VND (luật trong `tabs.day_extras`)."""

    label: str
    min: int
    max: int
    reason: str


class DayCost(BaseModel):
    """Chi phí một ngày, cả đoàn, VND. `total` = ở + vé + ăn + đi lại + phát sinh (số giữa);
    `total_min` / `total_max` cộng giá thấp / cao từng chỗ, từng chặng và từng khoản phát sinh."""

    stay: int = 0
    tickets: int = 0
    food: int = 0
    transport: int = 0
    extras: int = Field(0, description="Phát sinh, số giữa của `extra_items`")
    rental: int = Field(0, description="Thuê xe tự lái trong ngày (số giữa)")
    rental_min: int = 0
    rental_max: int = 0
    rental_note: str | None = None
    total: int = 0
    total_min: int = 0
    total_max: int = 0
    extra_items: list[ExtraItem] = Field(default_factory=list)


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
    route_paths: list[str] = Field(
        default_factory=list,
        description="Đường xe chạy thật cả ngày (chỗ ở → các chỗ → chỗ ở), polyline mã hoá kiểu "
        "Google theo `route_precision`, nối tiếp nhau; trống = bản đồ nối thẳng các điểm",
    )
    route_precision: int = Field(5, description="Số chữ số thập phân của polyline")
    meal_notes: list[str] = Field(
        default_factory=list,
        description="Bữa không xếp / tuỳ chọn và lý do (VD bắt đầu 10:00 nên bữa sáng tuỳ chọn)",
    )
    area_note: str | None = Field(
        None, description="Một câu về vùng đi của ngày: bán kính, xa nhất"
    )
    lunar: str | None = Field(None, description='Ngày âm lịch "ngày/tháng"')
    holiday: str | None = None
    crowd: Literal["low", "medium", "high"] | None = None
    crowd_note: str | None = None
    temp_min: float | None = None
    temp_max: float | None = None
    sessions: list[Session] = Field(default_factory=list)
    cost: DayCost | None = None
    ride_alt: list[TravelAlt] = Field(
        default_factory=list,
        description="Tổng phút đi lại trong ngày nếu đổi phương tiện (chỉ xe có ở mọi chặng)",
    )


class TransportOption(BaseModel):
    """Tổng tiền đi các chặng cả chuyến nếu gọi xe của một hãng (bảng giá `shared/fares.py`)."""

    brand: str
    service: str
    kind: Literal["car", "bike"]
    vehicles: int = Field(description="Số xe mỗi chặng (xe máy: mỗi khách một xe)")
    total: int


class CostBreakdown(BaseModel):
    """Cả đoàn cả chuyến, VND = tổng `Day.cost` các ngày. Khác `total_cost`: có tiền đi lại,
    không gồm vé đi về đã mua, giá chỗ chơi / quán không làm tròn nghìn."""

    stay: int = 0
    tickets: int = 0
    food: int = 0
    transport: int = 0
    extras: int = 0
    rental: int = Field(0, description="Thuê xe tự lái cả chuyến (số giữa)")
    total: int = 0
    total_min: int = 0
    total_max: int = 0
    budget: int | None = None
    per_person: int = 0
    per_person_min: int = 0
    per_person_max: int = 0
    extra_items: list[ExtraItem] = Field(
        default_factory=list, description="Phát sinh gộp theo khoản qua các ngày"
    )
    extras_pct_min: float = Field(0, description="Phát sinh thấp nhất / chi phí, VD 0.10")
    extras_pct_max: float = Field(0, description="Phát sinh cao nhất / chi phí, VD 0.20")
    extras_notes: list[str] = Field(
        default_factory=list, description="Vì sao cần để dành phát sinh (thuế, mưa, hư xe...)"
    )
    estimated: bool = False
    transport_options: list[TransportOption] = Field(
        default_factory=list, description="So sánh tiền gọi xe cả chuyến theo hãng, rẻ trước"
    )


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
    cost_breakdown: CostBreakdown | None = None


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


class Level(BaseModel):
    """Một mức chi của điểm đến: phòng + ăn cả đoàn cả chuyến (coordinator/levels.py)."""

    key: Literal["saving", "normal", "luxury"]
    label: str
    basis: str = Field(description='Cách tính, VD "trung vị nửa rẻ"')
    stay_night: int
    meal: int
    total: int


class BudgetLevel(BaseModel):
    """Ngân sách so với mặt bằng giá của điểm đến, hiện ở đầu kết quả."""

    kind: Literal["low", "below", "surplus", "unlimited"] = Field(
        description="low: dưới cả mức tiết kiệm; below: dưới mức của mức chi đã chọn; surplus: "
        "ngân sách dư (lịch tiêu dưới khoảng ngân sách, ngân sách trên cả mức sang); unlimited"
    )
    severity: Literal["warn", "info"]
    title: str
    text: str
    style: str = Field(description="Mức chi user chọn (spend_style)")
    target: Literal["normal", "luxury"] | None = Field(
        None, description="Mức của điểm đến đem so với ngân sách; không giới hạn: None"
    )
    budget: int | None = None
    budget_text: str | None = Field(
        None,
        description='Ngân sách quy về cả đoàn cả chuyến, VD "1 triệu mỗi người mỗi ngày × 2 '
        'người × 3 ngày = 6 triệu cả đoàn cả chuyến"',
    )
    levels: list[Level] = Field(default_factory=list)
    scope: str = ""


class Stats(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    seconds: float = 0


class TraceEvent(BaseModel):
    """Một bước trong khung "Quá trình xử lý", lưu cùng kết quả để mở lại vẫn xem được."""

    id: str
    ts: str = ""
    node: str
    kind: str
    text: str = ""
    data: dict[str, str] = Field(default_factory=dict, description="Chỉ giữ tên tool")


class PackItem(BaseModel):
    group: Literal["Giấy tờ", "Quần áo", "Sức khỏe", "Thiết bị"]
    item: str
    reason: str


class ItineraryResponse(BaseModel):
    # Mã để tìm trace khi debug: Langfuse session = thread_id, LangSmith run gốc = trace_run_id.
    thread_id: str
    trace_run_id: str | None = None
    status: Literal["ok", "need_info"]
    destination: str
    start_date: str
    days: int
    group_size: int = Field(1, description="Số người đi")
    travel_party: str | None = Field(
        None, description='VD "2 người · 1 xe máy (2 người/xe)"; không rõ phương tiện: None'
    )
    questions: list[Question] = Field(default_factory=list)
    profile: Profile | None = None
    stay: Stay | None = None
    options: list[Option] = Field(default_factory=list)
    chosen: str | None = None
    why: str = ""
    warnings: list[dict[str, str]] = Field(default_factory=list)
    ask_user: dict[str, Any] | None = None
    budget_level: BudgetLevel | None = Field(
        None, description="Ngân sách thấp so với điểm đến / mức chi không giới hạn"
    )
    guesses: list[str] = Field(default_factory=list, description="Đang giả định vì user chưa nói")
    understood: list[dict[str, str]] = Field(
        default_factory=list, description="Ý lấy từ lời nhắn, kèm câu trích (bước 2)"
    )
    notes: list[str] = Field(default_factory=list, description="Chỗ bị bỏ, chưa xác minh...")
    festivals: list[str] = Field(default_factory=list)
    stats: Stats = Field(default_factory=Stats)
    trace: list[TraceEvent] = Field(default_factory=list, description="Nhật ký từng bước")
    packing: list[PackItem] = Field(default_factory=list, description="Hành lý, theo luật")


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
        "group_size": form.group_size,
        "travel_party": travel_party(form.travel_mode, form.group_size),
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
            days.append(_day(p, run, places, form, state, i == 0, profile.daily_budget, matrix))
        add_extras(days, form)
        estimated = any(
            it.price is None or (it.price_by or "").startswith("ước")
            for d in days
            for it in d.items
            if it.type in ("visit", "meal")
        ) or (night_price(state, form) is None and form.num_nights > 0)
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
                cost_breakdown=cost_breakdown(days, form, profile.trip_budget, estimated),
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
    chosen = next(
        (o for o in state["trip"]["options"] if o["id"] == decision.get("chosen")),
        state["trip"]["options"][0] if state["trip"]["options"] else {},
    )
    level = budget_level(form, profile, chosen.get("estimate"))
    if level and level["kind"] == "surplus":
        # Khung "Ngân sách dư" ở đầu đã nói lịch tiêu thấp hơn ngân sách: bỏ ghi chú cùng ý ở cuối.
        first = (state["trip"]["options"] or [{}])[0]
        dup = budget_note(first.get("estimate"), form)
        notes = [n for n in notes if n != dup]
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
        budget_level=BudgetLevel(**level) if level else None,
        notes=notes,
        festivals=state.get("festivals") or [],
        packing=[
            PackItem(**x)
            for x in packing(
                form,
                next((o for o in options if o.id == decision.get("chosen")), options[0]).days
                if options
                else [],
                profile.purposes,
            )
        ],
        **base,
    )


def night_price(state: dict[str, Any], form: TripForm) -> int | None:
    """Tiền 1 đêm cả đoàn (giá phòng × số phòng, 2 người 1 phòng); đã đặt chỗ thì 0."""
    if form.stay_booked:
        return 0
    price = (state.get("stay") or {}).get("price")
    return price * math.ceil(form.group_size / 2) if price else None


def day_meal_notes(state: dict[str, Any], day: int) -> list[str]:
    """Bữa không xếp / tuỳ chọn của ngày theo giờ bắt đầu, giờ kết thúc (bộ quy tắc ăn uống)."""
    if not state.get("profile"):
        return []
    window = next((d for d in profile_of(state).days if d.day == day), None)
    return skipped_meals(window.day_start, window.day_end) if window else []


def day_cost(
    items: list[Item],
    night: int,
    form: TripForm | None = None,
    rainy: bool = False,
    peak: bool = False,
) -> DayCost:
    """Chi phí một ngày; có `form` thì thêm phát sinh theo % (`tabs.day_extras`; ngày mưa / cao
    điểm để dành nhiều hơn). Chỗ chưa có giá / chặng chưa rõ tiền tính 0 ở cả 3 mức."""

    def add(kinds: tuple[str, ...], field: str) -> int:
        return sum(getattr(i, field) or 0 for i in items if i.type in kinds)

    tickets, food = add(("visit",), "price"), add(("meal",), "price")
    # Nghỉ trưa ở quán cà phê: tiền nước vào ăn uống; nghỉ theo giờ ở khách sạn: vào chỗ ở.
    nap = [i for i in items if i.type == "rest" and i.price]
    nap_cafe = sum(i.price or 0 for i in nap if "ly" in (i.price_by or ""))
    nap_room = sum(i.price or 0 for i in nap) - nap_cafe
    food, night = food + nap_cafe, night + nap_room
    transport = sum(i.travel_cost or 0 for i in items)
    low = (
        add(("visit", "meal"), "price_min")
        + nap_cafe  # phòng nghỉ trưa đã nằm trong `night`
        + sum(i.travel_cost_min or 0 for i in items)
    )
    high = (
        add(("visit", "meal"), "price_max")
        + nap_cafe  # phòng nghỉ trưa đã nằm trong `night`
        + sum(i.travel_cost_max or 0 for i in items)
    )
    # Tự lái: cộng tiền thuê xe trong ngày (không có thì tiền xăng mỗi chặng trông rẻ ảo).
    rent = rental_of(form.travel_mode, form.group_size) if form else None
    r_low, r_high, r_note = rent if rent else (0, 0, None)
    rental = int(round((r_low + r_high) / 2 / 1000) * 1000)
    low, high = low + r_low, high + r_high
    subtotal = night + tickets + food + transport + rental
    extra = [
        ExtraItem(**e) for e in (day_extras(items, form, subtotal, rainy, peak) if form else [])
    ]
    ex_low, ex_high = sum(e.min for e in extra), sum(e.max for e in extra)
    extras = int(round((ex_low + ex_high) / 2 / 1000) * 1000)
    return DayCost(
        stay=night,
        tickets=tickets,
        food=food,
        transport=transport,
        extras=extras,
        rental=rental,
        rental_min=r_low,
        rental_max=r_high,
        rental_note=r_note,
        total=subtotal + extras,
        total_min=night + low + ex_low,
        total_max=night + high + ex_high,
        extra_items=extra,
    )


def add_extras(days: list[Day], form: TripForm) -> None:
    """Tính lại `Day.cost` các ngày của một phương án kèm phát sinh theo % (mưa / cao điểm)."""
    for d in days:
        night = d.cost.stay if d.cost else 0
        rainy = any((s.rain_chance or 0) >= RAIN_PCT for s in d.sessions)
        d.cost = day_cost(d.items, night, form, rainy, d.crowd == "high")


def merge_extras(days: list[Day]) -> list[ExtraItem]:
    """Gộp phát sinh các ngày theo khoản; lý do giống nhau giữ 1, khác thì ghi theo ngày."""
    by: dict[str, list[tuple[int, ExtraItem]]] = {}
    for d in days:
        for e in (d.cost or DayCost()).extra_items:
            by.setdefault(e.label, []).append((d.day, e))
    out = []
    for label, rows in by.items():
        reasons = list(dict.fromkeys(e.reason for _, e in rows))
        if len(reasons) == 1:
            reason = reasons[0]
        else:
            parts = [f"ngày {n}: {e.reason}" for n, e in rows]
            reason = "; ".join(parts[:3]) + (" …" if len(parts) > 3 else "")
            reason = reason[0].upper() + reason[1:]
        out.append(
            ExtraItem(
                label=label,
                min=sum(e.min for _, e in rows),
                max=sum(e.max for _, e in rows),
                reason=reason,
            )
        )
    return out


def _share(extra: int, costs: list[DayCost]) -> float:
    """Phát sinh chiếm bao nhiêu phần chi phí (ở + vé + ăn + đi lại) cả chuyến."""
    base = sum(c.stay + c.tickets + c.food + c.transport + c.rental for c in costs)
    return round(extra / base, 3) if base else 0


def cost_breakdown(
    days: list[Day], form: TripForm, budget: int | None, estimated: bool
) -> CostBreakdown:
    """Cộng `Day.cost` các ngày (không làm tròn thêm, nên tổng khớp đúng từng ngày)."""
    costs = [d.cost or DayCost() for d in days]
    total = sum(c.total for c in costs)
    low, high = sum(c.total_min for c in costs), sum(c.total_max for c in costs)
    return CostBreakdown(
        stay=sum(c.stay for c in costs),
        tickets=sum(c.tickets for c in costs),
        food=sum(c.food for c in costs),
        transport=sum(c.transport for c in costs),
        extras=sum(c.extras for c in costs),
        rental=sum(c.rental for c in costs),
        total=total,
        total_min=low,
        total_max=high,
        budget=budget,
        per_person=round(total / form.group_size),
        per_person_min=round(low / form.group_size),
        per_person_max=round(high / form.group_size),
        extra_items=merge_extras(days),
        extras_pct_min=_share(sum(e.min for c in costs for e in c.extra_items), costs),
        extras_pct_max=_share(sum(e.max for c in costs for e in c.extra_items), costs),
        extras_notes=list(
            dict.fromkeys(
                note
                for c in costs
                for e in c.extra_items
                for note in e.reason.split(": ", 1)[-1].split("; ")
            )
        ),
        estimated=estimated,
        transport_options=[
            TransportOption.model_validate(r)
            for r in compare(
                [
                    (i.travel_km, i.travel_min, is_night(to_minutes(i.start) - i.travel_min))
                    for d in days
                    for i in d.items
                    if i.travel_km is not None
                ],
                form.group_size,
                city_of(form.destination),
            )
        ],
    )


def _day(
    plan: DayPlan,
    run: Run,
    places: dict[str, Place],
    form: TripForm,
    state: dict[str, Any],
    first_option: bool,
    daily_budget: int | None = None,
    matrix: dict[tuple[str, str], float] | None = None,
) -> Day:
    date = form.day_date(plan.day)
    stay_d = state.get("stay") or {}
    stay_name = stay_d.get("name") or "chỗ ở"
    pos = {pid: (p.lat, p.lng) for pid, p in places.items()}
    if stay_d.get("lat") is not None:
        pos[STAY] = (stay_d["lat"], stay_d["lng"])
    km_matrix = state.get("km_matrix") or {}
    mode = form.travel_mode

    def leg(a: str, b: str, arrive: float | None = None) -> dict[str, Any]:
        dist = leg_km(a, b, pos, km_matrix)
        minutes = (matrix or {}).get((a, b)) or 0
        city = city_of(form.destination)
        # Đi đêm (Grab phụ thu): xét lúc khởi hành = lúc tới - phút đi.
        night = is_night(arrive - minutes) if arrive is not None else False
        span = leg_range(dist, mode, form.group_size, minutes, city, night)
        return {
            "travel_mode": mode,
            "travel_km": dist,
            "travel_cost": leg_cost(dist, mode, form.group_size, minutes, city, night),
            "travel_cost_min": span[0] if span else None,
            "travel_cost_max": span[1] if span else None,
            "travel_alt": travel_alt(dist, mode, minutes),
        }

    prev = STAY
    items = [
        Item(
            type="start",
            start=plan.leave_stay,
            end=plan.leave_stay,
            name=f"Xuất phát từ {stay_name}",
        )
    ]
    for k, s in enumerate(run.stops):
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
                **price_detail(p, form.group_size),
                travel_min=s.travel,
                wait_min=s.wait,
                rating=p.rating,
                rating_floor=p.rating_floor,
                reviews=p.reviews,
                stay_by=p.stay_by,
                matches_purpose=p.matches_purpose,
                image=p.thumbnail,
                summary=short_summary(p.summary),
                why=why_of(p, plan.day, s, first=k == 0, daily_budget=daily_budget),
                must=p.must,
                must_time=p.must_time if p.must else None,
                wanted=p.wanted if not p.must else None,
                **leg(prev, p.place_id, s.start),
            )
        )
        prev = p.place_id
        if run.rest and run.rest[0] == k:
            _k, arrive, leave, at = run.rest
            spot = places.get(at)
            if spot is None:  # về chỗ ở
                rest = Item(
                    type="rest",
                    start=hhmm(arrive),
                    end=hhmm(leave),
                    name=f"Nghỉ trưa tại {stay_name}",
                    travel_min=int(arrive - s.leave),
                    **leg(prev, STAY, arrive),
                )
            else:  # quán cà phê / khách sạn gần quán trưa
                pos[at] = (spot.lat, spot.lng)
                rest = Item(
                    type="rest",
                    start=hhmm(arrive),
                    end=hhmm(leave),
                    name=f"Nghỉ trưa tại {spot.name}",
                    place_id=spot.place_id,
                    category=spot.category,
                    lat=spot.lat,
                    lng=spot.lng,
                    price=spot.price * form.group_size if spot.price else None,
                    price_by="ước: 1 ly mỗi người"
                    if spot.rest_of == "cafe"
                    else "ước: nghỉ theo giờ ~ nửa giá phòng",
                    rating=spot.rating,
                    reviews=spot.reviews,
                    image=spot.thumbnail,
                    travel_min=int(arrive - s.leave),
                    **leg(prev, at, arrive),
                )
            items.append(rest)
            prev = at
    last_travel = run.back - (run.stops[-1].leave if run.stops else to_minutes(plan.leave_stay))
    items.append(
        Item(
            type="back",
            start=hhmm(run.back),
            end=hhmm(run.back),
            name=(
                f"Về nghỉ đêm tại {stay_name}"
                if plan.day <= form.num_nights
                else f"Về {stay_name}, kết thúc chuyến"
            ),
            travel_min=int(last_travel),
            **(leg(prev, STAY, run.back) if run.stops else {"travel_mode": mode}),
        )
    )
    temp_min, temp_max, sessions = day_weather(state, plan.day, date.isoformat())
    crowd, crowd_note = crowd_of(date, state.get("festival_dates"))
    night = (night_price(state, form) or 0) if plan.day <= form.num_nights else 0
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
        route_paths=((state.get("paths") or {}).get(plan.id) or {}).get("paths") or [],
        route_precision=((state.get("paths") or {}).get(plan.id) or {}).get("precision") or 5,
        area_note=area_note([places[s.place_id] for s in run.stops], state, matrix or {}),
        meal_notes=day_meal_notes(state, plan.day),
        lunar=str(to_lunar(date)),
        holiday=holiday_of(date, state.get("festival_dates")),
        crowd=crowd,  # type: ignore[arg-type]
        crowd_note=crowd_note,
        temp_min=temp_min,
        temp_max=temp_max,
        sessions=[Session(**x) for x in sessions],
        cost=day_cost(items, night),
        ride_alt=ride_alt([[a.model_dump() for a in i.travel_alt] for i in items]),
    )


SUMMARY_CHARS = 220
NEAR_MIN = 10  # đi từ điểm trước tới đây không quá ngần này phút thì nêu là "gần"
CHEAP_SHARE = 0.15  # giá ≤ 15% ngân sách mỗi người mỗi ngày thì nêu là "nhẹ"
OUTLIER_KM = 5  # chỗ xa nhất cách chỗ ở từ ngần này km và ≥ 2 lần chỗ xa thứ hai thì giải thích


def short_summary(text: str | None) -> str | None:
    """Mô tả gọn: bỏ thẻ HTML, gộp khoảng trắng, cắt ở ranh giới chữ trong `SUMMARY_CHARS`."""
    clean = " ".join(re.sub(r"<[^>]+>", " ", nfc(text)).split()).strip(" -–·|")
    if not clean:
        return None
    if len(clean) <= SUMMARY_CHARS:
        return clean
    cut = clean[: SUMMARY_CHARS - 1]
    cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return cut.rstrip(" ,;:.–-") + "…"


def _count(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def purpose_reason(p: Place) -> str | None:
    if not p.matches_purpose:
        return None
    by = p.match_by or ""
    if by.startswith("Agent Experience"):
        quote = by.split(":", 1)[1].strip().strip('“”" ')
        return f"Hợp mục đích chuyến — khách từng viết “{_clip(quote, 80)}”"
    return f"Hợp mục đích chuyến ({by})" if by else "Hợp mục đích chuyến"


# Chợ / phố đi bộ / mua sắm: vào cửa thường miễn phí, tiền mua quà, ăn vặt tuỳ người và vùng.
SHOPPING_NOTE = "Vào cửa miễn phí; mua quà, ăn vặt tuỳ nhu cầu và giá từng vùng nên không ước tính"


def price_detail(p: Place, group_size: int) -> dict[str, Any]:
    """Giá mỗi người (thấp, cao) + giá cả nhóm theo khoảng đó + câu giải thích.

    Có khoảng Google ghi thì dùng đúng khoảng ("Trên X" thì chỉ có mức thấp); giá ước ±25%;
    chợ / phố đi bộ / chỗ mua sắm: không ước tiền mua sắm.
    """
    if p.kind == "visit" and (
        any(w in p.text for w in CATEGORY_GROUPS["chợ"]) or EVENING_WORDS.search(p.text)
    ):
        return {"price_note": SHOPPING_NOTE}
    if p.price is None:
        return {}
    by = p.price_by or ""
    if p.price_low or p.price_high:
        low, high = p.price_low or p.price, p.price_high
        src = "theo Google"
    elif by.startswith("ước"):
        low, high = price_range(p.price, by)
        src = by
    else:
        low = high = p.price
        src = "giá vé" if p.kind == "visit" else "theo nguồn giá"

    def vnd(v: int | None) -> str:
        return f"{v or 0:,}".replace(",", ".")

    if high is None:
        text = f"Từ {vnd(low)} đ/người ({src})"
    elif low == high:
        text = f"{vnd(low)} đ/người ({src})"
    else:
        text = f"Khoảng {vnd(low)}–{vnd(high)} đ/người ({src})"
    return {
        "price_pp_min": low,
        "price_pp_max": high,
        "price_min": low * group_size if low is not None else None,
        "price_max": (high or low) * group_size if (high or low) is not None else None,
        "price_note": text if p.kind == "meal" or src != "giá vé" else None,
    }


def travel_party(mode: str | None, group_size: int) -> str | None:
    """VD "2 người · 1 xe máy (2 người/xe)", "5 người · gọi 1 xe 7 chỗ"."""
    people = f"{group_size} người"
    if mode in SEATS_PER_VEHICLE:
        n, seats = vehicles_of(mode, group_size), SEATS_PER_VEHICLE[mode]
        name = "xe máy" if mode == "motorbike" else "ô tô tự lái"
        return f"{people} · {n} {name} ({seats} người/xe)"
    if mode == "taxi":
        cars = cars_for(group_size)
        kinds = [f"{cars.count(k)} xe {k[3:]} chỗ" for k in ("car7", "car4") if cars.count(k)]
        return f"{people} · gọi " + " + ".join(kinds)
    return people


def why_of(
    p: Place, day: int, stop: Any, first: bool, daily_budget: int | None = None
) -> list[str]:
    """Tối đa 3 lý do chọn chỗ này, ghép từ số đã tính (không gọi LLM), theo thứ tự: hợp mục
    đích, sao tin được, gần điểm trước, giá so với ngân sách, giờ mở cửa vừa lúc ghé."""
    out = []
    if p.must:
        when = must_time_text(p.must_time)
        free = f" (tới sớm, tự do {stop.wait} phút)" if p.must_time and stop.wait else ""
        out.append("Bạn chọn chỗ này" + (f", muốn tới {when}" if when else "") + free)
    if p.wanted and not p.must:
        out.append(f"Đúng thứ bạn nhắc: {p.wanted}")
    if reason := purpose_reason(p):
        out.append(reason)
    if p.rating is not None and p.rating_trusted:
        out.append(f"{p.rating:.1f}★ tin được, {_count(p.reviews)} đánh giá")
    if stop.travel <= NEAR_MIN:
        out.append(f"Cách {'chỗ ở' if first else 'điểm trước'} {stop.travel} phút")
    estimated = (p.price_by or "").startswith("ước")
    if p.price == 0:
        out.append("Thường không mất vé" if estimated else "Miễn phí vào cửa")
    elif p.price and not estimated and daily_budget and p.price <= daily_budget * CHEAP_SHARE:
        out.append(
            f"Giá {money(p.price)}/người, nhẹ so với ngân sách {money(daily_budget)}/người/ngày"
        )
    span = p.hours.get(day)
    if span and span[0] <= stop.start and stop.leave <= span[1]:
        out.append(f"Mở cửa {hhmm(span[0])}–{hhmm(span[1])}, ghé lúc {hhmm(stop.start)} vừa giờ")
    return out[:3]


def _far_reason(p: Place) -> str:
    parts = []
    if p.matches_purpose:
        parts.append("hợp mục đích chuyến")
    bayes = (p.rating_basis or {}).get("method") == "bayes"
    # Bayes: đủ review và hơn mức chung cùng loại; Hoeffding: từ 4,3 (tự đặt, từ trước).
    if p.rating is not None and (p.rating_above_peers if bayes else (p.rating_floor or 0) >= 4.3):
        parts.append(f"được đánh giá cao ({p.rating:.1f}★, {_count(p.reviews)} đánh giá)")
    elif p.reviews >= 5000:
        parts.append(f"rất nổi tiếng ({_count(p.reviews)} đánh giá)")
    return " và ".join(parts) or "ngày này vẫn đủ giờ đi về"


def area_note(
    stops: list[Place], state: dict[str, Any], matrix: dict[tuple[str, str], float]
) -> str | None:
    """Một câu về vùng đi của ngày: bán kính quanh chỗ ở (chưa có chỗ ở thì quanh trung tâm), chỗ
    xa nhất; chỗ xa hẳn các chỗ khác thì nói vì sao vẫn giữ."""
    stay, center = state.get("stay"), state.get("center")
    if not stops or not (stay or center):
        return None
    base = (stay["lat"], stay["lng"]) if stay else (center[0], center[1])
    where = "chỗ ở" if stay else "trung tâm"
    dist = sorted(((km(base, (p.lat, p.lng)), p) for p in stops), key=lambda x: x[0])
    far_km, far = dist[-1]
    ride = matrix.get((STAY, far.place_id)) if stay else None
    far_text = (
        f"{round(ride)} phút từ chỗ ở" if ride is not None else f"{far_km:.1f} km".replace(".", ",")
    )
    second = dist[-2][0] if len(dist) >= 2 else 0.0
    if len(dist) >= 2 and far_km >= OUTLIER_KM and far_km >= 2 * max(second, 1.0):
        return (
            f"Các điểm khác trong bán kính {max(1, math.ceil(second))} km quanh {where}; riêng "
            f"{far.name} xa hơn ({far_text}), vẫn giữ vì {_far_reason(far)}"
        )
    return (
        f"Các điểm trong bán kính {max(1, math.ceil(far_km))} km quanh {where}; xa nhất: "
        f"{far.name} ({far_text})"
    )


async def tra_ket_qua(state: ItineraryState, ctx: Ctx) -> dict:
    photos, paths = await asyncio.gather(fill_photos(state, ctx), fill_paths(state, ctx))
    out = {**photos, **{k: v for k, v in paths.items() if k != "tool_log"}}
    out["tool_log"] = (photos.get("tool_log") or []) + (paths.get("tool_log") or [])
    await ctx.log("tra_ket_qua", "answer", "Trả 3 phương án")
    return out


MAX_ROUTE_POINTS = 10  # = giới hạn số điểm mỗi lượt của tool get_route (MCP `_TOI_DA_DIEM`)


def route_chunks(points: list[str]) -> list[list[str]]:
    """Chia dãy điểm thành các đoạn ≤ `MAX_ROUTE_POINTS` điểm; đoạn sau bắt đầu ở điểm cuối của
    đoạn trước để đường vẽ liền mạch."""
    out: list[list[str]] = []
    i = 0
    while i < len(points) - 1:
        chunk = points[i : i + MAX_ROUTE_POINTS]
        out.append(chunk)
        i += len(chunk) - 1
    return out


async def fill_paths(state: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    """Đường xe chạy thật (Vietmap) cho mỗi lịch nằm trong phương án: chỗ ở → các chỗ theo thứ
    tự → chỗ ở. Lịch dùng chung giữa các phương án chỉ gọi 1 lần. Không có toạ độ chỗ ở hoặc
    tool hỏng thì bỏ qua: bản đồ nối thẳng các điểm như trước."""
    stay = state.get("stay") or {}
    if stay.get("lat") is None or stay.get("lng") is None:
        return {}
    places, plans = places_of(state), state.get("plans") or {}
    home = f"{stay['lat']},{stay['lng']}"
    wanted = list(
        dict.fromkeys(
            pid for o in (state.get("trip") or {}).get("options") or [] for pid in o["days"]
        )
    )
    calls, owners = [], []
    for pid in wanted:
        plan = plans.get(pid) or {}
        stops = [s for s in plan.get("stops") or [] if s in places]
        if not stops:
            continue
        points = [home]
        for k, s in enumerate(stops):
            points.append(f"{places[s].lat},{places[s].lng}")
            if plan.get("rest_after") == k:  # ghé chỗ nghỉ trưa (chỗ ở / quán gần đó) rồi đi tiếp
                spot = places.get(plan.get("rest_at") or "")
                points.append(f"{spot.lat},{spot.lng}" if spot else home)
        points.append(home)
        for chunk in route_chunks(points):
            calls.append(
                (
                    "get_route",
                    {"points": chunk, "vehicle": form_of(state).vehicle, "include_path": True},
                )
            )
            owners.append(pid)
    if not calls:
        return {}
    results, logs = await ctx.many(calls)
    paths: dict[str, dict[str, Any]] = {}
    broken: set[str] = set()
    for pid, data in zip(owners, results, strict=True):
        path = data.get("path") if tool_ok(data) else None
        if not isinstance(path, str) or not path:
            broken.add(pid)
            continue
        entry = paths.setdefault(pid, {"paths": [], "precision": data.get("path_precision") or 5})
        entry["paths"].append(path)
    # Thiếu 1 đoạn thì bỏ cả lịch đó: vẽ nửa đường thật nửa đường thẳng dễ hiểu nhầm.
    paths = {pid: v for pid, v in paths.items() if pid not in broken}
    await ctx.log(
        "tra_ket_qua", "finding", f"Đường đi thật cho {len(paths)}/{len(wanted)} lịch trên bản đồ"
    )
    return {"paths": paths, "tool_log": logs}


async def fill_photos(state: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    """Ảnh cho các chỗ chơi / quán trong phương án còn thiếu `thumbnail`: 1 lượt
    `get_place_photos`. Không thiếu chỗ nào thì không gọi; tool hỏng thì giữ nguyên."""
    places = places_of(state)
    plans = state.get("plans") or {}
    missing = list(
        dict.fromkeys(
            s
            for o in (state.get("trip") or {}).get("options") or []
            for pid in o["days"]
            for s in (plans.get(pid) or {}).get("stops") or []
            if s in places and not places[s].thumbnail
        )
    )
    if not missing:
        return {}
    data, log = await ctx.tool(
        "get_place_photos", places=missing, destination=form_of(state).destination
    )
    filled = add_photos(places, data)
    if not filled:
        return {"tool_log": [log]}
    await ctx.log("tra_ket_qua", "finding", f"Thêm ảnh cho {filled}/{len(missing)} chỗ")
    return {"places": dump_places(places), "tool_log": [log]}


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
    unit = {
        "group_trip": "cả đoàn cả chuyến",
        "person_trip": "mỗi người cả chuyến",
        "person_day": "mỗi người mỗi ngày",
    }[form.budget_unit]
    if form.budget_min is not None and (form.budget_min or form.budget):
        # Khoảng user chọn ở câu hỏi lại ("3–6 triệu", "trên 10 triệu"), không phải 1 con số.
        low, high = form.budget_min, form.budget
        span = (
            _range(low, high)
            if low and high
            else (f"từ {_money(low)}" if low else f"tối đa {_money(high)}")
        )
        lines.append(f"Ngân sách {span} {unit}.")
    elif form.budget:
        # Đơn vị "mỗi người": ghi luôn số cả đoàn cả chuyến (bấm nhầm thì thấy ngay).
        days = form.num_days if form.budget_unit == "person_day" else 1
        whole = form.budget * form.group_size * days
        lines.append(
            f"Ngân sách {_money(form.budget)} {unit}"
            + (
                f" (= {_money(whole)} cả đoàn cả chuyến)"
                if form.budget_unit != "group_trip"
                else ""
            )
            + "."
        )
    if form.purposes:
        lines.append("Mục đích: " + ", ".join(PURPOSE_NAMES[p] for p in form.purposes) + ".")
    if form.must_visits:
        musts = []
        for m in form.must_visits:
            when = ", ".join(
                x for x in (f"ngày {m.day}" if m.day else "", must_time_text(m.time) or "") if x
            )
            musts.append(m.name + (f" ({when})" if when else ""))
        lines.append("Muốn đi: " + "; ".join(musts) + ".")
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
    if lv := resp.budget_level:
        lines += ["", f"{'⚠️' if lv.severity == 'warn' else 'ℹ️'} **{lv.title}.** {lv.text}"]
    if resp.why:
        lines += ["", resp.why]
    if resp.stay:
        lines += [
            "",
            f"🏨 **Chỗ ở:** {resp.stay.name}"
            + (f" · {_money(resp.stay.price_per_night)}/đêm" if resp.stay.price_per_night else ""),
        ]
    for day in chosen.days:
        extra = [f"âm lịch {day.lunar}" if day.lunar else "", day.holiday or ""]
        extra += [day.crowd_note or ""] if day.crowd == "high" else []
        lines += [
            "",
            f"**Ngày {day.day} · {day.weekday} {day.date[8:10]}/{day.date[5:7]}**"
            + "".join(f" · {x}" for x in extra if x),
        ]
        for item in day.items:
            if item.type in ("visit", "meal"):
                price = f" · {_money(item.price)}" if item.price else ""
                lines.append(f"- {item.start} {ICONS[item.type]} {item.name}{price}")
        if day.issues:
            lines.append(f"- ⚠️ {'; '.join(day.issues)}")
    if cb := chosen.cost_breakdown:
        lines += [
            "",
            f"💰 **Tổng:** khoảng {_range(cb.total_min, cb.total_max)} cả đoàn (khoảng "
            f"{_range(cb.per_person_min, cb.per_person_max)}/người, gồm đi lại và phát sinh)",
            f"Chi tiết (ước): ở {_money(cb.stay)} · vé {_money(cb.tickets)} · ăn "
            f"{_money(cb.food)} · đi lại {_money(cb.transport)} · phát sinh {_money(cb.extras)}",
        ]
    else:
        lines += [
            "",
            f"💰 **Tổng:** {_money(chosen.total_cost) if chosen.total_cost else 'chưa đủ giá'}"
            + (
                f" (gồm giá ước của {len(chosen.estimated_prices)} chỗ chưa có giá thật)"
                if chosen.estimated_prices and chosen.total_cost
                else ""
            ),
        ]
    if resp.packing:
        lines += ["", "🎒 **Mang theo:** " + ", ".join(p.item for p in resp.packing) + "."]
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


def _range(low: int, high: int) -> str:
    if low == high:
        return _money(low)
    return f"{low:,}–{high:,} đ".replace(",", ".")

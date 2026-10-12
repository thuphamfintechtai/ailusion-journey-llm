"""Dữ liệu đi qua các bước: địa điểm, lịch một ngày, hồ sơ chuyến đi, state của graph.

State của LangGraph chỉ chứa dict/list thuần để checkpointer lưu được: đọc ra object bằng
`form_of`, `profile_of`, `places_of`, `plans_of`…; ghi lại bằng `dump_places`, `dump_plans`.
"""

from __future__ import annotations

import operator
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field

from ..shared.form import TripForm
from ..shared.math import rating_floor

STAY = "STAY"  # mã chỗ ở trong ma trận phút đi (bước 8)


# Loại chỗ gần như giống nhau dù Google đặt tên khác: gom lại để luật "mỗi ngày 1 chỗ mỗi loại"
# không bị lách (VD "Spa", "Dịch vụ mát xa", "Tiệm massage" đều là spa).
CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    "spa": ("spa", "massage", "mát xa", "xông hơi"),
    "chùa": ("chùa", "đền", "miếu", "thiền viện", "nhà thờ"),
    "bảo tàng": ("bảo tàng", "triển lãm", "di tích"),
    "biển": ("bãi biển", "bãi tắm"),
    "chợ": ("chợ", "trung tâm thương mại", "mua sắm"),
    "cà phê": ("cà phê", "cafe", "coffee"),
}


@dataclass
class Place:
    place_id: str
    name: str
    category: str
    kind: str  # "visit" | "meal" | "stay"
    lat: float
    lng: float
    rating: float | None = None
    reviews: int = 0
    price: int | None = None  # mỗi người (chỗ chơi, quán) hoặc mỗi đêm (chỗ ở), VND
    price_by: str | None = None  # "tool" | "Agent Budget"
    summary: str = ""
    need_status: dict[str, str] = field(default_factory=dict)  # mã nhu cầu -> yes / no / unknown
    hours: dict[int, tuple[int, int] | None] = field(default_factory=dict)  # ngày (1..) -> mở, đóng
    # ngày -> các khung đóng cửa giữa giờ mở và giờ đóng (VD nghỉ trưa 11:00–13:00), phút
    breaks: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    stay_low: int | None = None  # phút ở lại, mức ít
    stay_high: int | None = None
    stay_by: str | None = None  # "google" | "ước theo loại"
    matches_purpose: bool | None = None
    match_by: str | None = None
    to_verify: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def text(self) -> str:
        # NFC: dữ liệu cũ trong checkpoint có thể còn chữ dạng tổ hợp (xem shared/tools.nfc).
        return unicodedata.normalize("NFC", f"{self.category} {self.name}").lower()

    @property
    def group(self) -> str:
        """Nhóm loại chỗ (spa, chùa, bảo tàng…); không thuộc nhóm nào thì là loại Google ghi."""
        for name, words in CATEGORY_GROUPS.items():
            if any(w in self.text for w in words):
                return name
        return unicodedata.normalize("NFC", self.category).strip().lower() or self.text

    @property
    def rating_floor(self) -> float | None:
        return rating_floor(self.rating, self.reviews)


@dataclass
class Stop:
    place_id: str
    arrive: int
    start: int
    leave: int
    travel: int
    wait: int


@dataclass
class Run:
    stops: list[Stop]
    back: int
    issues: list[str]
    unchecked: list[str]


@dataclass
class DayPlan:
    id: str
    day: int
    leave_stay: str
    stops: list[str]
    status: str = "ok"  # ok | risky | warn | fail
    issues: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    metrics: dict[str, float | None] = field(default_factory=dict)
    beaten_by: str | None = None
    fixed: bool = False
    run: Run | None = None


class DayWindow(BaseModel):
    day: int
    day_start: str
    day_end: str
    blocked_slots: list[str] = Field(default_factory=list)
    meal_only_slots: list[str] = Field(default_factory=list)
    free_minutes: int
    guess: list[str] = Field(default_factory=list)


class TripProfile(BaseModel):
    """Kết quả bước 3."""

    needs_care: bool
    required_needs: list[str]
    nice_to_have_needs: list[str]
    night_return_time: str
    days: list[DayWindow]
    purposes: list[str]
    main_purpose: str | None
    max_effort: str | None
    group: str
    rule: str
    sort_order: list[tuple[str, str]]
    daily_budget: int | None
    trip_budget: int | None
    base_daily_cost: int | None
    questions: list[str] = Field(default_factory=list)
    guess: list[str] = Field(default_factory=list)


class ItineraryState(TypedDict, total=False):
    form: dict[str, Any]
    sent: list[str]  # ô user đã điền trên form
    understood: list[dict[str, str]]
    center: list[float] | None
    profile: dict[str, Any]
    places: dict[str, dict[str, Any]]
    stays: dict[str, dict[str, Any]]
    weather: list[dict[str, Any]]
    festivals: list[str]
    alerts: list[str]
    notes: list[str]
    to_experience: list[str]
    to_budget: list[str]
    stay: dict[str, Any] | None
    pool: list[str]
    codes: dict[str, str]  # mã ngắn -> place_id
    day_codes: dict[str, list[str]]  # ngày -> mã chỗ Planner được dùng ngày đó (bước 8)
    matrix: dict[str, float]  # "from|to" -> phút
    plans: dict[str, dict[str, Any]]  # plan_id -> DayPlan dạng dict
    ranked: dict[str, list[str]]  # ngày -> plan_id theo hạng
    reasons: dict[str, list[str]]
    trip: dict[str, Any]
    decision: dict[str, Any]
    maps: dict[str, str]
    llm_calls: Annotated[int, operator.add]
    usage: Annotated[list[dict[str, Any]], operator.add]
    tool_log: Annotated[list[dict[str, Any]], operator.add]


def form_of(state: dict[str, Any]) -> TripForm:
    return TripForm.model_validate(state["form"])


def profile_of(state: dict[str, Any]) -> TripProfile:
    return TripProfile.model_validate(state["profile"])


def places_of(state: dict[str, Any]) -> dict[str, Place]:
    return {k: place_from_dict(v) for k, v in (state.get("places") or {}).items()}


def stays_of(state: dict[str, Any]) -> dict[str, Place]:
    return {k: place_from_dict(v) for k, v in (state.get("stays") or {}).items()}


def dump_places(places: dict[str, Place]) -> dict[str, dict[str, Any]]:
    return {k: place_to_dict(v) for k, v in places.items()}


def place_to_dict(p: Place) -> dict[str, Any]:
    d = asdict(p)
    d["hours"] = {str(k): list(v) if v else None for k, v in p.hours.items()}
    d["breaks"] = {str(k): [list(b) for b in v] for k, v in p.breaks.items()}
    return d


def place_from_dict(d: dict[str, Any]) -> Place:
    d = dict(d)
    d["hours"] = {int(k): tuple(v) if v else None for k, v in (d.get("hours") or {}).items()}
    d["breaks"] = {int(k): [tuple(b) for b in v] for k, v in (d.get("breaks") or {}).items()}
    return Place(**d)


def plans_of(state: dict[str, Any]) -> dict[str, DayPlan]:
    return {k: plan_from_dict(v) for k, v in (state.get("plans") or {}).items()}


def dump_plans(plans: dict[str, DayPlan]) -> dict[str, dict[str, Any]]:
    return {k: plan_to_dict(v) for k, v in plans.items()}


def plan_to_dict(p: DayPlan) -> dict[str, Any]:
    d = asdict(p)
    d["run"] = None  # tính lại khi cần
    return d


def plan_from_dict(d: dict[str, Any]) -> DayPlan:
    return DayPlan(**{k: v for k, v in d.items() if k != "run"})


def matrix_of(state: dict[str, Any]) -> dict[tuple[str, str], float]:
    """Ma trận phút đi (bước 8): khoá "from|to" trong state -> (from, to)."""
    return {tuple(k.split("|")): v for k, v in (state.get("matrix") or {}).items()}  # type: ignore[misc]


def weather_text(state: dict[str, Any], day: int) -> str | None:
    """Thời tiết ngày thứ `day` của chuyến (tính từ 1) dạng một dòng, không có thì None."""
    days = state.get("weather") or []
    if not 1 <= day <= len(days):
        return None
    day_w = days[day - 1]
    hours = {h.get("time"): h.get("chance_of_rain_pct", 0) for h in day_w.get("hours") or []}
    morning = max((hours.get(t, 0) for t in ("06:00", "09:00")), default=0)
    afternoon = max((hours.get(t, 0) for t in ("12:00", "15:00")), default=0)
    parts = [day_w.get("condition") or ""]
    if max(morning, afternoon) >= 50:
        parts.append(f"dễ mưa {'buổi sáng' if morning >= afternoon else 'buổi chiều'}")
    if day_w.get("temp_min_c") is not None:
        parts.append(f"{day_w['temp_min_c']:.0f}–{day_w.get('temp_max_c', 0):.0f}°C")
    return ", ".join(p for p in parts if p) or None

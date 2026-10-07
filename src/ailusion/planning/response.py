"""Lộ trình trả về của POST /plan — đủ dữ liệu để vẽ màn hình kết quả như prototype."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

OptionKey = Literal["canbang", "tietkiem", "trainghiem"]
ItemType = Literal["poi", "meal", "hotel", "siesta", "fixed", "free"]
Confidence = Literal["cao", "trung_binh", "thap"]


class Leg(BaseModel):
    """Chặng di chuyển tới mục này."""

    minutes: int
    km: float


class WhyCriterion(BaseModel):
    label: str
    value: float = Field(ge=0, le=1)
    note: str = ""


class Item(BaseModel):
    type: ItemType
    start: str = Field(description="HH:MM")
    end: str = Field(description="HH:MM")
    name: str
    place_id: str | None = None
    category: str | None = None
    lat: float | None = None
    lng: float | None = None
    price_vnd: int = Field(0, description="Chi phí mục này cho cả đoàn")
    leg: Leg | None = None
    indoor: bool | None = None
    rating: float | None = None
    source: str = Field(description="Tool cho ra dữ liệu, VD browse_places")
    confidence: Confidence = "trung_binh"
    why: list[WhyCriterion] = Field(default_factory=list)
    note: str | None = None


class WeatherBlock(BaseModel):
    morning_rain_pct: int | None = None
    afternoon_rain_pct: int | None = None
    evening_rain_pct: int | None = None
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    condition: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Day(BaseModel):
    index: int
    date: str
    weekday: str
    weather: WeatherBlock
    items: list[Item]
    travel_minutes: int
    travel_km: float
    maps_url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Cost(BaseModel):
    stay: int = 0
    food: int = 0
    tickets: int = 0
    move: int = 0
    intercity: int = 0
    reserve: int = 0
    total: int = 0


class Risk(BaseModel):
    simulations: int
    seed: int
    p_break: float = Field(description="Xác suất ít nhất một khung giờ bị vỡ")
    p_wet: float = Field(description="Xác suất dính mưa ở điểm ngoài trời")
    late_p50_min: int
    late_p90_min: int


class Option(BaseModel):
    key: OptionKey
    name: str
    description: str
    hotel: Item | None = None
    days: list[Day]
    cost: Cost
    hard_ok: bool = Field(description="Thoả mọi ràng buộc cứng")
    issues: list[str] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list, description="Điều chỉnh để giữ ngân sách")
    risk: Risk
    scores: dict[str, float] = Field(default_factory=dict, description="Điểm thành phần 0-100")
    summary: str = ""


class Excluded(BaseModel):
    name: str
    reason: str


class Decision(BaseModel):
    winner: OptionKey
    matrix: dict[str, dict[str, float]] = Field(
        description="Điểm TOPSIS: bộ trọng số -> phương án -> điểm"
    )
    winner_by_weight_set: dict[str, str]
    robust: bool = Field(description="Thắng ở mọi bộ trọng số")
    tie_break: str | None = None
    binding: list[str] = Field(default_factory=list, description="Ràng buộc đang bị chạm")
    excluded: list[Excluded] = Field(default_factory=list)


class Explanation(BaseModel):
    summary: str
    why_winner: str
    tips: list[str] = Field(default_factory=list)


class Stats(BaseModel):
    llm_calls: int
    tool_calls: int
    tool_errors: int
    seconds: float


class PlanResponse(BaseModel):
    thread_id: str
    persona: str = Field(description="Nhóm người dùng P1–P4")
    persona_name: str
    strategy: dict[str, Any] = Field(description="Chiến lược đã dùng (sau khi ép vào khoảng)")
    strategy_source: Literal["llm", "mac_dinh"] = Field(
        description="llm = do LLM lập theo prompt của nhóm; mac_dinh = LLM lỗi, dùng mặc định"
    )
    destination: str
    start_date: str
    days: int
    options: dict[str, Option]
    decision: Decision
    explanation: Explanation
    assumptions: list[str]
    festivals: list[str] = Field(default_factory=list)
    stats: Stats

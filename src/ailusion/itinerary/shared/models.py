"""Dữ liệu đi qua các bước: địa điểm, lịch một ngày, hồ sơ chuyến đi, state của graph.

State của LangGraph chỉ chứa dict/list thuần để checkpointer lưu được: đọc ra object bằng
`form_of`, `profile_of`, `places_of`, `plans_of`…; ghi lại bằng `dump_places`, `dump_plans`.
"""

from __future__ import annotations

import operator
import re
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
    "quán bar": ("karaoke", "quán bar", "quán rượu", "hộp đêm", "pub"),
}
# Loại Google chung chung, không nói chỗ đó là gì: làng gốm, show Ký Ức Hội An, nông trại cún,
# rừng thông… đều ghi "Điểm thu hút khách du lịch" (134/360 chỗ chơi của 5 chuyến thử). Coi là
# một loại thì luật "mỗi lịch / mỗi ngày 1 chỗ mỗi loại" và trần mỗi loại cả chuyến (`type_cap`)
# chặn cả trăm chỗ khác hẳn nhau: Đà Lạt 5 ngày chỉ còn 1–3 chỗ chơi mỗi ngày.
GENERIC_CATEGORIES = ("điểm thu hút khách du lịch", "tourist attraction")


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
    # Sao tin được MCP tính (`rating_adjusted`): Bayes theo tiên nghiệm của điểm đến × loại hình;
    # `rating_basis` = cách tính (method, prior_mean, prior_reviews, prior_level).
    rating_adjusted: float | None = None
    rating_basis: dict[str, Any] | None = None
    price: int | None = None  # mỗi người (chỗ chơi, quán) hoặc mỗi đêm (chỗ ở), VND
    price_by: str | None = None  # "tool" | "Agent Budget"
    # Khoảng giá mỗi người Google ghi (VD "100–200 N ₫"); `price` là số giữa. Thiếu cao = "Trên …".
    price_low: int | None = None
    price_high: int | None = None
    summary: str = ""
    thumbnail: str | None = None  # link ảnh (tool tìm kiếm hoặc get_place_photos)
    need_status: dict[str, str] = field(default_factory=dict)  # mã nhu cầu -> yes / no / unknown
    hours: dict[int, tuple[int, int] | None] = field(default_factory=dict)  # ngày (1..) -> mở, đóng
    # ngày -> các khung đóng cửa giữa giờ mở và giờ đóng (VD nghỉ trưa 11:00–13:00), phút
    breaks: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    stay_low: int | None = None  # phút ở lại, mức ít
    stay_high: int | None = None
    stay_by: str | None = None  # "google" | "ước theo loại"
    rest_of: str | None = None  # kind "rest": "cafe" | "hotel" — chỗ nghỉ trưa dọc đường
    matches_purpose: bool | None = None
    match_by: str | None = None
    to_verify: list[str] = field(default_factory=list)
    source: str = ""
    # User chỉ định chỗ này (form `must_visits`): lộ trình phải có, đúng ngày / giờ nếu có.
    must: bool = False
    must_day: int | None = None
    must_time: str | None = None  # "HH:MM" | morning | afternoon | evening
    must_name: str | None = None  # tên user gõ, để báo lại
    # Từ khoá user nhắc (ô sở thích / lời nhắn, VD "bún bò") mà chỗ này khớp; None = không khớp.
    wanted: str | None = None

    @property
    def text(self) -> str:
        # NFC: dữ liệu cũ trong checkpoint có thể còn chữ dạng tổ hợp (xem shared/tools.nfc).
        return unicodedata.normalize("NFC", f"{self.category} {self.name}").lower()

    @property
    def group(self) -> str:
        """Nhóm loại chỗ (spa, chùa, bảo tàng…) theo loại Google ghi trước, tên chỉ xét khi loại
        trống / chỉ là mã số: "Chùa Cầu" loại "Điểm mốc lịch sử" là cây cầu, không phải chùa (tính
        là chùa thì bị luật mỗi ngày 1 chùa đẩy khỏi danh sách). Không thuộc nhóm nào thì là loại
        Google ghi."""
        cat = unicodedata.normalize("NFC", self.category).strip().lower()
        text = cat if re.search(r"[^\W\d_]", cat) else self.text
        for name, words in CATEGORY_GROUPS.items():
            if any(w in text for w in words):
                return name
        return cat or self.text

    @property
    def typed(self) -> bool:
        """Có loại cụ thể (chùa, spa, chợ, công viên…): loại Google chung chung
        (`GENERIC_CATEGORIES`) hay chỉ là mã số ("4004, 4004-2") thì không."""
        return bool(re.search(r"[^\W\d_]", self.group)) and self.group not in GENERIC_CATEGORIES

    @property
    def type_key(self) -> str:
        """Khoá của luật "cùng loại" (mỗi lịch / mỗi ngày 1 chỗ mỗi loại, trần mỗi loại cả chuyến).
        Chỗ không có loại cụ thể: khoá riêng, không cùng loại với chỗ nào. `group` giữ nguyên cho
        gộp chỗ trùng tên và ước giá theo loại."""
        return self.group if self.typed else f"#{self.place_id}"

    @property
    def type_label(self) -> str:
        """Cột "loại" đưa Planner: "–" khi chưa rõ loại (khớp luật 5 của prompt)."""
        return self.group if self.typed else "–"

    @property
    def breakfast_only(self) -> bool:
        """Quán chỉ bán bữa sáng (loại Google "Nhà hàng chuyên phục vụ bữa sáng"): không làm bữa
        trưa / tối."""
        cat = unicodedata.normalize("NFC", self.category).lower()
        return self.kind == "meal" and any(w in cat for w in BREAKFAST_WORDS)

    @property
    def rating_floor(self) -> float | None:
        """Sao tin được: số MCP tính (cùng công thức với bước lấy chỗ của MCP); tool không trả
        (search_hotels, Vietmap…) thì Hoeffding."""
        if self.rating_adjusted is not None:
            return self.rating_adjusted
        return rating_floor(self.rating, self.reviews)

    @property
    def rating_trusted(self) -> bool:
        """Sao tin được để nêu làm lý do chọn. Bayes: chỉ cần đủ review — số review thật nhiều hơn
        số review ảo của tiên nghiệm (dữ liệu thật quyết định điểm, không phải sao giả định).
        Hoeffding: từ TRUSTED_HOEFFDING."""
        basis = self.rating_basis or {}
        if self.rating_adjusted is not None and basis.get("method") == "bayes":
            return self.reviews >= (basis.get("prior_reviews") or 0)
        return (self.rating_floor or 0) >= TRUSTED_HOEFFDING

    @property
    def rating_above_peers(self) -> bool:
        """Bayes: sao tin được (đủ review) và cao hơn sao tiên nghiệm — hơn mức chung của cùng
        loại ở điểm đến. Dùng cho "được đánh giá cao"."""
        basis = self.rating_basis or {}
        return (
            self.rating_trusted
            and self.rating_adjusted is not None
            and self.rating_adjusted > (basis.get("prior_mean") or 0)
        )


TRUSTED_HOEFFDING = 4.0  # tự đặt (từ trước): sao Hoeffding từ mức này thì nêu "tin được"
BREAKFAST_WORDS = ("bữa sáng", "ăn sáng", "điểm tâm", "breakfast")


# Tên quán: phần sau các dấu này là địa chỉ / chi nhánh, không phải thương hiệu
# ("Lẩu Gà Lá É TAO NGỘ - 70A 3 tháng 2", "… 39 Đường 3/2", "… (Quán Nhi)").
BRANCH_CUT = re.compile(r"\s[-–|,/]\s|\(|\b(?:đường|chi nhánh|cn|cơ sở|branch|ngõ|hẻm|kiệt|tại)\b")
# Chữ chung của tên quán, bỏ khi so thương hiệu (so trên chữ đã bỏ dấu).
GENERIC_MEAL_WORDS = {"quan", "nha", "hang", "restaurant", "tiem", "the"}
SAME_BRAND_MIN_WORDS = 3  # tên ngắn hơn mà là đầu tên kia thì chưa đủ chắc là cùng chuỗi


def _plain(text: str) -> str:
    """Thường hoá, bỏ dấu (đ -> d) để so tên."""
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if not unicodedata.combining(c))


def brand_key(name: str) -> tuple[str, ...]:
    """Các chữ thương hiệu của tên quán: bỏ địa chỉ / chi nhánh phía sau, bỏ dấu, bỏ chữ chung."""
    name = unicodedata.normalize("NFC", name).lower()
    head = BRANCH_CUT.split(name, maxsplit=1)[0]
    words = re.findall(r"\w+", head)
    # Cắt ở số nhà đầu tiên ("… 39 …"), trừ khi số là một phần tên ngắn ("Phở 24").
    cut = next((i for i, w in enumerate(words) if any(c.isdigit() for c in w)), None)
    if cut is not None and cut >= 2:
        words = words[:cut]
    plain = [_plain(w) for w in words]
    return tuple(w for w in plain if w not in GENERIC_MEAL_WORDS) or tuple(plain)


# Nhãn sở thích chung của trợ lý chat (chat/trip_info.Interest): là loại, không phải thứ cụ thể
# user đòi, nên không bắt lịch phải có.
GENERIC_INTERESTS = {
    "thien nhien",
    "am thuc",
    "van hoa",
    "lich su",
    "chup anh",
    "check-in",
    "check in",
    "ca phe",
    "nghi duong",
    "phieu luu",
    "bien",
    "mua sam",
    "ve dem",
}


def wanted_words(interests: str) -> list[str]:
    """Thứ cụ thể user nhắc trong ô sở thích (đã gồm phần lấy từ lời nhắn, VD "bún bò"): bỏ
    trùng, bỏ nhãn chung của trợ lý chat, giữ thứ tự."""
    words = (w.strip() for w in (interests or "").split(","))
    return list(dict.fromkeys(w for w in words if w and _plain(w) not in GENERIC_INTERESTS))


def wanted_match(p: Place, words: list[str]) -> str | None:
    """Từ khoá user nhắc có trong tên / loại / mô tả của chỗ (so bỏ dấu) -> trả từ khoá đó."""
    text = " " + re.sub(r"\s+", " ", _plain(f"{p.name} {p.category} {p.summary}")) + " "
    for w in words:
        key = re.sub(r"\s+", " ", _plain(w)).strip()
        if key and f" {key} " in text.replace(",", " ").replace(".", " "):
            return w
    return None


def same_brand(a: Place, b: Place) -> bool:
    """2 quán cùng thương hiệu / chuỗi (khác chi nhánh): chữ thương hiệu trùng, hoặc tên này là
    đầu tên kia và đủ dài (≥ SAME_BRAND_MIN_WORDS chữ)."""
    ka, kb = brand_key(a.name), brand_key(b.name)
    if not ka or not kb:
        return False
    short, long_ = sorted((ka, kb), key=len)
    return ka == kb or (len(short) >= SAME_BRAND_MIN_WORDS and long_[: len(short)] == short)


# Chỗ chỉ đáng đi buổi tối (chợ đêm, phố đi bộ, bar…) hoặc mở cửa từ ngần này trở đi: xếp cuối ngày.
EVENING_OPEN = 16 * 60
EVENING_WORDS = re.compile(r"chợ đêm|phố đi bộ|night market|walking street|\bbar\b|\bpub\b")


def opens_late(p: Place, day: int) -> bool:
    span = p.hours.get(day)
    return bool(span and span[0] >= EVENING_OPEN) or bool(EVENING_WORDS.search(p.text))


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
    # Nghỉ trưa: (sau chỗ thứ k trong `stops`, giờ tới chỗ nghỉ, giờ rời, chỗ nghỉ: STAY hoặc
    # place_id quán cà phê / khách sạn) — phút trong ngày.
    rest: tuple[int, int, int, str] | None = None


@dataclass
class DayPlan:
    id: str
    day: int
    leave_stay: str
    stops: list[str]
    status: str = "ok"  # ok | risky | warn | fail
    rest_after: int | None = None  # nghỉ trưa sau chỗ thứ k (bước chạy thử)
    rest_at: str | None = None  # nơi nghỉ trưa: STAY hoặc place_id (kind "rest")
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
    nap: bool = False  # có nghỉ trưa NAP_MIN phút sau bữa trưa
    nap_at: str = "auto"  # TripForm.nap: auto | stay | cafe | hotel
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
    festival_dates: list[dict[str, Any]]  # {"name", "start", "end"} của lễ hội có ngày
    alerts: list[str]
    notes: list[str]
    branches: list[dict[str, Any]]  # state riêng của từng nhánh sau chot_nhanh (branches.py)
    stay: dict[str, Any] | None
    pool: list[str]
    codes: dict[str, str]  # mã ngắn -> place_id
    day_codes: dict[str, list[str]]  # ngày -> mã chỗ Planner được dùng ngày đó (bước 8)
    must_codes: dict[str, list[str]]  # ngày -> mã chỗ user chỉ định, mọi lịch ngày đó phải có
    matrix: dict[str, float]  # "from|to" -> phút
    km_matrix: dict[str, float]  # "from|to" -> km đường đi (cùng lượt distance_matrix)
    plans: dict[str, dict[str, Any]]  # plan_id -> DayPlan dạng dict
    # 3 phương án chạy song song (coordinator/branches.py): mỗi nhánh có state riêng.
    round: int  # nhánh (phương án) 1..BRANCHES — chỉ có trong state riêng của nhánh
    used: list[str]  # chỗ dành cho phương án khác — nhánh này xếp chúng cuối danh sách
    options: list[dict[str, Any]]  # phương án các nhánh: {"id", "days": [plan_id], ...}
    kept_plans: dict[str, dict[str, Any]]  # lịch mọi nhánh, mã "V{nhánh}-N1-L2"
    first_ranked: dict[str, list[str]]  # hạng lịch nhánh 1 (cho lựa chọn rẻ hơn khi vượt tiền)
    first_reasons: dict[str, list[str]]
    ranked: dict[str, list[str]]  # ngày -> plan_id theo hạng
    reasons: dict[str, list[str]]
    trip: dict[str, Any]
    decision: dict[str, Any]
    maps: dict[str, str]
    paths: dict[str, dict[str, Any]]  # plan_id -> {paths: [polyline], precision} (bước 17)
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

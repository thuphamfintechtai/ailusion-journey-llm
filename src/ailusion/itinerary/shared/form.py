"""Bước 1 — form lập lộ trình, và các bảng luật dùng chung của pipeline.

Mỗi ô form đúng bảng "Bước 1" của trang thiết kế. Ô nào trống thì Agent đọc lời nhắn tìm trong
`message`; vẫn trống thì làm theo cột "Để trống thì" (phân nhóm / hỏi lại). Bảng ở đây là quy
ước sản phẩm hoặc số tạm — số tạm được ghi vào `guess` để kết quả nói rõ là đang giả định.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Purpose = Literal["relax", "food", "culture", "fun", "adventure", "shopping"]


GroupType = Literal["solo", "couple", "friends", "colleagues", "family"]


Priority = Literal["saving", "comfort", "leisure", "short_time", "many_places"]


SpendStyle = Literal["normal", "unlimited", "luxury_within_budget"]


BudgetUnit = Literal["group_trip", "person_trip", "person_day"]
# Nghỉ trưa ở đâu: auto (chỗ ở gần thì về, xa thì quán cà phê gần quán trưa) · stay (về chỗ ở) ·
# cafe (quán cà phê gần quán trưa) · hotel (khách sạn gần quán trưa, nghỉ theo giờ) · none.
Nap = Literal["auto", "stay", "cafe", "hotel", "none"]


TravelMode = Literal["motorbike", "car", "bus", "taxi"]


Walking = Literal["M1", "M2", "M3", "M4", "M5"]


Group = Literal["P1", "P2", "P3", "P4", "P5"]


PURPOSE_NAMES: dict[str, str] = {
    "relax": "nghỉ dưỡng",
    "food": "ẩm thực",
    "culture": "khám phá, văn hoá",
    "fun": "vui chơi, sự kiện",
    "adventure": "phiêu lưu",
    "shopping": "mua sắm",
}


# Từ khoá của mục đích: dùng để tìm (bước 5) và để biết chỗ có hợp mục đích không (bước 7).
# Quy ước sản phẩm; chỗ không chứa từ nào thì Agent Experience xem (bước 6a).
PURPOSE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "relax": ("spa", "resort", "bãi biển", "biển", "hồ", "công viên", "suối nước nóng"),
    "food": ("nhà hàng", "quán ăn", "đặc sản", "hải sản", "chợ", "ẩm thực"),
    "culture": (
        "bảo tàng",
        "chùa",
        "di tích",
        "nhà thờ",
        "phố cổ",
        "làng cổ",
        "làng nghề",
        "làng chài",
        "đền",
        "lăng",
    ),
    "fun": ("khu vui chơi", "công viên giải trí", "chợ đêm", "bar", "phố đi bộ", "show"),
    "adventure": ("núi", "thác", "trekking", "hang", "đèo", "rừng"),
    "shopping": ("chợ", "trung tâm thương mại", "cửa hàng lưu niệm"),
}


GROUP_NAMES = {
    "P1": "tiết kiệm",
    "P2": "đỡ mệt",
    "P3": "thong thả",
    "P4": "ít thời gian",
    "P5": "đi nhiều chỗ",
}
PRIORITY_GROUP: dict[str, Group] = {
    "saving": "P1",
    "comfort": "P2",
    "leisure": "P3",
    "short_time": "P4",
    "many_places": "P5",
}


# Bảng A (bước 3): người đi cùng -> nhu cầu bắt buộc / mong có / giờ tối về.
# Mã nhu cầu đúng mã của MCP (_nhu_cau.NHU_CAU).
TABLE_A = [
    ("A2", "có trẻ dưới 6 tuổi", (), ("kids", "high_chair", "baby_changing"), "21:00"),
    ("A3", "có trẻ 6–12 tuổi", (), ("kids", "playground"), "21:30"),
    ("A4", "có người từ 65 tuổi", (), ("restroom",), None),
    ("A5", "có người xe lăn", ("wheelchair", "wheelchair_toilet"), (), None),
    ("A6", "cặp đôi", (), ("romantic",), None),
    ("A7", "bạn bè, đồng nghiệp", (), ("groups",), None),
    ("A8", "đi một mình", (), ("solo_dining",), None),
]


# Bước 14: mỗi nhóm so lịch theo thứ tự này. (số đo, "min" | "max")
SORT_ORDER: dict[str, list[tuple[str, str]]] = {
    "P1": [("cost", "min"), ("num_stops", "max"), ("needs_met", "max")],
    "P2": [("ride_min", "min"), ("num_stops", "min"), ("cost", "min")],
    "P3": [("num_stops", "min"), ("num_matches", "max"), ("quality", "max"), ("cost", "min")],
    "P4": [("ride_min", "min"), ("popularity", "max"), ("cost", "min")],
    # Đi nhiều chỗ: nhiều điểm nhất → nhiều chỗ hợp ý → ít ngồi xe (để còn thời gian đi tiếp).
    "P5": [("num_stops", "max"), ("num_matches", "max"), ("ride_min", "min")],
}


# Số tạm (nguồn "tu_dat"), ghi rõ trong kết quả khi phải dùng.
DEFAULT_ARRIVAL = "08:00"  # không nhập giờ: ngày đầu bắt đầu buổi sáng như các ngày khác
DEFAULT_DEPARTURE = "15:00"
DEFAULT_WAKE = "08:00"
# Nghỉ trưa: ngày dài (bắt đầu từ NAP_IF_START_BY, kéo tới ít nhất NAP_IF_END_FROM) thì sau bữa
# trưa về chỗ ở nghỉ NAP_MIN phút (bước chạy thử tự chèn, Planner chỉ cần chừa thời gian).
NAP_MIN = 90
NAP_IF_START_BY = "09:00"
NAP_IF_END_FROM = "17:00"
NAP_HOME_MAX = 20  # auto: từ quán trưa về chỗ ở quá ngần này phút thì nghỉ ở quán cà phê gần đó
NAP_SPOT_KM = 4  # quán cà phê / khách sạn nghỉ trưa cách quán trưa tối đa ngần này km
CAFE_REST_PRICE = 50_000  # mỗi người một ly khi ngồi nghỉ ở quán cà phê (số tạm, "tu_dat")
HOTEL_REST_SHARE = 0.5  # nghỉ theo giờ ở khách sạn ~ nửa giá phòng 1 đêm (số tạm, "tu_dat")
# User không điền "Tối cần về chỗ ở trước": bước 4 hỏi lại (H6) user thường đi chơi TỚI mấy giờ
# rồi mới về; giờ về chỗ ở = giờ đó + EVENING_RIDE_BACK (đường về). Chọn "chưa biết" (hay không trả
# lời) thì đi chơi tới 20:00. "Có đi chơi đêm" thì về tới 23:30.
EVENING_RIDE_BACK = 45
DEFAULT_EVENING_UNTIL = "20:00"
DEFAULT_NIGHT_RETURN = "20:45"  # DEFAULT_EVENING_UNTIL + EVENING_RIDE_BACK
CHECKIN_MIN = 90  # thủ tục ở bến
DEPARTURE_BUFFER_MIN = 30
HUB_EXIT_MIN = 15  # ra khỏi bến
HUB_TRAVEL_MIN = 30  # bến <-> chỗ ở khi chưa biết vị trí bến
BUSY_TRAVEL_MIN = 15  # tới chỗ bận khi chưa biết vị trí
LOW_FREE_MINUTES = 300  # B1: ngày rảnh ít
# Khung giờ ăn — bộ quy tắc sắp xếp ăn uống (khớp luật 6 của Agent Planner):
# - Sáng: mốc 06:30–08:30; ăn sáng muộn được tới 10:00. BẮT BUỘC chỉ khi ngày bắt đầu (giờ dậy /
#   giờ tới) từ BREAKFAST_IF_START_BY trở về trước; bắt đầu 08:00–11:00 thì tuỳ (ăn sáng muộn nếu
#   còn cách bữa trưa đủ xa); sau 11:00 không có bữa sáng.
# - Trưa: mốc 11:30–13:00, dịch tối đa 1 tiếng (10:30–14:00). Bắt đầu từ 13:00 thì không bắt buộc.
# - Tối: 18:00–20:00.
# - Hai bữa liền nhau cách ít nhất MIN_MEAL_GAP phút (không được quá sát); nên 3–4 tiếng.
BREAKFAST = ("06:30", "10:00")
BREAKFAST_TARGET = ("06:30", "08:30")
BREAKFAST_IF_START_BY = "08:00"
LUNCH = ("10:30", "14:00")
LUNCH_TARGET = ("11:30", "13:00")
LUNCH_IF_START_BEFORE = "13:00"
DINNER = ("18:00", "20:00")
MEALS = {"sáng": BREAKFAST, "trưa": LUNCH, "tối": DINNER}
# Ngày có ít nhất ngần này phút trong khung mốc của một bữa thì bữa đó tính là trong giờ rảnh.
MEAL_MIN_OVERLAP = 30
MIN_MEAL_GAP = 150
# Bắt đầu ngày ≤ BREAKFAST_IF_START_BY: bữa sáng là việc ĐẦU TIÊN, ăn trước giờ này (mốc 06:30–08:30
# + thời gian chuẩn bị). Ăn sáng muộn (tới 10:00) chỉ dành cho ngày bắt đầu 08:00–11:00.
BREAKFAST_FIRST_BY = "09:00"
# Chỗ hợp đi buổi chiều / tối (spa, massage…): không xếp trước giờ này.
AFTERNOON_GROUPS = ("spa",)
AFTERNOON_FROM = "13:00"


def required_meals(day_start: str, day_end: str) -> list[str]:
    """Các bữa bắt buộc của một ngày theo giờ bắt đầu (giờ dậy / giờ tới) và giờ kết thúc."""
    a, b = to_minutes(day_start), to_minutes(day_end)

    def covers(window: tuple[str, str]) -> bool:
        return min(b, to_minutes(window[1])) - max(a, to_minutes(window[0])) >= MEAL_MIN_OVERLAP

    out = []
    if a <= to_minutes(BREAKFAST_IF_START_BY) and covers(BREAKFAST_TARGET):
        out.append("sáng")
    if a < to_minutes(LUNCH_IF_START_BEFORE) and covers(LUNCH_TARGET):
        out.append("trưa")
    if covers(DINNER):
        out.append("tối")
    return out


def skipped_meals(day_start: str, day_end: str) -> list[str]:
    """Bữa không xếp và lý do (hiện cho user, để không ai tự thêm lại bữa không cần)."""
    a, b = to_minutes(day_start), to_minutes(day_end)
    need = required_meals(day_start, day_end)
    out = []
    if "sáng" not in need:
        if a <= to_minutes("11:00") and a > to_minutes(BREAKFAST_IF_START_BY):
            out.append(f"Bữa sáng tuỳ chọn: ngày bắt đầu {day_start}, có thể ăn sáng muộn")
        elif a > to_minutes("11:00"):
            out.append(f"Không xếp bữa sáng: ngày bắt đầu {day_start}")
    if "trưa" not in need and a >= to_minutes(LUNCH_IF_START_BEFORE):
        out.append(f"Không bắt buộc bữa trưa: ngày bắt đầu {day_start}")
    if "trưa" not in need and b < to_minutes(LUNCH_TARGET[0]) + MEAL_MIN_OVERLAP:
        out.append(f"Không xếp bữa trưa: ngày kết thúc {day_end}")
    if "tối" not in need and b < to_minutes(DINNER[0]) + MEAL_MIN_OVERLAP:
        out.append(f"Không xếp bữa tối: ngày kết thúc {day_end}")
    return out


BUCKET_TIMES = {"morning": "09:00", "afternoon": "14:00", "evening": "19:00"}


class BusySlot(BaseModel):
    day: int = Field(ge=1)
    # Trống = user có nói bận nhưng không nói giờ -> hỏi lại (câu H5), không tự đoán.
    busy_start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    busy_end: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    one_person: bool = False
    place: str | None = Field(None, description="Chỗ bận, để hỏi phút đi (get_route)")


# Địa điểm user muốn đi: có giờ buổi thì phải bắt đầu ở chỗ đó trong khung này.
MUST_BUCKETS = {
    "morning": ("08:00", "11:30"),
    "afternoon": ("13:30", "17:30"),
    "evening": ("18:00", "21:30"),
}
MUST_BUCKET_NAMES = {"morning": "buổi sáng", "afternoon": "buổi chiều", "evening": "buổi tối"}
MUST_LATE_MAX = 20  # tới trễ giờ user hẹn quá ngần này phút là lỗi (số tạm, "tu_dat")
# Hẹn giờ cụ thể buổi tối: ngày kết thúc ít nhất giờ hẹn + ngần này phút (ở lại + về; số tạm).
MUST_EVENING_STAY = 90


class MustVisit(BaseModel):
    """Địa điểm user chỉ định: lộ trình phải có, đúng ngày / giờ nếu user nói."""

    name: str = Field(min_length=1, max_length=120)
    day: int | None = Field(
        None, ge=1, description="Ngày thứ mấy của chuyến; None = ngày nào cũng được"
    )
    time: str | None = Field(
        None,
        pattern=r"^(\d{2}:\d{2}|morning|afternoon|evening)$",
        description='"HH:MM" (tới lúc) hoặc morning / afternoon / evening; None = giờ nào cũng',
    )


def must_window(time: str | None) -> tuple[int, int] | None:
    """Khung phút được phép BẮT ĐẦU ở chỗ user chỉ định: giờ cụ thể -> [giờ, giờ + MUST_LATE_MAX];
    buổi -> khung `MUST_BUCKETS`; không nói giờ -> None."""
    if not time:
        return None
    if time in MUST_BUCKETS:
        a, b = MUST_BUCKETS[time]
        return to_minutes(a), to_minutes(b)
    return to_minutes(time), to_minutes(time) + MUST_LATE_MAX


def must_time_text(time: str | None) -> str | None:
    if not time:
        return None
    return MUST_BUCKET_NAMES.get(time, f"lúc {time}")


class TripForm(BaseModel):
    """Form bước 1. Tên trường đúng tên biến trên trang thiết kế."""

    # Chuyến
    destination: str = Field(min_length=1)
    start_date: date
    end_date: date
    group_size: int = Field(ge=1, le=30)
    budget: int | None = Field(None, ge=0, description="VND")
    budget_unit: BudgetUnit = "group_trip"
    stay_booked: str | None = None
    prepaid_tickets: int = Field(0, ge=0, description="Tiền vé đi về đã mua, mỗi người")
    travel_mode: TravelMode | None = None
    message: str = ""
    # Đi cùng ai
    group_type: GroupType | None = None
    kids_under_6: int = Field(0, ge=0)
    kids_6_12: int = Field(0, ge=0)
    seniors_65: int = Field(0, ge=0)
    wheelchair_users: int = Field(0, ge=0)
    required_needs: list[str] = Field(default_factory=list)
    # Nhu cầu bắt buộc user gõ tự do (VD "ăn chay, ông đi xe lăn, không ăn cay"): bước 2 đổi ra mã
    # `required_needs` được thì lọc chỗ; phần không đổi được đưa Planner như lưu ý bắt buộc.
    needs_note: str = Field("", max_length=500)
    # Khung giờ
    arrival_time: str | None = None  # "HH:MM" hoặc morning / afternoon / evening
    departure_time: str | None = None
    checkin_close_time: str | None = None
    wake_time: str | None = None
    night_return_time: str | None = None
    busy_slots: list[BusySlot] = Field(default_factory=list)
    night_out: bool = False
    nap: Nap = "auto"
    # Mục đích, ưu tiên
    purposes: list[Purpose] = Field(default_factory=list)
    interests: str = ""
    walking_level: Walking | None = None
    must_visits: list[MustVisit] = Field(default_factory=list, max_length=10)
    priority: Priority | None = None
    spend_style: SpendStyle = "normal"

    user_id: str | None = None
    thread_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> TripForm:
        if self.end_date < self.start_date:
            raise ValueError("Ngày về phải sau hoặc bằng ngày đi")
        if self.num_days > 7:
            raise ValueError("Tối đa 7 ngày")
        for m in self.must_visits:
            if m.day is not None and m.day > self.num_days:
                raise ValueError(
                    f"{m.name}: chuyến chỉ có {self.num_days} ngày, không có ngày {m.day}"
                )
        return self

    @property
    def num_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def num_nights(self) -> int:
        return self.num_days - 1

    def day_date(self, day: int) -> date:
        """`day` từ 1."""
        return self.start_date + timedelta(days=day - 1)

    @property
    def vehicle(self) -> str:
        """Phương tiện cho tool bản đồ (get_route, distance_matrix)."""
        return "motorcycle" if self.travel_mode == "motorbike" else "car"


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def hhmm(minutes: float) -> str:
    m = max(0, min(int(round(minutes)), 24 * 60 - 1))
    return f"{m // 60:02d}:{m % 60:02d}"


def clock(value: str | None) -> str | None:
    """ "14:30" giữ nguyên; "morning" / "afternoon" / "evening" -> giờ đại diện của buổi."""
    if not value:
        return None
    return BUCKET_TIMES.get(value, value)


def daily_budget(form: TripForm) -> int | None:
    """Ngân sách 1 người 1 ngày, theo cách user nhập (bảng bước 2)."""
    if not form.budget:
        return None
    n, d = form.group_size, form.num_days
    if form.budget_unit == "group_trip":
        return int((form.budget - form.prepaid_tickets * n) / n / d)
    if form.budget_unit == "person_trip":
        return int((form.budget - form.prepaid_tickets) / d)
    return form.budget


def trip_budget(form: TripForm) -> int | None:
    """Ngân sách cả đoàn cả chuyến (trừ vé đã mua). Tính thẳng, không qua số mỗi người mỗi ngày
    (chia rồi nhân lại thì 8 triệu thành 7.999.998 đ)."""
    if not form.budget:
        return None
    n, d = form.group_size, form.num_days
    if form.budget_unit == "group_trip":
        return int(form.budget - form.prepaid_tickets * n)
    if form.budget_unit == "person_trip":
        return int((form.budget - form.prepaid_tickets) * n)
    return int(form.budget * n * d)


def money(v: Any) -> str:
    return "chưa đủ giá" if v is None else f"{int(v):,} đ".replace(",", ".")

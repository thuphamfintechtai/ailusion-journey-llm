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


Priority = Literal["saving", "comfort", "leisure", "short_time"]


SpendStyle = Literal["normal", "unlimited", "luxury_within_budget"]


BudgetUnit = Literal["group_trip", "person_trip", "person_day"]


TravelMode = Literal["motorbike", "car", "bus", "taxi"]


Walking = Literal["M1", "M2", "M3", "M4", "M5"]


Group = Literal["P1", "P2", "P3", "P4"]


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
    "culture": ("bảo tàng", "chùa", "di tích", "nhà thờ", "phố cổ", "làng", "đền", "lăng"),
    "fun": ("khu vui chơi", "công viên giải trí", "chợ đêm", "bar", "phố đi bộ", "show"),
    "adventure": ("núi", "thác", "trekking", "hang", "đèo", "rừng"),
    "shopping": ("chợ", "trung tâm thương mại", "cửa hàng lưu niệm"),
}


GROUP_NAMES = {"P1": "tiết kiệm", "P2": "đỡ mệt", "P3": "thong thả", "P4": "ít thời gian"}
PRIORITY_GROUP: dict[str, Group] = {
    "saving": "P1",
    "comfort": "P2",
    "leisure": "P3",
    "short_time": "P4",
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
}


# Số tạm (nguồn "tu_dat"), ghi rõ trong kết quả khi phải dùng.
DEFAULT_ARRIVAL = "11:00"
DEFAULT_DEPARTURE = "15:00"
DEFAULT_WAKE = "08:00"
DEFAULT_NIGHT_RETURN = "22:00"
CHECKIN_MIN = 90  # thủ tục ở bến
DEPARTURE_BUFFER_MIN = 30
HUB_EXIT_MIN = 15  # ra khỏi bến
HUB_TRAVEL_MIN = 30  # bến <-> chỗ ở khi chưa biết vị trí bến
BUSY_TRAVEL_MIN = 15  # tới chỗ bận khi chưa biết vị trí
LOW_FREE_MINUTES = 300  # B1: ngày rảnh ít
LOW_BUDGET_RATIO = 1.5  # B3
# Khung giờ ăn (quy ước sản phẩm, khớp luật 6 của Agent Planner). Bữa sáng ăn ở chỗ ở, không xếp.
LUNCH = ("11:00", "13:30")
DINNER = ("18:00", "20:00")
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
    # Khung giờ
    arrival_time: str | None = None  # "HH:MM" hoặc morning / afternoon / evening
    departure_time: str | None = None
    checkin_close_time: str | None = None
    wake_time: str | None = None
    night_return_time: str | None = None
    busy_slots: list[BusySlot] = Field(default_factory=list)
    night_out: bool = False
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
    per_day = daily_budget(form)
    return per_day * form.group_size * form.num_days if per_day is not None else None


def money(v: Any) -> str:
    return "chưa đủ giá" if v is None else f"{int(v):,} đ".replace(",", ".")

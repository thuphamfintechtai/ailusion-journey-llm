"""Form đầu vào của POST /plan — 8 nhóm, 29 trường, khớp bảng thông tin của prototype.

Mọi trường đều có mặc định: người dùng bỏ trống nhóm nào thì nhóm đó dùng mặc định và được ghi
vào `assumptions()` để trả về cùng lộ trình. Ràng buộc cứng (vi phạm là loại hẳn) ghi chú
"cứng" trong mô tả; còn lại là ràng buộc mềm, chỉ ảnh hưởng điểm chấm.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"

Interest = Literal[
    "thiennhien",
    "amthuc",
    "vanhoa",
    "checkin",
    "cafe",
    "nghiduong",
    "phieuluu",
    "bien",
    "muasam",
    "giaitri",
    "dem",
]
INTEREST_NAMES: dict[str, str] = {
    "thiennhien": "Thiên nhiên",
    "amthuc": "Ẩm thực",
    "vanhoa": "Văn hoá, lịch sử",
    "checkin": "Chụp ảnh, check-in",
    "cafe": "Cà phê",
    "nghiduong": "Nghỉ dưỡng",
    "phieuluu": "Phiêu lưu",
    "bien": "Biển",
    "muasam": "Mua sắm",
    "giaitri": "Vui chơi, giải trí",
    "dem": "Về đêm",
}
Persona = Literal["P1", "P2", "P3", "P4"]
PERSONA_NAMES = {
    "P1": "Khám phá tiết kiệm",
    "P2": "Thoải mái và dễ tiếp cận",
    "P3": "Thư giãn và trải nghiệm",
    "P4": "Quỹ thời gian hạn chế",
}
Transport = Literal["xemay", "taxi", "xerieng", "congcong"]
TRANSPORT_NAMES = {
    "xemay": "Xe máy tự lái",
    "taxi": "Grab hoặc taxi",
    "xerieng": "Thuê xe riêng có tài xế",
    "congcong": "Đi bộ và xe buýt",
}
Mobility = Literal["nhe", "vua", "cao"]
MOBILITY_NAMES = {"nhe": "Nhẹ nhàng", "vua": "Vừa phải", "cao": "Năng động"}
# Trần số điểm tham quan mỗi ngày đủ giờ theo nhịp độ người dùng chọn — chiến lược không vượt.
MOBILITY_STOPS = {"nhe": 3, "vua": 4, "cao": 5}
RainPolicy = Literal["indoor", "flex", "ok"]
RAIN_NAMES = {
    "indoor": "Ưu tiên hoạt động trong nhà",
    "flex": "Linh hoạt theo dự báo",
    "ok": "Không ngại mưa",
}


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def busy_minutes(start: int, end: int, events: list[tuple[int, int]]) -> int:
    """Số phút lịch cố định chồng lên khung [start, end] — phần nằm ngoài khung không tính."""
    return sum(max(0, min(e, end) - max(s, start)) for s, e in events)


def _default_start() -> date:
    return datetime.now(VN_TZ).date() + timedelta(days=7)


class Basic(BaseModel):
    destination: str = Field("Đà Lạt", description="Điểm đến")
    start_date: date = Field(default_factory=_default_start, description="Ngày đi")
    days: int = Field(3, ge=1, le=5, description="Số ngày")
    adults: int = Field(2, ge=1, le=12, description="Người lớn")
    children: int = Field(0, ge=0, le=8, description="Trẻ dưới 12 tuổi")
    seniors: int = Field(0, ge=0, le=6, description="Người lớn tuổi")

    @property
    def people(self) -> int:
        return self.adults + self.children + self.seniors

    @property
    def end_date(self) -> date:
        return self.start_date + timedelta(days=self.days - 1)


class Caps(BaseModel):
    """Trần theo hạng mục cho cả chuyến (cứng). Bỏ trống = không giới hạn."""

    stay: int | None = Field(None, ge=0, description="Lưu trú tối đa, VND")
    food: int | None = Field(None, ge=0, description="Ăn uống tối đa, VND")
    tickets: int | None = Field(None, ge=0, description="Vé tham quan tối đa, VND")
    move: int | None = Field(None, ge=0, description="Di chuyển tại chỗ tối đa, VND")


class Budget(BaseModel):
    total_vnd: int = Field(8_000_000, ge=500_000, description="Tổng ngân sách cả đoàn")
    hard: bool = Field(True, description="True = không được vượt (cứng); False = linh hoạt")
    need_stay: bool = Field(True, description="False = đã có chỗ ở, không tính tiền phòng")
    intercity: bool = Field(False, description="Ngân sách đã gồm vé đi – về")
    origin: str | None = Field(None, description="Nơi xuất phát khi có vé đi – về")
    caps: Caps = Field(default_factory=Caps)


class FixedEvent(BaseModel):
    """Lịch cố định không dời được (cứng), VD họp, đám cưới."""

    day: int = Field(ge=1, description="Ngày thứ mấy của chuyến đi")
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)
    label: str = "Lịch cố định"

    @model_validator(mode="after")
    def _ordered(self) -> FixedEvent:
        if to_minutes(self.end) <= to_minutes(self.start):
            raise ValueError("giờ kết thúc phải sau giờ bắt đầu")
        return self


class TimePrefs(BaseModel):
    day_start: str = Field("08:00", pattern=HHMM, description="Bắt đầu mỗi ngày")
    day_end: str = Field("21:00", pattern=HHMM, description="Về chỗ ở trước (cứng)")
    first_start: str = Field("09:00", pattern=HHMM, description="Ngày đầu bắt đầu từ (cứng)")
    last_end: str = Field("17:00", pattern=HHMM, description="Ngày cuối kết thúc lúc (cứng)")
    fixed: list[FixedEvent] = Field(default_factory=list)


class Interests(BaseModel):
    tags: dict[Interest, Literal[1, 2]] = Field(
        default_factory=dict, description="1 = thích, 2 = rất thích"
    )
    avoid_crowd: bool = Field(False, description="Ưu tiên nơi vắng (mềm)")
    crowd_hard: bool = Field(False, description="Tuyệt đối không đến nơi rất đông (cứng)")


class WeatherPrefs(BaseModel):
    rain: RainPolicy = "flex"
    rain_hard: bool = Field(False, description="Không xếp ngoài trời khi dự báo mưa từ 60% (cứng)")


class Access(BaseModel):
    """Yêu cầu đặc biệt của đoàn — tất cả là ràng buộc cứng."""

    no_stairs: bool = Field(False, description="Hạn chế leo dốc, bậc thang")
    wheelchair: bool = Field(False, description="Có người dùng xe lăn")
    siesta: bool = Field(False, description="Cần nghỉ trưa 90 phút")
    veg: bool = Field(False, description="Ăn chay")
    no_seafood: bool = Field(False, description="Dị ứng hải sản")


class PlanRequest(BaseModel):
    persona: Persona = Field(
        "P1",
        description="Nhóm người dùng: P1 khám phá tiết kiệm · P2 thoải mái, dễ tiếp cận · "
        "P3 thư giãn, trải nghiệm · P4 quỹ thời gian hạn chế",
    )
    basic: Basic = Field(default_factory=Basic)
    budget: Budget = Field(default_factory=Budget)
    time: TimePrefs = Field(default_factory=TimePrefs)
    interests: Interests = Field(default_factory=Interests)
    weather: WeatherPrefs = Field(default_factory=WeatherPrefs)
    transport: Transport = "taxi"
    mobility: Mobility = "vua"
    access: Access = Field(default_factory=Access)

    user_id: str | None = Field(None, description="Mã người dùng, để gắn trace")
    thread_id: str | None = Field(None, description="Bỏ trống -> tạo mới")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "persona": "P2",
                    "basic": {
                        "destination": "Đà Lạt",
                        "start_date": "2026-10-17",
                        "days": 3,
                        "adults": 2,
                        "children": 1,
                    },
                    "budget": {"total_vnd": 15_000_000, "hard": True},
                    "interests": {"tags": {"thiennhien": 2, "cafe": 1, "amthuc": 1}},
                    "access": {"no_seafood": True},
                }
            ]
        }
    }

    @field_validator("time")
    @classmethod
    def _window(cls, t: TimePrefs) -> TimePrefs:
        if to_minutes(t.day_end) - to_minutes(t.day_start) < 240:
            raise ValueError("khung giờ mỗi ngày phải dài ít nhất 4 tiếng")
        return t

    @model_validator(mode="after")
    def _fixed_in_trip(self) -> PlanRequest:
        for event in self.time.fixed:
            if event.day > self.basic.days:
                raise ValueError(f"lịch cố định ngày {event.day} nằm ngoài {self.basic.days} ngày")
        if self.budget.intercity and not self.budget.origin:
            raise ValueError("có vé đi – về thì cần nơi xuất phát (budget.origin)")
        return self


GROUP_NAMES = {
    "basic": "Cơ bản",
    "budget": "Ngân sách",
    "time": "Thời gian",
    "interests": "Sở thích",
    "weather": "Thời tiết",
    "transport": "Phương tiện",
    "mobility": "Nhịp độ",
    "access": "Tiếp cận",
}


def assumptions(req: PlanRequest) -> list[str]:
    """Nhóm người dùng không gửi đang dùng mặc định — ghi lại để giải thích cùng lộ trình."""
    notes = []
    sent = req.model_fields_set
    if "persona" not in sent:
        notes.append(f"Nhóm người dùng: chưa chọn, mặc định {PERSONA_NAMES[req.persona]}.")
    b = req.basic
    if "basic" not in sent:
        notes.append(
            f"Cơ bản: mặc định {b.destination}, {b.days} ngày từ {b.start_date:%d/%m}, "
            f"{b.adults} người lớn."
        )
    if "budget" not in sent:
        notes.append(
            f"Ngân sách: mặc định {req.budget.total_vnd:,} đ, không được vượt.".replace(",", ".")
        )
    if "time" not in sent:
        t = req.time
        notes.append(f"Thời gian: mặc định đi chơi {t.day_start}–{t.day_end} mỗi ngày.")
    if "interests" not in sent or not req.interests.tags:
        notes.append("Sở thích: chưa chọn, ưu tiên các điểm được đánh giá cao.")
    if "weather" not in sent:
        notes.append(f"Thời tiết: mặc định {RAIN_NAMES[req.weather.rain].lower()}.")
    if "transport" not in sent:
        notes.append(f"Phương tiện: mặc định {TRANSPORT_NAMES[req.transport].lower()}.")
    if "mobility" not in sent:
        notes.append(f"Nhịp độ: mặc định {MOBILITY_NAMES[req.mobility].lower()}.")
    return notes

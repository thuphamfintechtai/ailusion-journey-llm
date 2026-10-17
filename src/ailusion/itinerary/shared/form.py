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
GROUP_TYPE_NAMES: dict[str, str] = {
    "solo": "Một mình",
    "couple": "Cặp đôi",
    "friends": "Bạn bè",
    "colleagues": "Đồng nghiệp",
    "family": "Gia đình",
}


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


# Mã nhu cầu đúng mã của MCP (`_nhu_cau.NHU_CAU`) -> nghĩa (Intake đổi chữ user gõ ra mã;
# Planner, ghi chú kết quả ghi bằng nghĩa).
NEED_NAMES: dict[str, str] = {
    "wheelchair": "lối vào cho xe lăn",
    "wheelchair_toilet": "nhà vệ sinh cho xe lăn",
    "wheelchair_parking": "chỗ đỗ xe cho xe lăn",
    "hearing_assist": "hỗ trợ người khiếm thính",
    "kids": "phù hợp trẻ em",
    "high_chair": "ghế cao cho bé",
    "kids_menu": "thực đơn trẻ em",
    "playground": "sân chơi",
    "baby_changing": "bàn thay tã / phòng cho con bú",
    "vegetarian": "có món chay",
    "vegan": "có món thuần chay",
    "late_night_food": "đồ ăn khuya",
    "breakfast": "phục vụ bữa sáng",
    "alcohol": "có đồ uống có cồn",
    "private_room": "phòng ăn riêng",
    "car_parking": "có chỗ đỗ ô tô",
    "free_parking": "đỗ xe miễn phí",
    "card_payment": "thanh toán thẻ",
    "pets": "cho mang chó theo",
    "wifi": "Wi-Fi",
    "restroom": "có nhà vệ sinh",
    "luggage_storage": "giữ hộ hành lý",
    "outdoor_seating": "chỗ ngồi ngoài trời",
    "reservations": "nhận đặt chỗ",
    "groups": "hợp đi nhóm đông",
    "quiet": "yên tĩnh",
}

# Nhu cầu chỉ có nghĩa với quán ăn (chỗ chơi, chỗ ở không có "món chay", "ghế cao cho bé"): chỉ
# xét ở quán — trước đây bãi biển, bảo tàng cũng bị ghi "chưa rõ có đáp ứng nhu cầu bắt buộc".
MEAL_NEEDS = frozenset(
    {"vegetarian", "vegan", "high_chair", "kids_menu", "late_night_food", "breakfast",
     "alcohol", "private_room"}
)  # fmt: skip


def needs_for(kind: str, needs: list[str]) -> list[str]:
    """Nhu cầu áp cho một chỗ loại `kind` ("visit" | "meal" | "stay")."""
    return [n for n in needs if kind == "meal" or n not in MEAL_NEEDS]


def meets_needs(kind: str, status: dict[str, str], needs: list[str]) -> bool:
    """Chỗ có bằng chứng ("yes") đáp ứng mọi nhu cầu bắt buộc áp cho loại của nó."""
    return all(status.get(n) == "yes" for n in needs_for(kind, needs))


def unknown_needs(kind: str, status: dict[str, str], needs: list[str]) -> list[str]:
    return [n for n in needs_for(kind, needs) if status.get(n, "unknown") == "unknown"]


def need_text(needs: list[str]) -> str:
    return ", ".join(NEED_NAMES.get(n, n) for n in needs)


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
# Ngày có ít nhất ngần này phút trong khung mốc của một bữa thì bữa đó tính là trong giờ rảnh;
# đang bận / dự tiệc phủ ngần này phút của khung giờ ăn thì ăn ở đó.
MEAL_MIN_OVERLAP = 30
# Một bữa ở quán: (ngắn, dài) phút (số tạm, "tu_dat"). Bữa chỉ bắt buộc khi cả khung giờ ăn còn
# chứa được bữa ngắn nhất: về chỗ ở trước 18:30 thì bữa tối (18:00–20:00) chỉ còn 30 phút, từng
# bắt buộc rồi lịch nào cũng lỗi (có bữa tối thì về trễ, bỏ thì "thiếu bữa tối").
MEAL_STAY = (45, 75)
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

    def overlap(window: tuple[str, str]) -> int:
        return min(b, to_minutes(window[1])) - max(a, to_minutes(window[0]))

    def covers(target: tuple[str, str], whole: tuple[str, str]) -> bool:
        return overlap(target) >= MEAL_MIN_OVERLAP and overlap(whole) >= MEAL_STAY[0]

    out = []
    if a <= to_minutes(BREAKFAST_IF_START_BY) and covers(BREAKFAST_TARGET, BREAKFAST):
        out.append("sáng")
    if a < to_minutes(LUNCH_IF_START_BEFORE) and covers(LUNCH_TARGET, LUNCH):
        out.append("trưa")
    if covers(DINNER, DINNER):
        out.append("tối")
    return out


def busy_meals(window: Any) -> set[str]:
    """Bữa có giờ bận / dự tiệc phủ ít nhất MEAL_MIN_OVERLAP phút của khung giờ ăn (`DayWindow`):
    ăn ở đó / quanh đó (tiệc cưới từ 16:45), không xếp quán, không bắt "có quán ăn"."""
    busy = [
        (to_minutes(a), to_minutes(b))
        for a, b in (s.split("–") for s in [*window.blocked_slots, *window.meal_only_slots])
    ]
    return {
        name
        for name, (m0, m1) in MEALS.items()
        if any(
            min(b1, to_minutes(m1)) - max(b0, to_minutes(m0)) >= MEAL_MIN_OVERLAP for b0, b1 in busy
        )
    }


def needed_meals(window: Any) -> set[str]:
    """Bữa phải xếp quán của một ngày (`DayWindow`): `required_meals` trừ `busy_meals`. Dùng
    chung cho Planner, bước chia chỗ và bước kiểm lịch — trước đây Planner vẫn được bảo "bữa bắt
    buộc: trưa" khi giờ bận phủ cả buổi trưa, xếp bữa trưa sau giờ bận rồi về trễ."""
    return set(required_meals(window.day_start, window.day_end)) - busy_meals(window)


def _is_hhmm(value: str | None) -> bool:
    return bool(value) and len(value) == 5 and value[2] == ":" and value.replace(":", "").isdigit()  # type: ignore[union-attr]


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
    if "tối" not in need and a > to_minutes(DINNER[1]) - MEAL_STAY[0]:
        # Tới nơi buổi tối (19:45): trước đây không xếp bữa tối mà không nói gì.
        out.append(f"Không xếp bữa tối: ngày bắt đầu {day_start}")
    elif "tối" not in need and b < to_minutes(DINNER[0]) + MEAL_STAY[0]:
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
    # Mức dưới của ngân sách (cùng đơn vị `budget_unit`): user chọn khoảng ("3–6 triệu", "trên 10
    # triệu" ở câu hỏi H3) thì ngân sách là khoảng budget_min–budget (budget trống = không trần),
    # thay vì ±BUDGET_BAND quanh một số user không nói.
    budget_min: int | None = Field(None, ge=0, description="VND")
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
        # Giờ nhập sai: báo ngay (trước đây lập ra cả chuyến trống, không nói gì).
        arrive, leave = clock(self.arrival_time), clock(self.departure_time)
        if self.num_days == 1 and _is_hhmm(arrive) and _is_hhmm(leave):
            if to_minutes(leave) <= to_minutes(arrive):  # type: ignore[arg-type]
                raise ValueError(
                    f"Đi trong ngày: giờ khởi hành về ({leave}) phải sau giờ đến ({arrive})"
                )
        wake, back = self.wake_time, self.night_return_time
        # Giờ về tối nhập buổi sáng (01:00 = khuya) thì không so được: chỉ so khi về từ 12:00.
        if (
            not self.night_out
            and _is_hhmm(wake)
            and _is_hhmm(back)
            and to_minutes(back) >= 12 * 60  # type: ignore[arg-type]
            and to_minutes(wake) >= to_minutes(back)  # type: ignore[arg-type]
        ):
            raise ValueError(f"Giờ thức dậy ({wake}) phải trước giờ về chỗ ở buổi tối ({back})")
        # "Đi cùng ai" va số người: báo ngay (trước đây "Một mình" mà 3 người vẫn lập lộ trình,
        # ghi "hợp đi một mình", tính 2 phòng; 3 bé trong đoàn 2 người chỉ tính tiền 2 người).
        n, kids = self.group_size, self.kids_under_6 + self.kids_6_12
        if self.group_type == "solo" and n > 1:
            raise ValueError(f'Đi một mình nhưng số người là {n}: sửa "Số người" hoặc "Đi cùng ai"')
        if self.group_type not in (None, "solo") and n == 1:
            raise ValueError(
                f'{GROUP_TYPE_NAMES[self.group_type]} nhưng số người là 1: sửa "Số người" '
                'hoặc "Đi cùng ai"'
            )
        if kids + self.seniors_65 > n:
            raise ValueError(
                f"Số trẻ em và người từ 65 tuổi ({kids + self.seniors_65}) nhiều hơn số người ({n})"
            )
        if kids and kids >= n:
            raise ValueError(f"Có {kids} trẻ em thì số người ({n}) phải gồm cả người lớn đi cùng")
        if self.wheelchair_users > n:
            raise ValueError(
                f"Số người ngồi xe lăn ({self.wheelchair_users}) nhiều hơn số người ({n})"
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


# Luật lịch dùng chung cho bước chạy thử (coordinator/simulate.py) và prompt Planner, để prompt nói
# đúng số code kiểm:
LONG_WAIT_MAX = 45  # tới một chỗ phải chờ mở cửa / chờ giờ ăn quá ngần này phút là lỗi
DINNER_FREE_MAX = 150  # bữa tối là điểm cuối ngày: trống trước đó tối đa ngần này phút (tự do)
MIN_VISITS = 2  # ngày dài (rảnh ≥ FULL_DAY_MIN phút) ít nhất ngần này chỗ chơi, thiếu thì code bù
FULL_DAY_MIN = 360
# "Gần": cách một chỗ đang có của ngày tối đa ngần này phút. Bù chỗ (simulate.fill_visits) chỉ lấy
# chỗ gần (không thêm đường vòng); chia chỗ theo ngày (score.split_by_day) xét ngày gần trước (không
# trộn 2 vùng trong một ngày khi không cần). Số tạm (tự đặt).
NEAR_MIN = 30

# Tổng ước lệch ngân sách tối đa ±BUDGET_BAND (user yêu cầu) khi user nhập 1 con số.
BUDGET_BAND = 0.05
# Chi phí phát sinh: không tính trước được từng khoản (thuế, mưa làm giá xe tăng, gửi xe, nước,
# ăn vặt...), để dành theo % tiền ở + vé + ăn + đi lại. Số tạm (tu_dat). Ghép phương án theo ngân
# sách và Planner dùng số giữa `EXTRA_MID`; tab Chi phí hiện đủ khoảng (tabs.extra_pct).
EXTRA_PCT = (0.10, 0.15)
EXTRA_MID = sum(EXTRA_PCT) / 2


def _to_trip(form: TripForm, value: int) -> int:
    """Một mức tiền theo đơn vị user nhập -> cả đoàn cả chuyến (trừ vé đã mua)."""
    n, d = form.group_size, form.num_days
    if form.budget_unit == "group_trip":
        return int(value - form.prepaid_tickets * n)
    if form.budget_unit == "person_trip":
        return int((value - form.prepaid_tickets) * n)
    return int(value * n * d)


def budget_band(form: TripForm) -> tuple[int | None, int | None] | None:
    """(thấp, cao) ngân sách cả đoàn cả chuyến mà tổng ước phải nằm trong; None = không có ngân
    sách. User chọn khoảng (`budget_min`) thì đúng khoảng đó (thiếu cao = không trần, thấp 0 =
    không sàn); "sang nhưng trong ngân sách" thì không được vượt (cao = ngân sách); còn lại
    ±BUDGET_BAND quanh ngân sách."""
    if form.budget_min is not None:
        low = _to_trip(form, form.budget_min) if form.budget_min > 0 else None
        high = _to_trip(form, form.budget) if form.budget else None
        return (low, high) if low or high else None
    if not form.budget:
        return None
    b = _to_trip(form, form.budget)
    if form.spend_style == "luxury_within_budget":
        return int(b * (1 - BUDGET_BAND)), b
    return int(b * (1 - BUDGET_BAND)), int(b * (1 + BUDGET_BAND))


def trip_budget(form: TripForm) -> int | None:
    """Ngân sách cả đoàn cả chuyến làm MỐC (chia tiền phòng, tiền mỗi ngày): số user nhập; user
    chọn khoảng thì giữa khoảng (không sàn thì mức trần, không trần thì mức sàn). Tính thẳng,
    không qua số mỗi người mỗi ngày (chia rồi nhân lại thì 8 triệu thành 7.999.998 đ)."""
    if form.budget_min is not None:
        band = budget_band(form)
        if band is None:
            return None
        low, high = band
        return (low + high) // 2 if low and high else (high or low)
    if not form.budget:
        return None
    return _to_trip(form, form.budget)


def daily_budget(form: TripForm) -> int | None:
    """Ngân sách 1 người 1 ngày (theo mốc `trip_budget`)."""
    total = trip_budget(form)
    return int(total / form.group_size / form.num_days) if total else None


def group_of(form: TripForm) -> GroupType | None:
    """Đi cùng ai: user chọn; chưa chọn thì suy từ số người — 1 người là một mình, có trẻ em /
    người từ 65 tuổi là gia đình, không hỏi lại "Bạn đi cùng ai?" (trước đây hỏi, bấm "Gia đình có
    trẻ nhỏ" còn ghi đè số bé user đã nhập thành 1). Không suy được thì None."""
    if form.group_type:
        return form.group_type
    if form.group_size == 1:
        return "solo"
    if form.kids_under_6 or form.kids_6_12 or form.seniors_65:
        return "family"
    return None


def rooms_for(form: TripForm) -> int:
    """Số phòng: 2 người một phòng, trẻ dưới 6 tuổi ngủ chung giường bố mẹ (khách sạn thường không
    tính thêm) — trước đây gia đình 2 người lớn + 2 bé dưới 6 tuổi tính 2 phòng."""
    return max(1, -(-(form.group_size - form.kids_under_6) // 2))


def day_allowance(form: TripForm, trip_total: int | None, stay_price: int | None) -> int | None:
    """Tiền nên tiêu mỗi ngày cho cả đoàn — vé + ăn + đi lại (đúng phần `day_spend` đo), chưa
    gồm chỗ ở và phát sinh: (ngân sách ÷ (1 + phát sinh) − tiền phòng) ÷ số ngày. Planner nhận số
    này, bước chạy thử so lịch với số này. Không có ngân sách / "không giới hạn" thì None."""
    if not trip_total or form.spend_style == "unlimited":
        return None
    rooms = rooms_for(form)
    stay_total = (stay_price or 0) * form.num_nights * rooms if not form.stay_booked else 0
    rest = trip_total / (1 + EXTRA_MID) - stay_total
    return int(round(max(0.0, rest) / form.num_days / 1000) * 1000) or None


def money(v: Any) -> str:
    return "chưa đủ giá" if v is None else f"{int(v):,} đ".replace(",", ".")

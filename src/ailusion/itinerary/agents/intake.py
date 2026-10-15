"""Agent đọc lời nhắn — bước 2.

Vào: các ô form còn trống + lời nhắn tự do. Ra: `Extraction` (giá trị + câu trích cho từng ô).
Code kiểm (`apply_extracted`): chỉ nhận ý có câu trích nằm nguyên văn trong lời nhắn và giá trị
nằm trong danh sách cho phép; ô user đã điền giữ nguyên. Chỉ chạy khi có lời nhắn và còn ô trống.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..shared.context import Ctx
from ..shared.form import TripForm
from ..shared.llm import OUTPUT_TOKENS, norm, quote_found
from ..shared.models import ItineraryState, _plain, form_of
from ..shared.tools import kind_of

INTAKE_TEMPLATE = """Bạn đọc lời nhắn tự do của người dùng trong ứng dụng lập lộ trình du lịch Việt Nam, để điền các ô form họ còn bỏ trống.

# Quy tắc

1. Chỉ điền ô có trong danh sách "Ô còn trống". Ô user đã điền trên form thì không đụng.
2. Mỗi ý phải kèm `quote`: câu user đã viết, chép NGUYÊN VĂN từ lời nhắn (đúng chữ, đúng dấu). Không chép được thì không điền ý đó.
3. Ô có danh sách giá trị thì chỉ dùng đúng một giá trị trong danh sách. Không chắc thì bỏ.
4. Không tự đặt số, không đổi đơn vị: tiền và giờ giữ nguyên chữ user viết ("1,5 triệu", "5 giờ chiều"), hệ thống tự đổi.
5. Không chọn nhóm khách, không đoán điều user không nói.

# Các ô và giá trị cho phép

- `group_type`: solo (một mình) · couple (cặp đôi) · friends (bạn bè) · colleagues (đồng nghiệp) · family (gia đình)
- `kids_under_6`, `kids_6_12`, `seniors_65` (từ 65 tuổi), `wheelchair_users`: số người, ghi chữ số
- `purposes` (mỗi mục đích một dòng, tối đa 2, theo thứ tự user nhắc): relax (nghỉ ngơi, thư giãn) · food (ăn uống, đặc sản) · culture (khám phá, văn hoá, lịch sử) · fun (vui chơi, sự kiện, về đêm) · adventure (phiêu lưu, leo núi, trekking) · shopping (mua sắm)
- `interests`: thứ cụ thể user muốn ăn / đi / làm, VD "bún bò", "hải sản", "cà phê view", "biển" — lấy cả trong lời nhắn lẫn phần "Nhu cầu bắt buộc user tự ghi" (user hay ghi món muốn ăn ở đó). Ô này LUÔN được đọc (kể cả khi user đã điền), phần lấy được sẽ cộng thêm vào. Mỗi thứ một dòng
- `priority`: comfort (đỡ mệt) · leisure (thong thả) · short_time (ít thời gian) · many_places (đi được nhiều chỗ, "đi nhiều nơi"). "Tiết kiệm" KHÔNG phải ưu tiên (đã có ngân sách), không điền
- `spend_style`: normal · unlimited ("không ngại chi thêm") · luxury_within_budget ("sang nhưng không vượt")
- `arrival_time`, `departure_time`, `wake_time`, `night_return_time`: giờ như user viết, hoặc morning / afternoon / evening
- `night_out`: true / false
- `nap`: nghỉ trưa ở đâu — stay (về khách sạn / chỗ ở nghỉ trưa) · cafe ("nghỉ trưa ở quán cà phê", "ngồi cà phê cho mát") · hotel ("thuê phòng nghỉ trưa", "khách sạn gần đó") · none ("không cần nghỉ trưa", "đi xuyên trưa")
- `walking_level`: M1 (dạo 1–2 tiếng) · M2 (3 tiếng có dốc) · M3 (4–5 tiếng leo dốc) · M4 (cả ngày)
- `budget`: số tiền như user viết; `budget_unit`: group_trip (cả đoàn cả chuyến) · person_trip (mỗi người cả chuyến) · person_day (mỗi người mỗi ngày)
- `busy_slots`: lịch bận — ngày thứ mấy của chuyến, giờ bắt đầu, giờ xong, kèm câu trích. User không nói giờ (VD "ngày 2 có việc") thì để trống giờ, hệ thống sẽ hỏi lại
- `required_needs`: nhu cầu BẮT BUỘC user nêu (trong "Nhu cầu bắt buộc user tự ghi" hoặc lời nhắn), mỗi nhu cầu một dòng, chỉ dùng mã: {need_codes}. Không có mã khớp thì bỏ (hệ thống tự giữ nguyên câu user làm lưu ý)
- `must_visits`: địa điểm CỤ THỂ user muốn đi (tên riêng: "Bà Nà Hills", "chợ Hàn", "Bếp Hên"), kèm ngày thứ mấy và giờ nếu user nói (chép giờ như user viết: "7 giờ tối", "buổi sáng"). Loại chung chung ("đi biển", "ăn hải sản") thì KHÔNG phải địa điểm cụ thể

# Ví dụ

Lời nhắn: "cả nhà 3 thế hệ, bé 4 tuổi, ông bà ngoài 70, muốn nghỉ ngơi và ăn hải sản; ngày 2 dự tiệc từ 5 giờ chiều"
-> group_type = family (quote "cả nhà 3 thế hệ"); kids_under_6 = 1 (quote "bé 4 tuổi"); seniors_65 = 2 (quote "ông bà ngoài 70"); purposes = relax (quote "muốn nghỉ ngơi"), food (quote "ăn hải sản"); interests = "hải sản" (quote "ăn hải sản"); busy_slots: ngày 2, bắt đầu "5 giờ chiều", giờ xong trống (quote "ngày 2 dự tiệc từ 5 giờ chiều").

Lời nhắn: "nhất định phải lên Bà Nà Hills, tối ngày 2 ăn ở Bếp Hên lúc 7 giờ"
-> must_visits: "Bà Nà Hills", ngày trống, giờ trống (quote "nhất định phải lên Bà Nà Hills"); "Bếp Hên", ngày 2, giờ "7 giờ tối" (quote "tối ngày 2 ăn ở Bếp Hên lúc 7 giờ").
"""


# Ô LLM được điền và giá trị cho phép (None = chữ / số tự do, code tự đổi).
ALLOWED: dict[str, tuple[str, ...] | None] = {
    "group_type": ("solo", "couple", "friends", "colleagues", "family"),
    "kids_under_6": None,
    "kids_6_12": None,
    "seniors_65": None,
    "wheelchair_users": None,
    "purposes": ("relax", "food", "culture", "fun", "adventure", "shopping"),
    "interests": None,
    # Không nhận "saving": đã có ngân sách, tiết kiệm thì user giảm ngân sách.
    "priority": ("comfort", "leisure", "short_time", "many_places"),
    "spend_style": ("normal", "unlimited", "luxury_within_budget"),
    "arrival_time": None,
    "departure_time": None,
    "wake_time": None,
    "night_return_time": None,
    "night_out": ("true", "false"),
    "nap": ("stay", "cafe", "hotel", "none"),
    "walking_level": ("M1", "M2", "M3", "M4", "M5"),
    "budget": None,
    "budget_unit": ("group_trip", "person_trip", "person_day"),
}


INT_FIELDS = {"kids_under_6", "kids_6_12", "seniors_65", "wheelchair_users", "budget"}
TIME_FIELDS = {"arrival_time", "departure_time", "wake_time", "night_return_time"}


class Extracted(BaseModel):
    field: str = Field(description="Tên ô form, VD 'group_type', 'purposes', 'busy_slots'")
    value: str = Field(
        description="Giá trị; số giữ nguyên chữ user viết, VD '1,5 triệu', '5 giờ chiều'"
    )
    quote: str = Field(description="Câu user viết, chép nguyên văn từ lời nhắn")


class ExtractedBusy(BaseModel):
    day: int = Field(ge=1)
    busy_start: str = Field(
        "", description="Giờ bắt đầu bận, user viết thế nào chép thế đó; không nói thì để trống"
    )
    busy_end: str | None = None
    quote: str


class ExtractedMust(BaseModel):
    name: str = Field(description="Tên riêng của địa điểm, chép như user viết")
    day: int | None = Field(None, ge=1, description="Ngày thứ mấy của chuyến; không nói thì trống")
    time: str = Field(
        "", description="Giờ như user viết ('7 giờ tối', 'buổi sáng'); không nói thì trống"
    )
    quote: str


# Mã nhu cầu đúng mã của MCP (`_nhu_cau.NHU_CAU`) -> nghĩa, để LLM đổi chữ user gõ ra mã lọc được.
NEED_CODES: dict[str, str] = {
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


INTAKE = INTAKE_TEMPLATE.replace(
    "{need_codes}", " · ".join(f"{c} ({v})" for c, v in NEED_CODES.items())
)


class ExtractedNeed(BaseModel):
    code: str = Field(description="Mã nhu cầu trong danh sách cho phép")
    quote: str = Field(description="Câu user viết, chép nguyên văn")


class Extraction(BaseModel):
    """Đầu ra LLM bước 2."""

    items: list[Extracted] = Field(default_factory=list)
    busy_slots: list[ExtractedBusy] = Field(default_factory=list)
    must_visits: list[ExtractedMust] = Field(default_factory=list)
    required_needs: list[ExtractedNeed] = Field(default_factory=list)


def parse_clock(text: str) -> str | None:
    """ "5 giờ chiều" -> "17:00", "17h30" -> "17:30", "9:15" -> "09:15", "sáng" -> None."""
    t = norm(text)
    m = re.search(r"(\d{1,2})\s*(?:h|giờ|g|:)\s*(\d{2})?", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if any(w in t for w in ("chiều", "tối", "pm")) and h < 12:
        h += 12
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def parse_must_time(text: str) -> str | None:
    """ "7 giờ tối" -> "19:00"; "buổi sáng" -> "morning"; không đọc được -> None."""
    if clock_ := parse_clock(text):
        return clock_
    t = norm(text)
    for word, bucket in (
        ("sáng", "morning"),
        ("trưa", "afternoon"),
        ("chiều", "afternoon"),
        ("tối", "evening"),
        ("đêm", "evening"),
    ):
        if word in t:
            return bucket
    return None


def parse_money_vi(text: str) -> int | None:
    """ "1,5 triệu" -> 1_500_000, "500k" -> 500_000, "2 củ" -> 2_000_000. Dự phòng khi tool lỗi."""
    t = norm(text).replace(" ", "")
    m = re.search(r"(\d+(?:[.,]\d+)?)(triệu|tr|củ|m|k|nghìn|ngàn|đ|vnd)?", t)
    if not m:
        return None
    num = float(m.group(1).replace(",", "."))
    unit = m.group(2) or ""
    mult = {"triệu": 1e6, "tr": 1e6, "củ": 1e6, "m": 1e6, "k": 1e3, "nghìn": 1e3, "ngàn": 1e3}
    return int(num * mult.get(unit, 1 if num >= 1000 else 1e6))


def apply_extracted(
    form: TripForm, extraction: Extraction, sent: set[str]
) -> tuple[TripForm, list[dict[str, str]]]:
    """Điền ô còn trống bằng ý LLM lấy ra. `sent`: các ô user đã điền trên form (giữ nguyên).

    -> (form mới, danh sách ý đã nhận kèm câu trích — để giải thích "vì sao máy hiểu vậy").
    """
    data = form.model_dump()
    kept: list[dict[str, str]] = []
    purposes: list[str] = []
    said = f"{form.message}\n{form.needs_note}"
    for item in extraction.items:
        field, value = item.field, item.value.strip()
        # Sở thích luôn được cộng thêm từ lời nhắn (ô đã điền thì nối vào, không ghi đè); ô khác
        # user đã điền thì giữ nguyên.
        appendable = field == "interests"
        if (
            field not in ALLOWED
            or (field in sent and not appendable)
            # Sở thích có thể nằm ở ô "nhu cầu khác" (VD "bún bò"); ô khác chỉ đọc lời nhắn.
            or not quote_found(item.quote, said if appendable else form.message)
        ):
            continue
        allowed = ALLOWED[field]
        if allowed is not None and value not in allowed:
            continue
        if field == "purposes":
            if value not in purposes:
                purposes.append(value)
        elif field in INT_FIELDS:
            number = parse_money_vi(value) if field == "budget" else _int(value)
            if number is None:
                continue
            data[field] = number
        elif field in TIME_FIELDS:
            parsed = parse_clock(value) or (
                value if value in ("morning", "afternoon", "evening") else None
            )
            if parsed is None:
                continue
            data[field] = parsed
        elif field == "interests":
            have = [w.strip() for w in (data.get("interests") or "").split(",") if w.strip()]
            new = [w.strip() for w in value.split(",") if w.strip()]
            low = {norm(w) for w in have}
            data["interests"] = ", ".join(have + [w for w in new if norm(w) not in low])
        elif field == "night_out":
            data[field] = value == "true"
        else:
            data[field] = value
        kept.append({"field": field, "value": value, "quote": item.quote})
    if purposes and "purposes" not in sent:
        data["purposes"] = purposes[:2]
    if extraction.busy_slots and "busy_slots" not in sent:
        busy = []
        for b in extraction.busy_slots:
            # Không đọc được giờ thì vẫn giữ lịch bận với giờ trống: bước 4 hỏi lại (H5).
            start = parse_clock(b.busy_start)
            if b.day > form.num_days or not quote_found(b.quote, form.message):
                continue
            busy.append(
                {"day": b.day, "busy_start": start, "busy_end": parse_clock(b.busy_end or "")}
            )
            when = start or "chưa rõ giờ"
            kept.append({"field": "busy_slots", "value": f"ngày {b.day} {when}", "quote": b.quote})
        if busy:
            data["busy_slots"] = busy
    # Nhu cầu bắt buộc: luôn cộng thêm (không ghi đè ô đã chọn), mã phải nằm trong danh sách và
    # câu trích phải có trong lời nhắn hoặc ô "nhu cầu khác".
    needs = list(data.get("required_needs") or [])
    for n in extraction.required_needs:
        if n.code in NEED_CODES and quote_found(n.quote, said) and n.code not in needs:
            needs.append(n.code)
            kept.append({"field": "required_needs", "value": NEED_CODES[n.code], "quote": n.quote})
    data["required_needs"] = needs
    if extraction.must_visits and "must_visits" not in sent:
        musts = []
        for m in extraction.must_visits:
            name = m.name.strip()
            if (
                not name
                or not quote_found(m.quote, form.message)
                or not quote_found(name, form.message)
            ):
                continue
            day = m.day if m.day and m.day <= form.num_days else None
            time = parse_must_time(m.time) if m.time else None
            musts.append({"name": name, "day": day, "time": time})
            when = ", ".join(x for x in (f"ngày {day}" if day else "", time or "") if x)
            kept.append(
                {
                    "field": "must_visits",
                    "value": name + (f" ({when})" if when else ""),
                    "quote": m.quote,
                }
            )
        if musts:
            data["must_visits"] = musts[:10]
    return TripForm.model_validate(data), kept


def _int(text: str) -> int | None:
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


ALL_FILLABLE = (
    "group_type",
    "kids_under_6",
    "kids_6_12",
    "seniors_65",
    "wheelchair_users",
    "purposes",
    "interests",
    "priority",
    "spend_style",
    "arrival_time",
    "departure_time",
    "wake_time",
    "night_return_time",
    "night_out",
    "nap",
    "walking_level",
    "budget",
    "busy_slots",
    "must_visits",
    "required_needs",
)


def foods_in_note(note: str) -> list[str]:
    """Cụm trong ô "nhu cầu khác" là món ăn (VD "bún bò", "hải sản") -> sở thích, không cần LLM.
    Bỏ cụm phủ định ("không ăn cay") — đó là nhu cầu, không phải món muốn ăn."""
    out = []
    for part in re.split(r"[,;\n.]+|\s+(?:và|với|hoặc)\s+", note):
        part = part.strip()
        plain = _plain(part)
        if not part or plain.startswith(NOT_FOOD):
            continue
        # "muốn ăn hải sản" -> "hải sản"
        part = re.sub(
            r"^(?:muốn|thích|thèm|cần)?\s*(?:được\s+)?(?:ăn|uống|thử)\s+", "", part, flags=re.I
        )
        if part and kind_of("", part) == "meal" and len(part) <= 40:
            out.append(part)
    return out


# Cụm phủ định / dị ứng trong ô "nhu cầu khác": là nhu cầu, không phải món muốn ăn.
NOT_FOOD = ("khong", "ko ", "k an", "tranh", "kieng", "di ung", "it ", "han che")


def add_interests(form: TripForm, items: list[str]) -> TripForm:
    have = [x.strip() for x in form.interests.split(",") if x.strip()]
    for item in items:
        if norm(item) not in {norm(h) for h in have}:
            have.append(item)
    return form.model_copy(update={"interests": ", ".join(have)})


async def doc_loi_nhan(state: ItineraryState, ctx: Ctx) -> dict:
    form, sent = form_of(state), set(state.get("sent") or [])
    foods = foods_in_note(form.needs_note)
    if foods:  # món ghi ở ô "nhu cầu khác" luôn thành sở thích (tìm quán riêng, lịch phải có)
        form = add_interests(form, foods)
        state = {**state, "form": form.model_dump(mode="json")}
    early = {"understood": [], **({"form": form.model_dump(mode="json")} if foods else {})}
    if not form.message.strip() and not form.needs_note.strip():
        return early
    blank = [f for f in ALL_FILLABLE if f not in sent or f in ("interests", "required_needs")]
    if not blank:
        return early
    await ctx.log("doc_loi_nhan", "step", "Đọc lời nhắn, điền ô còn trống")
    human = "# Ô còn trống\n" + ", ".join(blank) + "\n\n# Lời nhắn\n" + form.message
    if form.needs_note.strip():
        human += "\n\n# Nhu cầu bắt buộc user tự ghi\n" + form.needs_note.strip()
    out, calls, usage = await ctx.ask(
        INTAKE, human, Extraction, OUTPUT_TOKENS["intake"], "doc_loi_nhan"
    )
    if out is None:
        return {**early, "llm_calls": calls, "usage": usage}
    new, kept = apply_extracted(form, out, sent)
    await ctx.log("doc_loi_nhan", "finding", f"Nhận {len(kept)} ý có câu trích")
    return {
        "form": new.model_dump(mode="json"),
        "understood": kept,
        "llm_calls": calls,
        "usage": usage,
    }

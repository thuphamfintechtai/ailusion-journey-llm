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
from ..shared.models import ItineraryState, form_of

INTAKE = """Bạn đọc lời nhắn tự do của người dùng trong ứng dụng lập lộ trình du lịch Việt Nam, để điền các ô form họ còn bỏ trống.

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
- `interests`: từ khoá sở thích cụ thể, VD "hải sản, biển"
- `priority`: saving (tiết kiệm) · comfort (đỡ mệt) · leisure (thong thả) · short_time (ít thời gian)
- `spend_style`: normal · unlimited ("không ngại chi thêm") · luxury_within_budget ("sang nhưng không vượt")
- `arrival_time`, `departure_time`, `wake_time`, `night_return_time`: giờ như user viết, hoặc morning / afternoon / evening
- `night_out`: true / false
- `walking_level`: M1 (dạo 1–2 tiếng) · M2 (3 tiếng có dốc) · M3 (4–5 tiếng leo dốc) · M4 (cả ngày)
- `budget`: số tiền như user viết; `budget_unit`: group_trip (cả đoàn cả chuyến) · person_trip (mỗi người cả chuyến) · person_day (mỗi người mỗi ngày)
- `busy_slots`: lịch bận — ngày thứ mấy của chuyến, giờ bắt đầu, giờ xong, kèm câu trích. User không nói giờ (VD "ngày 2 có việc") thì để trống giờ, hệ thống sẽ hỏi lại

# Ví dụ

Lời nhắn: "cả nhà 3 thế hệ, bé 4 tuổi, ông bà ngoài 70, muốn nghỉ ngơi và ăn hải sản; ngày 2 dự tiệc từ 5 giờ chiều"
-> group_type = family (quote "cả nhà 3 thế hệ"); kids_under_6 = 1 (quote "bé 4 tuổi"); seniors_65 = 2 (quote "ông bà ngoài 70"); purposes = relax (quote "muốn nghỉ ngơi"), food (quote "ăn hải sản"); interests = "hải sản" (quote "ăn hải sản"); busy_slots: ngày 2, bắt đầu "5 giờ chiều", giờ xong trống (quote "ngày 2 dự tiệc từ 5 giờ chiều").
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
    "priority": ("saving", "comfort", "leisure", "short_time"),
    "spend_style": ("normal", "unlimited", "luxury_within_budget"),
    "arrival_time": None,
    "departure_time": None,
    "wake_time": None,
    "night_return_time": None,
    "night_out": ("true", "false"),
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


class Extraction(BaseModel):
    """Đầu ra LLM bước 2."""

    items: list[Extracted] = Field(default_factory=list)
    busy_slots: list[ExtractedBusy] = Field(default_factory=list)


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
    for item in extraction.items:
        field, value = item.field, item.value.strip()
        if field not in ALLOWED or field in sent or not quote_found(item.quote, form.message):
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
    "walking_level",
    "budget",
    "busy_slots",
)


async def doc_loi_nhan(state: ItineraryState, ctx: Ctx) -> dict:
    form, sent = form_of(state), set(state.get("sent") or [])
    if not form.message.strip():
        return {"understood": []}
    blank = [f for f in ALL_FILLABLE if f not in sent]
    if not blank:
        return {"understood": []}
    await ctx.log("doc_loi_nhan", "step", "Đọc lời nhắn, điền ô còn trống")
    human = "# Ô còn trống\n" + ", ".join(blank) + "\n\n# Lời nhắn\n" + form.message
    out, calls, usage = await ctx.ask(
        INTAKE, human, Extraction, OUTPUT_TOKENS["intake"], "doc_loi_nhan"
    )
    if out is None:
        return {"understood": [], "llm_calls": calls, "usage": usage}
    new, kept = apply_extracted(form, out, sent)
    await ctx.log("doc_loi_nhan", "finding", f"Nhận {len(kept)} ý có câu trích")
    return {
        "form": new.model_dump(mode="json"),
        "understood": kept,
        "llm_calls": calls,
        "usage": usage,
    }

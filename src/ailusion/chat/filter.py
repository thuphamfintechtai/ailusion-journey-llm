"""Bộ lọc tin nhắn chat — quyết định câu nào đáng gọi LLM để cập nhật form chuyến đi.

Chạy bằng luật (regex + từ khoá), không tốn lượt LLM. Đa số câu chat ("cảm ơn", "quán nào
ngon?", "kể thêm về Hồ Tuyền Lâm") không chứa thông tin chuyến đi nên bị bỏ qua; chỉ câu có
dấu hiệu (số ngày, số người, tiền, địa danh, ngày tháng, dị ứng...) hoặc câu người dùng muốn
lập lộ trình mới được chuyển cho LLM trích xuất (`ailusion.trip_info.update_trip_info`).

Lọc thừa (gọi LLM khi không cần) chỉ tốn một lượt nhỏ; lọc sót thì form thiếu thông tin. Vì
vậy luật ở đây nghiêng về bắt rộng.

So khớp trên chữ đã bỏ dấu, viết thường ("Đà Lạt" -> "da lat"), để bắt được cả người gõ
không dấu.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def fold(text: str) -> str:
    """Bỏ dấu tiếng Việt, viết thường, gộp khoảng trắng: "Đà Lạt 3N2Đ" -> "da lat 3n2d"."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text.lower()).strip()


def _words(*phrases: str) -> str:
    return r"\b(?:" + "|".join(phrases) + r")\b"


# Từng loại dấu hiệu, viết trên chữ đã fold().
SIGNALS: dict[str, re.Pattern[str]] = {
    "days": re.compile(
        r"\b\d+\s*n\s*\d+\s*d\b"  # 3n2d, 3N2Đ
        r"|\b\d+\s*(?:ngay|dem|hom|tuan)\b"
        r"|" + _words("cuoi tuan", "mot tuan", "nua thang", "vai ngay", "may ngay")
    ),
    "people": re.compile(
        r"\b\d+\s*(?:nguoi|nl|be|tre|dua|chau|ong|ba|dua nho|vo chong|cap)\b"
        r"|"
        + _words(
            "vo chong",
            "gia dinh",
            "nha minh",
            "mot minh",
            "1 minh",
            "cap doi",
            "ban be",
            "nhom ban",
            "ong ba",
            "bo me",
            "ba me",
            "con nho",
            "em be",
            "tre em",
            "tre nho",
            "nguoi gia",
            "nguoi lon tuoi",
            "nguoi yeu",
            "ban gai",
            "ban trai",
            "dong nghiep",
        )
    ),
    "budget": re.compile(
        r"\b\d+(?:[.,]\d+)?\s*(?:tr|trieu|cu|k|nghin|ngan|m|ty|ti)\b"
        r"|" + _words("ngan sach", "kinh phi", "chi phi", "tien", "tiet kiem", "eo hep")
    ),
    "date": re.compile(
        r"\b\d{1,2}\s*[/-]\s*\d{1,2}\b"
        r"|\bthang\s*\d{1,2}\b|\bthu\s*[2-7]\b"
        r"|"
        + _words(
            "tuan sau",
            "tuan toi",
            "thang sau",
            "thang toi",
            "chu nhat",
            "ngay mai",
            "mai",
            "tet",
            "dip le",
            "nghi le",
            "30/4",
            "2/9",
            "giang sinh",
            "noel",
        )
    ),
    "access": re.compile(
        _words(
            "di ung",
            "an chay",
            "xe lan",
            "leo doc",
            "bac thang",
            "cau thang",
            "nghi trua",
            "hai san",
            "tom cua",
            "di lai kho",
            "dau chan",
            "dau goi",
            "mang thai",
            "bau",
        )
    ),
    "style": re.compile(
        _words(
            "cham",
            "thong tha",
            "nhe nhang",
            "di nhieu",
            "kham pha",
            "nghi duong",
            "thu gian",
            "cong tac",
            "ngam canh",
            "chill",
            "phuot",
            "trang mat",
            "khong muon di nhieu",
            "it diem",
            "sang chanh",
            "binh dan",
        )
    ),
    "interest": re.compile(
        _words(
            "bien",
            "an uong",
            "am thuc",
            "an ngon",
            "ca phe",
            "cafe",
            "chup anh",
            "check in",
            "checkin",
            "song ao",
            "thien nhien",
            "lich su",
            "van hoa",
            "mua sam",
            "ve dem",
            "phieu luu",
            "leo nui",
            "trekking",
            "suoi",
            "thac",
        )
    ),
    "transport": re.compile(
        _words(
            "xe may",
            "grab",
            "taxi",
            "xe rieng",
            "xe buyt",
            "di bo",
            "thue xe",
            "o to",
            "tu lai",
            "co tai xe",
        )
    ),
    "place": re.compile(
        _words(
            "da lat",
            "ha noi",
            "sai gon",
            "ho chi minh",
            "hcm",
            "tphcm",
            "da nang",
            "hoi an",
            "hue",
            "nha trang",
            "phu quoc",
            "sa pa",
            "sapa",
            "ha long",
            "ninh binh",
            "vung tau",
            "quy nhon",
            "phan thiet",
            "mui ne",
            "con dao",
            "ha giang",
            "cat ba",
            "moc chau",
            "buon ma thuot",
            "can tho",
            "phu yen",
            "tam dao",
            "mai chau",
            "cao bang",
            "quang binh",
            "phong nha",
            "con dao",
            "binh ba",
            "ly son",
            "kon tum",
            "pleiku",
            "tay ninh",
            "chau doc",
            "ben tre",
            "my tho",
            "bac ha",
            "y ty",
            "ta xua",
            "mu cang chai",
        )
    ),
}

# Người dùng nói thẳng là muốn lập lộ trình / kế hoạch.
PLAN_INTENT = re.compile(
    _words(
        "len ke hoach",
        "lap ke hoach",
        "len lich",
        "lap lich",
        "len lich trinh",
        "lap lich trinh",
        "lap lo trinh",
        "len lo trinh",
        "len plan",
        "lam plan",
        "len tour",
        "sap xep lich",
        "xep lich",
        "lich trinh chi tiet",
        "lo trinh chi tiet",
        "mo form",
        "dien form",
    )
)

# Câu trả lời ngắn kiểu đồng ý — chỉ tính khi trợ lý vừa mời lập lộ trình.
AGREEMENT = re.compile(
    r"^(?:"
    + "|".join(
        [
            "ok",
            "oke",
            "okay",
            "okie",
            "uk",
            "uh",
            "um",
            "u",
            "dong y",
            "duoc",
            "dc",
            "chot",
            "lam di",
            "len di",
            "lap di",
            "yes",
            "co",
            "vang",
            "da",
            "nhat tri",
            "tot",
            "trien",
            "trien khai",
            "bat dau",
            "go",
        ]
    )
    + r")\b"
)

# Trợ lý vừa mời lập lộ trình / mở form chưa (đọc trên câu trả lời trước đó).
OFFERED_PLAN = re.compile(
    _words(
        "lap lo trinh",
        "lap lich trinh",
        "len lich trinh",
        "len ke hoach",
        "dien form",
        "mo form",
        "form",
    )
)

AGREEMENT_MAX_CHARS = 40


@dataclass(frozen=True)
class FilterResult:
    """Kết quả lọc một tin nhắn."""

    signals: frozenset[str] = field(default_factory=frozenset)
    plan_intent: bool = False

    @property
    def should_extract(self) -> bool:
        """Có nên gọi LLM để cập nhật form không."""
        return bool(self.signals) or self.plan_intent


def check(message: str, last_ai: str | None = None) -> FilterResult:
    """Lọc một tin nhắn của người dùng. `last_ai`: câu trả lời ngay trước của trợ lý."""
    text = fold(message)
    signals = frozenset(name for name, pattern in SIGNALS.items() if pattern.search(text))
    plan_intent = bool(PLAN_INTENT.search(text))
    if not plan_intent and last_ai and len(text) <= AGREEMENT_MAX_CHARS:
        plan_intent = bool(AGREEMENT.search(text)) and bool(OFFERED_PLAN.search(fold(last_ai)))
    return FilterResult(signals=signals, plan_intent=plan_intent)

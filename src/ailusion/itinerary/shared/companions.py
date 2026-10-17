"""Người đi cùng: món phải tránh (dị ứng / kiêng ghi ở ô "Nhu cầu khác"), chỗ không hợp trẻ nhỏ,
lời dặn từng chỗ khi đi cùng trẻ em.

- `avoided_foods`: "bé dị ứng hải sản" -> quán có món đó (tên / loại có "cua", "tôm"…) bị bỏ ở bước
  lọc. Chạy thật Nha Trang: "Bánh Canh Cua Linh" từng được xếp 2 lần dù bé dị ứng hải sản
  (lưu ý chỉ đưa Planner, Planner bỏ qua).
- `KID_UNFIT_GROUPS`: đoàn có trẻ dưới 6 tuổi thì bỏ spa, massage, quán bar (phương án nào cũng
  từng có spa vì mục đích "nghỉ ngơi" khớp spa).
- `kid_tips`: chỗ trong lịch cần lưu ý vì có trẻ (bãi biển, đi tàu ra đảo, tắm bùn…) và nên làm gì.

Danh sách chữ và lời dặn ở đây là tự đặt.
"""

from __future__ import annotations

import re
import unicodedata

from .form import TripForm
from .models import Place

# Món phải tránh -> chữ nhận ra quán bán món đó trong tên / loại quán (tự đặt). "hải sản" gồm cả
# cua, tôm, mực…: quán "Bánh Canh Cua" không có chữ "hải sản" trong tên.
SEAFOOD = ("hải sản", "seafood", "cua", "ghẹ", "tôm", "mực", "ốc", "sò", "hàu", "nghêu", "ngao",
           "hến", "bạch tuộc", "cá")  # fmt: skip
AVOID_WORDS: dict[str, tuple[str, ...]] = {
    "hải sản": SEAFOOD,
    "đồ biển": SEAFOOD,
    "tôm": ("tôm",),
    "cua": ("cua", "ghẹ"),
    "ghẹ": ("ghẹ",),
    "mực": ("mực", "bạch tuộc"),
    "ốc": ("ốc",),
    "sò": ("sò", "hàu", "nghêu", "ngao"),
    "hàu": ("hàu",),
    "cá": ("cá",),
    "thịt bò": ("bò", "beef"),
    "bò": ("bò", "beef"),
    "thịt heo": ("heo", "lợn", "pork"),
    "thịt lợn": ("heo", "lợn", "pork"),
    "heo": ("heo", "lợn", "pork"),
    "lợn": ("heo", "lợn", "pork"),
    "gà": ("gà", "chicken"),
    "vịt": ("vịt", "duck"),
    "dê": ("dê",),
    "thịt chó": ("thịt chó", "cầy"),
}
# "bé dị ứng hải sản", "kiêng thịt bò", "mẹ không ăn được tôm"… -> phần sau là món.
AVOID_VERB = re.compile(
    r"(?:dị ứng|kiêng|tránh|không ăn được|không ăn|không dùng được|ko ăn|k ăn)\s+(.+)"
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip().lower()


def _has(text: str, word: str) -> bool:
    """Khớp nguyên chữ, giữ dấu: "cá" không khớp "cà phê", "các"; "bò" không khớp "bờ"."""
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def _foods(text: str) -> list[str]:
    """Món (khoá của AVOID_WORDS) có trong cụm, khoá dài trước ("thịt bò" rồi mới "bò")."""
    out: list[str] = []
    for key in sorted(AVOID_WORDS, key=len, reverse=True):
        if _has(text, key) and not any(key in k for k in out):
            out.append(key)
    return out


def avoided_foods(note: str) -> list[tuple[str, tuple[str, ...]]]:
    """Món phải tránh trong ô "nhu cầu khác": (cụm user ghi, chữ nhận ra quán). "bé dị ứng tôm,
    cua" -> cụm "bé dị ứng tôm, cua" với chữ của tôm và cua. Cụm phủ định mà không nhận ra món
    ("không ăn cay", "dị ứng đậu phộng") -> chữ rỗng: không lọc được quán, chỉ nhắc hỏi quán."""
    out: list[tuple[str, tuple[str, ...]]] = []
    for sentence in re.split(r"[;\n.]+", note):
        phrase, words, chained = "", [], False
        for part in re.split(r",|\s+(?:và|hoặc)\s+", _norm(sentence)):
            part = part.strip()
            if not part:
                continue
            if m := AVOID_VERB.search(part):
                if phrase:
                    out.append((phrase, tuple(dict.fromkeys(words))))
                phrase, chained = part, True
                words = [w for f in _foods(m.group(1)) for w in AVOID_WORDS[f]]
            elif chained and (foods := _foods(part)) and len(part.split()) <= 3:
                # "dị ứng tôm, cua": "cua" là món tiếp theo của cùng cụm.
                phrase += f", {part}"
                words += [w for f in foods for w in AVOID_WORDS[f]]
            else:
                chained = False
        if phrase:
            out.append((phrase, tuple(dict.fromkeys(words))))
    return out


def avoid_match(p: Place, avoids: list[tuple[str, tuple[str, ...]]]) -> tuple[str, str] | None:
    """Quán có món phải tránh trong tên / loại -> (cụm user ghi, chữ khớp): ("bé dị ứng hải sản",
    "cua") cho quán "Bánh Canh Cua"."""
    if p.kind != "meal":
        return None
    text = p.text
    for phrase, words in avoids:
        if hit := next((w for w in words if _has(text, w)), None):
            return phrase, hit
    return None


# Đoàn có trẻ dưới 6 tuổi: bỏ chỗ thuộc các nhóm này (CATEGORY_GROUPS) hay có chữ trò mạo hiểm
# (chạy thật: "Lặn Biển LabiXa" từng được xếp cho đoàn có 2 bé dưới 6), trừ chỗ user tự chọn.
KID_UNFIT_GROUPS = ("spa", "quán bar")
KID_UNFIT_WORDS = re.compile(
    r"lặn biển|lặn bình|scuba|diving|dù lượn|nhảy dù|paragliding|zipline|đu dây"
)
KID_UNFIT_REASON = (
    "đoàn có trẻ dưới 6 tuổi: spa, massage, quán bar, lặn biển, dù lượn không hợp trẻ nhỏ"
)


def kid_unfit(p: Place) -> bool:
    """Chỗ chơi không hợp trẻ dưới 6 tuổi (`KID_UNFIT_GROUPS`, `KID_UNFIT_WORDS`)."""
    return p.kind == "visit" and (
        p.group in KID_UNFIT_GROUPS or KID_UNFIT_WORDS.search(p.text) is not None
    )


# Chỗ chơi cần lưu ý khi đi cùng trẻ -> nên làm gì (tự đặt). Xét theo thứ tự, mỗi chỗ 1 lời dặn
# (dòng đầu khớp): "Khu du lịch Đảo Khỉ" là khỉ trước, đi tàu sau.
KID_TIPS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"khỉ|monkey|vườn thú|thảo cầm viên|safari|\bzoo\b"),
     "thú có thể giật đồ, cào — giữ trẻ sát bên, không cho trẻ tự cho thú ăn"),
    (re.compile(r"tắm bùn|mud bath|suối khoáng|suối nước nóng|hot spring|onsen"),
     "hỏi trước khu tắm, nhiệt độ nước cho trẻ em; mang đồ thay"),
    (re.compile(r"bãi biển|bãi tắm|beach|hồ bơi|bể bơi|swimming pool|công viên nước|water park"),
     "trông trẻ sát khi xuống nước, mang phao tay cho trẻ, che nắng"),
    (re.compile(r"đảo|cano|ca nô|du thuyền|thuyền|tàu|\btour\b|vịnh|lặn|snorkel"),
     "đi tàu / cano — hỏi trước có áo phao cỡ trẻ em"),
    (re.compile(r"thác|suối|ghềnh"), "đá trơn, nước chảy — nắm tay trẻ"),
    (re.compile(r"\bnúi\b|\bđèo\b|\bhang\b|\bđộng\b|leo|bậc thang|cáp treo|hải đăng"),
     "dốc, nhiều bậc — mang địu (xe đẩy khó đi)"),
    (re.compile(r"\bchợ\b|market"), "đông người, nhiều xe máy — giữ trẻ sát bên"),
]  # fmt: skip


def kid_tips(places: list[Place], form: TripForm) -> list[str]:
    """Chỗ chơi trong lịch ngày cần lưu ý vì đoàn có trẻ em: "Bãi biển Nha Trang: trông trẻ sát
    khi xuống nước…". Đoàn không có trẻ em thì rỗng."""
    if not form.kids_under_6 and not form.kids_6_12:
        return []
    out = []
    for p in places:
        if p.kind != "visit":
            continue
        tip = next((t for pattern, t in KID_TIPS if pattern.search(p.text)), None)
        if tip:
            out.append(f"{p.name}: {tip}")
    return list(dict.fromkeys(out))


def kid_count_text(form: TripForm) -> str:
    """ "2 bé dưới 6 tuổi", "1 trẻ 6–12 tuổi", "2 bé dưới 6 tuổi, 1 trẻ 6–12 tuổi"."""
    parts = []
    if form.kids_under_6:
        parts.append(f"{form.kids_under_6} bé dưới 6 tuổi")
    if form.kids_6_12:
        parts.append(f"{form.kids_6_12} trẻ 6–12 tuổi")
    return ", ".join(parts)

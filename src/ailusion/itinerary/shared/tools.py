"""Đọc kết quả tool MCP thành `Place`.

Mỗi số gắn với nơi lấy ra nó (sao, số review, loại, giá, toạ độ: Postgres đã cào, SerpApi,
Vietmap). Không số nào tự điền: thiếu thì để None, các bước sau ghi "chưa có".
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..shared.models import Place

FOOD_WORDS = (
    "nhà hàng",
    "quán ăn",
    "quán",
    "ẩm thực",
    "bánh mì",
    "sandwich",
    "phở",
    "bún",
    "lẩu",
    "nướng",
    "hải sản",
    "cơm",
    "mì",
    "street food",
    "food",
    "restaurant",
    # Loại Google của quán mà không có chữ "nhà hàng" / "quán ăn" ("Khu ăn uống" — Lẩu gà lá é
    # Tao Ngộ, Đà Lạt bị coi là chỗ chơi nên lọt luật 2 bữa cách nhau, ăn 8:11 rồi 8:59 ăn tiếp).
    "ăn uống",
    "ăn vặt",
    "nhậu",
    "buffet",
    "bistro",
    "eatery",
    "bbq",
)


STAY_WORDS = (
    "khách sạn",
    "resort",
    "homestay",
    "nhà nghỉ",
    "hostel",
    "villa",
    "căn hộ",
    "hotel",
    "residence",
    "apartment",
    "condotel",
)
SKIP_WORDS = (
    "nhà điều hành du lịch",
    "công ty du lịch",
    "đại lý",
    "văn phòng",
    "tiếp viên nữ",  # "Quán bar tiếp viên nữ": không đưa vào lộ trình du lịch
    # Quầy bán vé / thông tin du lịch (loại "Phòng vé"): không phải chỗ chơi, tên có "phố cổ"
    # nên từng được nhận là hợp mục đích và vào lịch.
    "phòng vé",
    "quầy vé",
    "quầy thông tin",
    "bán vé",  # "Doanh nghiệp bán vé sự kiện": đại lý bán vé show, không phải nơi diễn
)
SWEET_WORDS = ("yogurt", "sữa chua", "ice cream", "gelato")
# Chỗ chơi đêm, không phải bữa ăn: loại Google "Quán bar karaoke", "Quán bar", "Quán rượu" có chữ
# "quán" nên từng bị coi là quán. Quán bia / quán nhậu vẫn là quán (có bán đồ ăn).
NIGHT_WORDS = ("karaoke", "quán bar", "quán rượu", "hộp đêm", "pub", "night club", "nightclub")


def nfc(text: Any) -> str:
    """Chữ tiếng Việt về một dạng Unicode (dựng sẵn). Dữ liệu cào có chỗ ở dạng tổ hợp (NFD):
    "nhà hàng" NFD không khớp "nhà hàng" NFC khi so `in`, làm trượt mọi luật theo từ khoá."""
    return unicodedata.normalize("NFC", str(text or ""))


def kind_of(category: str, name: str) -> str | None:
    """Theo loại Google ghi trước; chỉ khi không có loại mới đoán theo tên. Tên dễ nhầm: "Chùa
    Quán Thế Âm" có chữ "quán", "Bảo tàng Hải sản" có chữ "hải sản" — loại của chúng là chùa,
    bảo tàng nên là chỗ chơi."""
    text = nfc(f" {category} {name} ").lower()
    if any(w in text for w in SKIP_WORDS):
        return None
    # Quán đồ ngọt Google ghi sai loại ("Hội an market yogurt" loại "Chợ"): không phải chỗ chơi,
    # cũng không làm bữa chính. Thứ user nhắc (VD sữa chua) vẫn tìm riêng (any_kind).
    if any(w in nfc(name).lower() for w in SWEET_WORDS):
        return None
    # Tên bắt đầu bằng "Vé …" / "Ticket …": trang bán vé ("Vé Show Ký Ức Hội An - Huyền Tất Tần
    # Tật vé Đà Nẵng" Google ghi loại "Điểm thu hút"), không phải nơi đến.
    if re.match(r"(vé|ticket)\b", nfc(name).lower().strip()):
        return None
    # Loại chỉ là mã số (Vietmap trả "4004, 4004-2") thì coi như không có loại: đoán theo tên.
    words = nfc(f" {category} ").lower() if re.search(r"[^\W\d_]", category) else text
    if any(w in words for w in STAY_WORDS):
        return "stay"
    # "karaoke" xét cả tên: "KARAOKE VT KTV" Google ghi loại "Nhà hàng". Chữ "KTV" thì không
    # ("Nhà hàng KTV" ở Huế là quán bún thật). Loại có "nhà hàng" ("Nhà hàng quán rượu dành cho
    # người sành ăn" — gastropub) vẫn là quán.
    if "karaoke" in text or (any(w in words for w in NIGHT_WORDS) and "nhà hàng" not in words):
        return "visit"
    if any(w in words for w in FOOD_WORDS):
        return "meal"
    return "visit"


def tool_ok(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "success"


def _price(raw: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """Giá mỗi người của tool: (giữa, thấp, cao) theo khoảng giá Google ghi (`price_vnd`)."""
    p = raw.get("price_vnd")
    if isinstance(p, dict) and p.get("kind") == "per_person" and p.get("vnd") is not None:
        low, high = p.get("min_vnd"), p.get("max_vnd")
        return (
            int(p["vnd"]),
            int(low) if low else None,
            int(high) if high else None,
        )
    return None, None, None


def add_places(pool: dict[str, Place], result: Any, source: str, any_kind: bool = False) -> None:
    """Gộp kết quả find_places / search_places / find_nearby.

    Cùng place_id thì gộp; nhu cầu "unknown" lần này được thay bằng giá trị biết ở lần khác.
    `any_kind`: giữ cả chỗ thường bị bỏ (chỗ ở, công ty du lịch…) dưới dạng chỗ chơi — dùng khi
    user tự gọi tên chỗ muốn đi.
    """
    if not tool_ok(result):
        return
    for raw in result.get("places") or result.get("results") or []:
        pid = raw.get("place_id") or raw.get("ref_id") or raw.get("name")
        gps = raw.get("gps") or {}
        lat = raw.get("lat") if raw.get("lat") is not None else gps.get("latitude")
        lng = raw.get("lng") if raw.get("lng") is not None else gps.get("longitude")
        if not pid or lat is None or lng is None:
            continue
        # Vietmap (find_nearby) trả `categories` là danh sách mã loại.
        category = nfc(
            raw.get("category") or raw.get("type") or ", ".join(raw.get("categories") or [])
        )
        kind = kind_of(category, nfc(raw.get("name")))
        if kind is None or kind == "stay":
            if not any_kind:
                continue
            kind = "visit"
        price, price_low, price_high = _price(raw)
        if pid in pool:
            place = pool[pid]
        else:
            place = Place(
                place_id=pid,
                name=nfc(raw.get("name")),
                category=category,
                kind=kind,
                lat=float(lat),
                lng=float(lng),
                rating=float(raw["rating"]) if raw.get("rating") else None,
                reviews=int(raw.get("reviews") or 0),
                summary=nfc(raw.get("summary") or raw.get("description")),
                source=source,
            )
            pool[pid] = place
        if not place.thumbnail and raw.get("thumbnail"):
            place.thumbnail = str(raw["thumbnail"])
        set_rating_adjusted(place, raw)
        if place.price is None and price is not None:
            place.price, place.price_by = price, "tool"
            place.price_low, place.price_high = price_low, price_high
        for need, status in (raw.get("needs_status") or {}).items():
            if place.need_status.get(need, "unknown") == "unknown":
                place.need_status[need] = status


def add_photos(pool: dict[str, Place], result: Any) -> int:
    """get_place_photos: `{"photos": {place_id: link hoặc null}}` -> điền `thumbnail` còn trống.
    Trả số chỗ vừa có ảnh; tool lỗi thì không đổi gì."""
    if not isinstance(result, dict) or result.get("status") == "error":
        return 0
    photos = result.get("photos")
    filled = 0
    for pid, url in photos.items() if isinstance(photos, dict) else []:
        place = pool.get(pid)
        if place is not None and not place.thumbnail and isinstance(url, str) and url:
            place.thumbnail = url
            filled += 1
    return filled


def add_stays(stays: dict[str, Place], result: Any, source: str) -> None:
    """find_stays (giá phổ biến) / search_hotels (giá thật theo ngày, ưu tiên khi trùng tên)."""
    if not tool_ok(result):
        return
    for raw in result.get("stays") or result.get("hotels") or []:
        gps = raw.get("gps") or {}
        lat = raw.get("lat") if raw.get("lat") is not None else gps.get("latitude")
        lng = raw.get("lng") if raw.get("lng") is not None else gps.get("longitude")
        price = raw.get("price_per_night_vnd")
        if lat is None or lng is None or not price:
            continue
        name = nfc(raw.get("name"))
        key = name.lower().strip()
        if key in stays and source != "search_hotels":
            continue
        stays[key] = Place(
            place_id=raw.get("place_id") or name,
            name=name,
            category=nfc(raw.get("category") or raw.get("kind") or "Chỗ ở"),
            kind="stay",
            lat=float(lat),
            lng=float(lng),
            rating=float(raw["rating"]) if raw.get("rating") else None,
            reviews=int(raw.get("reviews") or 0),
            price=int(price),
            price_by="tool",
            need_status=dict(raw.get("needs_status") or {}),
            source=source,
        )
        set_rating_adjusted(stays[key], raw)


def set_rating_adjusted(place: Place, raw: dict[str, Any]) -> None:
    """Sao tin được MCP đã tính (`rating_adjusted` + `rating_basis`); lần sau có thì ghi đè."""
    if raw.get("rating_adjusted") is not None:
        place.rating_adjusted = float(raw["rating_adjusted"])
        basis = raw.get("rating_basis")
        place.rating_basis = dict(basis) if isinstance(basis, dict) else None


def add_hours(pool: dict[str, Place], day: int, result: Any) -> None:
    """check_opening_hours cho ngày `day`: "đóng cửa" ghi None, không đọc được thì bỏ qua."""
    if not tool_ok(result):
        return
    for r in result.get("results", []):
        place = pool.get(r.get("place_id") or "") or next(
            (p for p in pool.values() if p.name == nfc(r.get("input"))), None
        )
        if place is None:
            continue
        text = r.get("hours_that_day") or ""
        if r.get("status") == "closed" and not text:
            place.hours[day] = None
            continue
        spans = _spans(text)
        if spans:
            place.hours[day] = (spans[0][0], spans[-1][1])
            gaps = [(a[1], b[0]) for a, b in zip(spans, spans[1:], strict=False) if b[0] > a[1]]
            if gaps:
                place.breaks[day] = gaps


def _spans(text: str) -> list[tuple[int, int]]:
    """ "07:00–11:00, 13:30–17:00" -> [(420, 660), (810, 1020)], theo giờ mở. Khung qua nửa đêm
    ("18:00–02:00") tính tới hết ngày. Không đọc được thì []."""
    if "24 giờ" in text or "cả ngày" in text.lower():
        return [(0, 24 * 60)]
    times = [int(h) * 60 + int(m) for h, m in re.findall(r"(\d{1,2}):(\d{2})", text)]
    spans = [(a, b if b > a else 24 * 60) for a, b in zip(times[::2], times[1::2], strict=False)]
    return sorted(spans)


def add_needs(pool: dict[str, Place], result: Any) -> None:
    """check_place_needs: ghi trạng thái từng nhu cầu (yes / no / unknown)."""
    if not tool_ok(result):
        return
    for r in result.get("results", []):
        place = pool.get(r.get("place_id") or "")
        if place is None:
            continue
        for need, status in (r.get("needs_status") or {}).items():
            place.need_status[need] = status

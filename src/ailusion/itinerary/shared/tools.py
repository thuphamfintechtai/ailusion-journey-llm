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
)


STAY_WORDS = ("khách sạn", "resort", "homestay", "nhà nghỉ", "hostel", "villa", "căn hộ", "hotel")
SKIP_WORDS = ("nhà điều hành du lịch", "công ty du lịch", "đại lý", "văn phòng")


def nfc(text: Any) -> str:
    """Chữ tiếng Việt về một dạng Unicode (dựng sẵn). Dữ liệu cào có chỗ ở dạng tổ hợp (NFD):
    "nhà hàng" NFD không khớp "nhà hàng" NFC khi so `in`, làm trượt mọi luật theo từ khoá."""
    return unicodedata.normalize("NFC", str(text or ""))


def kind_of(category: str, name: str) -> str | None:
    text = nfc(f" {category} {name} ").lower()
    if any(w in text for w in SKIP_WORDS):
        return None
    if any(w in text for w in STAY_WORDS):
        return "stay"
    if any(w in text for w in FOOD_WORDS):
        return "meal"
    return "visit"


def tool_ok(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "success"


def _price(raw: dict[str, Any]) -> int | None:
    """Giá mỗi người: `price_vnd.vnd` (giữa khoảng giá Google ghi) của tool."""
    p = raw.get("price_vnd")
    if isinstance(p, dict) and p.get("kind") == "per_person" and p.get("vnd") is not None:
        return int(p["vnd"])
    return None


def add_places(pool: dict[str, Place], result: Any, source: str) -> None:
    """Gộp kết quả browse_places / find_places_by_needs / search_places / find_nearby.

    Cùng place_id thì gộp; nhu cầu "unknown" lần này được thay bằng giá trị biết ở lần khác.
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
        category = nfc(raw.get("category") or raw.get("type"))
        kind = kind_of(category, nfc(raw.get("name")))
        if kind is None or kind == "stay":
            continue
        price = _price(raw)
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
        if place.price is None and price is not None:
            place.price, place.price_by = price, "tool"
        for need, status in (raw.get("needs_status") or {}).items():
            if place.need_status.get(need, "unknown") == "unknown":
                place.need_status[need] = status


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

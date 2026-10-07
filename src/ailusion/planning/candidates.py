"""Đọc kết quả tool thành dữ liệu có cấu trúc cho bước xếp lịch. Hàm thuần, không gọi mạng.

Địa điểm từ browse_places chỉ có tên, loại, rating, toạ độ. Phần còn lại (nhãn sở thích, trong
nhà hay ngoài trời, thời gian tham quan, giá vé) suy ra từ loại + tên theo bảng RULES; giá vé
vì thế là ước tính và được gắn độ tin cậy "trung bình".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .request import PlanRequest

# (từ khoá trong loại/tên, nhãn sở thích, trong nhà?, phút tham quan, vé/người VND, kiểu)
RULES: list[tuple[tuple[str, ...], tuple[str, ...], bool, int, int, str]] = [
    (("khách sạn", "resort", "homestay", "nhà nghỉ"), (), True, 0, 0, "hotel"),
    (("quán cà phê", "cà phê", "coffee", "cafe"), ("cafe", "checkin"), True, 60, 60_000, "cafe"),
    (
        ("nhà hàng", "quán ăn", "lẩu", "nướng", "bữa sáng", "bánh căn", "phở", "bún"),
        ("amthuc",),
        True,
        0,
        0,
        "food",
    ),
    (("chợ đêm",), ("dem", "amthuc", "muasam"), False, 90, 0, "poi"),
    (("bảo tàng",), ("vanhoa",), True, 75, 30_000, "poi"),
    (
        ("lâu đài", "dinh ", "biệt thự", "crazy house"),
        ("vanhoa", "checkin"),
        True,
        60,
        80_000,
        "poi",
    ),
    (
        ("chùa", "thiền viện", "nhà thờ", "đền", "tu viện"),
        ("vanhoa", "checkin"),
        False,
        45,
        0,
        "poi",
    ),
    (("thác",), ("thiennhien", "phieuluu"), False, 90, 80_000, "poi"),
    (("cáp treo", "máng trượt"), ("phieuluu", "thiennhien"), False, 60, 150_000, "poi"),
    (
        ("vườn", "trang trại", "nông trại", "đồi", "trà"),
        ("thiennhien", "checkin"),
        False,
        75,
        50_000,
        "poi",
    ),
    (("hồ", "rừng", "núi", "thung lũng"), ("thiennhien", "checkin"), False, 60, 30_000, "poi"),
    (("biển", "bãi"), ("bien", "thiennhien"), False, 120, 0, "poi"),
    (("spa", "massage", "suối nước nóng"), ("nghiduong",), True, 90, 350_000, "poi"),
    (
        ("vui chơi", "công viên", "khu du lịch"),
        ("giaitri", "thiennhien"),
        False,
        120,
        120_000,
        "poi",
    ),
    (("bar", "pub", "rooftop"), ("dem",), True, 90, 150_000, "poi"),
    (("chợ", "cửa hàng", "trung tâm thương mại"), ("muasam",), True, 60, 0, "poi"),
    (("quảng trường", "ga ", "nhà ga"), ("checkin", "vanhoa"), False, 40, 0, "poi"),
    (("làng",), ("vanhoa", "checkin"), False, 90, 50_000, "poi"),
    (("điểm thu hút khách du lịch",), ("checkin",), False, 90, 50_000, "poi"),
]
# Dốc, nhiều bậc: loại khi đoàn cần "hạn chế leo dốc, bậc thang" hoặc có xe lăn.
STEEP = ("thác", "đồi", "núi", "đỉnh", "langbiang", "lang biang", "máng trượt")
SEAFOOD_PHRASES = ("hải sản", "cá viên", "chả cá", "bún cá")
SEAFOOD_WORDS = {"ốc", "cua", "ghẹ", "tôm", "mực", "sò", "nghêu", "hàu", "seafood"}
CROWDED_REVIEWS = 15_000  # số review từ ngưỡng này coi là "thường rất đông"


@dataclass
class Place:
    place_id: str
    name: str
    category: str
    lat: float
    lng: float
    rating: float
    reviews: int
    kind: str  # poi | food | cafe
    group: str  # nhóm loại địa điểm (vườn, hồ, chùa, lẩu...) để tránh xếp lặp
    tags: tuple[str, ...]
    indoor: bool
    visit_min: int
    ticket_vnd: int
    source: str
    wheelchair_ok: bool = False
    veg: bool = False
    hours: dict[int, tuple[int, int] | None] = field(default_factory=dict)  # ngày -> (mở, đóng)

    @property
    def text(self) -> str:
        return f"{self.category} {self.name}".lower()

    @property
    def crowded(self) -> bool:
        return self.reviews >= CROWDED_REVIEWS


@dataclass
class Hotel:
    name: str
    price_night: int
    rating: float
    reviews: int
    lat: float
    lng: float
    hotel_class: int | None
    amenities: list[str]


@dataclass
class Pool:
    """Mọi thứ thu thập được — chính là "gói bối cảnh" đóng băng ở bước 05."""

    places: dict[str, Place] = field(default_factory=dict)
    hotels: list[Hotel] = field(default_factory=list)
    weather: list[dict[str, Any]] = field(default_factory=list)
    festivals: list[str] = field(default_factory=list)
    intercity: dict[str, Any] = field(default_factory=dict)
    health: list[str] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    center: tuple[float, float] | None = None


def ok(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "success"


def classify(category: str, name: str) -> tuple[tuple[str, ...], bool, int, int, str, str]:
    """-> (nhãn, trong nhà?, phút tham quan, vé/người, kiểu, nhóm). Nhóm dùng để tránh lặp loại."""
    text = f" {category} {name} ".lower()
    for words, tags, indoor, visit, ticket, kind in RULES:
        hit = next((w for w in words if w in text), None)
        if hit:
            return tags, indoor, visit, ticket, kind, hit.strip()
    return ("checkin",), False, 60, 30_000, "poi", "khac"


def add_places(pool: Pool, key: str, result: Any, wheelchair_filtered: bool = False) -> None:
    """Gộp kết quả browse_places / find_nearby vào pool, bỏ trùng theo place_id.

    `wheelchair_filtered`: lượt gọi đã lọc `wheelchair=true`, nên mọi điểm trả về đều tiếp cận được.
    """
    if not ok(result):
        return
    for raw in result.get("places") or result.get("results") or []:
        pid = raw.get("place_id") or raw.get("ref_id") or raw.get("name")
        lat, lng = raw.get("lat"), raw.get("lng")
        if not pid or lat is None or lng is None:
            continue
        if pid in pool.places:
            place = pool.places[pid]
        else:
            category = raw.get("category") or raw.get("type") or ""
            tags, indoor, visit, ticket, kind, group = classify(category, raw.get("name", ""))
            if kind == "hotel":
                continue
            place = Place(
                place_id=pid,
                name=raw.get("name", ""),
                category=category,
                lat=float(lat),
                lng=float(lng),
                rating=float(raw.get("rating") or 4.0),
                reviews=int(raw.get("reviews") or 0),
                kind=kind,
                group=group,
                tags=tags,
                indoor=indoor,
                visit_min=visit,
                ticket_vnd=ticket,
                source=key.split(":")[0] if key.startswith("food") else "browse_places",
            )
            pool.places[pid] = place
        if key.startswith("food:veg"):
            place.veg, place.kind = True, "food"
        if wheelchair_filtered or raw.get("wheelchair"):
            place.wheelchair_ok = True


def add_hotels(pool: Pool, result: Any) -> None:
    if not ok(result):
        return
    for raw in result.get("hotels", []):
        gps = raw.get("gps") or {}
        price = raw.get("price_per_night_vnd")
        if not price or gps.get("latitude") is None:
            continue
        pool.hotels.append(
            Hotel(
                name=raw.get("name", ""),
                price_night=int(price),
                rating=float(raw.get("rating") or 4.0),
                reviews=int(raw.get("reviews") or 0),
                lat=float(gps["latitude"]),
                lng=float(gps["longitude"]),
                hotel_class=raw.get("hotel_class"),
                amenities=raw.get("amenities") or [],
            )
        )
    pool.hotels.sort(key=lambda h: h.price_night)


def add_weather(pool: Pool, result: Any) -> None:
    if ok(result):
        pool.weather = result.get("days", [])


def add_festivals(pool: Pool, result: Any) -> None:
    if not ok(result):
        return
    for f in result.get("festivals") or result.get("events") or []:
        name = f.get("name") or f.get("title")
        when = f.get("date") or f.get("start_date") or ""
        if name:
            pool.festivals.append(f"{name} ({when})" if when else name)


def add_intercity(pool: Pool, key: str, result: Any) -> None:
    if not ok(result):
        return
    if key == "intercity:road":
        km = result.get("distance_km") or result.get("total_distance_km")
        minutes = result.get("duration_min") or result.get("total_travel_min")
        if km:
            pool.intercity["road"] = {"km": float(km), "minutes": float(minutes or km * 1.4)}
    elif key == "intercity:flight":
        flights = result.get("best_flights") or result.get("flights") or []
        prices = [f.get("price_vnd") or f.get("price") for f in flights]
        prices = [int(p) for p in prices if isinstance(p, (int, float))]
        if prices:
            pool.intercity["flight_round_trip_per_person"] = min(prices)


def add_health(pool: Pool, result: Any) -> None:
    if ok(result):
        pool.health = [p.get("name", "") for p in (result.get("places") or [])][:3]


def add_hours(pool: Pool, day: int, result: Any) -> None:
    """Giờ mở cửa theo ngày. Không có dữ liệu -> không ghi (bước xếp lịch dùng giờ mặc định)."""
    if not ok(result):
        return
    for r in result.get("results", []):
        place = pool.places.get(r.get("place_id") or r.get("input"))
        if place is None:
            continue
        if r.get("status") == "closed" and not r.get("hours_that_day"):
            place.hours[day] = None
            continue
        span = _parse_span(r.get("hours_that_day"))
        if span:
            place.hours[day] = span


def _parse_span(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    if "24 giờ" in text or "cả ngày" in text.lower():
        return (0, 24 * 60)
    times = re.findall(r"(\d{1,2}):(\d{2})", text)
    if len(times) < 2:
        return None
    (h1, m1), (h2, m2) = times[0], times[-1]
    start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
    return (start, end if end > start else 24 * 60)


def apply_hard_filters(pool: Pool, req: PlanRequest) -> None:
    """Loại hẳn những nơi vi phạm ràng buộc cứng, ghi lý do để giải thích."""
    acc, crowd_hard = req.access, req.interests.crowd_hard
    for pid, p in list(pool.places.items()):
        reason = None
        if (
            p.kind == "poi"
            and (acc.no_stairs or acc.wheelchair)
            and any(w in p.text for w in STEEP)
        ):
            reason = "nhiều dốc, bậc thang"
        elif p.kind == "poi" and acc.wheelchair and not p.wheelchair_ok:
            reason = "chưa có thông tin tiếp cận xe lăn"
        elif p.kind == "food" and acc.no_seafood and is_seafood(p.text):
            reason = "quán hải sản, đoàn có người dị ứng"
        elif p.kind == "food" and acc.veg and not (p.veg or "chay" in p.text):
            reason = None  # quán thường vẫn giữ làm dự phòng, bước xếp lịch ưu tiên quán chay
        elif p.kind == "poi" and crowd_hard and p.crowded:
            reason = f"thường rất đông ({p.reviews:,} lượt đánh giá)".replace(",", ".")
        if reason:
            pool.excluded.append((p.name, reason))
            del pool.places[pid]


def is_seafood(text: str) -> bool:
    """So theo từng từ: "chính gốc" không được tính là "ốc"."""
    words = set(re.findall(r"\w+", text.lower()))
    return bool(words & SEAFOOD_WORDS) or any(ph in text.lower() for ph in SEAFOOD_PHRASES)


def shortlist(pool: Pool, req: PlanRequest, n: int = 15) -> list[str]:
    """Điểm tham quan đáng xét nhất — dùng để tra giờ mở cửa (tối đa 15 mỗi lượt)."""
    pois = [p for p in pool.places.values() if p.kind in ("poi", "cafe")]
    pois.sort(key=lambda p: -base_score(p, req))
    return [p.place_id for p in pois[:n]]


def base_score(p: Place, req: PlanRequest) -> float:
    """Điểm 0-1 không phụ thuộc phương án: hợp sở thích + chất lượng."""
    return 0.6 * interest_match(p, req) + 0.4 * quality(p)


def interest_match(p: Place, req: PlanRequest) -> float:
    tags = req.interests.tags
    if not tags:
        return 0.5
    best = max((tags.get(t, 0) for t in p.tags), default=0)  # type: ignore[call-overload]
    return {0: 0.15, 1: 0.75, 2: 1.0}[best]


def quality(p: Place) -> float:
    import math

    rating = max(0.0, min(1.0, (p.rating - 3.5) / 1.5))
    volume = min(1.0, math.log10(p.reviews + 1) / 4.5)
    return 0.7 * rating + 0.3 * volume

"""Planner theo luật: đọc form, ra danh sách bước gọi tool (tool + tham số + lý do). Không gọi LLM.

Hai đợt vì có phụ thuộc dữ liệu:
- `collect_steps`: chạy song song ngay sau chuẩn hoá — địa điểm, khách sạn, thời tiết, lễ hội,
  chặng liên tỉnh.
- `hours_steps`: cần place_id từ đợt trước — giờ mở cửa của danh sách ngắn, theo đúng từng ngày đi.

Mỗi bước có `key` để bước đọc kết quả (candidates.py) biết kết quả đó dùng vào việc gì.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from .request import PlanRequest

MAX_STEPS = 15
BROWSE_LIMIT = 30  # browse_places nhận tối đa 30

# Sở thích -> các lượt browse_places bổ sung (query / category theo dữ liệu Google Maps tiếng Việt).
INTEREST_QUERIES: dict[str, list[dict[str, str]]] = {
    "thiennhien": [{"query": "hồ"}, {"query": "vườn"}],
    "vanhoa": [{"category": "Bảo tàng"}, {"query": "chùa"}],
    "cafe": [{"category": "Quán cà phê"}],
    "amthuc": [{"query": "quán ăn"}],
    "nghiduong": [{"query": "spa"}],
    "phieuluu": [{"query": "thác"}, {"query": "cáp treo"}],
    "bien": [{"query": "biển"}],
    "muasam": [{"query": "chợ"}],
    "giaitri": [{"query": "vui chơi"}],
    "dem": [{"query": "chợ đêm"}, {"query": "bar"}],
    "checkin": [{"query": "check-in"}],
}

# Mã sân bay của các điểm hay đi — đủ để gọi search_flights mà không phải đoán.
AIRPORTS = {
    "tp.hcm": "SGN",
    "hồ chí minh": "SGN",
    "sài gòn": "SGN",
    "hà nội": "HAN",
    "đà nẵng": "DAD",
    "hội an": "DAD",
    "đà lạt": "DLI",
    "huế": "HUI",
    "nha trang": "CXR",
    "phú quốc": "PQC",
    "quy nhơn": "UIH",
    "hải phòng": "HPH",
    "côn đảo": "VCS",
}


def airport(place: str | None) -> str | None:
    return AIRPORTS.get((place or "").strip().lower())


def rooms_needed(req: PlanRequest) -> int:
    """Hai người lớn một phòng; trẻ em ở chung."""
    return max(1, math.ceil((req.basic.adults + req.basic.seniors) / 2))


def nights(req: PlanRequest) -> int:
    return max(0, req.basic.days - 1)


def collect_steps(
    req: PlanRequest, province: str | None, strategy: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """`strategy`: chiến lược của nhóm — thêm các lượt tìm `extra_searches` nó đề xuất."""
    b, acc = req.basic, req.access
    start, end = b.start_date.isoformat(), b.end_date.isoformat()
    dest = b.destination
    wheel = {"wheelchair": True} if acc.wheelchair else {}
    steps: list[dict[str, Any]] = []

    def add(key: str, tool: str, why: str, **args: Any) -> None:
        steps.append({"key": key, "tool": tool, "args": args, "why": why})

    add(
        "weather",
        "weather_forecast",
        "Thời tiết theo giờ từng ngày đi",
        start_date=start,
        end_date=end,
        location=dest,
        hourly=True,
    )
    add(
        "festivals",
        "find_festivals",
        "Lễ hội trùng ngày đi",
        location=province or dest,
        start_date=start,
        end_date=end,
        limit=5,
    )
    add(
        "places:popular",
        "browse_places",
        "Điểm tham quan phổ biến",
        destination=dest,
        category="Điểm thu hút khách du lịch",
        limit=BROWSE_LIMIT,
        **wheel,
    )
    add(
        "places:rated",
        "browse_places",
        "Địa điểm được đánh giá cao, đủ nhiều review",
        destination=dest,
        sort_by="rating",
        min_reviews=200,
        limit=BROWSE_LIMIT,
        **wheel,
    )
    add(
        "food",
        "browse_places",
        "Quán ăn cho bữa trưa, bữa tối",
        destination=dest,
        category="Nhà hàng",
        limit=BROWSE_LIMIT,
    )

    if req.budget.need_stay and nights(req) > 0:
        rooms = rooms_needed(req)
        cap = req.budget.caps.stay or int(req.budget.total_vnd * 0.5)
        add(
            "hotels",
            "search_hotels",
            "Chỗ ở có giá thật cho đúng ngày",
            destination=dest,
            check_in_date=start,
            check_out_date=(b.end_date + timedelta(days=1)).isoformat(),
            adults=b.adults + b.seniors,
            children_ages=[8] * b.children or None,
            max_price=max(300_000, cap // (nights(req) * rooms)),
            limit=15,
        )

    if req.budget.intercity and req.budget.origin:
        add(
            "intercity:road",
            "get_route",
            "Chặng đường bộ đi – về",
            points=[req.budget.origin, dest],
            vehicle="car",
        )
        dep, arr = airport(req.budget.origin), airport(dest)
        if dep and arr and dep != arr:
            add(
                "intercity:flight",
                "search_flights",
                "Vé máy bay khứ hồi",
                departure_id=dep,
                arrival_id=arr,
                outbound_date=start,
                return_date=end,
                adults=b.adults + b.seniors,
                children=b.children,
                limit=3,
            )

    if acc.veg:
        add("food:veg", "browse_places", "Quán chay", destination=dest, query="chay", limit=15)
        add(
            "food:veg_nearby",
            "find_nearby",
            "Quán chay quanh trung tâm",
            query="quán chay",
            near=dest,
            limit=10,
        )
    if b.seniors:
        add(
            "health",
            "find_nearby",
            "Bệnh viện, nhà thuốc gần trung tâm cho người lớn tuổi",
            query="bệnh viện",
            near=dest,
            limit=3,
            with_coordinates=False,
        )

    extra: list[tuple[str, dict[str, str]]] = []
    for tag, _level in sorted(req.interests.tags.items(), key=lambda kv: -kv[1]):
        extra += [(tag, q) for q in INTEREST_QUERIES.get(tag, [])]
    if b.children:
        extra.append(("giaitri", {"query": "vui chơi"}))
    if req.weather.rain == "indoor":
        extra += [("vanhoa", {"category": "Bảo tàng"}), ("cafe", {"category": "Quán cà phê"})]

    # Lượt tìm do chiến lược của nhóm đề xuất đứng trước, để không bị cắt khi chạm MAX_STEPS.
    hints = (strategy or {}).get("extra_searches") or []
    extra = [("nhom", {"query": h["query"]}) for h in hints] + extra

    seen: set[str] = set()
    for tag, query in extra:
        sig = str(sorted(query.items()))
        if sig in seen or len(steps) >= MAX_STEPS:
            continue
        seen.add(sig)
        add(
            f"places:{tag}",
            "browse_places",
            f"Chiến lược nhóm: {query.get('query')}" if tag == "nhom" else f"Sở thích: {tag}",
            destination=dest,
            limit=BROWSE_LIMIT,
            **query,
            **wheel,
        )
    return steps[:MAX_STEPS]


def hours_steps(req: PlanRequest, place_ids: list[str]) -> list[dict[str, Any]]:
    """Giờ mở cửa của danh sách ngắn, cho từng ngày đi (giờ mở cửa khác nhau theo thứ)."""
    ids = place_ids[:15]
    if not ids:
        return []
    steps = []
    for i in range(req.basic.days):
        day = req.basic.start_date + timedelta(days=i)
        steps.append(
            {
                "key": f"hours:{i}",
                "tool": "check_opening_hours",
                "args": {
                    "places": ids,
                    "at": f"{day.isoformat()} 10:00",
                    "stay_minutes": 60,
                    "destination": req.basic.destination,
                },
                "why": f"Giờ mở cửa ngày {i + 1}",
            }
        )
    return steps

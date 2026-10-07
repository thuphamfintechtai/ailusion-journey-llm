"""Node nào được dùng tool nào — khớp sơ đồ `kien-truc-agent-du-lich.drawio`.

Mỗi node chỉ thấy đúng nhóm tool của mình: LLM chọn tool chính xác hơn khi danh sách ngắn,
và trace nhìn vào là biết node đó được phép làm gì.

Chưa có nguồn dữ liệu, để sau: Cảnh báo lừa đảo, Giấy tờ & quy định, Ăn kiêng & dị ứng,
Y tế & cấp cứu (tạm dùng `find_nearby` để tìm quán chay / bệnh viện / nhà thuốc).
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

# ---------------------------------------------------------------- Graph A — nghiên cứu
SLOT = (  # Trích slot + Chuẩn hoá yêu cầu: chữ -> số, ngày, địa danh
    "parse_money",
    "convert_currency",
    "vn_calendar",
    "convert_lunar_date",
    "geocode_vn",
    "convert_admin_unit",
    "list_admin_units",
    "mask_pii",
)
PLAN = ("destination_snapshot", "vn_calendar", "weather_forecast")  # Lên kế hoạch
PLACES = (  # Tìm kiếm địa điểm
    "browse_places",
    "search_places",
    "find_nearby",
    "search_tripadvisor",
    "get_place_details",
    "check_opening_hours",
    "best_time_to_visit",
    "elevation_profile",
)
TRANSPORT = (  # Di chuyển & phương tiện
    "find_airports",
    "search_flights",
    "get_route",
    "distance_matrix",
    "elevation_profile",
    "weather_forecast",
    "marine_forecast",
)
LOCAL = (  # Tin địa phương: lễ hội, sự kiện, thiên tai, cấm đường
    "local_news",
    "google_news",
    "search_events",
    "find_festivals",
    "vn_calendar",
    "disaster_alerts",
    "air_quality",
    "weather_forecast",
    "source_trust",
)
PRICES = (  # Dò giá & chỗ trống
    "search_hotels",
    "search_flights",
    "find_airports",
    "convert_currency",
    "parse_money",
    "web_search",
)
REVIEWS = (  # Thu thập đánh giá
    "get_place_reviews",
    "get_place_details",
    "search_tripadvisor",
    "source_trust",
)
CRITIC = ("source_trust", "web_search", "fetch_page", "check_opening_hours")  # Phản biện

# ---------------------------------------------------------------- Graph B — quyết định
OPTIONS = ("plan_day_route", "optimize_route", "distance_matrix", "check_opening_hours")
PICK = ("score_options",)
EXPLAIN = ("export_itinerary", "sun_and_sky", "mask_pii")

NODE_TOOLS: dict[str, tuple[str, ...]] = {
    "slot": SLOT,
    "plan": PLAN,
    "places": PLACES,
    "transport": TRANSPORT,
    "local": LOCAL,
    "prices": PRICES,
    "reviews": REVIEWS,
    "critic": CRITIC,
    "options": OPTIONS,
    "pick": PICK,
    "explain": EXPLAIN,
}


def pick_tools(tools: list[BaseTool], node: str) -> list[BaseTool]:
    """Lọc đúng nhóm tool của `node`. Tên nào server không có thì ném lỗi ngay, khỏi gọi nhầm."""
    wanted = NODE_TOOLS[node]
    by_name = {t.name: t for t in tools}
    missing = [name for name in wanted if name not in by_name]
    if missing:
        raise LookupError(f"MCP server thiếu tool cho node '{node}': {missing}")
    return [by_name[name] for name in wanted]

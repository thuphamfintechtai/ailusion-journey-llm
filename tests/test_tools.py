"""Parse kết quả MCP và làm sạch schema — không cần MCP server chạy."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from ailusion.core.mcp import _parse_content, call_tool, gemini_safe_schema


def test_parse_content_reads_json_text_block():
    content = [{"type": "text", "text": '{"status": "success", "n": 1}', "id": "lc_1"}]
    assert _parse_content(content) == {"status": "success", "n": 1}


def test_parse_content_keeps_non_json_text():
    assert _parse_content("Không tìm thấy") == "Không tìm thấy"


async def test_call_tool_parses_result():
    @tool
    def echo_json(x: str) -> str:
        """trả JSON"""
        return json.dumps({"x": x})

    assert await call_tool(echo_json, x="a") == {"x": "a"}


def test_gemini_safe_schema_moves_non_string_enum_into_description():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hotel_class": {
                "anyOf": [{"type": "array", "items": {"type": "integer", "enum": [2, 3, 4, 5]}}],
                "description": "Lọc hạng sao.",
            },
            "sort_by": {"type": "string", "enum": ["price", "rating"]},
        },
    }
    cleaned = gemini_safe_schema(schema)
    items = cleaned["properties"]["hotel_class"]["anyOf"][0]["items"]
    assert "enum" not in items
    assert items["description"] == "Giá trị hợp lệ: 2, 3, 4, 5."
    assert cleaned["properties"]["sort_by"]["enum"] == ["price", "rating"]
    assert "additionalProperties" not in cleaned


def test_rating_floor_uses_mcp_bayes_and_falls_back_to_hoeffding() -> None:
    from ailusion.itinerary.shared.math import rating_floor
    from ailusion.itinerary.shared.tools import add_places

    basis = {"method": "bayes", "prior_mean": 4.35, "prior_reviews": 14.1, "prior_level": "rieng"}
    result = {
        "status": "success",
        "places": [
            # MCP đã tính sao Bayes (bước lấy chỗ và bước chấm cùng một số).
            {
                "place_id": "a",
                "name": "Quán A",
                "category": "Quán cà phê",
                "lat": 11.9,
                "lng": 108.4,
                "rating": 4.8,
                "reviews": 40,
                "rating_adjusted": 4.683,
                "rating_basis": basis,
            },
            # 5★ / 5 review: Bayes cao nhưng ít review hơn số review ảo -> chưa "tin được".
            {
                "place_id": "b",
                "name": "Quán B",
                "category": "Quán cà phê",
                "lat": 11.9,
                "lng": 108.4,
                "rating": 5.0,
                "reviews": 5,
                "rating_adjusted": 4.52,
                "rating_basis": basis,
            },
            # Tool không trả sao Bayes (SerpApi khách sạn, Vietmap): Hoeffding như cũ.
            {
                "place_id": "c",
                "name": "Bảo tàng C",
                "category": "Bảo tàng",
                "lat": 11.9,
                "lng": 108.4,
                "rating": 4.8,
                "reviews": 40,
            },
        ],
    }
    pool: dict = {}
    add_places(pool, result, "find_places")
    a, b, c = pool["a"], pool["b"], pool["c"]
    assert a.rating_floor == 4.683 and a.rating_trusted and a.rating_above_peers
    assert b.rating_floor == 4.52 and not b.rating_trusted
    # Đủ review mà sao chỉ ngang mức chung (Lăng Khải Định 4,5★ / 17.168): vẫn "tin được",
    # nhưng không phải "được đánh giá cao".
    pool["a"].rating_adjusted = 4.3
    assert a.rating_trusted and not a.rating_above_peers
    assert c.rating_floor == rating_floor(4.8, 40) and c.rating_trusted  # 4,026 ≥ 4,0


def test_bars_and_karaoke_are_not_meals() -> None:
    from ailusion.itinerary.shared.tools import kind_of

    # Loại Google có chữ "quán" nhưng không phải bữa ăn: chỗ chơi đêm.
    assert kind_of("Quán bar karaoke", "Karaoke ICOOL") == "visit"
    assert kind_of("Quán bar cocktail", "Chill Skybar & Dining") == "visit"
    assert kind_of("Quán rượu", "Malt South") == "visit"
    assert kind_of("Nhà hàng", "KARAOKE VT KTV") == "visit"  # karaoke xét cả tên
    assert kind_of("Quán bar tiếp viên nữ", "YUMI Counter & Lounge") is None
    # Vẫn là quán: quán bia / nhậu, gastropub, "Nhà hàng KTV" (quán bún thật ở Huế).
    assert kind_of("Quán bia", "Bia Hơi") == "meal"
    assert kind_of("Nhà hàng quán rượu dành cho người sành ăn", "Khoai Ruin Bar") == "meal"
    assert kind_of("Nhà hàng gia đình", "Nhà hàng KTV") == "meal"


def test_hoi_an_pool_fixes() -> None:
    """Lịch Hội An thật: phòng vé, quán sữa chua vào lịch; Chùa Cầu bị tính là chùa; 2 bản
    "Phố cổ Hội An"; show buổi tối chia vào ngày cuối về trưa."""
    from ailusion.itinerary.coordinator.filter import merge_duplicates
    from ailusion.itinerary.coordinator.score import split_by_day
    from ailusion.itinerary.shared.models import Place
    from ailusion.itinerary.shared.tools import kind_of

    assert kind_of("Phòng vé", "Quầy thông tin du lịch và bán vé tham quan phố cổ") is None
    assert kind_of("Chợ", "Hội an market yogurt") is None
    assert kind_of("Chợ", "Chợ Bà Lê") == "visit"

    def place(pid, name, cat, lat=15.877, lng=108.33, reviews=100, hours=None):
        return Place(
            place_id=pid,
            name=name,
            category=cat,
            kind="visit",
            lat=lat,
            lng=lng,
            rating=4.5,
            reviews=reviews,
            stay_low=60,
            hours=hours or {},
        )

    assert place("c", "Chùa Cầu", "Điểm mốc lịch sử").group == "điểm mốc lịch sử"
    assert place("p", "Chùa Pháp Bảo", "Chùa phật giáo").group == "chùa"
    assert place("v", "Chùa Cầu", "4004").group == "chùa"  # loại chỉ là mã số: xét tên

    places = {
        "a": place("a", "Phố Cổ Hội An", "Điểm mốc lịch sử", 15.8786, 108.3327, 770),
        "b": place("b", "Phố cổ Hội An", "Điểm thu hút khách du lịch", 15.8779, 108.3289, 16),
    }
    assert merge_duplicates(places) == ["Phố cổ Hội An (trùng Phố Cổ Hội An)"]
    assert list(places) == ["a"]
    # Tên toàn chữ chung ("Chùa Cầu", "Bảo Tàng Hội An" sau khi bỏ chữ chung) không phải trùng tên.
    places = {
        "c": place("c", "Chùa Cầu", "Điểm mốc lịch sử", 15.8771, 108.3261, 29000),
        "m": place("m", "Bảo Tàng Hội An", "Bảo tàng", 15.8790, 108.3300, 343),
    }
    assert merge_duplicates(places) == [] and set(places) == {"c", "m"}

    show = place(
        "s", "KÝ ỨC HỘI AN", "Điểm thu hút khách du lịch", hours={d: (960, 1320) for d in (1, 2, 3)}
    )
    other = [place(f"o{i}", f"Chỗ {i}", f"Loại {i}") for i in range(4)]
    windows = {1: (480, 1410), 2: (480, 1410), 3: (480, 750)}  # ngày 3 về 12:30
    by_day = split_by_day([show, *other], [1, 2, 3], {}, None, windows)
    assert "s" not in by_day[3]


def test_famous_place_of_another_branch_is_not_in_every_pool() -> None:
    from ailusion.itinerary.coordinator.score import pick_pool
    from ailusion.itinerary.shared.models import Place

    def place(pid, reviews, rating=4.5):
        return Place(
            place_id=pid,
            name=pid,
            category=f"Loại {pid}",
            kind="visit",
            lat=15.88,
            lng=108.33,
            rating=rating,
            reviews=reviews,
        )

    famous = place("chua-cau", 29000, 4.3)  # phần của phương án khác
    own = [place(f"o{i}", 100 + i, 4.6) for i in range(4)]
    pool, _ = pick_pool([famous, *own], [], 4, 0, None, per_group=3, used={"chua-cau"})
    assert famous not in pool  # đủ chỗ của mình thì không lấy chỗ của phương án khác
    pool, _ = pick_pool([famous, *own], [], 5, 0, None, per_group=3, used={"chua-cau"})
    assert famous in pool  # thiếu thì vẫn dùng


def test_evening_show_after_dinner_and_idle_gap_filled() -> None:
    from ailusion.itinerary.coordinator.score import split_by_day
    from ailusion.itinerary.coordinator.simulate import evening_after_dinner, fill_visits, simulate
    from ailusion.itinerary.shared.models import STAY, DayPlan, DayWindow, Place
    from ailusion.itinerary.shared.tools import kind_of

    assert kind_of("Doanh nghiệp bán vé sự kiện", "Memoryland- Vé Ký Ức Hội An") is None
    assert kind_of("Điểm thu hút khách du lịch", "Vé Show Ký Ức Hội An - Huyền") is None
    assert kind_of("Điểm thu hút khách du lịch", "KÝ ỨC HỘI AN") == "visit"

    def p(pid, kind="visit", hours=None, stay=60, cat=None, reviews=100):
        return Place(
            place_id=pid,
            name=pid,
            category=cat or f"Loại {pid}",
            kind=kind,
            lat=15.88,
            lng=108.33,
            stay_low=stay,
            stay_high=stay,
            reviews=reviews,
            hours={1: hours} if hours else {},
        )

    places = {
        "b": p("b", "meal", cat="Nhà hàng"),
        "v": p("v"),
        "l": p("l", "meal", cat="Nhà hàng"),
        "show": p("show", hours=(960, 1320)),
        "d": p("d", "meal", cat="Nhà hàng"),
        "x": p("x"),
        "y": p("y"),
    }
    ids = [*places, STAY]
    m = {(a, b): 10.0 for a in ids for b in ids if a != b}
    w = DayWindow(day=1, day_start="08:00", day_end="23:30", free_minutes=900)
    # Show 16:00–22:00 xếp trước bữa tối: nên đi sau bữa tối (ưu tiên khi đặt quán, không là lỗi).
    plan = DayPlan(id="L", day=1, leave_stay="08:00", stops=["b", "v", "l", "show", "d"])
    run = simulate(plan, places, m, w, [], False)
    assert evening_after_dinner(plan, run.stops, places)
    assert not any("sau bữa tối" in i for i in run.issues)
    plan = DayPlan(id="L", day=1, leave_stay="08:00", stops=["b", "v", "l", "d", "show"])
    assert not evening_after_dinner(plan, simulate(plan, places, m, w, [], False).stops, places)
    # Trống lâu trước show (16:00): bù chỗ chơi vào khoảng trống.
    plan = DayPlan(id="L", day=1, leave_stay="08:00", stops=["b", "v", "l", "show"])
    assert fill_visits(plan, places, m, w, ["x", "y"], [], []) >= 1
    assert {"x", "y"} & set(plan.stops)

    # Mỏ neo ngày 1 là chỗ của phương án này, không phải chỗ nổi tiếng mượn của phương án khác.
    famous, mine = p("cc", reviews=29000), p("m1", reviews=300)
    by_day = split_by_day([famous, mine], [1, 2], {}, None, None, {"cc"})
    assert by_day[1][0] == "m1"

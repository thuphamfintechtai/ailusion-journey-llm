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
    assert a.rating_floor == 4.683 and a.rating_trusted
    assert b.rating_floor == 4.52 and not b.rating_trusted
    assert c.rating_floor == rating_floor(4.8, 40) and c.rating_trusted  # 4,026 ≥ 4,0

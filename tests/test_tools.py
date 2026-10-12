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

"""Nạp kho tool từ MCP server "Ailusion Journey" thành LangChain tool.

Server chạy ở `MCP_URL` (mặc định http://localhost:8002/mcp, transport streamable HTTP).
Tool lấy về là `BaseTool` bình thường: gắn vào LLM bằng `bind_tools`, hoặc gọi thẳng
`await tool.ainvoke({...})`. Mỗi lượt gọi tool mở một phiên MCP riêng (server không giữ trạng
thái), nên dùng chung được giữa các node chạy song song.

    tools = await load_mcp_tools()
    places = await call_tool(tools_by_name(tools)["search_places"], query="cà phê Đà Lạt")
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from ailusion.config import Settings, get_settings

SERVER_NAME = "ailusion"


def mcp_client(settings: Settings | None = None) -> MultiServerMCPClient:
    s = settings or get_settings()
    return MultiServerMCPClient(
        {SERVER_NAME: {"transport": "streamable_http", "url": s.tools.mcp_url}},
        # Lỗi của tool (sai tham số, hết quota...) trả về cho LLM đọc thay vì ném exception.
        handle_tool_errors=True,
    )


async def load_mcp_tools(settings: Settings | None = None) -> list[BaseTool]:
    """Toàn bộ tool server đang có (gọi `list_tools` một lần), schema đã làm sạch cho Gemini."""
    tools = await mcp_client(settings).get_tools(server_name=SERVER_NAME)
    for tool in tools:
        if isinstance(tool.args_schema, dict):
            tool.args_schema = gemini_safe_schema(tool.args_schema)
    return tools


def gemini_safe_schema(schema: Any) -> Any:
    """Bỏ những phần JSON Schema mà SDK Gemini không nhận, giữ nguyên ý nghĩa cho LLM.

    - `enum` không phải chuỗi (VD `hotel_class: [2, 3, 4, 5]`) làm SDK ném ValidationError,
      cả nhánh có tool đó chết từ lượt gọi model đầu tiên -> bỏ enum, ghi giá trị vào mô tả.
      Server vẫn tự kiểm tra tham số như cũ.
    - `additionalProperties`: Gemini bỏ qua nhưng in cảnh báo cho mỗi tool -> bỏ luôn.
    """
    if isinstance(schema, list):
        return [gemini_safe_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned = {
        key: gemini_safe_schema(value)
        for key, value in schema.items()
        if key != "additionalProperties"
    }
    enum = cleaned.get("enum")
    if enum and not all(isinstance(value, str) for value in enum):
        del cleaned["enum"]
        allowed = ", ".join(str(value) for value in enum)
        hint = f"Giá trị hợp lệ: {allowed}."
        cleaned["description"] = f"{cleaned.get('description', '')} {hint}".strip()
    return cleaned


def tools_by_name(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {t.name: t for t in tools}


async def call_tool(tool: BaseTool, **args: Any) -> Any:
    """Gọi tool rồi trả kết quả đã parse JSON (dict/list), cho node gọi tool trực tiếp.

    Server trả về content block `[{"type": "text", "text": "<json>"}]`; block nào không phải
    JSON thì giữ nguyên chuỗi. Khi để LLM tự gọi tool thì không cần hàm này.
    """
    return _parse_content(await tool.ainvoke(args))


def _parse_content(content: Any) -> Any:
    if isinstance(content, str):
        return _maybe_json(content)
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
        parsed = [_maybe_json(b.get("text", "")) for b in blocks]
        return parsed[0] if len(parsed) == 1 else parsed
    return content


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text

"""Những việc một node được làm, gom vào `Ctx` để node chỉ cần nhận `(state, ctx)`:

    data, log = await ctx.tool("geocode_vn", address="Đà Nẵng")          # 1 tool
    results, logs = await ctx.many([("find_places", {...}), ...])       # nhiều tool song song
    out, calls, usage = await ctx.ask(PROMPT, human, Schema, 900, "node") # 1 lượt LLM
    await ctx.log("node", "step", "đang làm gì")                         # nhật ký cho web xem

`Spent` cộng dồn số lượt LLM / token / lượt tool một việc đã dùng để trả vào state.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from ailusion.core.agent_log import AgentLog
from ailusion.core.llm import get_chat_model
from ailusion.core.mcp import call_tool, tools_by_name

from ..shared.tools import tool_ok

agent_log = AgentLog()


log = logging.getLogger(__name__)


@dataclass
class Spent:
    """Số lượt LLM, token và lượt tool một việc đã dùng — node cộng dồn rồi trả vào state."""

    llm_calls: int = 0
    usage: list[dict[str, Any]] = field(default_factory=list)
    tool_log: list[dict[str, Any]] = field(default_factory=list)

    def add(self, other: Spent) -> Spent:
        return Spent(
            self.llm_calls + other.llm_calls,
            self.usage + other.usage,
            self.tool_log + other.tool_log,
        )

    def state(self) -> dict[str, Any]:
        return {"llm_calls": self.llm_calls, "usage": self.usage, "tool_log": self.tool_log}


class Ctx:
    def __init__(self, tools: list[BaseTool], config: RunnableConfig, node: str = "") -> None:
        self.tools = tools_by_name(tools)
        self.thread_id = config["configurable"]["thread_id"]
        # Node đang chạy: ghi kèm mỗi lượt gọi tool vào nhật ký, để web hiện chip nguồn đã dùng.
        self.node = node
        # Nhánh song song (VD "pa2" của node lap_phuong_an): mọi nhật ký ghi "node:nhánh" để web
        # hiện mỗi nhánh một làn, không lẫn bước của các nhánh với nhau.
        self.lane = ""

    def branch(self, node: str, lane: str) -> Ctx:
        c = copy.copy(self)
        c.node, c.lane = node, lane
        return c

    def _node(self, node: str) -> str:
        return f"{self.node}:{self.lane}" if self.lane else node

    async def tool(self, name: str, **args: Any) -> tuple[Any, dict[str, Any]]:
        """Gọi 1 tool MCP -> (kết quả, dòng nhật ký). Tool hỏng thì trả lỗi, không ném lỗi."""
        if name not in self.tools:
            return {"status": "error", "message": f"không có tool {name}"}, {
                "tool": name,
                "ok": False,
            }
        await self._trace("tool_call", f"gọi {name}", tool=name)
        try:
            data = await call_tool(self.tools[name], **args)
        except Exception as e:  # noqa: BLE001 - một tool hỏng không làm sập cả lộ trình
            data = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        ok = tool_ok(data)
        await self._trace("tool_result", f"{name} {'xong' if ok else 'lỗi'}", tool=name, ok=ok)
        return data, {"tool": name, "ok": ok}

    async def many(self, calls: list[tuple[str, dict[str, Any]]]) -> tuple[list[Any], list[dict]]:
        """Gọi nhiều tool song song, giữ đúng thứ tự."""
        done = await asyncio.gather(*(self.tool(n, **a) for n, a in calls))
        return [d for d, _ in done], [t for _, t in done]

    async def ask(
        self, system: str, human: str, schema: type[BaseModel], max_tokens: int, node: str
    ) -> tuple[Any, int, list[dict[str, Any]]]:
        """Một lượt LLM có structured output -> (kết quả hoặc None, số lượt gọi, token đã dùng)."""
        try:
            # function_calling thay vì json_schema "strict" mặc định của OpenAI: strict bắt mọi
            # trường phải bắt buộc và cấm dict tự do (VD DecisionOutput.explanation) -> lỗi 400.
            llm = get_chat_model(max_tokens=max_tokens, max_retries=1).with_structured_output(
                schema, include_raw=True, method="function_calling"
            )
            out = await llm.ainvoke([SystemMessage(system), HumanMessage(human)])
        except Exception as e:  # noqa: BLE001
            log.warning("%s: LLM lỗi — %s: %s", node, type(e).__name__, str(e)[:300])
            await self._trace("step", "LLM lỗi, dùng phương án dự phòng", node=node)
            return None, 0, []
        raw, parsed = (out.get("raw"), out.get("parsed")) if isinstance(out, dict) else (None, out)
        meta = getattr(raw, "usage_metadata", None) or {}
        usage = [
            {"node": node, "in": meta.get("input_tokens", 0), "out": meta.get("output_tokens", 0)}
        ]
        if parsed is None:
            return None, 1, usage
        return (parsed if isinstance(parsed, schema) else schema.model_validate(parsed)), 1, usage

    async def log(self, node: str, kind: str, text: str, **data: Any) -> None:
        """Nhật ký bước cho web. Ghi hỏng (Redis timeout khi máy bận) thì bỏ qua: nhật ký không được
        làm sập nhánh / cả lộ trình (4 lộ trình chạy cùng lúc từng trả HTTP 500 vì vậy)."""
        try:
            await agent_log.publish(
                self.thread_id, node=self._node(node), kind=kind, text=text, **data
            )
        except Exception:  # noqa: BLE001
            log.warning("không ghi được nhật ký %s/%s", self._node(node), kind)

    async def _trace(self, kind: str, text: str, node: str = "", **data: Any) -> None:
        """Nhật ký lượt gọi tool / lỗi LLM. Ghi nhật ký hỏng thì bỏ qua, không làm hỏng lộ trình."""
        node = self._node(node or self.node)
        if not node:
            return
        try:
            await agent_log.publish(self.thread_id, node=node, kind=kind, text=text, **data)
        except Exception:  # noqa: BLE001
            log.debug("không ghi được nhật ký %s/%s", node, kind)

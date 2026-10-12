"""Chạy pipeline lập lộ trình 1 lần từ terminal, in tóm tắt và ghi JSON đầy đủ.

    make itinerary                 # form mẫu: Đà Nẵng 3 ngày, cặp đôi, 15tr
    make itinerary F=form.json     # form của bạn (JSON đúng TripForm)

TỐN TIỀN: mỗi lần chạy khoảng 2–5 lượt LLM và vài chục lượt tool (phần lớn đọc DB miễn phí,
vài lượt Tavily/SerpApi khi DB thiếu). Cần: MCP server (MCP_URL), Redis + Postgres.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from ailusion.core.agent_log import AgentLog
from ailusion.core.mcp import load_mcp_tools
from ailusion.core.memory import memory_backends
from ailusion.core.redis import close_redis
from ailusion.core.tracing import run_config, setup_observability, shutdown_observability
from ailusion.itinerary import TripForm, build_itinerary_graph, itinerary_markdown, to_response

START = date.today() + timedelta(days=10)
SAMPLE = {
    "destination": "Đà Nẵng",
    "start_date": START.isoformat(),
    "end_date": (START + timedelta(days=2)).isoformat(),
    "group_size": 2,
    "group_type": "couple",
    "budget": 15_000_000,
    "purposes": ["relax", "food"],
}


async def main(form_path: str | None) -> int:
    data = json.loads(Path(form_path).read_text()) if form_path else SAMPLE
    form = TripForm.model_validate(data)
    try:
        tools = await load_mcp_tools()
    except Exception as e:  # noqa: BLE001
        print(f"Không kết nối được MCP server: {type(e).__name__}: {e}")
        return 2

    setup_observability()
    thread_id = f"plan-{uuid.uuid4().hex[:8]}"
    print(f"thread_id: {thread_id} · {len(tools)} tool MCP")
    started = time.monotonic()
    async with memory_backends() as (checkpointer, store):
        graph = build_itinerary_graph(tools).compile(checkpointer=checkpointer, store=store)
        config = run_config(thread_id=thread_id, graph="itinerary", user_id="cli")
        async with AgentLog().turn(thread_id):
            state = await graph.ainvoke(
                {"form": form.model_dump(mode="json"), "sent": sorted(data)}, config
            )
    resp = to_response(state, thread_id, time.monotonic() - started)
    await close_redis()
    shutdown_observability()

    print(itinerary_markdown(resp))
    s = resp.stats
    print(
        f"\n{s.llm_calls} lượt LLM · {s.tool_calls} lượt tool ({s.tool_errors} lỗi) · "
        f"{s.tokens_in}+{s.tokens_out} token · ${s.cost_usd} · {s.seconds}s"
    )
    out = Path(f"{thread_id}.json")
    out.write_text(resp.model_dump_json(indent=2), encoding="utf-8")
    print(f"JSON đầy đủ: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))

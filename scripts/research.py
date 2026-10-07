"""Chạy Graph A (nghiên cứu) từ terminal và in diễn biến từng node.

    make research Q="Đi Đà Lạt 3 ngày cuối tuần này, 2 người lớn 1 bé 5 tuổi, tầm 15tr"

Cần: MCP server (MCP_URL), Redis + Postgres (`make up`), GEMINI_API_KEY.
Gói bối cảnh cuối cùng được ghi ra `<thread_id>.json` để xem đầy đủ.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

from ailusion import (
    AgentLog,
    close_redis,
    memory_backends,
    run_config,
    setup_observability,
    shutdown_observability,
)
from ailusion.graphs import build_research_graph
from ailusion.llm import MissingApiKeyError, get_chat_model
from ailusion.tools import load_mcp_tools

DEFAULT_REQUEST = "Đi Đà Lạt 3 ngày cuối tuần này, 2 người lớn 1 bé 5 tuổi, ngân sách tầm 15tr"


async def main(request: str) -> int:
    try:
        get_chat_model()  # báo thiếu khoá ngay
        tools = await load_mcp_tools()
    except MissingApiKeyError as e:
        print(e)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"Không kết nối được MCP server: {type(e).__name__}: {e}")
        return 2

    setup_observability()
    thread_id = f"research-{uuid.uuid4().hex[:8]}"
    print(f"thread_id: {thread_id} · {len(tools)} tool MCP\nYêu cầu: {request}\n")

    final: dict = {}
    async with memory_backends() as (checkpointer, store):
        graph = build_research_graph(tools).compile(checkpointer=checkpointer, store=store)
        config = run_config(thread_id=thread_id, graph="research", user_id="cli")
        async with AgentLog().turn(thread_id):
            stream = graph.astream({"request": request}, config, stream_mode="updates")
            async for update in stream:
                for node, change in update.items():
                    print(f"▸ {node}: {_describe(node, change)}", flush=True)
        final = (await graph.aget_state(config)).values

    shutdown_observability()
    await close_redis()

    if final.get("question"):
        print(f"\nCần hỏi lại: {final['question']}")
        return 0
    context = final["context"]
    out = Path(f"{thread_id}.json")
    out.write_text(json.dumps(context, ensure_ascii=False, indent=2, default=str))
    _print_digest(context)
    print(f"\nGói bối cảnh đầy đủ: {out}")
    return 0


def _print_digest(context: dict) -> None:
    """Kết quả nghiên cứu dạng đọc được: tóm tắt từng nhánh, kết luận phản biện."""
    digest, stats = context["digest"], context["stats"]
    print(f"\n══ KẾT QUẢ · {stats['tool_calls']} lần gọi tool ({stats['tool_errors']} lỗi)")
    for branch in digest["branches"]:
        print(f"\n[{branch['branch']}] {branch['summary']}")
        for item in branch["items"]:
            print(f"  • {item['title']}: {item['detail']}")
        for gap in branch["gaps"]:
            print(f"  ? {gap}")
    print(f"\nPhản biện: {'đủ dữ kiện' if digest['verdict'] == 'du' else 'còn thiếu'}")
    for line in digest["missing"] + digest["conflicts"] + digest["trust_notes"]:
        print(f"  - {line}")


def _describe(node: str, change: dict | None) -> str:
    """Một dòng tóm tắt thay đổi của node, đủ để theo dõi luồng chạy."""
    change = change or {}
    if node == "trich_slot":
        s = change["slots"]
        return f"{s.get('destination')} · {s.get('days')} ngày · ngân sách '{s.get('budget_text')}'"
    if node == "chuan_hoa":
        n = change["normalized"]
        return f"{n['budget_vnd']} đ · {n['location'] and n['location']['province']} · {n['assumptions']}"
    if node == "lap_ke_hoach":
        steps = change["plan"]["steps"]
        lines = [f"{len(steps)} bước — {change['plan']['reasoning']}"]
        lines += [f"    {st['branch']:9s} {st['tool']}({st['args']})" for st in steps]
        return "\n".join(lines)
    if node == "goi_tool":
        (result,) = change["results"]
        return f"{'✓' if result['ok'] else '✗'} {result['tool']} ({len(result['data'])} ký tự)"
    if node == "phan_bien":
        d = change["digest"]
        return f"{d['verdict']} {d['missing'] if d['replan'] else ''}"
    return ", ".join(change) or "-"


if __name__ == "__main__":
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]) or DEFAULT_REQUEST)))

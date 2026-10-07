"""Chạy pipeline POST /plan từ terminal với một form mẫu, in lộ trình gọn và ghi JSON đầy đủ.

    make plan                     # form mẫu: Đà Lạt 3 ngày, 2 người lớn 1 bé, 15tr
    make plan F=form.json         # form của bạn (JSON đúng PlanRequest)

Cần: MCP server (MCP_URL), Redis + Postgres (`make up`). Thiếu GEMINI_API_KEY vẫn chạy,
chỉ phần giải thích dùng mẫu soạn sẵn.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from ailusion import (
    AgentLog,
    close_redis,
    memory_backends,
    run_config,
    setup_observability,
    shutdown_observability,
)
from ailusion.planning import PlanRequest, build_plan_graph, to_response
from ailusion.tools import load_mcp_tools

SAMPLE = {
    "basic": {
        "destination": "Đà Lạt",
        "start_date": (date.today() + timedelta(days=10)).isoformat(),
        "days": 3,
        "adults": 2,
        "children": 1,
    },
    "budget": {"total_vnd": 15_000_000, "hard": True},
    "interests": {"tags": {"thiennhien": 2, "cafe": 1, "amthuc": 1}},
    "weather": {"rain": "flex"},
    "access": {"no_seafood": True},
}


async def main(form_path: str | None) -> int:
    form = json.loads(Path(form_path).read_text()) if form_path else SAMPLE
    req = PlanRequest.model_validate(form)
    try:
        tools = await load_mcp_tools()
    except Exception as e:  # noqa: BLE001
        print(f"Không kết nối được MCP server: {type(e).__name__}: {e}")
        return 2

    setup_observability()
    thread_id = f"plan-{uuid.uuid4().hex[:8]}"
    print(f"thread_id: {thread_id} · {len(tools)} tool MCP")
    t0 = time.monotonic()
    async with memory_backends() as (checkpointer, store):
        graph = build_plan_graph(tools).compile(checkpointer=checkpointer, store=store)
        config = run_config(thread_id=thread_id, graph="plan", user_id="cli")
        async with AgentLog().turn(thread_id):
            stream = graph.astream(
                {"request": req.model_dump(mode="json")}, config, stream_mode="updates"
            )
            async for update in stream:
                for node in update:
                    print(f"  ▸ {node}  {time.monotonic() - t0:5.1f}s", flush=True)
        state = (await graph.aget_state(config)).values
    shutdown_observability()
    await close_redis()

    plan = to_response(state, thread_id, time.monotonic() - t0)
    out = Path(f"{thread_id}.json")
    out.write_text(plan.model_dump_json(indent=2))
    _print(plan)
    print(f"\nĐầy đủ: {out}")
    return 0


def _vnd(x: int) -> str:
    return f"{x:,}".replace(",", ".") + " đ"


def _print(plan) -> None:  # noqa: ANN001
    d, s = plan.decision, plan.stats
    print(f"\n══ {plan.destination} · {plan.days} ngày từ {plan.start_date}")
    print(
        f"   {s.llm_calls} lượt LLM · {s.tool_calls} lượt tool ({s.tool_errors} lỗi) · {s.seconds}s"
    )
    for key, o in plan.options.items():
        star = "★" if key == d.winner else " "
        print(
            f"\n{star} {o.name}: {_vnd(o.cost.total)} · vỡ lịch {round(o.risk.p_break * 100)}%"
            f" · {'đạt' if o.hard_ok else 'KHÔNG đạt'} ràng buộc cứng"
        )
        if o.hotel:
            print(f"    Ở: {o.hotel.name} ({o.hotel.note})")
        for issue in o.issues:
            print(f"    ! {issue}")
        if key != d.winner:
            continue
        for day in o.days:
            w = day.weather
            print(
                f"\n    Ngày {day.index} · {day.weekday} {day.date} · mưa sáng/chiều/tối "
                f"{w.morning_rain_pct}/{w.afternoon_rain_pct}/{w.evening_rain_pct}%"
            )
            for it in day.items:
                leg = f" (+{it.leg.minutes}′ {it.leg.km}km)" if it.leg else ""
                price = f" · {_vnd(it.price_vnd)}" if it.price_vnd else ""
                print(f"      {it.start}–{it.end} {it.name}{leg}{price}")
            for warn in day.warnings:
                print(f"      ! {warn}")
    print(f"\nQuyết định: {d.winner_by_weight_set} · {d.tie_break or ''}")
    print(f"Giải thích: {plan.explanation.summary}\n  {plan.explanation.why_winner}")
    for tip in plan.explanation.tips:
        print(f"  - {tip}")
    for note in plan.assumptions:
        print(f"  ~ {note}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))

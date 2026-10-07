"""Smoke test end-to-end cho hạ tầng: `make smoke` (thêm `--llm` để gọi LLM thật).

Graph mini đi đúng các chặng của sơ đồ, logic là giả (không gọi tool thật):

    trich_slot -> dong_bang_boi_canh              (Graph A rút gọn)
               -> lap_phuong_an -> chon_phuong_an (Graph B rút gọn)

Sau đó chạy What-if: lấy checkpoint ngay sau "đóng băng gói bối cảnh", đổi ngân sách,
chạy lại riêng phần B. Kiểm tra được:

- Redis checkpointer lưu state + time-travel (what-if) hoạt động
- Postgres store đọc/ghi bộ nhớ dài hạn (hồ sơ người dùng)
- Redis Stream nhật ký agent nhận sự kiện
- Trace lên LangSmith + Langfuse, cùng một session, số điện thoại trong yêu cầu đã bị che
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from ailusion import (
    AgentLog,
    Namespaces,
    close_redis,
    get_settings,
    memory_backends,
    run_config,
    setup_observability,
    shutdown_observability,
)
from ailusion.llm import MissingApiKeyError, get_chat_model, message_text
from ailusion.prompts.smoke import DESTINATION

REQUEST = "Đi Đà Lạt 3 ngày, 2 người lớn 1 bé, ngân sách 15tr. Liên hệ 0901234567."
FULL_BUDGET_VND = 15_000_000
WHAT_IF_BUDGET_VND = 10_000_000

agent_log = AgentLog()


class TripState(TypedDict, total=False):
    request: str
    slots: dict[str, Any]
    budget_vnd: int
    context_frozen: bool
    options: list[dict[str, Any]]
    chosen: dict[str, Any]


def build_graph(*, use_llm: bool) -> StateGraph:
    """Dựng graph mini. `use_llm` quyết định node đầu có gọi LLM thật hay không."""

    async def trich_slot(state: TripState, config: RunnableConfig, runtime: Runtime) -> dict:
        """Graph A: rút slot từ yêu cầu, có tham khảo hồ sơ trong bộ nhớ dài hạn."""
        user_id = config["configurable"].get("user_id", "anon")
        profile = (
            await runtime.store.aget(Namespaces.profile(user_id), "main") if runtime.store else None
        )

        slots: dict[str, Any] = {"destination": "Đà Lạt", "days": 3, "adults": 2, "children": 1}
        if profile:
            slots["dietary"] = profile.value.get("dietary")
        if use_llm:
            slots["destination_llm"] = await _ask_llm_for_destination(state["request"])

        await _log(config, node="trich_slot", kind="finding", text="Đã trích slot", slots=slots)
        return {"slots": slots, "budget_vnd": FULL_BUDGET_VND}

    async def dong_bang_boi_canh(state: TripState, config: RunnableConfig) -> dict:
        """Ranh giới A -> B: chốt bối cảnh. What-if sẽ tua lại đúng checkpoint này."""
        await _log(config, node="dong_bang", kind="plan", text="Đóng băng gói bối cảnh")
        return {"context_frozen": True}

    async def lap_phuong_an(state: TripState, config: RunnableConfig) -> dict:
        """Graph B: dựng các phương án kèm chi phí và điểm đánh giá."""
        options = [
            {"name": "Tiết kiệm", "cost_vnd": 8_500_000, "score": 0.62},
            {"name": "Cân bằng", "cost_vnd": 12_000_000, "score": 0.78},
            {"name": "Thoải mái", "cost_vnd": 18_000_000, "score": 0.91},
        ]
        await _log(config, node="lap_phuong_an", kind="plan", text="Dựng 3 phương án")
        return {"options": options}

    async def chon_phuong_an(state: TripState, config: RunnableConfig) -> dict:
        """Graph B: chọn phương án điểm cao nhất còn nằm trong ngân sách."""
        feasible = [o for o in state["options"] if o["cost_vnd"] <= state["budget_vnd"]]
        chosen = max(feasible, key=lambda o: o["score"]) if feasible else {"name": "không khả thi"}
        await _log(config, node="chon_phuong_an", kind="done", text=f"Chọn: {chosen['name']}")
        return {"chosen": chosen}

    graph = StateGraph(TripState)
    for node in (trich_slot, dong_bang_boi_canh, lap_phuong_an, chon_phuong_an):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "trich_slot")
    graph.add_edge("trich_slot", "dong_bang_boi_canh")
    graph.add_edge("dong_bang_boi_canh", "lap_phuong_an")
    graph.add_edge("lap_phuong_an", "chon_phuong_an")
    graph.add_edge("chon_phuong_an", END)
    return graph


async def _log(config: RunnableConfig, *, node: str, kind: str, text: str, **data: Any) -> None:
    """Ghi nhật ký agent cho thread đang chạy."""
    await agent_log.publish(
        config["configurable"]["thread_id"], node=node, kind=kind, text=text, **data
    )


async def _ask_llm_for_destination(request: str) -> str:
    message = await get_chat_model().ainvoke(DESTINATION.format(request=request))
    return message_text(message.content).strip()


async def run_full_plan(graph: Any, thread_id: str, user_id: str) -> dict:
    """Lượt 1: chạy hết A -> B với ngân sách đầy đủ."""
    config = run_config(thread_id=thread_id, graph="smoke", user_id=user_id)
    result = await graph.ainvoke({"request": REQUEST}, config)
    print(f"Lượt 1  (ngân sách 15tr): chọn {result['chosen']['name']}")
    return result


async def run_what_if(graph: Any, thread_id: str, user_id: str) -> dict:
    """Lượt 2: tua về checkpoint sau "đóng băng", hạ ngân sách, chạy lại riêng phần B."""
    frozen = await _find_checkpoint_before(graph, thread_id, node="lap_phuong_an")
    forked = await graph.aupdate_state(frozen.config, {"budget_vnd": WHAT_IF_BUDGET_VND})

    config = run_config(
        thread_id=thread_id,
        graph="what_if",
        user_id=user_id,
        checkpoint_id=forked["configurable"]["checkpoint_id"],
        checkpoint_ns=forked["configurable"].get("checkpoint_ns", ""),
    )
    result = await graph.ainvoke(None, config)
    print(f"What-if (ngân sách 10tr): chọn {result['chosen']['name']}")
    return result


async def _find_checkpoint_before(graph: Any, thread_id: str, *, node: str) -> Any:
    """Checkpoint mà node kế tiếp đúng bằng `node` — tức là ngay trước khi node đó chạy."""
    async for snapshot in graph.aget_state_history({"configurable": {"thread_id": thread_id}}):
        if snapshot.next == (node,):
            return snapshot
    raise AssertionError(f"không tìm thấy checkpoint ngay trước node {node}")


def print_trace_links(thread_id: str, status: Any) -> None:
    settings = get_settings()
    print(f"\nthread_id / session: {thread_id}")
    if status.langsmith:
        project = settings.langsmith.project
        print(f"  LangSmith: project '{project}' -> lọc tag graph:smoke / graph:what_if")
    if status.langfuse:
        print(f"  Langfuse:  {settings.langfuse.base_url} -> Sessions -> {thread_id}")
    if not (status.langsmith or status.langfuse):
        print("  (chưa bật LangSmith/Langfuse — điền khoá vào .env)")
    print("  Kiểm tra trên UI: số điện thoại phải hiển thị là <PHONE>.")


async def main(*, use_llm: bool) -> int:
    if use_llm:
        try:
            get_chat_model()  # báo thiếu khoá ngay, trước khi đụng tới Redis/Postgres
        except MissingApiKeyError as e:
            print(e)
            return 2

    status = setup_observability()
    thread_id = f"smoke-{uuid.uuid4().hex[:8]}"
    user_id = "smoke-user"

    async with memory_backends() as (checkpointer, store):
        profile_ns = Namespaces.profile(user_id)
        await store.aput(profile_ns, "main", {"dietary": "bé dị ứng hải sản"}, index=False)
        graph = build_graph(use_llm=use_llm).compile(checkpointer=checkpointer, store=store)

        full = await run_full_plan(graph, thread_id, user_id)
        what_if = await run_what_if(graph, thread_id, user_id)
        assert full["chosen"]["name"] == "Cân bằng", full["chosen"]
        assert what_if["chosen"]["name"] == "Tiết kiệm", what_if["chosen"]

        thread = {"configurable": {"thread_id": thread_id}}
        n_checkpoints = len([c async for c in checkpointer.alist(thread)])
        await store.adelete(profile_ns, "main")

    events = await agent_log.read(thread_id)
    print(f"Checkpoint trong Redis: {n_checkpoints} · sự kiện nhật ký agent: {len(events)}")

    shutdown_observability()
    await close_redis()
    print_trace_links(thread_id, status)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="gọi LLM thật trong node trich_slot")
    sys.exit(asyncio.run(main(use_llm=parser.parse_args().llm)))

"""Route lập lộ trình (pipeline `ailusion.itinerary`).

    POST /itinerary               form -> tối đa 3 phương án (hoặc câu hỏi nếu còn thiếu ý)
    POST /chat/{thread_id}/plan   như /itinerary, rồi nối form + lộ trình dạng chữ vào hội thoại

Tiến độ từng bước: GET /chat/{form.thread_id}/events.
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, FastAPI, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from ailusion.chat.graph import REPLY_NODE
from ailusion.core.agent_log import AgentLog
from ailusion.core.mcp import load_mcp_tools
from ailusion.core.tracing import run_config
from ailusion.itinerary import (
    ItineraryResponse,
    NoPlacesError,
    TripForm,
    build_itinerary_graph,
    itinerary_markdown,
    request_text,
    to_response,
)

from .schemas import ChatPlanReply

router = APIRouter(tags=["itinerary"])
agent_log = AgentLog()
# Ô không phải thông tin chuyến đi: không tính là "user đã điền".
NOT_FORM_FIELDS = {"message", "user_id", "thread_id"}


@router.post("/itinerary", response_model=ItineraryResponse)
async def itinerary(form: TripForm, request: Request) -> ItineraryResponse:
    return await run_itinerary(request.app, form)


@router.post("/chat/{thread_id}/plan", response_model=ChatPlanReply)
async def chat_plan(thread_id: str, form: TripForm, request: Request) -> ChatPlanReply:
    """Bấm "Lập lộ trình" trong form của trang chat (`form.thread_id` là mã lượt lập lộ trình).

    Nối form và kết quả dạng chữ vào hội thoại `thread_id` — lộ trình, hoặc câu hỏi lại khi còn
    thiếu ý (need_info) — để trợ lý nhớ đã lập / đã hỏi gì khi user nói tiếp.
    """
    plan = await run_itinerary(request.app, form)
    asked, answer = request_text(form), itinerary_markdown(plan)
    await request.app.state.graph.aupdate_state(
        {"configurable": {"thread_id": thread_id}},
        {"messages": [HumanMessage(asked), AIMessage(answer)], "open_form": False},
        as_node=REPLY_NODE,
    )
    return ChatPlanReply(plan=plan, request_text=asked, reply=answer)


async def run_itinerary(app: FastAPI, form: TripForm) -> ItineraryResponse:
    thread_id = form.thread_id or f"plan-{uuid.uuid4().hex[:12]}"
    graph = await itinerary_graph(app)
    config = run_config(
        thread_id=thread_id, graph="itinerary", user_id=form.user_id, session_id=thread_id
    )
    # Mã đã dùng cho lần chạy trước (bấm lại, gửi lại): xoá state cũ. Không thì các trường cộng
    # dồn (số lượt LLM, token, nhật ký tool) cộng tiếp, và kết quả cũ (`trip`, `decision`) còn
    # nằm đó — lần chạy mới dừng ở câu hỏi lại vẫn bị trả phương án cũ.
    if (await graph.aget_state(config)).values:
        await graph.checkpointer.adelete_thread(thread_id)
    # Ô user đã điền trên form: bước 2 không ghi đè bằng ý đọc từ lời nhắn.
    sent = sorted(form.model_fields_set - NOT_FORM_FIELDS)
    started = time.monotonic()
    try:
        async with agent_log.turn(thread_id):
            state = await graph.ainvoke(
                {"form": form.model_dump(mode="json"), "sent": sent}, config
            )
    except NoPlacesError as e:  # lỗi của dữ liệu đầu vào: báo 422 kèm lời giải thích
        raise HTTPException(status_code=422, detail=str(e)) from e
    return to_response(state, thread_id, time.monotonic() - started)


async def itinerary_graph(app: FastAPI) -> CompiledStateGraph:
    """Dựng graph ở request đầu tiên: cần nạp tool từ MCP server, server tắt thì báo 503."""
    async with app.state.itinerary_lock:
        if app.state.itinerary_graph is None:
            try:
                tools = await load_mcp_tools()
            except Exception as e:  # noqa: BLE001
                raise HTTPException(
                    status_code=503, detail=f"Không kết nối được MCP server: {type(e).__name__}"
                ) from e
            checkpointer, store = app.state.memory
            app.state.itinerary_graph = build_itinerary_graph(tools).compile(
                checkpointer=checkpointer, store=store
            )
    return app.state.itinerary_graph

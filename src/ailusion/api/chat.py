"""Route chat.

POST /chat                    hỏi, chờ câu trả lời đầy đủ
POST /chat/stream             hỏi, nhận câu trả lời theo từng mẩu (SSE)
GET  /chat/{thread_id}        lịch sử hội thoại
GET  /chat/{thread_id}/trip   form chuyến đi đã lọc từ tin nhắn (Postgres, không gọi LLM)
PUT  /chat/{thread_id}/trip   người dùng xác nhận form trước khi lập lộ trình
POST /trip-info               cả cuộc trò chuyện -> form (hội thoại cũ chưa lưu form)
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from ailusion.chat.graph import stream_reply, trip_event, trip_payload
from ailusion.chat.trip_info import TripInfo, extract_trip_info
from ailusion.chat.trip_store import TripDraft, load_draft, save_draft
from ailusion.core.agent_log import AgentLog
from ailusion.core.llm import MissingApiKeyError, message_text
from ailusion.core.tracing import run_config

from .schemas import (
    ChatReply,
    ChatRequest,
    History,
    Message,
    TripConfirm,
    TripInfoRequest,
    TripState,
)

router = APIRouter(tags=["chat"])
agent_log = AgentLog()
logger = logging.getLogger(__name__)
CHAT_ERROR = "Trợ lý đang gặp sự cố khi trả lời. Bạn thử gửi lại sau ít giây nhé."


@router.post("/chat", response_model=ChatReply)
async def chat(body: ChatRequest, request: Request) -> ChatReply:
    """Hỏi một câu, nhận câu trả lời đầy đủ."""
    thread_id = body.thread_id or _new_thread_id()
    graph = request.app.state.graph
    try:
        async with agent_log.turn(thread_id):
            messages = await _turn_messages(graph, body, thread_id)
            result = await graph.ainvoke({"messages": messages}, _config(body, thread_id))
    except MissingApiKeyError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    draft = TripDraft.model_validate(result.get("trip") or {})
    return ChatReply(
        thread_id=thread_id,
        reply=message_text(result["messages"][-1].content),
        trip=TripState.model_validate(trip_payload(draft, open_form=bool(result.get("open_form")))),
    )


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """Như /chat nhưng trả lời theo từng mẩu, để giao diện hiện chữ dần."""
    thread_id = body.thread_id or _new_thread_id()
    graph = request.app.state.graph

    async def events() -> AsyncIterator[str]:
        yield _sse({"type": "start", "thread_id": thread_id})
        try:
            async with agent_log.turn(thread_id):
                config = _config(body, thread_id)
                messages = await _turn_messages(graph, body, thread_id)
                async for text in stream_reply(graph, messages, config):
                    yield _sse({"type": "delta", "text": text})
                # Form chuyến đi sau lượt này: giao diện điền sẵn, và tự mở khi open_form.
                yield _sse({"type": "trip", **await trip_event(graph, config)})
        except MissingApiKeyError as e:
            yield _sse({"type": "error", "message": str(e)})
        except Exception:  # noqa: BLE001 - báo cho người dùng thay vì ngắt stream im lặng
            logger.exception("Lượt chat của %s bị lỗi", thread_id)
            yield _sse({"type": "error", "message": CHAT_ERROR})
        else:
            yield _sse({"type": "done", "thread_id": thread_id})

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/chat/{thread_id}", response_model=History)
async def history(thread_id: str, request: Request) -> History:
    """Lịch sử hội thoại đang nằm trong checkpointer."""
    state = await request.app.state.graph.aget_state({"configurable": {"thread_id": thread_id}})
    if not state.values:
        raise HTTPException(status_code=404, detail=f"Không có hội thoại {thread_id}")
    return History(
        thread_id=thread_id,
        messages=[
            Message(role=m.type, content=message_text(m.content)) for m in state.values["messages"]
        ],
    )


@router.get("/chat/{thread_id}/trip", response_model=TripState)
async def get_trip(thread_id: str, request: Request, user_id: str | None = None) -> TripState:
    """Form chuyến đi đã lọc từ tin nhắn của hội thoại. Đọc Postgres, không gọi LLM."""
    _, store = request.app.state.memory
    fallback = None
    try:
        snapshot = await request.app.state.graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        fallback = (snapshot.values or {}).get("trip")
    except Exception:  # noqa: BLE001 - Redis trục trặc: vẫn còn bản form trong Postgres
        logger.warning("Không đọc được state chat của %s, chỉ dùng form trong store", thread_id)
    draft = await load_draft(store, thread_id, user_id, fallback=fallback)
    return TripState.model_validate(trip_payload(draft))


@router.put("/chat/{thread_id}/trip", response_model=TripState)
async def confirm_trip(thread_id: str, body: TripConfirm, request: Request) -> TripState:
    """Người dùng xác nhận form: lượt chat sau và hồ sơ người dùng dùng đúng bản này."""
    _, store = request.app.state.memory
    draft = TripDraft(info=body.info.cleaned(), confirmed=True)
    await save_draft(store, thread_id, draft, body.user_id, replace_access=True)
    return TripState.model_validate(trip_payload(draft))


@router.post("/trip-info", response_model=TripInfo)
async def trip_info(body: TripInfoRequest) -> TripInfo:
    """Đọc cuộc trò chuyện, trả về điểm đến, số ngày, số người... đã nhắc tới (trống = chưa nói)."""
    try:
        return await extract_trip_info([(m.role, m.content) for m in body.messages])
    except MissingApiKeyError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


# ---------------------------------------------------------------- tiện ích


async def _turn_messages(graph: Any, body: ChatRequest, thread_id: str) -> list[AnyMessage]:
    """Tin nhắn đưa vào graph ở lượt này: câu hỏi, cộng lịch sử nếu bộ nhớ đã hết hạn."""
    has_memory = False
    if body.history:
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        has_memory = bool(state.values and state.values.get("messages"))
    return messages_for_turn(body.message, body.history, has_memory=has_memory)


def messages_for_turn(
    message: str, history: list[Message], *, has_memory: bool
) -> list[AnyMessage]:
    """Checkpointer còn giữ hội thoại thì chỉ gửi câu hỏi mới (graph tự nối vào lịch sử).

    Đã hết hạn thì nạp lại lịch sử backend gửi kèm trước câu hỏi, để trợ lý nhớ đã nói gì.
    """
    question: AnyMessage = HumanMessage(message)
    if has_memory or not history:
        return [question]
    replayed: list[AnyMessage] = [
        HumanMessage(m.content) if m.role == "human" else AIMessage(m.content)
        for m in history
        if m.role in ("human", "ai") and m.content
    ]
    return [*replayed, question]


def _config(body: ChatRequest, thread_id: str) -> RunnableConfig:
    return run_config(
        thread_id=thread_id,
        graph="chat",
        user_id=body.user_id,
        session_id=body.session_id or thread_id,
    )


def _new_thread_id() -> str:
    return f"chat-{uuid.uuid4().hex[:12]}"


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

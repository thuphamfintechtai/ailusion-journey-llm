"""HTTP API — chỗ backend gọi vào service này.

    POST /chat                    hỏi, chờ câu trả lời đầy đủ
    POST /chat/stream             hỏi, nhận câu trả lời theo từng mẩu (SSE)
    GET  /chat/{thread_id}        lịch sử hội thoại
    GET  /chat/{thread_id}/events nhật ký agent theo thời gian thực (SSE)
    GET  /health                  kiểm tra service sống

Chạy: `make api` (hoặc `uv run uvicorn ailusion.api:app --reload --port 8001`).
Tài liệu tự sinh: http://localhost:8001/docs
Cổng 8001 để chừa 8000 cho backend (repo ailusion-journey-be).

Checkpointer / store / graph được mở **một lần** lúc khởi động (xem `lifespan`) rồi dùng
chung cho mọi request — không mở lại kết nối Redis/Postgres theo từng lượt gọi.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage

from ailusion import close_redis, memory_backends, run_config, setup_observability
from ailusion.graphs import build_chat_graph
from ailusion.llm import MissingApiKeyError, message_text
from ailusion.observability import shutdown_observability
from ailusion.runtime import AgentLog
from ailusion.runtime.agent_log import FROM_BEGINNING

from .schemas import ChatReply, ChatRequest, History, Message

GRAPH_NAME = "chat"
agent_log = AgentLog()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Mở hạ tầng khi service khởi động, đóng gọn khi tắt."""
    setup_observability()
    async with memory_backends() as (checkpointer, store):
        app.state.graph = build_chat_graph().compile(checkpointer=checkpointer, store=store)
        yield
    await close_redis()
    shutdown_observability()


app = FastAPI(
    title="Ailusion Journey LLM",
    description="Agent lập kế hoạch du lịch chạy trên LangGraph + Gemini.",
    version="0.1.0",
    lifespan=lifespan,
)

# Backend / frontend ở origin khác gọi được. Siết lại danh sách khi lên production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatReply)
async def chat(request: ChatRequest, http_request: Request) -> ChatReply:
    """Hỏi một câu, nhận câu trả lời đầy đủ."""
    thread_id = request.thread_id or _new_thread_id()
    graph = http_request.app.state.graph
    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(request.message)]}, _config(request, thread_id)
        )
    except MissingApiKeyError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return ChatReply(thread_id=thread_id, reply=message_text(result["messages"][-1].content))


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    """Như /chat nhưng trả lời theo từng mẩu, để giao diện hiện chữ dần."""
    thread_id = request.thread_id or _new_thread_id()
    graph = http_request.app.state.graph

    async def events() -> AsyncIterator[str]:
        yield _sse({"type": "start", "thread_id": thread_id})
        try:
            stream = graph.astream(
                {"messages": [HumanMessage(request.message)]},
                _config(request, thread_id),
                stream_mode="messages",
            )
            async for chunk, _metadata in stream:
                if not isinstance(chunk, AIMessageChunk):
                    continue
                # Gemini chen vào những chunk chỉ có chữ ký suy luận, không có chữ -> bỏ qua.
                text = message_text(chunk.content)
                if text:
                    yield _sse({"type": "delta", "text": text})
        except MissingApiKeyError as e:
            yield _sse({"type": "error", "message": str(e)})
        else:
            yield _sse({"type": "done", "thread_id": thread_id})

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/chat/{thread_id}", response_model=History)
async def history(thread_id: str, http_request: Request) -> History:
    """Lịch sử hội thoại đang nằm trong checkpointer."""
    graph = http_request.app.state.graph
    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    if not state.values:
        raise HTTPException(status_code=404, detail=f"Không có hội thoại {thread_id}")

    return History(
        thread_id=thread_id,
        messages=[
            Message(role=m.type, content=message_text(m.content)) for m in state.values["messages"]
        ],
    )


@app.get("/chat/{thread_id}/events")
async def agent_events(
    thread_id: str, last_event_id: str | None = Header(default=None)
) -> StreamingResponse:
    """Nhật ký agent dạng SSE. Trình duyệt tự gửi `Last-Event-ID` khi nối lại."""

    async def events() -> AsyncIterator[str]:
        async for event_id, event in agent_log.tail(
            thread_id, last_id=last_event_id or FROM_BEGINNING
        ):
            yield f"id: {event_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _config(request: ChatRequest, thread_id: str) -> Any:
    return run_config(
        thread_id=thread_id,
        graph=GRAPH_NAME,
        user_id=request.user_id,
        session_id=request.session_id or thread_id,
    )


def _new_thread_id() -> str:
    return f"chat-{uuid.uuid4().hex[:12]}"


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

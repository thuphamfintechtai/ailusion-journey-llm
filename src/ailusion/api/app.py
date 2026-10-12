"""HTTP API — chỗ backend gọi vào service này.

    chat.py       POST /chat · POST /chat/stream · GET /chat/{id} · GET|PUT /chat/{id}/trip
                  POST /trip-info
    itinerary.py  POST /itinerary · POST /chat/{id}/plan
    app.py        GET /health · GET /chat/{id}/events (nhật ký agent, SSE)

Chạy: `make api` (hoặc `uv run uvicorn ailusion.api:app --reload --port 8001`).
Tài liệu tự sinh: http://localhost:8001/docs — cổng 8001 để chừa 8000 cho backend.

Checkpointer / store / graph chat mở **một lần** lúc khởi động (`lifespan`) rồi dùng chung cho
mọi request. Graph lập lộ trình dựng ở request đầu tiên (cần nạp tool từ MCP server).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ailusion.chat.graph import build_chat_graph
from ailusion.core.agent_log import AgentLog
from ailusion.core.memory import memory_backends
from ailusion.core.redis import close_redis
from ailusion.core.tracing import setup_observability, shutdown_observability

from . import chat, itinerary

agent_log = AgentLog()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Mở hạ tầng khi service khởi động, đóng gọn khi tắt."""
    setup_observability()
    async with memory_backends() as (checkpointer, store):
        app.state.graph = build_chat_graph().compile(checkpointer=checkpointer, store=store)
        app.state.memory = (checkpointer, store)
        app.state.itinerary_graph = None
        app.state.itinerary_lock = asyncio.Lock()
        yield
    await close_redis()
    shutdown_observability()


app = FastAPI(
    title="Ailusion Journey LLM",
    description="Trợ lý du lịch: chat + pipeline lập lộ trình, chạy trên LangGraph.",
    version="0.2.0",
    lifespan=lifespan,
)

# Backend / frontend ở origin khác gọi được. Siết lại danh sách khi lên production.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(chat.router)
app.include_router(itinerary.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/chat/{thread_id}/events")
async def agent_events(
    thread_id: str, last_event_id: str | None = Header(default=None)
) -> StreamingResponse:
    """Nhật ký agent của một lượt (chat hoặc lập lộ trình), dạng SSE; đóng sau done/error.

    Không có `Last-Event-ID` thì phát từ đầu lượt gần nhất. Trình duyệt tự nối lại với
    `Last-Event-ID` sau khi stream đóng, nên sẽ nhận tiếp lượt kế theo.
    """

    async def events() -> AsyncIterator[str]:
        async for event_id, event in agent_log.tail(thread_id, last_id=last_event_id):
            yield f"id: {event_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")

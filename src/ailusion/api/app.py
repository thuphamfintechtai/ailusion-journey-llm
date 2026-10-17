"""HTTP API — chỗ backend gọi vào service này.

    chat.py       POST /chat · POST /chat/stream · GET /chat/{id} · GET|PUT /chat/{id}/trip
                  POST /trip-info
    itinerary.py  POST /itinerary · POST /chat/{id}/plan
    places.py     GET /places/{place_id} (chi tiết + review khi bấm thẻ địa điểm)
    app.py        GET /health · GET /chat/{id}/events (nhật ký agent, SSE)

Chạy: `make api` (hoặc `uv run uvicorn ailusion.api:app --reload --port 8001`).
Tài liệu tự sinh: http://localhost:8001/docs — cổng 8001 để chừa 8000 cho backend.

Checkpointer / store / graph chat mở **một lần** lúc khởi động (`lifespan`) rồi dùng chung cho
mọi request. Graph lập lộ trình dựng ở request đầu tiên (cần nạp tool từ MCP server).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from ailusion.chat.graph import build_chat_graph
from ailusion.core.agent_log import AgentLog
from ailusion.core.memory import memory_backends
from ailusion.core.redis import close_redis
from ailusion.core.settings import get_settings
from ailusion.core.tracing import setup_observability, shutdown_observability

from . import chat, itinerary, places

agent_log = AgentLog()
logger = logging.getLogger(__name__)


async def require_service_key(x_service_key: str | None = Header(default=None)) -> None:
    """Chỉ backend được gọi: header X-Service-Key phải khớp SERVICE_API_KEY.

    Service này không có đăng nhập người dùng — backend lo việc đó. Không có khoá thì ai biết
    địa chỉ cũng chat / lập lộ trình được (bỏ qua đăng nhập, tốn tiền LLM).
    """
    key = get_settings().service_api_key
    if key is None:
        return
    if not x_service_key or not hmac.compare_digest(x_service_key, key.get_secret_value()):
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai X-Service-Key")


SERVICE_ONLY = [Depends(require_service_key)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Mở hạ tầng khi service khởi động, đóng gọn khi tắt."""
    setup_observability()
    if get_settings().service_api_key is None:
        logger.warning("SERVICE_API_KEY trống: mọi người gọi được service này mà không cần khoá")
    async with memory_backends() as (checkpointer, store):
        app.state.graph = build_chat_graph().compile(checkpointer=checkpointer, store=store)
        app.state.memory = (checkpointer, store)
        app.state.itinerary_graph = None
        app.state.itinerary_lock = asyncio.Lock()
        app.state.mcp_tools = None
        app.state.mcp_lock = asyncio.Lock()
        yield
    await close_redis()
    shutdown_observability()


app = FastAPI(
    title="Ailusion Journey LLM",
    description="Trợ lý du lịch: chat + pipeline lập lộ trình, chạy trên LangGraph.",
    version="1.1.0",
    lifespan=lifespan,
)

# Không bật CORS: chỉ backend gọi service này (từ server), trình duyệt không gọi thẳng.
app.include_router(chat.router, dependencies=SERVICE_ONLY)
app.include_router(itinerary.router, dependencies=SERVICE_ONLY)
app.include_router(places.router, dependencies=SERVICE_ONLY)


def form_error_text(errors: list[dict]) -> str:
    """Lỗi kiểm form -> một câu đọc được cho user (BE chuyển nguyên câu này tới web): lỗi luật của
    form ("Giờ thức dậy (21:00) phải trước giờ về chỗ ở buổi tối (20:45)") bỏ tiền tố "Value
    error, " của Pydantic; lỗi kiểu dữ liệu ghi kèm tên trường."""
    out = []
    for e in errors:
        msg = str(e.get("msg", "giá trị không hợp lệ"))
        if e.get("type") == "value_error":
            out.append(msg.removeprefix("Value error, "))
            continue
        where = ".".join(str(x) for x in e.get("loc", [])[1:])
        out.append(f"{where}: {msg}" if where else msg)
    return "; ".join(out) or "Form lập lộ trình không hợp lệ"


@app.exception_handler(RequestValidationError)
async def form_errors(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": form_error_text(exc.errors())})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/chat/{thread_id}/events", dependencies=SERVICE_ONLY)
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

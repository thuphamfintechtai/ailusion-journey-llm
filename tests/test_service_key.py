"""Chỉ backend gọi được service: mọi route trừ /health cần header X-Service-Key."""

from __future__ import annotations

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from ailusion.api.app import app
from ailusion.chat import graph as chat_graph
from ailusion.core.settings import get_settings


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "service_api_key", SecretStr("khoa-test"))
    app.state.graph = chat_graph.build_chat_graph().compile(checkpointer=InMemorySaver())


async def _get(path: str, key: str | None = None) -> int:
    headers = {"X-Service-Key": key} if key else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://llm.test") as client:
        return (await client.get(path, headers=headers)).status_code


async def test_requests_without_or_with_wrong_key_are_refused(keyed: None) -> None:
    assert await _get("/chat/chat-x") == 401
    assert await _get("/chat/chat-x", "sai") == 401
    # Đúng khoá thì qua lớp kiểm (hội thoại không tồn tại -> 404, không phải 401).
    assert await _get("/chat/chat-x", "khoa-test") == 404


async def test_health_needs_no_key(keyed: None) -> None:
    assert await _get("/health") == 200

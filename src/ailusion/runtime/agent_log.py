"""Kênh stream nhật ký agent — để web app xem agent "suy nghĩ" theo thời gian thực.

Mỗi thread có một Redis Stream `agentlog:<thread_id>`. Node trong graph `publish()` sự kiện;
endpoint SSE của web app `tail()` và gửi `id` làm `Last-Event-ID` để client nối lại không
mất sự kiện. Stream hết hạn cùng lúc với checkpoint và bị cắt ở `AGENT_LOG_MAXLEN` bản ghi.

    agent_log = AgentLog()
    await agent_log.publish(thread_id, node="dieu_phoi", kind="plan", text="Chia 5 nhánh...")

    async for event_id, event in agent_log.tail(thread_id, last_id=request_last_event_id):
        yield f"id: {event_id}\\ndata: {json.dumps(event)}\\n\\n"
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from ailusion.config import get_settings
from ailusion.redis import get_redis, keys

# Sự kiện mang một trong các `kind` này thì coi như thread đã chạy xong.
TERMINAL_KINDS = ("done", "error")

# Giá trị `last_id` nghĩa là "phát lại từ sự kiện đầu tiên".
FROM_BEGINNING = "0-0"


class AgentLog:
    """Đọc/ghi stream nhật ký của một thread. Dùng client Redis chung nếu không truyền vào."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    @property
    def redis(self) -> Redis:
        return self._redis or get_redis()

    async def publish(
        self, thread_id: str, *, node: str, kind: str, text: str = "", **data: Any
    ) -> str:
        """Ghi một sự kiện, trả về id của nó.

        kind gợi ý: start | plan | tool_call | tool_result | finding | replan | done | error.
        Mọi tham số thừa (`**data`) được gói thành JSON trong trường `data`.
        """
        s = get_settings()
        key = keys.agent_log(thread_id)
        fields = {
            "ts": f"{time.time():.3f}",
            "node": node,
            "kind": kind,
            "text": text,
            "data": json.dumps(data, ensure_ascii=False, default=str),
        }
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.xadd(key, fields, maxlen=s.redis.agent_log_maxlen, approximate=True)
            pipe.expire(key, s.redis.checkpoint_ttl_seconds)
            event_id, _ = await pipe.execute()
        return event_id

    async def read(
        self,
        thread_id: str,
        last_id: str = FROM_BEGINNING,
        count: int = 200,
        block_ms: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Đọc một lô sự kiện sau `last_id`. `block_ms` > 0 thì chờ tới khi có sự kiện mới."""
        response = await self.redis.xread(
            {keys.agent_log(thread_id): last_id}, count=count, block=block_ms
        )
        events: list[tuple[str, dict[str, Any]]] = []
        for _stream, entries in response or []:
            for event_id, raw_fields in entries:
                events.append((event_id, _parse_event(raw_fields)))
        return events

    async def tail(
        self, thread_id: str, last_id: str = FROM_BEGINNING, block_ms: int = 15_000
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Phát lại từ `last_id` rồi chờ sự kiện mới; dừng sau sự kiện kind=done|error."""
        while True:
            batch = await self.read(thread_id, last_id=last_id, block_ms=block_ms)
            for event_id, event in batch:
                last_id = event_id
                yield event_id, event
                if event.get("kind") in TERMINAL_KINDS:
                    return


def _parse_event(raw_fields: dict[str, Any]) -> dict[str, Any]:
    """Redis trả về toàn chuỗi; mở lại trường `data` thành dict."""
    event = dict(raw_fields)
    event["data"] = json.loads(event.get("data") or "{}")
    return event

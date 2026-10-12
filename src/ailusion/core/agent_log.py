"""Kênh stream nhật ký agent — để web app xem agent "suy nghĩ" theo thời gian thực.

Mỗi thread có một Redis Stream `agentlog:<thread_id>`, dùng chung cho mọi lượt hỏi của thread.
Mỗi lượt được bọc trong `turn()`: sự kiện `start` mở lượt, `done` hoặc `error` đóng lượt.
Node trong graph `publish()` sự kiện của riêng nó ở giữa. Endpoint SSE `tail()` một lượt và
gửi `id` làm `Last-Event-ID` để client nối lại không mất sự kiện. Stream hết hạn cùng lúc với
checkpoint và bị cắt ở `AGENT_LOG_MAXLEN` bản ghi.

    agent_log = AgentLog()
    async with agent_log.turn(thread_id):
        await graph.ainvoke(...)          # node: agent_log.publish(thread_id, node=..., kind=...)

    async for event_id, event in agent_log.tail(thread_id, last_id=request_last_event_id):
        yield f"id: {event_id}\\ndata: {json.dumps(event)}\\n\\n"
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from redis.asyncio import Redis

from .redis import agent_log_key, get_redis
from .settings import get_settings

# Sự kiện mở/đóng lượt mang `node` này; chỉ `turn()` được ghi chúng.
TURN_NODE = "turn"
TURN_START = "start"
# Sự kiện lượt mang một trong các `kind` này thì coi như lượt đã chạy xong.
TERMINAL_KINDS = ("done", "error")

# Giá trị `last_id` nghĩa là "phát lại từ sự kiện đầu tiên".
FROM_BEGINNING = "0-0"

# Phần sequence lớn nhất của một stream id (số nguyên 64 bit không dấu).
_MAX_SEQ = 2**64 - 1


# Mỗi lần XREAD chờ tối đa bao lâu. Phải ngắn hơn socket timeout của Redis client
# (redis.py · SOCKET_TIMEOUT_SECONDS): chờ lâu hơn thì client ngắt lệnh với
# TimeoutError dù Redis vẫn bình thường — một bước pipeline dài là đủ làm chết stream /events.
TAIL_BLOCK_MS = 5_000


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

        kind gợi ý cho node: step | answer | plan | tool_call | tool_result | finding | replan.
        start / done / error của node "turn" dành riêng cho `turn()`.
        Mọi tham số thừa (`**data`) được gói thành JSON trong trường `data`.
        """
        s = get_settings()
        key = agent_log_key(thread_id)
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

    @asynccontextmanager
    async def turn(self, thread_id: str) -> AsyncIterator[None]:
        """Bọc một lượt chạy graph: ghi `start` khi vào, `done` khi xong, `error` khi lỗi.

        Lỗi gồm cả trường hợp lượt bị huỷ giữa chừng (client ngắt SSE), nên người đang `tail()`
        luôn nhận được sự kiện kết thúc.
        """
        await self.publish(thread_id, node=TURN_NODE, kind=TURN_START)
        try:
            yield
        except BaseException as exc:
            # Lượt bị huỷ thì mọi `await` trong scope đang huỷ cũng bị huỷ theo; che lại để
            # sự kiện error vẫn ghi được trước khi ném tiếp.
            with anyio.CancelScope(shield=True):
                await self.publish(
                    thread_id,
                    node=TURN_NODE,
                    kind="error",
                    text=_error_text(exc),
                    error=type(exc).__name__,
                )
            raise
        await self.publish(thread_id, node=TURN_NODE, kind="done")

    async def read(
        self,
        thread_id: str,
        last_id: str = FROM_BEGINNING,
        count: int = 200,
        block_ms: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Đọc một lô sự kiện sau `last_id`. `block_ms` > 0 thì chờ tới khi có sự kiện mới."""
        response = await self.redis.xread(
            {agent_log_key(thread_id): last_id}, count=count, block=block_ms
        )
        events: list[tuple[str, dict[str, Any]]] = []
        for _stream, entries in response or []:
            for event_id, raw_fields in entries:
                events.append((event_id, _parse_event(raw_fields)))
        return events

    async def tail(
        self, thread_id: str, last_id: str | None = None, block_ms: int = TAIL_BLOCK_MS
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Phát lại từ sau `last_id` rồi chờ sự kiện mới; dừng khi một lượt kết thúc.

        `last_id=None` (client chưa có `Last-Event-ID`): bắt đầu từ `start` của lượt gần nhất,
        để thread nhiều lượt không bị dừng ở `done` của lượt đầu tiên. Sau khi stream đóng,
        EventSource tự nối lại với id cuối cùng và chờ lượt tiếp theo.
        """
        if last_id is None:
            last_id = await self._before_latest_turn(thread_id)
        while True:
            batch = await self.read(thread_id, last_id=last_id, block_ms=block_ms)
            for event_id, event in batch:
                last_id = event_id
                yield event_id, event
                if event.get("node") == TURN_NODE and event.get("kind") in TERMINAL_KINDS:
                    return

    async def latest_turn(self, thread_id: str) -> list[tuple[str, dict[str, Any]]]:
        """Mọi sự kiện của lượt gần nhất (đã chạy xong), từ `start` tới `done`/`error`."""
        last_id = await self._before_latest_turn(thread_id)
        events: list[tuple[str, dict[str, Any]]] = []
        while batch := await self.read(thread_id, last_id=last_id):
            for event_id, event in batch:
                last_id = event_id
                events.append((event_id, event))
                if event.get("node") == TURN_NODE and event.get("kind") in TERMINAL_KINDS:
                    return events
        return events

    async def _before_latest_turn(self, thread_id: str, page: int = 100) -> str:
        """Id ngay trước sự kiện `start` mới nhất (XREAD chỉ trả sự kiện *sau* id truyền vào)."""
        key = agent_log_key(thread_id)
        upper = "+"
        while True:
            batch = await self.redis.xrevrange(key, max=upper, min="-", count=page)
            if not batch:
                return FROM_BEGINNING
            for event_id, fields in batch:
                if fields.get("node") == TURN_NODE and fields.get("kind") == TURN_START:
                    return _previous_id(event_id)
            upper = f"({batch[-1][0]}"


def _parse_event(raw_fields: dict[str, Any]) -> dict[str, Any]:
    """Redis trả về toàn chuỗi; mở lại trường `data` thành dict."""
    event = dict(raw_fields)
    event["data"] = json.loads(event.get("data") or "{}")
    return event


def _previous_id(event_id: str) -> str:
    """Stream id liền trước: "1700-3" -> "1700-2", "1700-0" -> "1699-<max>"."""
    ms, seq = (int(part) for part in event_id.split("-"))
    if seq > 0:
        return f"{ms}-{seq - 1}"
    if ms > 0:
        return f"{ms - 1}-{_MAX_SEQ}"
    return FROM_BEGINNING


def _error_text(exc: BaseException) -> str:
    """Câu hiện cho người dùng; không đưa chi tiết lỗi nội bộ ra giao diện."""
    if isinstance(exc, (anyio.get_cancelled_exc_class(), GeneratorExit)):
        return "Lượt chạy bị huỷ"
    return "Có lỗi khi soạn câu trả lời"

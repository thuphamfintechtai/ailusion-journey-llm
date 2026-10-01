"""Một async Redis client dùng chung cho cache tool và stream nhật ký agent.

Dùng chung một connection pool cho cả tiến trình; `close_redis()` khi script/worker kết thúc.
(Checkpointer của LangGraph tự mở kết nối riêng — xem `ailusion.memory.checkpointer`.)
"""

from __future__ import annotations

# `ailusion.redis` không che khuất thư viện `redis`: Python 3 luôn import tuyệt đối.
from redis.asyncio import Redis

from ailusion.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Client dùng chung, tạo ở lần gọi đầu tiên."""
    global _client
    if _client is None:
        _client = Redis.from_url(get_settings().redis.url, decode_responses=True)
    return _client


async def close_redis() -> None:
    """Đóng connection pool; lần `get_redis()` sau sẽ tạo client mới."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

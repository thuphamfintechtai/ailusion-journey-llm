"""Bộ nhớ ngắn hạn — checkpointer Redis cho LangGraph.

Giữ trạng thái graph giữa các node và giữa các lượt invoke, nhờ đó có time-travel:
quay lại một checkpoint cũ, đổi ràng buộc rồi chạy tiếp (chuỗi what-if).

Có TTL nên hết phiên là tự dọn — cái gì đáng giữ lâu thì "cổng lọc" thăng cấp sang
bộ nhớ dài hạn (`store.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from ailusion.config import Settings, get_settings


@asynccontextmanager
async def short_term_checkpointer(
    settings: Settings | None = None,
) -> AsyncIterator[AsyncRedisSaver]:
    """Mở checkpointer Redis đã `asetup()` sẵn (tạo index cần thiết)."""
    s = settings or get_settings()
    # refresh_on_read: mỗi lần đọc lại gia hạn TTL, phiên đang dùng không bị hết hạn giữa chừng.
    ttl = {"default_ttl": s.redis.checkpoint_ttl_minutes, "refresh_on_read": True}
    async with AsyncRedisSaver.from_conn_string(s.redis.url, ttl=ttl) as saver:
        await saver.asetup()
        yield saver

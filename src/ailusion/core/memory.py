"""Hai tầng bộ nhớ của LangGraph.

- Ngắn hạn: checkpointer Redis — trạng thái graph giữa các node / các lượt chat, có TTL.
- Dài hạn: store Postgres — thứ sống lâu hơn một phiên (form chuyến đi đang bàn, hồ sơ user).
  Có khoá embedding thì thêm vector index; không có vẫn chạy, chỉ tra theo namespace/key.

    async with memory_backends() as (checkpointer, store):
        graph = builder.compile(checkpointer=checkpointer, store=store)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from .settings import Settings, get_settings


def profile_namespace(user_id: str) -> tuple[str, ...]:
    """Hồ sơ người dùng (key "main"): yêu cầu bắt buộc nhớ qua nhiều hội thoại."""
    return ("users", user_id, "profile")


def trip_namespace(thread_id: str) -> tuple[str, ...]:
    """Form chuyến đi đang bàn trong một hội thoại chat (key "draft"), lọc từ tin nhắn."""
    return ("chats", thread_id, "trip")


@asynccontextmanager
async def memory_backends(
    settings: Settings | None = None,
) -> AsyncIterator[tuple[AsyncRedisSaver, AsyncPostgresStore]]:
    """Mở cùng lúc checkpointer ngắn hạn và store dài hạn."""
    async with (
        short_term_checkpointer(settings) as checkpointer,
        long_term_store(settings) as store,
    ):
        yield checkpointer, store


@asynccontextmanager
async def short_term_checkpointer(
    settings: Settings | None = None,
) -> AsyncIterator[AsyncRedisSaver]:
    """Checkpointer Redis đã `asetup()` sẵn (tạo index cần thiết)."""
    s = settings or get_settings()
    # refresh_on_read: mỗi lần đọc lại gia hạn TTL, phiên đang dùng không bị hết hạn giữa chừng.
    ttl = {"default_ttl": s.redis.checkpoint_ttl_minutes, "refresh_on_read": True}
    async with AsyncRedisSaver.from_conn_string(s.redis.url, ttl=ttl) as saver:
        await saver.asetup()
        yield saver


@asynccontextmanager
async def long_term_store(settings: Settings | None = None) -> AsyncIterator[AsyncPostgresStore]:
    """Mở store Postgres đã `setup()` sẵn (tạo bảng + index)."""
    s = settings or get_settings()
    async with AsyncPostgresStore.from_conn_string(
        s.postgres.url, index=_index_config(s), pool_config=POOL_CONFIG
    ) as store:
        await store.setup()
        yield store


# Postgres nằm trên máy chủ từ xa. Một kết nối duy nhất (mặc định của LangGraph) không có
# timeout: đổi mạng (wifi -> hotspot) hay NAT cắt kết nối đang rảnh là kết nối "chết im
# lặng", mọi lệnh sau treo vô hạn. Dùng pool để:
# - `check`: thử kết nối trước khi cho mượn, hỏng thì bỏ và mở cái mới;
# - `max_idle`: đóng kết nối rảnh lâu trước khi NAT kịp cắt;
# - keepalive TCP + `connect_timeout` + `statement_timeout`: không lệnh nào chờ quá vài giây.
POOL_CONFIG: Any = {
    "min_size": 1,
    "max_size": 5,
    "check": AsyncConnectionPool.check_connection,
    "max_idle": 120,
    "timeout": 10,
    "kwargs": {
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "options": "-c statement_timeout=10000",
    },
}


def _index_config(s: Settings) -> dict[str, Any] | None:
    """Cấu hình vector index, hoặc None nếu chưa bật embedding."""
    if not s.embeddings_enabled:
        return None
    # "provider:model" được LangGraph phân giải qua langchain.embeddings.init_embeddings.
    # "fields" mặc định = ["$"] (embed cả value); chỉ định field cụ thể khi put() để tiết kiệm.
    return {"dims": s.postgres.embedding_dims, "embed": s.postgres.embedding_model}

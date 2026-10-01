"""Hai tầng bộ nhớ của LangGraph: ngắn hạn (Redis) và dài hạn (Postgres).

Thường thì mở cả hai một lượt rồi compile graph:

    async with memory_backends() as (checkpointer, store):
        graph = builder.compile(checkpointer=checkpointer, store=store)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from ailusion.config import Settings

from .checkpointer import short_term_checkpointer
from .namespaces import Namespaces
from .store import long_term_store

__all__ = [
    "Namespaces",
    "long_term_store",
    "memory_backends",
    "short_term_checkpointer",
]


@asynccontextmanager
async def memory_backends(
    settings: Settings | None = None,
) -> AsyncIterator[tuple[AsyncRedisSaver, AsyncPostgresStore]]:
    """Mở cùng lúc checkpointer ngắn hạn và store dài hạn."""
    async with short_term_checkpointer(settings) as checkpointer, long_term_store(
        settings
    ) as store:
        yield checkpointer, store

"""Bộ nhớ dài hạn — store Postgres (+ pgvector) cho LangGraph.

Giữ thứ sống lâu hơn một phiên: hồ sơ người dùng, kho tri thức theo vùng, hiệu chỉnh
sai số của từng tool, thói quen. Khoá tra cứu theo namespace — quy ước ở `namespaces.py`.

Có khoá embedding thì store thêm vector index để tìm kiếm ngữ nghĩa; không có thì vẫn
chạy bình thường, chỉ tra được theo namespace/key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.store.postgres.aio import AsyncPostgresStore

from ailusion.config import Settings, get_settings


@asynccontextmanager
async def long_term_store(settings: Settings | None = None) -> AsyncIterator[AsyncPostgresStore]:
    """Mở store Postgres đã `setup()` sẵn (tạo bảng + index)."""
    s = settings or get_settings()
    async with AsyncPostgresStore.from_conn_string(s.postgres.url, index=_index_config(s)) as store:
        await store.setup()
        yield store


def _index_config(s: Settings) -> dict[str, Any] | None:
    """Cấu hình vector index, hoặc None nếu chưa bật embedding."""
    if not s.embeddings_enabled:
        return None
    # "provider:model" được LangGraph phân giải qua langchain.embeddings.init_embeddings.
    # "fields" mặc định = ["$"] (embed cả value); chỉ định field cụ thể khi put() để tiết kiệm.
    return {"dims": s.postgres.embedding_dims, "embed": s.postgres.embedding_model}

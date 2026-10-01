"""Dựng `RunnableConfig` cho mỗi lượt chạy graph.

Một chỗ duy nhất quyết định: thread nào giữ checkpoint, trace gắn tag/session/user gì,
callback nào được cắm vào. Nhờ vậy mọi lượt chạy Graph A / Graph B / What-if đều hiện
lên LangSmith và Langfuse theo cùng một quy ước.

    cfg = run_config(thread_id=f"{trip_id}:A", session_id=trip_id, user_id=u, graph="research")
    await graph_a.ainvoke(state, cfg)
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from ailusion.observability import langfuse_callbacks


def run_config(
    *,
    thread_id: str,
    graph: str,
    user_id: str | None = None,
    session_id: str | None = None,
    checkpoint_ns: str | None = None,
    checkpoint_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    recursion_limit: int = 50,
) -> RunnableConfig:
    """Config chung cho Graph A / Graph B / What-if.

    - thread_id: khoá checkpoint trong Redis (một chuyến đi / một phiên lập kế hoạch).
    - graph: "research" | "decision" | "what_if" -> thành tag + run_name để lọc trace.
    - session_id: gom nhiều lượt (Graph A -> B -> what-if...) thành một session trên
      Langfuse; mặc định = thread_id.
    - checkpoint_id: chạy lại từ một checkpoint cụ thể (vd. gói bối cảnh đã đóng băng).
    """
    all_tags = [f"graph:{graph}", *(tags or [])]
    return RunnableConfig(
        configurable=_configurable(thread_id, user_id, checkpoint_ns, checkpoint_id),
        callbacks=langfuse_callbacks(),
        metadata=_metadata(thread_id, graph, user_id, session_id, all_tags, metadata),
        tags=all_tags,
        run_name=f"graph_{graph}",
        recursion_limit=recursion_limit,
    )


def _configurable(
    thread_id: str,
    user_id: str | None,
    checkpoint_ns: str | None,
    checkpoint_id: str | None,
) -> dict[str, Any]:
    """Phần LangGraph đọc: chọn thread và (nếu có) checkpoint để chạy lại từ đó."""
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if checkpoint_ns is not None:
        configurable["checkpoint_ns"] = checkpoint_ns
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    if user_id:
        configurable["user_id"] = user_id
    return configurable


def _metadata(
    thread_id: str,
    graph: str,
    user_id: str | None,
    session_id: str | None,
    tags: list[str],
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    """Phần tracer đọc. Khoá `langfuse_*` là quy ước Langfuse dùng để gom session/user."""
    metadata: dict[str, Any] = {
        "thread_id": thread_id,
        "graph": graph,
        "langfuse_session_id": session_id or thread_id,
        "langfuse_tags": tags,
        **(extra or {}),
    }
    if user_id:
        metadata["user_id"] = user_id
        metadata["langfuse_user_id"] = user_id
    return metadata

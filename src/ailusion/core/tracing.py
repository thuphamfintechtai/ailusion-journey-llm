"""Bật LangSmith + Langfuse (Cloud) cho mọi lượt chạy graph, và dựng config mỗi lượt chạy.

- LangSmith: cấu hình client toàn cục qua `langsmith.configure`, nên mọi Runnable/LangGraph
  và hàm `@traceable` tự gửi trace — không cần truyền callback.
- Langfuse: dùng `CallbackHandler` của SDK v4 (OTel), phải truyền qua `config["callbacks"]`
  — việc đó do `run_config()` ở cuối file lo.
- Cả hai cùng nhận payload đã che PII (xem `pii.py`).

Vòng đời:

    setup_observability()      # 1 lần khi app khởi động (gọi lại là no-op)
    ...
    shutdown_observability()   # đẩy hết trace còn trong hàng đợi trước khi thoát
"""

from __future__ import annotations

import atexit
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from .pii import langfuse_mask, langsmith_mask
from .settings import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class TracingStatus:
    """Tracer nào thật sự đang chạy (thiếu khoá -> False, không phải lỗi)."""

    langsmith: bool = False
    langfuse: bool = False


@dataclass
class _TracingState:
    """Trạng thái toàn cục của module — gom một chỗ thay vì rải nhiều biến `global`."""

    status: TracingStatus | None = None
    langfuse_client: Any = field(default=None)


_state = _TracingState()


def setup_observability(settings: Settings | None = None) -> TracingStatus:
    """Khởi tạo tracer. An toàn khi gọi nhiều lần (chỉ chạy thật ở lần đầu)."""
    if _state.status is not None:
        return _state.status

    s = settings or get_settings()
    status = TracingStatus(
        langsmith=_setup_langsmith(s),
        langfuse=_setup_langfuse(s),
    )

    atexit.register(shutdown_observability)
    _state.status = status
    log.info("observability: langsmith=%s langfuse=%s", status.langsmith, status.langfuse)
    return status


def langfuse_client() -> Any:
    """Client Langfuse (để chấm điểm, lấy trace URL, ...). None nếu chưa cấu hình."""
    setup_observability()
    return _state.langfuse_client


def langfuse_callbacks() -> list[Any]:
    """Callback Langfuse cần gắn vào mỗi lượt invoke (LangSmith đã tự gắn qua `configure`)."""
    if not setup_observability().langfuse:
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler(public_key=get_settings().langfuse.public_key)]


def shutdown_observability() -> None:
    """Đẩy hết trace còn trong hàng đợi. Gọi khi script/worker kết thúc."""
    if _state.langfuse_client is not None:
        try:
            _state.langfuse_client.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("langfuse shutdown failed")
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception:  # noqa: BLE001
        log.exception("langsmith flush failed")


def _setup_langsmith(s: Settings) -> bool:
    from langsmith import Client
    from langsmith import configure as langsmith_configure

    if not s.langsmith.enabled:
        # Tắt hẳn: LANGSMITH_TRACING=true mà thiếu khoá sẽ không bắn cảnh báo,
        # cũng không gửi đi trace chưa che PII.
        langsmith_configure(enabled=False)
        return False

    mask = langsmith_mask if s.pii_masking else None
    client = Client(
        api_key=s.langsmith.api_key.get_secret_value(),  # type: ignore[union-attr]
        api_url=s.langsmith.endpoint,
        hide_inputs=mask,
        hide_outputs=mask,
        hide_metadata=mask,
    )
    langsmith_configure(client=client, enabled=True, project_name=s.langsmith.project)
    return True


def _setup_langfuse(s: Settings) -> bool:
    if not s.langfuse.enabled:
        return False

    from langfuse import Langfuse

    _state.langfuse_client = Langfuse(
        public_key=s.langfuse.public_key,
        secret_key=s.langfuse.secret_key.get_secret_value(),  # type: ignore[union-attr]
        base_url=s.langfuse.base_url,
        environment=s.langfuse.tracing_environment,
        sample_rate=s.langfuse.sample_rate,
        mask=langfuse_mask if s.pii_masking else None,
    )
    return True


# ---------------------------------------------------------------- config mỗi lượt chạy


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
    run_id: uuid.UUID | None = None,
) -> RunnableConfig:
    """Config một lượt chạy graph: thread giữ checkpoint, tag/session/user của trace.

    - thread_id: khoá checkpoint trong Redis (một hội thoại / một lần lập lộ trình).
    - graph: "chat" | "itinerary" -> thành tag + run_name để lọc trace.
    - session_id: gom nhiều lượt thành một session trên Langfuse; mặc định = thread_id.
    - checkpoint_id: chạy lại từ một checkpoint cụ thể.
    - run_id: mã run gốc trên LangSmith (đặt trước để trả về cho web dựng link tới đúng trace).
    """
    all_tags = [f"graph:{graph}", *(tags or [])]
    return RunnableConfig(
        configurable=_configurable(thread_id, user_id, checkpoint_ns, checkpoint_id),
        callbacks=langfuse_callbacks(),
        metadata=_metadata(thread_id, graph, user_id, session_id, all_tags, metadata),
        tags=all_tags,
        run_name=f"graph_{graph}",
        recursion_limit=recursion_limit,
        **({"run_id": run_id} if run_id else {}),
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

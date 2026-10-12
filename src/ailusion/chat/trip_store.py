"""Form chuyến đi của từng hội thoại chat, lưu trong store Postgres (bộ nhớ dài hạn).

Hai chỗ lưu:

- `trip_namespace(thread_id)` / "draft": form đang bàn trong hội thoại — điểm đến, số ngày,
  số người, ngân sách... Node `loc_tin` cập nhật sau mỗi tin nhắn qua được bộ lọc; trang chat
  đọc lại để điền sẵn form mà không phải gọi LLM đọc lại cả cuộc trò chuyện.
- `profile_namespace(user_id)` / "main", khoá "access": yêu cầu bắt buộc của người dùng
  (dị ứng, ăn chay, xe lăn...). Sang hội thoại mới thì điền sẵn vào form để người dùng xác
  nhận lại — không tự áp dụng khi chưa được xác nhận.

Không lưu được (Postgres trục trặc) thì chat vẫn chạy: form nằm trong state của graph
(checkpointer Redis) cho tới khi hết hạn.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from ailusion.core.memory import profile_namespace, trip_namespace

from .trip_info import REQUIRED, TripInfo, missing_fields

logger = logging.getLogger(__name__)

DRAFT_KEY = "draft"
PROFILE_KEY = "main"
# Postgres ở xa: đọc / ghi form chậm quá mốc này thì bỏ qua (dùng bản trong state của graph),
# không để một lượt chat hay việc mở form phải chờ theo.
STORE_TIMEOUT_SECONDS = 8


class TripDraft(BaseModel):
    """Form chuyến đi của một hội thoại."""

    info: TripInfo = Field(default_factory=TripInfo)
    wants_plan: bool = Field(
        False, description="Người dùng đã muốn lập lộ trình, đang chờ hỏi cho đủ thông tin"
    )
    confirmed: bool = Field(False, description="Người dùng đã xác nhận form (bấm Lập lộ trình)")
    updated_at: str | None = None

    @property
    def missing(self) -> list[str]:
        return missing_fields(self.info)


def missing_labels(missing: list[str]) -> list[dict[str, str]]:
    """["days"] -> [{"field": "days", "label": "số ngày"}], để giao diện hiện chữ."""
    return [{"field": key, "label": REQUIRED[key]} for key in missing]


async def load_draft(
    store: BaseStore | None,
    thread_id: str,
    user_id: str | None = None,
    *,
    fallback: dict[str, Any] | None = None,
) -> TripDraft:
    """Form của hội thoại.

    Thứ tự: bản trong Postgres (mới nhất, kể cả bản người dùng vừa xác nhận) -> `fallback`
    (bản trong state của graph, khi Postgres lỗi) -> form trống kèm yêu cầu bắt buộc đã biết
    của người dùng.
    """
    backup = TripDraft.model_validate(fallback) if fallback else None
    if store is None:
        return backup or TripDraft()
    try:
        async with asyncio.timeout(STORE_TIMEOUT_SECONDS):
            item = await store.aget(trip_namespace(thread_id), DRAFT_KEY)
            if item is not None:
                return TripDraft.model_validate(item.value)
            if backup is not None:
                return backup
            return TripDraft(info=TripInfo(access=await _profile_access(store, user_id)))
    except Exception:  # noqa: BLE001 - mất form không được làm hỏng lượt chat
        logger.exception("Không đọc được form chuyến đi của %s", thread_id)
        return backup or TripDraft()


async def save_draft(
    store: BaseStore | None,
    thread_id: str,
    draft: TripDraft,
    user_id: str | None = None,
    *,
    replace_access: bool = False,
) -> None:
    """Lưu form; yêu cầu bắt buộc thì ghi thêm vào hồ sơ người dùng.

    `replace_access`: người dùng vừa xác nhận form thì hồ sơ lấy đúng danh sách đó (bỏ chọn
    là bỏ); còn đọc từ tin nhắn thì chỉ cộng thêm.
    """
    if store is None:
        return
    draft = draft.model_copy(update={"updated_at": datetime.now(UTC).isoformat()})
    try:
        async with asyncio.timeout(STORE_TIMEOUT_SECONDS):
            # index=False: không cần tìm kiếm ngữ nghĩa trên form, khỏi tốn embedding.
            await store.aput(
                trip_namespace(thread_id), DRAFT_KEY, draft.model_dump(mode="json"), index=False
            )
            if user_id and (draft.info.access or replace_access):
                await _save_profile_access(
                    store, user_id, draft.info.access, replace=replace_access
                )
    except Exception:  # noqa: BLE001
        logger.exception("Không lưu được form chuyến đi của %s", thread_id)


async def _profile_access(store: BaseStore, user_id: str | None) -> list[Any]:
    if not user_id:
        return []
    item = await store.aget(profile_namespace(user_id), PROFILE_KEY)
    access = (item.value if item else {}).get("access") or []
    return [a for a in access if isinstance(a, str)]


async def _save_profile_access(
    store: BaseStore, user_id: str, access: list[str], *, replace: bool
) -> None:
    namespace = profile_namespace(user_id)
    item = await store.aget(namespace, PROFILE_KEY)
    profile = dict(item.value) if item else {}
    old = profile.get("access") or []
    merged = list(access) if replace else list(dict.fromkeys([*old, *access]))
    if merged == old:
        return
    profile["access"] = merged
    await store.aput(namespace, PROFILE_KEY, profile, index=False)

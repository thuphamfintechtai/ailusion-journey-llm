"""Nhật ký agent: ranh giới lượt chạy và cách `tail()` chọn lượt để phát."""

from __future__ import annotations

import asyncio

import pytest
from fakeredis import FakeAsyncRedis

from ailusion.core.agent_log import FROM_BEGINNING, TURN_NODE, AgentLog, _previous_id

THREAD = "chat-test"


@pytest.fixture
def log() -> AgentLog:
    return AgentLog(FakeAsyncRedis(decode_responses=True))


async def _run_turn(log: AgentLog, answer: str) -> None:
    async with log.turn(THREAD):
        await log.publish(THREAD, node="tra_loi", kind="answer", text=answer)


async def _collect(log: AgentLog, last_id: str | None = None) -> list[dict]:
    return [e async for _, e in log.tail(THREAD, last_id=last_id, block_ms=50)]


def _kinds(events: list[dict]) -> list[tuple[str, str]]:
    return [(e["node"], e["kind"]) for e in events]


async def test_turn_wraps_node_events(log: AgentLog):
    await _run_turn(log, "Đà Lạt")
    assert _kinds(await _collect(log)) == [
        (TURN_NODE, "start"),
        ("tra_loi", "answer"),
        (TURN_NODE, "done"),
    ]


async def test_tail_without_last_id_replays_latest_turn_only(log: AgentLog):
    await _run_turn(log, "lượt 1")
    await _run_turn(log, "lượt 2")

    events = await _collect(log)
    assert [e["text"] for e in events if e["node"] == "tra_loi"] == ["lượt 2"]
    assert events[-1]["kind"] == "done"


async def test_tail_follows_running_turn_until_done(log: AgentLog):
    await _run_turn(log, "lượt 1")
    await log.publish(THREAD, node=TURN_NODE, kind="start")
    await log.publish(THREAD, node="tra_loi", kind="step", text="Đang soạn")

    task = asyncio.create_task(_collect(log))
    await asyncio.sleep(0.1)
    assert not task.done()  # lượt 2 chưa xong -> vẫn đang chờ, không dừng ở done của lượt 1

    await log.publish(THREAD, node=TURN_NODE, kind="done")
    events = await asyncio.wait_for(task, timeout=2)
    assert _kinds(events) == [(TURN_NODE, "start"), ("tra_loi", "step"), (TURN_NODE, "done")]


async def test_tail_after_last_event_id_waits_for_next_turn(log: AgentLog):
    await _run_turn(log, "lượt 1")
    done_id = (await log.read(THREAD))[-1][0]
    await _run_turn(log, "lượt 2")

    events = await _collect(log, last_id=done_id)
    assert [e["text"] for e in events if e["node"] == "tra_loi"] == ["lượt 2"]


async def test_node_done_kind_does_not_end_tail(log: AgentLog):
    """Chỉ done/error của lượt mới đóng stream, node tự ghi kind=done thì không."""
    async with log.turn(THREAD):
        await log.publish(THREAD, node="tra_loi", kind="done")
        await log.publish(THREAD, node="tra_loi", kind="answer")
    assert _kinds(await _collect(log))[-1] == (TURN_NODE, "done")


async def test_turn_publishes_error_and_reraises(log: AgentLog):
    with pytest.raises(ValueError):
        async with log.turn(THREAD):
            raise ValueError("Gemini 500: secret detail")

    last = (await _collect(log))[-1]
    assert (last["node"], last["kind"]) == (TURN_NODE, "error")
    assert last["data"] == {"error": "ValueError"}
    assert "secret" not in last["text"]


async def test_cancelled_turn_still_publishes_error(log: AgentLog):
    async def run() -> None:
        async with log.turn(THREAD):
            await asyncio.sleep(10)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    last = (await _collect(log))[-1]
    assert (last["kind"], last["text"]) == ("error", "Lượt chạy bị huỷ")


async def test_stream_without_turn_markers_starts_from_beginning(log: AgentLog):
    await log.publish(THREAD, node="tra_loi", kind="step")
    assert await log._before_latest_turn(THREAD) == FROM_BEGINNING


async def test_latest_turn_found_across_pages(log: AgentLog):
    await log.publish(THREAD, node=TURN_NODE, kind="start")
    for _ in range(5):
        await log.publish(THREAD, node="tra_loi", kind="step")
    first_id = (await log.read(THREAD))[0][0]
    assert await log._before_latest_turn(THREAD, page=2) == _previous_id(first_id)


@pytest.mark.parametrize(
    ("event_id", "expected"),
    [("1700-3", "1700-2"), ("1700-0", f"1699-{2**64 - 1}"), ("0-1", "0-0"), ("0-0", "0-0")],
)
def test_previous_id(event_id: str, expected: str):
    assert _previous_id(event_id) == expected


def test_tail_block_is_shorter_than_the_redis_socket_timeout() -> None:
    """XREAD BLOCK lâu hơn socket timeout thì client tự ngắt, stream /events chết giữa chừng."""
    from ailusion.core.agent_log import TAIL_BLOCK_MS
    from ailusion.core.redis import SOCKET_TIMEOUT_SECONDS

    assert TAIL_BLOCK_MS < SOCKET_TIMEOUT_SECONDS * 1000


async def test_latest_turn_returns_finished_turn_only(log: AgentLog):
    await _run_turn(log, "lượt 1")
    await _run_turn(log, "lượt 2")

    events = [e for _, e in await log.latest_turn(THREAD)]
    assert _kinds(events) == [(TURN_NODE, "start"), ("tra_loi", "answer"), (TURN_NODE, "done")]
    assert events[1]["text"] == "lượt 2"


async def test_ctx_log_failure_does_not_break_the_pipeline(monkeypatch):
    """Redis timeout khi máy bận (4 lộ trình chạy cùng lúc) từng làm nhánh lỗi -> HTTP 500."""
    from ailusion.itinerary.shared import context

    async def broken(*args, **kwargs):
        raise TimeoutError("Timeout reading from localhost:6381")

    monkeypatch.setattr(context.agent_log, "publish", broken)
    ctx = context.Ctx([], {"configurable": {"thread_id": "t"}}, node="kiem_lich")
    await ctx.log("kiem_lich", "finding", "Chạy thử 6 lịch")  # không ném lỗi

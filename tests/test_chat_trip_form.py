"""Graph chat: lọc tin nhắn vào form chuyến đi (Postgres), hỏi cho đủ, tự mở form.

LLM được thay bằng bản giả: không gọi mạng, không tốn quota.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from ailusion.chat import graph as chat_graph
from ailusion.chat.trip_info import TripInfo, TripUpdate, merge, missing_fields
from ailusion.chat.trip_store import TripDraft, load_draft, save_draft
from ailusion.core.memory import profile_namespace, trip_namespace

FULL = TripInfo(destination="Đà Lạt", start_date="2026-12-20", days=3, adults=2, budget_million=8)


# ---------------------------------------------------------------- form


def test_missing_fields_in_asking_order() -> None:
    assert missing_fields(TripInfo()) == [
        "destination",
        "start_date",
        "days",
        "party",
        "budget_million",
    ]
    assert missing_fields(FULL) == []
    # Chỉ cần nhắc tới một nhóm người là đủ mục "số người".
    assert "party" not in missing_fields(TripInfo(seniors=2))


def test_merge_keeps_old_values_the_update_left_empty() -> None:
    old = TripInfo(destination="Đà Lạt", days=3, access=["Ăn chay"])
    new = TripInfo(days=2, budget_million=5)
    merged = merge(old, new)
    assert (merged.destination, merged.days, merged.budget_million) == ("Đà Lạt", 2, 5)
    assert merged.access == ["Ăn chay"]


def test_form_text_marks_empty_fields() -> None:
    text = chat_graph.form_text(TripInfo(destination="Huế", adults=2))
    assert "- Điểm đến: Huế" in text
    assert "- Ngày đi: (chưa có)" in text
    assert "2 người lớn" in text


def test_trip_context_picks_the_turn_instruction() -> None:
    asking = chat_graph.trip_context(
        TripDraft(info=TripInfo(destination="Huế"), wants_plan=True), open_form=False
    )
    assert "còn thiếu: ngày đi, số ngày, số người đi, ngân sách" in asking
    opening = chat_graph.trip_context(TripDraft(info=FULL), open_form=True)
    assert "form đã đủ" in opening
    opening_missing = chat_graph.trip_context(
        TripDraft(info=TripInfo(destination="Huế")), open_form=True
    )
    assert "tô đỏ" in opening_missing
    chatting = chat_graph.trip_context(TripDraft(info=FULL), open_form=False)
    assert "Trò chuyện bình thường" in chatting


# ---------------------------------------------------------------- lưu trữ


async def test_new_thread_prefills_access_from_profile() -> None:
    store = InMemoryStore()
    await store.aput(profile_namespace("u1"), "main", {"access": ["Dị ứng hải sản"]})
    draft = await load_draft(store, "t-new", "u1")
    assert draft.info.access == ["Dị ứng hải sản"]


async def test_confirm_replaces_profile_access() -> None:
    store = InMemoryStore()
    await store.aput(profile_namespace("u1"), "main", {"access": ["Ăn chay"], "other": 1})
    await save_draft(store, "t1", TripDraft(info=TripInfo(access=[])), "u1", replace_access=True)
    profile = await store.aget(profile_namespace("u1"), "main")
    assert profile is not None
    assert profile.value == {"access": [], "other": 1}


async def test_stored_draft_wins_over_graph_state() -> None:
    store = InMemoryStore()
    await save_draft(store, "t1", TripDraft(info=TripInfo(days=4), confirmed=True))
    draft = await load_draft(store, "t1", fallback={"info": {"days": 2}})
    assert draft.info.days == 4
    assert draft.confirmed


# ---------------------------------------------------------------- graph


class Calls:
    def __init__(self) -> None:
        self.updates: list[str] = []
        self.prompts: list[str] = []


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, InMemoryStore, Calls]:
    calls = Calls()
    replies = iter(f"Trả lời {i}" for i in range(20))

    async def no_publish(*_: Any, **__: Any) -> None:
        return None

    class Model(GenericFakeChatModel):
        async def ainvoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            calls.prompts.append(messages[0].content)
            return await super().ainvoke(messages, *args, **kwargs)

    # Bản giả của bước cập nhật form: đọc vài từ khoá trong tin nhắn.
    async def fake_update(current: TripInfo, message: str, last_ai: str | None = None) -> Any:
        calls.updates.append(message)
        new = TripInfo()
        if "Đà Lạt" in message:
            new = TripInfo(destination="Đà Lạt", days=3, adults=2)
        if "8 triệu" in message:
            new = TripInfo(budget_million=8)
        if "20/12" in message:
            new = TripInfo(start_date="2026-12-20")
        merged = merge(current, new)
        return TripUpdate(**merged.model_dump(), wants_plan="lên lịch" in message)

    monkeypatch.setattr(chat_graph.agent_log, "publish", no_publish)
    monkeypatch.setattr(chat_graph, "get_chat_model", lambda **_: Model(messages=replies))
    monkeypatch.setattr(chat_graph, "update_trip_info", fake_update)

    store = InMemoryStore()
    compiled = chat_graph.build_chat_graph().compile(checkpointer=InMemorySaver(), store=store)
    return compiled, store, calls


def _config(thread: str = "t1") -> dict[str, Any]:
    return {"configurable": {"thread_id": thread, "user_id": "u1"}}


async def _turn(compiled: Any, text: str) -> dict[str, Any]:
    await compiled.ainvoke({"messages": [HumanMessage(text)]}, _config())
    return await chat_graph.trip_event(compiled, _config())


async def test_small_talk_does_not_call_the_form_llm(graph: Any) -> None:
    compiled, _, calls = graph
    event = await _turn(compiled, "cảm ơn nha")
    assert calls.updates == []
    assert event["open_form"] is False
    assert [m["field"] for m in event["missing"]][0] == "destination"


async def test_form_opens_on_agreement_then_reopens_when_complete(graph: Any) -> None:
    compiled, store, calls = graph

    # Đồng ý lập lộ trình: form mở ngay dù còn thiếu, trợ lý mời điền nốt trong form.
    event = await _turn(compiled, "Đi Đà Lạt 3 ngày 2 người lớn, lên lịch giúp mình")
    assert event["info"]["destination"] == "Đà Lạt"
    assert event["open_form"] is True
    assert event["wants_plan"] is True
    assert [m["field"] for m in event["missing"]] == ["start_date", "budget_million"]
    assert "các mục còn thiếu (ngày đi, ngân sách)" in calls.prompts[-1]
    assert "KHÔNG hỏi từng mục trong chat" in calls.prompts[-1]

    # Người dùng đóng form, vẫn kể trong chat: chưa đủ thì không mở lại, trợ lý nhắc mở form.
    event = await _turn(compiled, "tầm 8 triệu")
    assert event["open_form"] is False
    assert event["wants_plan"] is True
    assert [m["field"] for m in event["missing"]] == ["start_date"]
    assert "đóng form khi còn thiếu: ngày đi" in calls.prompts[-1]

    # Trả lời đủ: form tự mở lại để xác nhận.
    event = await _turn(compiled, "đi ngày 20/12")
    assert event["missing"] == []
    assert event["open_form"] is True
    assert event["wants_plan"] is False
    assert "form đã đủ" in calls.prompts[-1]

    saved = await store.aget(trip_namespace("t1"), "draft")
    assert saved is not None
    assert saved.value["info"]["budget_million"] == 8
    assert saved.value["wants_plan"] is False

    # Lượt sau không tự mở lại form.
    event = await _turn(compiled, "cảm ơn nha")
    assert event["open_form"] is False


async def test_trip_details_without_plan_intent_do_not_open_the_form(graph: Any) -> None:
    compiled, _, calls = graph
    event = await _turn(compiled, "Đi Đà Lạt 3 ngày 2 người lớn thì nên ăn gì?")
    assert calls.updates  # có thông tin chuyến đi -> form được cập nhật
    assert event["info"]["days"] == 3
    assert event["open_form"] is False
    assert event["wants_plan"] is False


async def test_stream_only_yields_the_reply_node(graph: Any) -> None:
    compiled, _, _ = graph
    chunks = [
        text
        async for text in chat_graph.stream_reply(
            compiled, "Đi Đà Lạt 3 ngày 2 người lớn", _config("t2")
        )
    ]
    assert "".join(chunks).startswith("Trả lời")


def test_last_turn_finds_the_previous_answer() -> None:
    question, last_ai = chat_graph._last_turn(
        [HumanMessage("a"), AIMessage("muốn lập lộ trình không?"), HumanMessage("ok")]
    )
    assert (question, last_ai) == ("ok", "muốn lập lộ trình không?")


# ---------------------------------------------------------------- HTTP


async def test_stream_endpoint_ends_with_the_trip_event(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib
    import json

    import httpx

    from ailusion.api import chat as chat_routes
    from ailusion.api.app import app

    compiled, store, _ = graph

    async def no_publish(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(chat_routes.agent_log, "publish", no_publish)
    # Không gửi trace của test lên Langfuse.
    tracing = importlib.import_module("ailusion.core.tracing")
    monkeypatch.setattr(tracing, "langfuse_callbacks", lambda: [])
    app.state.graph = compiled
    app.state.memory = (None, store)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://llm.test") as client:
        resp = await client.post(
            "/chat/stream",
            json={"message": "Đi Đà Lạt 3 ngày 2 người lớn", "thread_id": "t-http"},
        )
        events = [
            json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")
        ]
        assert [e["type"] for e in events][-2:] == ["trip", "done"]
        trip = events[-2]
        assert trip["info"]["destination"] == "Đà Lạt"
        assert trip["open_form"] is False
        assert "start_date" in [m["field"] for m in trip["missing"]]

        saved = await client.get("/chat/t-http/trip")
        assert saved.status_code == 200
        assert saved.json()["info"]["days"] == 3

        confirmed = await client.put(
            "/chat/t-http/trip",
            json={"info": {**trip["info"], "start_date": "2026-12-20", "budget_million": 5}},
        )
        assert confirmed.json()["missing"] == []
        again = await client.get("/chat/t-http/trip")
        assert again.json()["info"]["budget_million"] == 5

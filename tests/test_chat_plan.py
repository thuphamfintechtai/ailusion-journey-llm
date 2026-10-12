"""Lập lộ trình từ form của trang chat: lộ trình dạng chữ nối vào hội thoại."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from ailusion.api import itinerary as routes
from ailusion.api.app import app
from ailusion.chat import graph as chat_graph
from ailusion.itinerary import ItineraryResponse, TripForm, itinerary_markdown, request_text

PLAN = ItineraryResponse.model_validate(
    {
        "thread_id": "plan-0123456789abcdef",
        "status": "ok",
        "destination": "Đà Lạt",
        "start_date": "2026-12-20",
        "days": 1,
        "chosen": "PA1",
        "why": "PA1 ít ngồi xe nhất.",
        "options": [
            {
                "id": "PA1",
                "name": "Phương án 1",
                "total_cost": 1_200_000,
                "days": [
                    {
                        "day": 1,
                        "date": "2026-12-20",
                        "weekday": "Chủ Nhật",
                        "plan_id": "N1-L1",
                        "status": "ok",
                        "metrics": {},
                        "items": [
                            {
                                "type": "visit",
                                "start": "08:00",
                                "end": "09:00",
                                "name": "Hồ Xuân Hương",
                                "price": 0,
                            },
                        ],
                    }
                ],
            }
        ],
    }
)


def test_request_text_reads_like_the_users_message() -> None:
    form = TripForm.model_validate(
        {
            "destination": "Đà Lạt",
            "start_date": "2026-12-20",
            "end_date": "2026-12-21",
            "group_size": 3,
            "kids_under_6": 1,
            "budget": 8_000_000,
            "purposes": ["relax"],
            "message": "bé dị ứng hải sản",
        }
    )
    text = request_text(form)
    assert text.startswith(
        "Lập lộ trình: Đà Lạt, 2 ngày (20/12–21/12), 3 người, 1 trẻ dưới 6 tuổi."
    )
    assert "Ngân sách 8.000.000 đ cả đoàn cả chuyến." in text
    assert "Lời nhắn: bé dị ứng hải sản" in text


def test_itinerary_markdown_shows_the_chosen_option_day_by_day() -> None:
    text = itinerary_markdown(PLAN)
    assert "**Lộ trình Đà Lạt 1 ngày**" in text and "đề xuất **Phương án 1**" in text
    assert "**Ngày 1 · Chủ Nhật 20/12**" in text
    assert "- 08:00 📍 Hồ Xuân Hương" in text
    assert "💰 **Tổng:** 1.200.000 đ" in text


async def test_chat_plan_appends_the_plan_to_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = PLAN

    async def fake_run(app: Any, request: TripForm) -> ItineraryResponse:
        return resp

    monkeypatch.setattr(routes, "run_itinerary", fake_run)
    compiled = chat_graph.build_chat_graph().compile(
        checkpointer=InMemorySaver(), store=InMemoryStore()
    )
    app.state.graph = compiled
    form = {
        "destination": "Đà Lạt",
        "start_date": "2026-12-20",
        "end_date": "2026-12-20",
        "group_size": 2,
        "thread_id": "plan-0123456789abcdef",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://llm.test") as client:
        r = await client.post("/chat/chat-abc/plan", json=form)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["plan"]["chosen"] == "PA1"
        assert body["request_text"].startswith("Lập lộ trình: Đà Lạt, 1 ngày")
        assert "Hồ Xuân Hương" in body["reply"]

        history = (await client.get("/chat/chat-abc")).json()["messages"]
        assert [m["role"] for m in history] == ["human", "ai"]
        assert history[1]["content"] == body["reply"]


async def test_chat_plan_need_info_appends_the_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = ItineraryResponse(
        thread_id="p",
        status="need_info",
        destination="Huế",
        start_date="2026-12-20",
        days=2,
        questions=[{"id": "H1", "text": "Bạn đi cùng ai?", "choices": []}],
    )

    async def fake_run(app: Any, request: TripForm) -> ItineraryResponse:
        return resp

    monkeypatch.setattr(routes, "run_itinerary", fake_run)
    app.state.graph = chat_graph.build_chat_graph().compile(checkpointer=InMemorySaver())
    form = {
        "destination": "Huế",
        "start_date": "2026-12-20",
        "end_date": "2026-12-21",
        "group_size": 2,
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://llm.test") as client:
        body = (await client.post("/chat/chat-q/plan", json=form)).json()
        assert body["plan"]["status"] == "need_info"
        # Câu hỏi lại cũng nối vào hội thoại: trợ lý nhớ mình vừa hỏi gì.
        assert body["reply"].startswith("Mình cần thêm vài thông tin")
        history = (await client.get("/chat/chat-q")).json()["messages"]
        assert [m["role"] for m in history] == ["human", "ai"]


async def test_reused_thread_id_starts_from_a_clean_state() -> None:
    from langgraph.graph import END, START, StateGraph

    from ailusion.itinerary.shared.models import ItineraryState

    async def node(state: ItineraryState) -> dict:
        return {"llm_calls": 1, "trip": {"old": True} if not state.get("form") else state["form"]}

    builder = StateGraph(ItineraryState)
    builder.add_node("n", node)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "plan-reuse"}}
    await graph.ainvoke({}, config)
    assert (await graph.aget_state(config)).values["llm_calls"] == 1
    # Đúng bước API làm trước khi chạy lại cùng mã:
    if (await graph.aget_state(config)).values:
        await graph.checkpointer.adelete_thread("plan-reuse")
    assert not (await graph.aget_state(config)).values

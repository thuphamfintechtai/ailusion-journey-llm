"""Nạp lại lịch sử khi bộ nhớ (checkpointer) của hội thoại đã hết hạn."""

from langchain_core.messages import AIMessage, HumanMessage

from ailusion.api.chat import messages_for_turn
from ailusion.api.schemas import ChatRequest, Message

HISTORY = [
    Message(role="human", content="Đi Hội An 2 ngày"),
    Message(role="ai", content="Gợi ý: phố cổ, An Bàng."),
]


def test_memory_still_there_sends_only_the_question() -> None:
    out = messages_for_turn("Thêm 1 ngày", HISTORY, has_memory=True)
    assert len(out) == 1
    assert isinstance(out[0], HumanMessage)
    assert out[0].content == "Thêm 1 ngày"


def test_expired_memory_replays_history_before_the_question() -> None:
    out = messages_for_turn("Thêm 1 ngày", HISTORY, has_memory=False)
    assert [type(m) for m in out] == [HumanMessage, AIMessage, HumanMessage]
    assert [m.content for m in out] == [
        "Đi Hội An 2 ngày",
        "Gợi ý: phố cổ, An Bàng.",
        "Thêm 1 ngày",
    ]


def test_unknown_roles_and_empty_messages_are_skipped() -> None:
    history = [Message(role="system", content="x"), Message(role="ai", content="")]
    assert len(messages_for_turn("Chào", history, has_memory=False)) == 1


def test_history_is_optional_in_the_request() -> None:
    assert ChatRequest(message="Chào").history == []
    req = ChatRequest.model_validate(
        {"message": "Chào", "history": [{"role": "human", "content": "trước"}]}
    )
    assert req.history[0].content == "trước"

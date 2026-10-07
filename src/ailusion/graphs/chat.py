"""Graph chat một node — luồng đầu tiên: người dùng hỏi, Gemini trả lời.

    START -> tra_loi -> END

Lịch sử hội thoại do checkpointer giữ: cùng `thread_id` thì lượt sau tự thấy lượt trước,
không cần backend gửi lại toàn bộ hội thoại. Reducer `add_messages` lo việc nối tin nhắn.

Đây là bộ khung để sau này thay bằng Graph A (nghiên cứu) / Graph B (quyết định).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessageChunk, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from ailusion.llm import get_chat_model, message_text
from ailusion.prompts.chat_system import CHAT_SYSTEM
from ailusion.runtime import AgentLog

agent_log = AgentLog()


class ChatState(TypedDict):
    """`add_messages` nối tin nhắn mới vào lịch sử thay vì ghi đè."""

    messages: Annotated[list[AnyMessage], add_messages]


async def tra_loi(state: ChatState, config: RunnableConfig) -> dict:
    """Gọi LLM trên toàn bộ lịch sử hội thoại của thread."""
    thread_id = config["configurable"]["thread_id"]
    await agent_log.publish(thread_id, node="tra_loi", kind="step", text="Đang soạn câu trả lời")

    llm = get_chat_model()
    reply = await llm.ainvoke([SystemMessage(CHAT_SYSTEM), *state["messages"]])

    await agent_log.publish(
        thread_id, node="tra_loi", kind="answer", text=message_text(reply.content)[:200]
    )
    return {"messages": [reply]}


def build_chat_graph() -> StateGraph:
    graph = StateGraph(ChatState)
    graph.add_node("tra_loi", tra_loi)
    graph.add_edge(START, "tra_loi")
    graph.add_edge("tra_loi", END)
    return graph


async def stream_reply(graph: Any, message: str, config: RunnableConfig) -> AsyncIterator[str]:
    """Gửi một câu hỏi vào graph đã compile, trả về câu trả lời theo từng mẩu chữ."""
    stream = graph.astream({"messages": [HumanMessage(message)]}, config, stream_mode="messages")
    async for chunk, _metadata in stream:
        if not isinstance(chunk, AIMessageChunk):
            continue
        # Gemini chen vào những chunk chỉ có chữ ký suy luận, không có chữ -> bỏ qua.
        text = message_text(chunk.content)
        if text:
            yield text

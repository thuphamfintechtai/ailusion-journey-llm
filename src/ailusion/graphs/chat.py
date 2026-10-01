"""Graph chat một node — luồng đầu tiên: người dùng hỏi, Gemini trả lời.

    START -> tra_loi -> END

Lịch sử hội thoại do checkpointer giữ: cùng `thread_id` thì lượt sau tự thấy lượt trước,
không cần backend gửi lại toàn bộ hội thoại. Reducer `add_messages` lo việc nối tin nhắn.

Đây là bộ khung để sau này thay bằng Graph A (nghiên cứu) / Graph B (quyết định).
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from ailusion.llm import get_chat_model, message_text
from ailusion.runtime import AgentLog

SYSTEM_PROMPT = """Bạn là trợ lý lập kế hoạch du lịch Việt Nam của Ailusion Journey.
Trả lời bằng tiếng Việt, ngắn gọn và cụ thể. Khi thiếu thông tin để lập kế hoạch
(điểm đến, số ngày, số người, ngân sách), hãy hỏi lại đúng phần còn thiếu."""

agent_log = AgentLog()


class ChatState(TypedDict):
    """`add_messages` nối tin nhắn mới vào lịch sử thay vì ghi đè."""

    messages: Annotated[list[AnyMessage], add_messages]


async def tra_loi(state: ChatState, config: RunnableConfig) -> dict:
    """Gọi LLM trên toàn bộ lịch sử hội thoại của thread."""
    thread_id = config["configurable"]["thread_id"]
    await agent_log.publish(thread_id, node="tra_loi", kind="start", text="Đang soạn câu trả lời")

    llm = get_chat_model()
    reply = await llm.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])

    await agent_log.publish(
        thread_id, node="tra_loi", kind="done", text=message_text(reply.content)[:200]
    )
    return {"messages": [reply]}


def build_chat_graph() -> StateGraph:
    graph = StateGraph(ChatState)
    graph.add_node("tra_loi", tra_loi)
    graph.add_edge(START, "tra_loi")
    graph.add_edge("tra_loi", END)
    return graph

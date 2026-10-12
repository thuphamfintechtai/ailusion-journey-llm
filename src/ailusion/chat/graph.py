"""Graph chat — lọc tin nhắn vào form chuyến đi, rồi trả lời.

    START -> loc_tin -> tra_loi -> END

- `loc_tin`: bộ lọc luật (`ailusion.chat_filter`) xem tin nhắn có thông tin chuyến đi hoặc ý
  muốn lập lộ trình không. Có thì một lượt LLM cập nhật form (`update_trip_info`) và lưu vào
  Postgres (`ailusion.trip_store`); không thì bỏ qua, không tốn lượt nào. Người dùng muốn lập
  lộ trình thì bật `open_form` để giao diện tự mở form (mục bắt buộc còn thiếu tô đỏ); thiếu
  mà người dùng chưa điền thì trợ lý hỏi tiếp trong chat, trả lời đủ là form mở lại.
- `tra_loi`: trả lời trên toàn bộ lịch sử, prompt có kèm form hiện tại và các mục còn thiếu
  để trợ lý không hỏi lại điều đã biết, và hỏi cho đủ khi người dùng muốn lập lộ trình.

Lịch sử hội thoại do checkpointer giữ: cùng `thread_id` thì lượt sau tự thấy lượt trước.
Reducer `add_messages` lo việc nối tin nhắn.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from ailusion.core.agent_log import AgentLog
from ailusion.core.llm import MissingApiKeyError, get_chat_model, message_text

from . import filter as chat_filter
from .prompts import (
    CHAT_SYSTEM,
    STATE_ASK_MISSING,
    STATE_CHATTING,
    STATE_OPEN_FORM,
    STATE_OPEN_FORM_MISSING,
    TRIP_STATE,
)
from .trip_info import REQUIRED, TripInfo, update_trip_info
from .trip_store import TripDraft, load_draft, missing_labels, save_draft

logger = logging.getLogger(__name__)
agent_log = AgentLog()

REPLY_NODE = "tra_loi"
# Trần độ dài câu trả lời chat: đủ cho gợi ý 3 nhóm, chặn model viết lan man tốn tiền.
REPLY_TOKENS = 1200


class ChatState(TypedDict, total=False):
    """`add_messages` nối tin nhắn mới vào lịch sử thay vì ghi đè."""

    messages: Annotated[list[AnyMessage], add_messages]
    # TripDraft dạng dict: form chuyến đi sau lượt này.
    trip: dict[str, Any]
    # True ở lượt người dùng vừa đồng ý lập lộ trình, hoặc vừa trả lời đủ mục còn thiếu
    # -> giao diện tự mở form.
    open_form: bool


async def loc_tin(state: ChatState, config: RunnableConfig, runtime: Runtime) -> dict:
    """Lọc tin nhắn mới; có thông tin chuyến đi thì cập nhật form và lưu lại."""
    configurable = config["configurable"]
    thread_id = configurable["thread_id"]
    user_id = configurable.get("user_id")
    question, last_ai = _last_turn(state["messages"])

    draft = await load_draft(runtime.store, thread_id, user_id, fallback=state.get("trip"))

    found = chat_filter.check(question, last_ai)
    if not found.should_extract:
        return {"trip": draft.model_dump(mode="json"), "open_form": False}

    await agent_log.publish(
        thread_id, node="loc_tin", kind="step", text="Đang ghi thông tin chuyến đi vào form"
    )
    info, wants_plan = draft.info, found.plan_intent
    try:
        update = await update_trip_info(draft.info, question, last_ai)
        info = TripInfo.model_validate(update.model_dump(exclude={"wants_plan"}))
        wants_plan = wants_plan or update.wants_plan
    except MissingApiKeyError:
        raise
    except Exception:  # noqa: BLE001 - form không cập nhật được thì vẫn trả lời bình thường
        logger.exception("Không cập nhật được form chuyến đi của %s", thread_id)

    new = draft.model_copy(
        update={"info": info, "confirmed": draft.confirmed and info == draft.info}
    )
    # Mở form khi: người dùng vừa đồng ý lập lộ trình (đủ hay thiếu đều mở, mục thiếu tô
    # đỏ để họ điền), hoặc trước đó đã muốn lập mà còn thiếu, nay trả lời trong chat cho đủ.
    became_complete = draft.wants_plan and bool(draft.missing) and not new.missing
    open_form = wants_plan or became_complete
    # Còn thiếu thì giữ cờ "muốn lập": trợ lý hỏi tiếp trong chat, đủ là form mở lại.
    new = new.model_copy(
        update={"wants_plan": (draft.wants_plan or wants_plan) and bool(new.missing)}
    )
    if new != draft or open_form:
        await save_draft(runtime.store, thread_id, new, user_id)
    return {"trip": new.model_dump(mode="json"), "open_form": open_form}


async def tra_loi(state: ChatState, config: RunnableConfig) -> dict:
    """Gọi LLM trên toàn bộ lịch sử hội thoại của thread, kèm form chuyến đi hiện tại."""
    thread_id = config["configurable"]["thread_id"]
    await agent_log.publish(thread_id, node=REPLY_NODE, kind="step", text="Đang soạn câu trả lời")

    draft = TripDraft.model_validate(state.get("trip") or {})
    system = f"{CHAT_SYSTEM}\n\n{trip_context(draft, open_form=bool(state.get('open_form')))}"
    llm = get_chat_model(max_tokens=REPLY_TOKENS)
    reply = await llm.ainvoke([SystemMessage(system), *state["messages"]])

    await agent_log.publish(
        thread_id, node=REPLY_NODE, kind="answer", text=message_text(reply.content)[:200]
    )
    return {"messages": [reply]}


def trip_context(draft: TripDraft, *, open_form: bool) -> str:
    """Phần prompt nói cho trợ lý biết form đang có gì, thiếu gì, và lượt này nên làm gì."""
    missing = [REQUIRED[key] for key in draft.missing]
    if open_form and missing:
        instruction = STATE_OPEN_FORM_MISSING.format(missing=", ".join(missing))
    elif open_form:
        instruction = STATE_OPEN_FORM
    elif draft.wants_plan and missing:
        instruction = STATE_ASK_MISSING.format(missing=", ".join(missing))
    else:
        instruction = STATE_CHATTING
    return TRIP_STATE.format(
        form=form_text(draft.info),
        missing=", ".join(missing) if missing else "không còn mục nào",
        instruction=instruction,
    )


def form_text(info: TripInfo) -> str:
    """Form dạng gạch đầu dòng tiếng Việt, mục trống ghi "(chưa có)"."""

    def show(value: object, unit: str = "") -> str:
        if value is None or value == []:
            return "(chưa có)"
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return f"{value}{unit}"

    budget = show(info.budget_million, " triệu đồng cả đoàn")
    if info.budget_million is not None and info.budget_hard is not None:
        budget += " (không được vượt)" if info.budget_hard else " (có thể linh hoạt)"
    people = (
        "(chưa có)"
        if info.adults is None and info.children is None and info.seniors is None
        else f"{info.adults or 0} người lớn, {info.children or 0} trẻ dưới 12 tuổi, "
        f"{info.seniors or 0} người lớn tuổi"
    )
    lines = [
        f"- Điểm đến: {show(info.destination)}",
        f"- Ngày đi: {show(info.start_date)}",
        f"- Số ngày: {show(info.days)}",
        f"- Số người: {people}",
        f"- Ngân sách: {budget}",
        f"- Nhóm người dùng: {show(info.persona and info.persona.upper())}",
        f"- Yêu cầu bắt buộc: {show(info.access)}",
        f"- Sở thích: {show(info.interests)}",
        f"- Phương tiện: {show(info.transport)}",
        f"- Nhịp độ: {show(info.pace)}",
    ]
    return "\n".join(lines)


def _last_turn(messages: Sequence[AnyMessage]) -> tuple[str, str | None]:
    """(tin mới nhất của người dùng, câu trả lời của trợ lý ngay trước tin đó)."""
    question, last_ai = "", None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            question = message_text(messages[i].content)
            for earlier in reversed(messages[:i]):
                if isinstance(earlier, AIMessage):
                    last_ai = message_text(earlier.content)
                    break
            break
    return question, last_ai


def build_chat_graph() -> StateGraph:
    graph = StateGraph(ChatState)
    graph.add_node("loc_tin", loc_tin)
    graph.add_node(REPLY_NODE, tra_loi)
    graph.add_edge(START, "loc_tin")
    graph.add_edge("loc_tin", REPLY_NODE)
    graph.add_edge(REPLY_NODE, END)
    return graph


async def stream_reply(
    graph: Any, message: str | Sequence[AnyMessage], config: RunnableConfig
) -> AsyncIterator[str]:
    """Gửi một câu hỏi (hoặc danh sách tin nhắn, câu hỏi ở cuối) vào graph đã compile,
    trả về câu trả lời theo từng mẩu chữ."""
    messages = [HumanMessage(message)] if isinstance(message, str) else list(message)
    stream = graph.astream({"messages": messages}, config, stream_mode="messages")
    async for chunk, metadata in stream:
        # Chỉ lấy chữ của node trả lời: lượt LLM cập nhật form ở loc_tin trả JSON, không
        # được lọt ra màn hình chat.
        if not isinstance(chunk, AIMessageChunk) or metadata.get("langgraph_node") != REPLY_NODE:
            continue
        # Gemini chen vào những chunk chỉ có chữ ký suy luận, không có chữ -> bỏ qua.
        text = message_text(chunk.content)
        if text:
            yield text


async def trip_event(graph: Any, config: RunnableConfig) -> dict[str, Any]:
    """Form chuyến đi sau lượt vừa chạy, gửi cho giao diện cùng câu trả lời."""
    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    draft = TripDraft.model_validate(values.get("trip") or {})
    return trip_payload(draft, open_form=bool(values.get("open_form")))


def trip_payload(draft: TripDraft, *, open_form: bool = False) -> dict[str, Any]:
    return {
        "info": draft.info.model_dump(mode="json"),
        "missing": missing_labels(draft.missing),
        "wants_plan": draft.wants_plan,
        "open_form": open_form,
    }

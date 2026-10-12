"""Pipeline lập lộ trình (bước 1–17, chuyến ở 1 nơi) — file này chỉ nối các bước.

Mỗi bước do một làn làm, giống trang thiết kế (C = Coordinator, code; còn lại là agent LLM):

    bước  node              làn          file
    1     nhan_form         C            coordinator/profile.py
    2     doc_loi_nhan      Intake       agents/intake.py        (chỉ khi có lời nhắn)
    3     phan_nhom         C            coordinator/profile.py
    4     hoi_lai           C            coordinator/profile.py  còn thiếu ý -> dừng, hỏi user
    5     tim_kiem          C            coordinator/search.py
    6     loc               C            coordinator/filter.py
    6a/b  xac_minh          Experience   agents/experience.py    (C gửi 2 agent song song,
                            Budget       agents/budget.py         coordinator/filter.py)
    7–8   cham, chot_danh_sach  C        coordinator/score.py
    9     xep_lich          Planner      agents/planner.py
    10–11 kiem_lich         C            coordinator/simulate.py (lệch -> Planner sửa 1 lần)
    12–15 xep_hang, ghep    C            coordinator/rank.py
    16    quyet_dinh        Decision     agents/decision.py
    17    tra_ket_qua       C            response.py

Mỗi node là `async def ten_node(state, ctx) -> dict` (phần state cần cập nhật). `ctx`
(shared/context.py) gọi tool, gọi LLM, ghi nhật ký; state (shared/models.py) là dict thuần để
checkpointer lưu được.

LLM chỉ chạy ở 5 agent, thường 2–5 lượt mỗi lộ trình: Intake (0–1), Experience (0–1),
Budget (0–1), Planner (1 mỗi 4 ngày, +1 sửa), Decision (1, +1 sửa). Mọi thứ khác là code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from .agents.decision import quyet_dinh
from .agents.intake import doc_loi_nhan
from .agents.planner import xep_lich
from .coordinator.filter import loc, xac_minh
from .coordinator.profile import after_profile, hoi_lai, nhan_form, phan_nhom
from .coordinator.rank import ghep, xep_hang
from .coordinator.score import cham, chot_danh_sach
from .coordinator.search import tim_kiem
from .coordinator.simulate import kiem_lich
from .response import tra_ket_qua
from .shared.context import Ctx
from .shared.models import ItineraryState

Node = Callable[[ItineraryState, Ctx], Awaitable[dict[str, Any]]]


# Thứ tự chạy sau khi đã đủ thông tin (sau bước 4).
MAIN_FLOW: list[Node] = [
    tim_kiem,
    loc,
    xac_minh,
    cham,
    chot_danh_sach,
    xep_lich,
    kiem_lich,
    xep_hang,
    ghep,
    quyet_dinh,
    tra_ket_qua,
]


def build_itinerary_graph(tools: list[BaseTool]) -> StateGraph:
    def bind(fn: Node) -> Callable[[ItineraryState, RunnableConfig], Awaitable[dict[str, Any]]]:
        async def node(state: ItineraryState, config: RunnableConfig) -> dict[str, Any]:
            return await fn(state, Ctx(tools, config))

        node.__name__ = fn.__name__
        return node

    graph = StateGraph(ItineraryState)
    for fn in (nhan_form, doc_loi_nhan, phan_nhom, hoi_lai, *MAIN_FLOW):
        graph.add_node(fn.__name__, bind(fn))

    graph.add_edge(START, "nhan_form")
    graph.add_edge("nhan_form", "doc_loi_nhan")
    graph.add_edge("doc_loi_nhan", "phan_nhom")
    graph.add_conditional_edges("phan_nhom", after_profile, ["hoi_lai", "tim_kiem"])
    graph.add_edge("hoi_lai", END)
    for a, b in zip(MAIN_FLOW, MAIN_FLOW[1:], strict=False):
        graph.add_edge(a.__name__, b.__name__)
    graph.add_edge(MAIN_FLOW[-1].__name__, END)
    return graph

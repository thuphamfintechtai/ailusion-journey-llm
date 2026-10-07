"""Graph A — NGHIÊN CỨU, theo sơ đồ `kien-truc-agent-du-lich.drawio`.

    START -> trich_slot -> chuan_hoa ─┬─> hoi_lai -> END              (thiếu điểm đến)
                                      └─> lap_ke_hoach -> dieu_phoi
                                            (planner)  ▲      │ Send x N bước (song song)
                                                       │      ▼
                                                       │   goi_tool    (không gọi LLM)
                                                       │      │
                                                       │      ▼
                                                       └─ thiếu ─ phan_bien ─ đủ ─> dong_bang -> END

Planner quyết định cụ thể: mỗi bước là một tool + tham số + lý do, lấy từ danh mục tool của
năm nhánh (tools/groups.py). Điều phối chạy đúng các bước đó, song song, không qua LLM.
Phản biện đọc kết quả, tóm tắt từng nhánh và kết luận đủ / thiếu.

Chi phí một lượt: 3 lần gọi LLM (trích slot, planner, phản biện), thêm 2 nếu lập lại kế hoạch;
tối đa MAX_STEPS + MAX_REPLAN_STEPS lần gọi tool.

Đầu vào: `{"request": "<câu người dùng>"}`. Đầu ra: `context` (đủ dữ kiện) hoặc `question`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from ailusion.llm import get_chat_model
from ailusion.memory import Namespaces
from ailusion.prompts import research as prompts
from ailusion.runtime import AgentLog
from ailusion.tools import NODE_TOOLS, call_tool, pick_tools, tools_by_name

from . import normalize
from .schemas import BRANCHES, Digest, ResearchPlan, ResearchState, ToolStep, TripSlots

MAX_STEPS = 10  # số lần gọi tool tối đa của kế hoạch đầu
MAX_REPLAN_STEPS = 4  # số lần gọi tool tối đa khi lập lại kế hoạch
MAX_REPLANS = 1  # Phản biện được đòi lập lại kế hoạch tối đa ngần này lần
RESULT_CHARS = 2500  # mỗi kết quả tool đưa vào prompt Phản biện tối đa ngần này ký tự
PROMPT_JSON_CHARS = 6000

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
agent_log = AgentLog()


def build_research_graph(tools: list[BaseTool]) -> StateGraph:
    """Dựng Graph A với kho tool đã nạp từ MCP (`await load_mcp_tools()`)."""
    by_name = tools_by_name(tools)
    for group in (*BRANCHES, "slot"):
        pick_tools(tools, group)  # kiểm tra sớm: server phải có đủ tool cho mọi nhánh
    allowed = {branch: set(NODE_TOOLS[branch]) for branch in BRANCHES}
    catalog = _catalog(by_name)

    async def tool(name: str, **args: Any) -> Any:
        """Gọi một tool; lỗi kết nối thì trả dict lỗi, để node tự quyết chạy tiếp."""
        try:
            return await call_tool(by_name[name], **args)
        except Exception as e:  # noqa: BLE001 - một tool hỏng không được làm sập cả graph
            return {"status": "error", "message": f"{type(e).__name__}: {e}"}

    async def trich_slot(state: ResearchState, config: RunnableConfig, runtime: Runtime) -> dict:
        await _log(config, "trich_slot", "step", "Đọc yêu cầu")
        profile = await _read_profile(config, runtime)
        llm = get_chat_model().with_structured_output(TripSlots)
        prompt = prompts.SLOT.format(today=_today(), profile=_dump(profile))
        messages = [SystemMessage(prompt), HumanMessage(state["request"])]
        slots: TripSlots = await llm.ainvoke(messages)

        await _log(config, "trich_slot", "finding", _slots_text(slots), slots=slots.model_dump())
        # results=None xoá kết quả của yêu cầu trước nếu dùng lại thread.
        return {
            "slots": slots.model_dump(),
            "profile": profile,
            "results": None,
            "replans": 0,
            "question": None,
            "snapshot": None,
        }

    async def chuan_hoa(state: ResearchState, config: RunnableConfig) -> dict:
        slots = state["slots"]
        await _log(config, "chuan_hoa", "step", "Quy đổi ngân sách, ngày đi, địa danh")
        start, days = slots.get("start_date"), normalize.trip_days(slots)
        end = normalize.end_date(start, days)

        async def nothing() -> None:
            return None

        money, place, cal = await asyncio.gather(
            tool("parse_money", text=slots["budget_text"], convert_to_vnd=True)
            if slots.get("budget_text")
            else nothing(),
            tool("geocode_vn", address=slots["destination"])
            if slots.get("destination")
            else nothing(),
            tool("vn_calendar", start_date=start, end_date=end, province=slots.get("destination"))
            if start
            else nothing(),
        )
        normalized = {
            "budget_vnd": normalize.budget_vnd(money),
            "location": normalize.location(place),
            "start_date": start,
            "end_date": end,
            "days": days,
            "calendar": normalize.calendar(cal),
            "missing": normalize.missing_essentials(slots),
            "assumptions": normalize.assumptions(slots),
        }
        await _log(config, "chuan_hoa", "finding", _normalized_text(normalized), **normalized)
        return {"normalized": normalized}

    async def hoi_lai(state: ResearchState, config: RunnableConfig) -> dict:
        missing = ", ".join(state["normalized"]["missing"])
        question = f"Mình cần biết thêm {missing} để bắt đầu tìm. Bạn muốn đi đâu ạ?"
        await _log(config, "hoi_lai", "answer", question)
        return {"question": question}

    async def lap_ke_hoach(state: ResearchState, config: RunnableConfig) -> dict:
        """Planner: chọn đúng tool + tham số cho từng việc cần tìm."""
        slots, normalized = state["slots"], state["normalized"]
        replan = state.get("replans", 0) > 0
        max_steps = MAX_REPLAN_STEPS if replan else MAX_STEPS
        await _log(config, "lap_ke_hoach", "step", "Lập lại kế hoạch" if replan else "Lên kế hoạch")

        snapshot = state.get("snapshot")
        if snapshot is None:
            snapshot = await tool(
                "destination_snapshot",
                destination=slots["destination"],
                start_date=normalized["start_date"],
                days=min(normalized["days"], 14),
            )

        context: dict[str, Any] = {"yeu_cau": slots, "chuan_hoa": normalized, "tong_quan": snapshot}
        if replan:
            context["phan_bien_bao_thieu"] = state["digest"]["missing"]
            context["da_chay"] = [f"{r['tool']} {r['args']}" for r in state.get("results", [])]

        prompt = prompts.PLAN.format(
            today=_today(),
            catalog=catalog,
            max_steps=max_steps,
            replan_rule=prompts.REPLAN_RULE if replan else "",
        )
        llm = get_chat_model().with_structured_output(ResearchPlan)
        plan: ResearchPlan = await llm.ainvoke(
            [SystemMessage(prompt), HumanMessage(_dump(context))]
        )

        done = {normalize.step_key(r["tool"], r["args"]) for r in state.get("results", [])}
        steps = _valid_steps(plan.steps, allowed, done)[:max_steps]
        await _log(
            config,
            "lap_ke_hoach",
            "replan" if replan else "plan",
            f"{plan.reasoning} ({len(steps)} bước)",
            steps=steps,
        )
        return {"plan": {"reasoning": plan.reasoning, "steps": steps}, "snapshot": snapshot}

    async def dieu_phoi(state: ResearchState, config: RunnableConfig) -> dict:
        steps = state["plan"]["steps"]
        await _log(config, "dieu_phoi", "step", f"Chạy song song {len(steps)} bước")
        return {}

    def fan_out(state: ResearchState) -> list[Send] | str:
        steps = state["plan"]["steps"]
        if not steps:
            return "phan_bien"
        return [Send("goi_tool", {"step": s, "round": state.get("replans", 0)}) for s in steps]

    async def goi_tool(payload: dict[str, Any], config: RunnableConfig) -> dict:
        """Chạy một bước của kế hoạch. Không gọi LLM."""
        step = payload["step"]
        node = f"goi_tool:{step['branch']}"
        await _log(config, node, "tool_call", f"{step['tool']} — {step['why']}", args=step["args"])
        data = await tool(step["tool"], **step["args"])
        ok = not (isinstance(data, dict) and data.get("status") == "error")
        await _log(config, node, "tool_result", f"{step['tool']}: {'ok' if ok else 'lỗi'}")
        result = {**step, "ok": ok, "round": payload["round"]}
        result["data"] = normalize.compact(data, RESULT_CHARS)
        return {"results": [result]}

    async def phan_bien(state: ResearchState, config: RunnableConfig) -> dict:
        results = state.get("results", [])
        await _log(config, "phan_bien", "step", f"Đối chiếu {len(results)} kết quả")
        urls = normalize.collect_urls(results)
        trust = await tool("source_trust", urls=urls) if urls else "chưa có URL nào"

        evidence = {
            "yeu_cau": state["slots"],
            "chuan_hoa": state["normalized"],
            "tong_quan": state.get("snapshot"),
            "ket_qua_tool": [
                {"nhanh": r["branch"], "tool": r["tool"], "args": r["args"], "data": r["data"]}
                for r in results
            ],
        }
        llm = get_chat_model().with_structured_output(Digest)
        digest: Digest = await llm.ainvoke(
            [
                SystemMessage(prompts.CRITIC.format(trust=_dump(trust))),
                HumanMessage(json.dumps(evidence, ensure_ascii=False, default=str)),
            ]
        )
        replans = state.get("replans", 0)
        replan = digest.verdict == "thieu" and replans < MAX_REPLANS
        if replan:
            text = f"Thiếu: {'; '.join(digest.missing)}"
        else:
            text = "Đủ dữ kiện" if digest.verdict == "du" else "Còn thiếu nhưng đã hết lượt tìm"
        await _log(config, "phan_bien", "finding", text, **digest.model_dump())
        return {
            "digest": {**digest.model_dump(), "replan": replan},
            "replans": replans + 1 if replan else replans,
        }

    async def dong_bang(state: ResearchState, config: RunnableConfig) -> dict:
        results = state.get("results", [])
        context = {
            "request": state["request"],
            "slots": state["slots"],
            "normalized": state["normalized"],
            "snapshot": state.get("snapshot"),
            "digest": state["digest"],
            "results": results,
            "stats": {
                "tool_calls": len(results),
                "tool_errors": sum(not r["ok"] for r in results),
                "replans": state.get("replans", 0),
            },
            "frozen_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        }
        await _log(config, "dong_bang", "plan", "Đã đóng băng gói bối cảnh")
        return {"context": context}

    graph = StateGraph(ResearchState)
    for node in (trich_slot, chuan_hoa, hoi_lai, lap_ke_hoach, dieu_phoi, goi_tool, phan_bien):
        graph.add_node(node.__name__, node)
    graph.add_node("dong_bang", dong_bang)

    graph.add_edge(START, "trich_slot")
    graph.add_edge("trich_slot", "chuan_hoa")
    graph.add_conditional_edges(
        "chuan_hoa",
        lambda s: "hoi_lai" if s["normalized"]["missing"] else "lap_ke_hoach",
        ["hoi_lai", "lap_ke_hoach"],
    )
    graph.add_edge("hoi_lai", END)
    graph.add_edge("lap_ke_hoach", "dieu_phoi")
    graph.add_conditional_edges("dieu_phoi", fan_out, ["goi_tool", "phan_bien"])
    graph.add_edge("goi_tool", "phan_bien")
    graph.add_conditional_edges(
        "phan_bien",
        lambda s: "lap_ke_hoach" if s["digest"]["replan"] else "dong_bang",
        ["lap_ke_hoach", "dong_bang"],
    )
    graph.add_edge("dong_bang", END)
    return graph


def _valid_steps(
    steps: list[ToolStep], allowed: dict[str, set[str]], done: set[str]
) -> list[dict[str, Any]]:
    """Bỏ bước gọi tool ngoài nhánh, tham số không phải JSON object, hoặc trùng bước đã chạy."""
    valid: list[dict[str, Any]] = []
    seen = set(done)
    for step in steps:
        if step.tool not in allowed.get(step.branch, set()):
            continue
        try:
            args = json.loads(step.args_json or "{}")
        except ValueError:
            continue
        if not isinstance(args, dict):
            continue
        key = normalize.step_key(step.tool, args)
        if key in seen:
            continue
        seen.add(key)
        valid.append({"branch": step.branch, "tool": step.tool, "args": args, "why": step.why})
    return valid


def _catalog(by_name: dict[str, BaseTool]) -> str:
    """Danh mục tool cho planner: nhánh -> tên tool, mô tả một dòng, tham số."""
    lines = []
    for branch, description in BRANCHES.items():
        lines.append(f"\n[{branch}] {description}")
        for name in NODE_TOOLS[branch]:
            t = by_name[name]
            schema = t.args_schema if isinstance(t.args_schema, dict) else {}
            required = set(schema.get("required", []))
            params = ", ".join(
                p + ("*" if p in required else "") for p in schema.get("properties", {})
            )
            summary = (t.description or "").strip().split("\n")[0][:160]
            lines.append(f"- {name}({params}): {summary}")
    return "\n".join(lines)


async def _log(config: RunnableConfig, node: str, kind: str, text: str, **data: Any) -> None:
    await agent_log.publish(
        config["configurable"]["thread_id"], node=node, kind=kind, text=text, **data
    )


async def _read_profile(config: RunnableConfig, runtime: Runtime) -> dict[str, Any] | None:
    """Ưu tiên cá nhân từ bộ nhớ dài hạn (mũi tên "Bộ nhớ dài hạn -> Graph A")."""
    user_id = config["configurable"].get("user_id")
    if not user_id or runtime.store is None:
        return None
    item = await runtime.store.aget(Namespaces.profile(user_id), "main")
    return item.value if item else None


def _today() -> str:
    return datetime.now(VN_TZ).date().isoformat()


def _dump(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= PROMPT_JSON_CHARS else text[:PROMPT_JSON_CHARS] + "…"


def _slots_text(slots: TripSlots) -> str:
    people = f"{slots.adults or '?'} người lớn"
    if slots.children:
        people += f", {slots.children} trẻ"
    days = f"{slots.days} ngày" if slots.days else "chưa rõ số ngày"
    return f"{slots.destination or 'chưa rõ điểm đến'} · {days} · {people}"


def _normalized_text(normalized: dict[str, Any]) -> str:
    parts = []
    if normalized["budget_vnd"]:
        parts.append(f"ngân sách {normalized['budget_vnd']:,} đ".replace(",", "."))
    if normalized["location"]:
        parts.append(normalized["location"]["province"] or normalized["location"]["name"])
    if normalized["calendar"]:
        parts.append(f"cao điểm mức {normalized['calendar']['max_peak_level']}")
    return " · ".join(parts) or "Chưa có gì để quy đổi"

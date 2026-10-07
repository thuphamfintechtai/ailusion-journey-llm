"""Pipeline POST /plan — form -> lộ trình. Xem trang "POST /plan" trong docs/pipelines.drawio.

    START -> chuan_hoa -> chien_luoc -> planner -> dieu_phoi ──Send x N──> goi_tool ──> gom_ket_qua
          -> dong_bang -> xep_lich -> kiem_tra ─┬─ vượt ngân sách cứng -> xep_lich (siết, ≤ 2 lần)
                                                └─> rui_ro_chon -> giai_thich -> END

Hai lượt LLM: `chien_luoc` (prompt riêng theo nhóm người dùng P1–P4, prompts/strategy/) và
`giai_thich`. Chiến lược bị ép vào khoảng của nhóm (persona.clamp), LLM lỗi thì dùng chiến lược
mặc định của nhóm. Mọi bước khác là code hoặc tool MCP, nên cùng một form cho ra cùng
một lộ trình (trừ dữ liệu tool thay đổi theo thời gian, như giá phòng, dự báo thời tiết).

Đầu vào: `{"request": PlanRequest.model_dump(mode="json")}`. Đầu ra trong state: `options`,
`decision`, `explanation`, `context` (gói bối cảnh đã đóng băng).
"""

from __future__ import annotations

import asyncio
import json
import logging
import operator
import zlib
from datetime import datetime
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ailusion.llm import get_chat_model
from ailusion.prompts.plan_explain import PLAN_EXPLAIN
from ailusion.prompts.strategy import BY_PERSONA
from ailusion.runtime import AgentLog
from ailusion.tools import call_tool, tools_by_name

from . import candidates as cand
from .assemble import pool_from_dict, pool_to_dict, to_option
from .facts import decision_facts
from .persona import (
    PERSONA_WEIGHT_SET,
    PERSONAS,
    Strategy,
    clamp,
    default_strategy,
    trip_brief,
)
from .planner import collect_steps, hours_steps
from .request import PlanRequest, assumptions
from .response import Decision, Excluded, Explanation, PlanResponse, Stats
from .schedule import (
    PROFILES,
    WEIGHT_SETS,
    OptionPlan,
    binding,
    build_option,
    cost_of,
    hhmm,
    over_budget,
    score_parts,
    simulate,
    validate,
)

MAX_TIGHTEN = 2
agent_log = AgentLog()
log = logging.getLogger(__name__)


class PlanState(TypedDict, total=False):
    request: dict[str, Any]
    normalized: dict[str, Any]
    strategy: dict[str, Any]
    strategy_source: str
    steps: list[dict[str, Any]]
    results: Annotated[list[dict[str, Any]], operator.add]
    tool_log: Annotated[list[dict[str, Any]], operator.add]
    context: dict[str, Any]
    tighten: dict[str, int]
    retry: bool
    options: dict[str, Any]
    decision: dict[str, Any]
    explanation: dict[str, Any]
    llm_calls: int


def build_plan_graph(tools: list[BaseTool]) -> StateGraph:
    by_name = tools_by_name(tools)

    async def tool(name: str, **args: Any) -> tuple[Any, dict[str, Any]]:
        """Gọi một tool; lỗi thì trả dict lỗi để pipeline chạy tiếp. Kèm bản ghi cho thống kê."""
        try:
            data = await call_tool(by_name[name], **args)
        except Exception as e:  # noqa: BLE001 - một tool hỏng không được làm sập cả lộ trình
            data = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        good = cand.ok(data)
        return data, {"tool": name, "ok": good}

    def req_of(state: PlanState) -> PlanRequest:
        return PlanRequest.model_validate(state["request"])

    # 02 ------------------------------------------------------------------------------------
    async def chuan_hoa(state: PlanState, config: RunnableConfig) -> dict:
        req = req_of(state)
        b = req.basic
        await _log(config, "chuan_hoa", "step", "Chuẩn hoá điểm đến và ngày đi")
        (geo, g_log), (cal, c_log) = await asyncio.gather(
            tool("geocode_vn", address=b.destination),
            tool(
                "vn_calendar",
                start_date=b.start_date.isoformat(),
                end_date=b.end_date.isoformat(),
                province=b.destination,
            ),
        )
        top = (geo.get("results") or [{}])[0] if cand.ok(geo) else {}
        summary = cal.get("summary", {}) if cand.ok(cal) else {}
        normalized = {
            "province": (top.get("admin_new") or {}).get("province"),
            "center": [top["lat"], top["lng"]] if top.get("lat") else None,
            "holidays": summary.get("holidays", []),
            "max_peak_level": summary.get("max_peak_level"),
        }
        text = (
            f"{b.destination} · {normalized['province'] or '?'} · cao điểm mức "
            f"{normalized['max_peak_level']}"
        )
        await _log(config, "chuan_hoa", "finding", text, **normalized)
        return {"normalized": normalized, "tool_log": [g_log, c_log], "tighten": {}, "llm_calls": 0}

    # 02b -----------------------------------------------------------------------------------
    async def chien_luoc(state: PlanState, config: RunnableConfig) -> dict:
        """LLM lập chiến lược theo nhóm người dùng, rồi ép vào khoảng của nhóm."""
        req = req_of(state)
        persona = PERSONAS[req.persona]
        await _log(config, "chien_luoc", "step", f"Lập chiến lược cho nhóm {persona['name']}")
        brief = trip_brief(req, state["normalized"])
        try:
            llm = get_chat_model().with_structured_output(Strategy)
            raw: Strategy = await llm.ainvoke(
                [
                    SystemMessage(BY_PERSONA[req.persona]),
                    HumanMessage(json.dumps(brief, ensure_ascii=False, indent=1)),
                ]
            )
            strategy, source, calls = clamp(raw, req.persona), "llm", 1
        except Exception as e:  # noqa: BLE001 - không có LLM vẫn lập được lộ trình
            reason = f"{type(e).__name__}: {str(e)[:300]}"
            log.warning("chien_luoc: LLM lỗi, dùng chiến lược mặc định của nhóm — %s", reason)
            await _log(config, "chien_luoc", "finding", f"LLM lỗi: {reason}")
            strategy, source, calls = default_strategy(req.persona), "mac_dinh", 0
        await _log(config, "chien_luoc", "plan", strategy.summary, **strategy.model_dump())
        return {
            "strategy": strategy.model_dump(),
            "strategy_source": source,
            "llm_calls": state.get("llm_calls", 0) + calls,
        }

    # 03 ------------------------------------------------------------------------------------
    async def planner(state: PlanState, config: RunnableConfig) -> dict:
        steps = collect_steps(
            req_of(state), state["normalized"].get("province"), state.get("strategy")
        )
        await _log(
            config,
            "planner",
            "plan",
            f"{len(steps)} bước thu thập",
            steps=[f"{s['tool']} — {s['why']}" for s in steps],
        )
        return {"steps": steps}

    # 04 ------------------------------------------------------------------------------------
    async def dieu_phoi(state: PlanState, config: RunnableConfig) -> dict:
        await _log(config, "dieu_phoi", "step", f"Chạy song song {len(state['steps'])} bước")
        return {}

    def fan_out(state: PlanState) -> list[Send]:
        return [Send("goi_tool", {"step": s}) for s in state["steps"]]

    async def goi_tool(payload: dict[str, Any], config: RunnableConfig) -> dict:
        step = payload["step"]
        await _log(config, f"goi_tool:{step['key']}", "tool_call", step["why"], tool=step["tool"])
        data, log = await tool(step["tool"], **step["args"])
        await _log(
            config,
            f"goi_tool:{step['key']}",
            "tool_result",
            f"{step['tool']}: {'ok' if log['ok'] else 'lỗi'}",
        )
        return {"results": [{**step, "ok": log["ok"], "data": data}], "tool_log": [log]}

    async def gom_ket_qua(state: PlanState, config: RunnableConfig) -> dict:
        req = req_of(state)
        pool = cand.Pool()
        center = state["normalized"].get("center")
        pool.center = tuple(center) if center else None
        for r in state.get("results", []):
            key, data = r["key"], r["data"]
            if key.startswith(("places", "food")):
                cand.add_places(
                    pool, key, data, wheelchair_filtered=bool(r["args"].get("wheelchair"))
                )
            elif key == "hotels":
                cand.add_hotels(pool, data)
            elif key == "weather":
                cand.add_weather(pool, data)
            elif key == "festivals":
                cand.add_festivals(pool, data)
            elif key.startswith("intercity"):
                cand.add_intercity(pool, key, data)
            elif key == "health":
                cand.add_health(pool, data)
        cand.apply_hard_filters(pool, req)

        steps = hours_steps(req, cand.shortlist(pool, req))
        answers = await asyncio.gather(*(tool(s["tool"], **s["args"]) for s in steps))
        for s, (data, _) in zip(steps, answers, strict=True):
            cand.add_hours(pool, int(s["key"].split(":")[1]), data)

        n_poi = sum(p.kind in ("poi", "cafe") for p in pool.places.values())
        n_food = sum(p.kind == "food" for p in pool.places.values())
        await _log(
            config,
            "gom_ket_qua",
            "finding",
            f"{n_poi} điểm tham quan, {n_food} quán ăn, {len(pool.hotels)} khách sạn, "
            f"loại {len(pool.excluded)} điểm vi phạm ràng buộc cứng",
        )
        return {"context": pool_to_dict(pool), "tool_log": [log for _, log in answers]}

    # 05 ------------------------------------------------------------------------------------
    async def dong_bang(state: PlanState, config: RunnableConfig) -> dict:
        context = {**state["context"], "frozen_at": datetime.now().isoformat(timespec="seconds")}
        await _log(config, "dong_bang", "plan", "Đã đóng băng gói bối cảnh")
        return {"context": context}

    # 06 + 07 -------------------------------------------------------------------------------
    def build_all(state: PlanState) -> dict[str, OptionPlan]:
        req, pool = req_of(state), pool_from_dict(state["context"])
        tighten = state.get("tighten") or {}
        plans = {}
        for key in PROFILES:
            plan = build_option(key, pool, req, tighten.get(key, 0), state.get("strategy"))
            plan.cost = cost_of(plan, pool, req)
            validate(plan, req)
            plans[key] = plan
        return plans

    async def xep_lich(state: PlanState, config: RunnableConfig) -> dict:
        plans = build_all(state)
        text = " · ".join(
            f"{PROFILES[k]['name']} {p.cost['total']:,} đ".replace(",", ".")
            for k, p in plans.items()
        )
        await _log(config, "xep_lich", "step", f"Xếp 3 phương án: {text}")
        return {}

    async def kiem_tra(state: PlanState, config: RunnableConfig) -> dict:
        req, plans = req_of(state), build_all(state)
        tighten = dict(state.get("tighten") or {})
        retry = False
        for key, plan in plans.items():
            if over_budget(plan, req) and tighten.get(key, 0) < MAX_TIGHTEN:
                tighten[key] = tighten.get(key, 0) + 1
                retry = True
        issues = {k: p.issues for k, p in plans.items() if p.issues}
        text = (
            "Vượt ngân sách cứng, siết lại"
            if retry
            else (
                "Đạt mọi ràng buộc cứng" if not issues else "Còn vướng ràng buộc, giữ nguyên để báo"
            )
        )
        await _log(config, "kiem_tra", "finding", text, issues=issues, tighten=tighten)
        return {"tighten": tighten, "retry": retry}

    # 08 ------------------------------------------------------------------------------------
    async def rui_ro_chon(state: PlanState, config: RunnableConfig) -> dict:
        req, pool, plans = req_of(state), pool_from_dict(state["context"]), build_all(state)
        seed = zlib.crc32(json.dumps(state["request"], sort_keys=True).encode()) % 100_000
        for plan in plans.values():
            plan.risk = simulate(plan, req, seed)
            plan.scores = score_parts(plan, req, (state.get("strategy") or {}).get("stops_per_day"))

        metrics = {
            k: {**p.scores, "cost_vnd": p.cost["total"], "p_break": p.risk["p_break"]}
            for k, p in plans.items()
        }
        criteria = [
            {"name": n, "direction": "max"}
            for n in ("interest", "experience", "budget", "pace", "weather", "travel")
        ]
        criteria += [
            {"name": "cost_vnd", "direction": "min"},
            {"name": "p_break", "direction": "min"},
        ]
        scored, s_log = await tool(
            "score_options",
            options=[{"name": k, "metrics": m} for k, m in metrics.items()],
            criteria=criteria,
            weight_sets={PERSONA_WEIGHT_SET: PERSONAS[req.persona]["weights"], **WEIGHT_SETS},
            method="topsis",
        )
        decision = _decide(plans, scored, metrics, PERSONAS[req.persona]["weights"])

        winner = plans[decision["winner"]]
        maps, r_logs = await _route_check(tool, winner, pool, req)
        options = {
            k: to_option(p, pool, req, maps if k == decision["winner"] else {}).model_dump()
            for k, p in plans.items()
        }
        decision["binding"] = binding(winner, req)
        decision["excluded"] = [{"name": n, "reason": r} for n, r in pool.excluded][:12]
        await _log(
            config,
            "rui_ro_chon",
            "finding",
            f"Chọn {PROFILES[decision['winner']]['name']}"
            + (f" ({decision['tie_break']})" if decision.get("tie_break") else ""),
            matrix=decision["matrix"],
        )
        return {"options": options, "decision": decision, "tool_log": [s_log, *r_logs]}

    # 09 ------------------------------------------------------------------------------------
    async def giai_thich(state: PlanState, config: RunnableConfig) -> dict:
        await _log(config, "giai_thich", "step", "Viết lời giải thích")
        req = req_of(state)
        facts = decision_facts(
            req,
            state["options"],
            state["decision"],
            state["context"].get("festivals", []),
            state.get("strategy"),
        )
        try:
            llm = get_chat_model().with_structured_output(Explanation)
            explanation: Explanation = await llm.ainvoke(
                [
                    SystemMessage(PLAN_EXPLAIN),
                    HumanMessage(json.dumps(facts, ensure_ascii=False, indent=1)),
                ]
            )
            calls = 1
        except Exception as e:  # noqa: BLE001 - thiếu LLM vẫn trả được lộ trình
            # Ghi nguyên văn lỗi: RateLimitError của OpenAI dùng chung cho hết hạn mức lẫn
            # gọi quá nhanh, chỉ đọc message mới biết là trường hợp nào.
            reason = f"{type(e).__name__}: {str(e)[:300]}"
            log.warning("giai_thich: LLM lỗi, dùng lời giải thích soạn sẵn — %s", reason)
            await _log(config, "giai_thich", "finding", f"LLM lỗi: {reason}")
            explanation = _fallback_explanation(facts, e)
            calls = 0
        await _log(config, "giai_thich", "answer", explanation.summary)
        return {
            "explanation": explanation.model_dump(),
            "llm_calls": state.get("llm_calls", 0) + calls,
        }

    graph = StateGraph(PlanState)
    for node in (
        chuan_hoa,
        chien_luoc,
        planner,
        dieu_phoi,
        goi_tool,
        gom_ket_qua,
        dong_bang,
        xep_lich,
        kiem_tra,
        rui_ro_chon,
        giai_thich,
    ):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "chuan_hoa")
    graph.add_edge("chuan_hoa", "chien_luoc")
    graph.add_edge("chien_luoc", "planner")
    graph.add_edge("planner", "dieu_phoi")
    graph.add_conditional_edges("dieu_phoi", fan_out, ["goi_tool"])
    graph.add_edge("goi_tool", "gom_ket_qua")
    graph.add_edge("gom_ket_qua", "dong_bang")
    graph.add_edge("dong_bang", "xep_lich")
    graph.add_edge("xep_lich", "kiem_tra")
    graph.add_conditional_edges(
        "kiem_tra",
        lambda s: "xep_lich" if s["retry"] else "rui_ro_chon",
        ["xep_lich", "rui_ro_chon"],
    )
    graph.add_edge("rui_ro_chon", "giai_thich")
    graph.add_edge("giai_thich", END)
    return graph


# ---------------------------------------------------------------- chọn phương án


def _decide(
    plans: dict[str, OptionPlan],
    scored: Any,
    metrics: dict[str, Any],
    persona_weights: dict[str, float],
) -> dict[str, Any]:
    """Phương án thắng theo bộ trọng số của nhóm người dùng, trong số phương án đạt ràng buộc cứng.

    Ba bộ trọng số chung vẫn được chấm để người dùng thấy phương án nào mạnh ở mặt nào.
    """
    sets = {PERSONA_WEIGHT_SET: persona_weights, **WEIGHT_SETS}
    if cand.ok(scored):
        rankings = scored.get("rankings", {})
        matrix = {ws: {r["option"]: r["score"] for r in ranks} for ws, ranks in rankings.items()}
    else:  # score_options lỗi: tự tính tổng trọng số (chi phí, rủi ro tính ngược)
        matrix = {ws: {k: _weighted(m, w) for k, m in metrics.items()} for ws, w in sets.items()}
    by_set = {ws: max(row, key=row.get) for ws, row in matrix.items() if row}

    feasible = [k for k, p in plans.items() if p.hard_ok] or list(plans)
    persona_row = matrix.get(PERSONA_WEIGHT_SET, {})
    ranked = sorted(
        feasible,
        key=lambda k: (-persona_row.get(k, 0), plans[k].risk["p_break"], plans[k].cost["total"]),
    )
    winner, tie_break = ranked[0], None
    if (
        len(ranked) > 1
        and abs(persona_row.get(ranked[0], 0) - persona_row.get(ranked[1], 0)) < 0.02
    ):
        a, b = ranked[0], ranked[1]
        tie_break = (
            "điểm sát nhau, chọn phương án ít rủi ro vỡ lịch hơn"
            if plans[a].risk["p_break"] < plans[b].risk["p_break"]
            else "điểm sát nhau, chọn phương án rẻ hơn"
        )
    if by_set.get(PERSONA_WEIGHT_SET) not in (None, winner):
        tie_break = "phương án điểm cao hơn theo nhóm không đạt ràng buộc bắt buộc"
    return {
        "winner": winner,
        "matrix": matrix,
        "winner_by_weight_set": by_set,
        "robust": len(set(by_set.values())) == 1,
        "tie_break": tie_break,
    }


def _weighted(m: dict[str, float], weights: dict[str, float]) -> float:
    total = 0.0
    for c, w in weights.items():
        if c == "cost_vnd":
            continue  # không có thang chung để chuẩn hoá tiền khi tự tính
        value = 100 * (1 - m.get(c, 0)) if c == "p_break" else m.get(c, 0)
        total += w * value
    return round(total / 100 / max(1, sum(weights.values())) * 100, 3)


async def _route_check(
    tool: Any, plan: OptionPlan, pool: cand.Pool, req: PlanRequest
) -> tuple[dict[int, str], list[dict[str, Any]]]:
    """Đối chiếu phương án thắng với plan_day_route (đường thật Vietmap): link Maps + cảnh báo."""
    start = (
        f"{plan.hotel.lat},{plan.hotel.lng}"
        if plan.hotel
        else ",".join(str(x) for x in (pool.center or (0, 0)))
    )
    vehicle = "motorcycle" if req.transport == "xemay" else "car"
    jobs, days = [], []
    for d in plan.days:
        stops = [
            {"place": f"{s.place.lat},{s.place.lng}", "visit_minutes": s.end - s.start}
            for s in d.slots
            if s.type == "poi" and s.place
        ]
        if not stops:
            continue
        days.append(d)
        jobs.append(
            tool(
                "plan_day_route",
                start=start,
                stops=stops,
                day_start=hhmm(d.window[0]),
                day_end=hhmm(d.window[1]),
                lunch_break=True,
                vehicle=vehicle,
            )
        )
    maps, logs = {}, []
    for d, (data, log) in zip(days, await asyncio.gather(*jobs), strict=True):
        logs.append(log)
        if not cand.ok(data):
            continue
        if data.get("google_maps_url"):
            maps[d.index] = data["google_maps_url"]
        real = data.get("total_travel_min")
        ours = sum(s.travel for s in d.slots) + d.back_min
        if real and real > ours * 1.3 + 10:
            d.warnings.append(
                f"Đường thật mất khoảng {round(real)} phút di chuyển, "
                f"nhiều hơn ước tính {ours} phút"
            )
        for miss in data.get("unscheduled") or data.get("skipped") or []:
            d.warnings.append(f"plan_day_route không xếp kịp: {miss}")
    return maps, logs


# ---------------------------------------------------------------- giải thích


def _fallback_explanation(facts: dict[str, Any], error: Exception) -> Explanation:
    """Lời giải thích soạn sẵn khi không gọi được LLM — chỉ ghép lại các câu trong bản dữ kiện."""
    chosen = next(o for o in facts["phuong_an"] if o["ten"] == facts["phuong_an_duoc_chon"])
    return Explanation(
        summary=f"{facts['chuyen_di']}. Đề xuất phương án {chosen['ten']}, "
        f"tổng {chosen['tong_chi_phi']}.",
        why_winner=". ".join(facts["ly_do_chon"])
        + f". Xác suất vỡ lịch {chosen['xac_suat_vo_lich']}.",
        tips=[
            *facts["so_sanh"][:2],
            f"Chưa viết được lời giải thích chi tiết ({type(error).__name__}).",
        ],
    )


# ---------------------------------------------------------------- 10: trả lộ trình


def to_response(state: dict[str, Any], thread_id: str, seconds: float) -> PlanResponse:
    req = PlanRequest.model_validate(state["request"])
    decision = state["decision"]
    tool_log = state.get("tool_log", [])
    return PlanResponse(
        thread_id=thread_id,
        persona=req.persona,
        persona_name=PERSONAS[req.persona]["name"],
        strategy=state.get("strategy") or default_strategy(req.persona).model_dump(),
        strategy_source=state.get("strategy_source", "mac_dinh"),
        destination=req.basic.destination,
        start_date=req.basic.start_date.isoformat(),
        days=req.basic.days,
        options=state["options"],
        decision=Decision(
            **{k: v for k, v in decision.items() if k != "excluded"},
            excluded=[Excluded(**e) for e in decision.get("excluded", [])],
        ),
        explanation=Explanation(**state["explanation"]),
        assumptions=assumptions(req),
        festivals=state["context"].get("festivals", []),
        stats=Stats(
            llm_calls=state.get("llm_calls", 0),
            tool_calls=len(tool_log),
            tool_errors=sum(not t["ok"] for t in tool_log),
            seconds=round(seconds, 1),
        ),
    )


async def _log(config: RunnableConfig, node: str, kind: str, text: str, **data: Any) -> None:
    await agent_log.publish(
        config["configurable"]["thread_id"], node=node, kind=kind, text=text, **data
    )

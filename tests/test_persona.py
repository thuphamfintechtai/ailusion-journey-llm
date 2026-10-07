"""Nhóm người dùng P1–P4: khoảng tham số, chiến lược ảnh hưởng lịch, chọn phương án theo nhóm."""

from __future__ import annotations

import re

import pytest
from test_planning import pool  # noqa: F401 - fixture dùng chung

from ailusion.planning import PlanRequest
from ailusion.planning.graph import _decide
from ailusion.planning.persona import PERSONAS, Strategy, clamp, default_strategy
from ailusion.planning.planner import collect_steps
from ailusion.planning.schedule import OptionPlan, build_option, style_for
from ailusion.prompts.strategy import BY_PERSONA


def req(persona: str, **extra) -> PlanRequest:
    data = {
        "persona": persona,
        "basic": {"destination": "Đà Lạt", "start_date": "2026-10-17", "days": 2, "adults": 2},
        "mobility": "cao",
    }
    return PlanRequest.model_validate({**data, **extra})


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_prompt_ranges_match_code_bounds(persona):
    """Khoảng ghi trong prompt phải đúng khoảng code ép — sửa một bên mà quên bên kia là lỗi."""
    text = BY_PERSONA[persona]
    for field, (lo, hi) in PERSONAS[persona]["bounds"].items():
        found = re.search(rf"{field} ([\d.]+)–([\d.]+)", text)
        assert found, f"{persona}: prompt thiếu khoảng của {field}"
        a, b = (float(x.replace(".", "")) if len(x) > 4 else float(x) for x in found.groups())
        assert (a, b) == (lo, hi), f"{persona}.{field}: prompt {a}–{b}, code {lo}–{hi}"


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_default_strategy_is_inside_bounds(persona):
    s = default_strategy(persona)
    assert clamp(s, persona) == s


def test_clamp_forces_persona_character():
    wild = default_strategy("P2").model_copy(
        update={"stops_per_day": 6, "hotel_tier": 0.0, "buffer_min": 0, "avoid_crowd": False}
    )
    s = clamp(wild, "P2")
    assert (s.stops_per_day, s.hotel_tier, s.buffer_min, s.avoid_crowd) == (3, 0.4, 15, True)


def test_strategy_rejects_unknown_interest_tag():
    with pytest.raises(ValueError):
        Strategy(**{**default_strategy("P1").model_dump(), "focus_tags": ["khong_co"]})


def test_relaxed_persona_gets_fewer_stops_and_more_buffer(pool):  # noqa: F811
    busy = build_option("canbang", pool, req("P1"), strategy=default_strategy("P1").model_dump())
    calm = build_option("canbang", pool, req("P2"), strategy=default_strategy("P2").model_dump())

    def stops(plan: OptionPlan) -> int:
        return max(sum(s.type == "poi" for s in d.slots) for d in plan.days)

    assert stops(calm) <= 3 < stops(busy) or stops(calm) < stops(busy)
    calm_legs = [s.travel for d in calm.days for s in d.slots if s.travel]
    busy_legs = [s.travel for d in busy.days for s in d.slots if s.travel]
    assert min(calm_legs) >= 15 + 3  # dự phòng 25 phút của P2 cộng vào mọi chặng
    assert min(busy_legs) < min(calm_legs)


def test_form_mobility_caps_strategy():
    """Người dùng chọn nhịp nhẹ nhàng thì chiến lược P1 (5 điểm) cũng không được vượt trần."""
    style = style_for("canbang", req("P1", mobility="nhe"), default_strategy("P1").model_dump())
    assert style.slots == 3


def test_rest_break_added_after_lunch(pool):  # noqa: F811
    day = build_option("canbang", pool, req("P3"), strategy=default_strategy("P3").model_dump())
    rests = [s for d in day.days for s in d.slots if s.name == "Nghỉ ngơi giữa ngày"]
    assert rests and all(s.end - s.start == 60 for s in rests)


def test_planner_runs_strategy_searches_first():
    strategy = default_strategy("P2").model_dump()
    strategy["extra_searches"] = [{"query": "khu vui chơi trẻ em", "why": "có trẻ nhỏ"}]
    steps = collect_steps(req("P2", interests={"tags": {"vanhoa": 1}}), None, strategy)
    extra = [s for s in steps if s["key"].startswith("places:")]
    assert extra and extra[2]["args"]["query"] == "khu vui chơi trẻ em"


def test_decide_follows_persona_weight_set_among_feasible():
    def plan(key: str, ok: bool, p_break: float, cost: int) -> OptionPlan:
        p = OptionPlan(key=key, hotel=None, days=[], hard_ok=ok)
        p.risk, p.cost = {"p_break": p_break}, {"total": cost}
        return p

    plans = {
        "canbang": plan("canbang", True, 0.1, 7),
        "tietkiem": plan("tietkiem", True, 0.2, 5),
        "trainghiem": plan("trainghiem", False, 0.5, 9),
    }
    scored = {
        "status": "success",
        "rankings": {
            "nhom": [
                {"option": "trainghiem", "score": 0.9},
                {"option": "tietkiem", "score": 0.7},
                {"option": "canbang", "score": 0.4},
            ],
            "can_bang": [{"option": "canbang", "score": 0.8}, {"option": "tietkiem", "score": 0.5}],
        },
    }
    d = _decide(plans, scored, {}, PERSONAS["P1"]["weights"])
    assert d["winner"] == "tietkiem"  # trainghiem điểm cao hơn nhưng không đạt ràng buộc cứng
    assert "không đạt ràng buộc" in d["tie_break"]


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_prompt_example_is_a_valid_in_bounds_strategy(persona):
    """Ví dụ trong prompt là thứ model nhỏ hay bắt chước nhất — phải hợp lệ và đúng khoảng."""
    import json

    block = re.search(r"```json\n(.*?)\n```", BY_PERSONA[persona], re.S)
    assert block, f"{persona}: prompt thiếu ví dụ JSON"
    example = Strategy.model_validate(json.loads(block.group(1)))
    assert clamp(example, persona) == example

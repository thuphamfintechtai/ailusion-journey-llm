"""Thư mục prompts/ và bản dữ kiện cho bước giải thích — không gọi LLM."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ailusion.planning import PlanRequest
from ailusion.planning.facts import decision_facts
from ailusion.prompts import chat_system, plan_explain, research, smoke, strategy

SRC = Path(__file__).resolve().parents[1] / "src"
PROMPTS_DIR = SRC / "ailusion" / "prompts"

# prompt -> biến cần điền bằng .format()
PROMPTS = [
    (chat_system.CHAT_SYSTEM, {}),
    (plan_explain.PLAN_EXPLAIN, {}),
    (research.SLOT, {"today": "2026-10-17", "profile": "null"}),
    (research.PLAN, {"today": "2026-10-17", "catalog": "-", "max_steps": 10, "replan_rule": ""}),
    (research.REPLAN_RULE, {}),
    (research.CRITIC, {"trust": "{}"}),
    (smoke.DESTINATION, {"request": "Đi Đà Lạt"}),
    *[(prompt, {}) for prompt in strategy.BY_PERSONA.values()],
]


@pytest.mark.parametrize(("prompt", "values"), PROMPTS)
def test_prompt_formats_with_exactly_its_variables(prompt, values):
    text = prompt.format(**values) if values else prompt
    assert text.strip()
    assert not re.search(r"\{[a-z_]+\}", text), "còn biến chưa điền"


def test_missing_variable_fails_loudly():
    with pytest.raises(KeyError):
        research.SLOT.format(today="2026-10-17")


def test_no_prompt_text_outside_prompts_package():
    """Prompt viết trong ailusion/prompts/*.py, chỗ dùng chỉ import vào."""
    for path in SRC.rglob("*.py"):
        if PROMPTS_DIR in path.parents:
            continue
        code = path.read_text(encoding="utf-8")
        assert not re.search(r'^[A-Z_]*PROMPT\s*=\s*"""', code, re.M), path
        assert 'SystemMessage("' not in code and 'SystemMessage(f"' not in code, path


# ---------------------------------------------------------------- bản dữ kiện


def _option(key, name, total, p_break, p50, p90, hard_ok=True):
    day = {
        "index": 1,
        "date": "2026-10-17",
        "weekday": "Thứ Bảy",
        "weather": {
            "morning_rain_pct": 14,
            "afternoon_rain_pct": 27,
            "evening_rain_pct": 46,
            "temp_min_c": 14.4,
            "temp_max_c": 24.6,
            "warnings": [],
        },
        "items": [{"type": "poi", "name": f"Điểm của {name}"}],
    }
    cost = {"stay": 1, "food": 1, "tickets": 1, "move": 1, "intercity": 0, "reserve": 1}
    return {
        "key": key,
        "name": name,
        "description": "",
        "hotel": {"name": "Khách sạn A", "note": "900.000 đ/đêm"},
        "days": [day],
        "cost": {**cost, "total": total},
        "hard_ok": hard_ok,
        "issues": [],
        "risk": {"p_break": p_break, "p_wet": 0.86, "late_p50_min": p50, "late_p90_min": p90},
    }


@pytest.fixture
def facts():
    options = {
        "canbang": _option("canbang", "Cân bằng", 7_833_939, 0.143, 0, 9),
        "tietkiem": _option("tietkiem", "Tiết kiệm", 6_052_710, 0.175, 0, 11),
        "trainghiem": _option("trainghiem", "Trải nghiệm", 8_326_677, 0.642, 10, 25),
    }
    decision = {
        "winner": "canbang",
        "matrix": {"can_bang": {"canbang": 0.8498, "tietkiem": 0.8354, "trainghiem": 0.0195}},
        "winner_by_weight_set": {
            "can_bang": "canbang",
            "tiet_kiem": "tietkiem",
            "trai_nghiem": "canbang",
        },
        "tie_break": None,
        "binding": [],
        "excluded": [],
    }
    req = PlanRequest.model_validate(
        {"basic": {"destination": "Đà Lạt", "start_date": "2026-10-17", "days": 3, "children": 1}}
    )
    return decision_facts(req, options, decision, festivals=[])


def test_facts_state_comparisons_explicitly(facts):
    """Lỗi cũ: LLM khẳng định Cân bằng rẻ nhất. Giờ phần so sánh do code viết sẵn."""
    assert "Rẻ nhất: Tiết kiệm (6.052.710 đ)" in facts["so_sanh"]
    assert "Ít rủi ro vỡ lịch nhất: Cân bằng (14%)" in facts["so_sanh"]
    assert facts["ly_do_chon"][0].startswith("Theo 3 bộ trọng số chung: thắng 2 (cân bằng, trải")
    assert facts["nhom_nguoi_dung"].startswith("P1 Khám phá tiết kiệm")


def test_facts_name_delays_clearly(facts):
    """Lỗi cũ: gọi trễ p90 là "trễ trung bình"."""
    chosen = facts["phuong_an"][0]
    assert chosen["tre_thuong_gap"] == "0 phút"
    assert chosen["tre_khi_gap_ngay_xau"] == "9 phút"


def test_facts_weather_is_per_block_without_trip_wide_wet_probability(facts):
    """Lỗi cũ: lấy xác suất dính mưa cả chuyến (86%) làm "khả năng mưa"."""
    assert facts["thoi_tiet"][0].startswith(
        "Ngày 1 (Thứ Bảy 17/10): khả năng mưa sáng 14%, chiều 27%, tối 46%"
    )
    assert "86%" not in str(facts)

"""Prompt nằm trong file prompt riêng và điền biến đúng — không gọi LLM."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ailusion.chat import prompts as chat
from ailusion.itinerary.agents import budget, decision, experience, intake, planner

SRC = Path(__file__).resolve().parents[1] / "src" / "ailusion"
# Prompt của chat nằm trong chat/prompts.py; mỗi agent của pipeline giữ prompt trong file của nó.
PROMPT_FILES = {SRC / "chat" / "prompts.py", *(SRC / "itinerary" / "agents").glob("*.py")}

# prompt -> biến cần điền bằng .format()
PROMPTS = [
    (chat.CHAT_SYSTEM, {}),
    (chat.TRIP_STATE, {"form": "{}", "missing": "-", "instruction": chat.STATE_CHATTING}),
    (chat.STATE_OPEN_FORM_MISSING, {"missing": "số ngày"}),
    (chat.STATE_ASK_MISSING, {"missing": "số ngày"}),
    (chat.TRIP_UPDATE, {"today": "2026-10-17"}),
    (chat.TRIP_INFO, {"today": "2026-10-17"}),
    (intake.INTAKE, {}),
    (experience.EXPERIENCE, {}),
    (budget.BUDGET, {}),
    (planner.PLANNER, {}),
    (planner.PLANNER_FIX, {}),
    (decision.DECISION, {}),
]


@pytest.mark.parametrize(("prompt", "values"), PROMPTS)
def test_prompt_formats_with_exactly_its_variables(prompt, values):
    text = prompt.format(**values) if values else prompt
    assert text.strip()
    assert not re.search(r"\{[a-z_]+\}", text), "còn biến chưa điền"


def test_missing_variable_fails_loudly():
    with pytest.raises(KeyError):
        chat.TRIP_STATE.format(form="{}")


def test_no_prompt_text_outside_prompt_files():
    """Prompt chỉ viết trong chat/prompts.py và itinerary/agents/*.py, chỗ khác chỉ import vào."""
    for path in SRC.rglob("*.py"):
        if path in PROMPT_FILES:
            continue
        code = path.read_text(encoding="utf-8")
        assert not re.search(r'^[A-Z_]+\s*=\s*"""(Bạn|#)', code, re.M), path
        assert 'SystemMessage("' not in code and 'SystemMessage(f"' not in code, path

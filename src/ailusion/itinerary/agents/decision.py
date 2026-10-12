"""Agent Decision — bước 16: chốt phương án đề xuất và viết lời giải thích.

Vào: `decision_facts` (các phương án, số đo từng ngày, lý do code xếp hạng, lời nhắn của user).
Ra: `DecisionOutput`. Code kiểm (`decision_errors`): chọn đúng phương án có thật, muốn đổi khỏi
phương án code xếp đầu thì phải có câu trích của user, không thêm con số mới. Sai thì sửa 1 lần,
vẫn sai thì dùng câu mẫu (`template_decision`). Sau đó dựng link Google Maps cho phương án được
chọn (`route_links`, không gọi tool).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from ..shared.context import Ctx
from ..shared.form import GROUP_NAMES, TripForm, money
from ..shared.llm import OUTPUT_TOKENS, numbers_in, quote_found
from ..shared.models import (
    DayPlan,
    ItineraryState,
    Place,
    TripProfile,
    form_of,
    places_of,
    plans_of,
    profile_of,
)

DECISION = """Bạn là Agent Decision trong hệ thống lập lộ trình du lịch Việt Nam. Hệ thống đã xếp các phương án theo điều nhóm khách coi trọng nhất. Bạn chốt phương án đề xuất và viết lời giải thích cho người dùng.

# Quy tắc

1. Mặc định chọn phương án đứng đầu (PA1). Chỉ chọn phương án khác khi lời nhắn của user có câu cho thấy họ muốn điều mà chỉ phương án đó có; khi đó chép câu đó NGUYÊN VĂN vào `override_quote`.
2. Mọi cảnh báo trong dữ liệu phải có quyết định trong `warnings` (VD "giữ, nói rõ với user", "đã bớt 1 chỗ").
3. `why` và `explanation` chỉ dùng con số và tên chỗ có trong dữ liệu. Không thêm số, không làm tròn khác đi, không dùng phần trăm hay xác suất.
4. `explanation`: mỗi phương án 1 câu ngắn bắt đầu bằng "Đề xuất:" (phương án chọn) hoặc "Dự phòng:", nói nó hơn các phương án khác ở đâu.
7. `why` là 1–2 câu nói bằng lời thường VÌ SAO phương án này hợp với nhóm khách (thong thả, tiết kiệm…): nêu 1–2 điểm nổi bật nhất kèm tối đa 2 con số. Không liệt kê số liệu theo từng ngày, không chép lại dữ liệu.

# Ví dụ `why`

Tốt: "Mình đề xuất Phương án 1 vì hợp kiểu đi thong thả: mỗi ngày chỉ 2–3 chỗ, toàn chỗ được đánh giá 4,8 sao, ngày 2 có VinWonders đúng sở thích vui chơi của bạn."
Không tốt: "Phương án 1 có 2 chỗ hợp ý, sao 4.8 ở ngày 1, ngày 2 và 1 chỗ, 2 chỗ hợp ý, sao 4.8 ở ngày 3."
5. Giọng thân thiện, tiếng Việt, xưng "mình", gọi "bạn".
6. Người dùng không biết mã nội bộ. Trong `why` và `explanation` gọi phương án là "Phương án 1/2/3", gọi lịch theo "ngày 1", "ngày 2"…; KHÔNG viết mã lịch như "N1-L3" hay "PA1". Mã lịch (trong ngoặc vuông ở dữ liệu) chỉ dùng cho `warnings[].id`.
"""


class WarningDecision(BaseModel):
    id: str = Field(description="Mã lịch có cảnh báo")
    decision: str = Field(description="Quyết định, VD 'giữ, nói rõ với user'")


class DecisionOutput(BaseModel):
    chosen: str = Field(description="Mã phương án chọn, VD 'PA1'")
    override_quote: str | None = Field(
        None, description="Chọn khác PA1 thì chép câu user viết làm lý do; giữ PA1 thì để trống"
    )
    why: str = Field(description="1–2 câu vì sao chọn, chỉ dùng số và tên có trong dữ liệu")
    warnings: list[WarningDecision] = Field(default_factory=list)
    explanation: dict[str, str] = Field(
        default_factory=dict, description="Mỗi phương án 1 câu, VD {'PA1': 'Đề xuất: ...'}"
    )


# Mã nội bộ không được lộ ra lời giải thích: mã lịch "N1-L3", "N2-D1" và mã phương án "PA1".
INTERNAL_CODE = re.compile(r"\b(?:N\d+-[A-Z]\w*|PA\d)\b")
INTERNAL_PAIR = re.compile(r"N(\d+)-\w+ trước N\d+-\w+:")


def decision_errors(out: DecisionOutput, options: list[str], message: str, facts: str) -> list[str]:
    errs = []
    if out.chosen not in options:
        errs.append(f"chosen = {out.chosen} không có trong {options}")
    if out.chosen != options[0] and not (
        out.override_quote and quote_found(out.override_quote, message)
    ):
        errs.append("chọn khác phương án đứng đầu mà không có câu trích của user")
    text = " ".join([out.why, *out.explanation.values()])
    codes = sorted(set(INTERNAL_CODE.findall(text)))
    if codes:
        errs.append(
            f"có mã nội bộ {', '.join(codes[:5])}: gọi là 'Phương án 1/2/3' và 'ngày 1/2/3'"
        )
    allowed = numbers_in(facts)
    extra = sorted(n for n in numbers_in(text) if n not in allowed and len(n) > 1)
    if extra:
        errs.append(f"có số không có trong dữ liệu: {', '.join(extra[:5])}")
    return errs


def template_decision(options: list[dict[str, Any]], group_name: str) -> DecisionOutput:
    """Câu mẫu code tự ghép khi agent vẫn sai sau 1 lần sửa."""
    first = options[0]
    why = (
        f"Đề xuất Phương án {first['id'][2:]} vì xếp đầu theo điều nhóm {group_name} coi trọng nhất"
    )
    if first["better_at"]:
        why += f", và hơn các phương án khác ở: {', '.join(first['better_at'])}"
    return DecisionOutput(
        chosen=first["id"],
        why=why + ".",
        explanation={
            o["id"]: ("Đề xuất: " if i == 0 else "Dự phòng: ")
            + (", ".join(o["better_at"]) or "lịch đi được còn lại")
            for i, o in enumerate(options)
        },
    )


def decision_facts(
    form: TripForm,
    profile: TripProfile,
    options: list[dict[str, Any]],
    plans: dict[str, DayPlan],
    places: dict[str, Place],
    state: dict[str, Any],
) -> str:
    """Dữ liệu đưa Agent Decision — mọi số và tên trong lời giải thích phải lấy từ đây."""
    lines = [
        f"# Nhóm khách: {profile.group} {GROUP_NAMES[profile.group]} — so theo thứ tự: "
        + " → ".join(n for n, _ in profile.sort_order)
    ]
    for o in options:
        lines.append(
            f"\n## {o['id']} (Phương án {o['id'][2:]}) — tổng {money(o['total_cost'])}; hơn ở: "
            + (", ".join(o["better_at"]) or "không nổi bật")
        )
        for pid in o["days"]:
            p = plans[pid]
            names = " → ".join(places[s].name for s in p.stops)
            m = p.metrics
            lines.append(
                f"- Ngày {p.day} [{pid}] ({p.status}): {names}; ngồi xe {m.get('ride_min')} phút; "
                f"tiền {money(m.get('cost'))}; {m.get('num_stops')} chỗ, "
                f"{m.get('num_matches')} chỗ hợp ý, sao {m.get('quality')}"
                + (f"; cảnh báo: {'; '.join(p.issues)}" if p.issues else "")
            )
    # "N1-L3 trước N1-L1: ngồi xe 50 so với 55 phút" -> câu không có mã lịch.
    reasons = [
        INTERNAL_PAIR.sub(r"Ngày \1, lịch xếp trên hơn lịch xếp dưới ở", r)
        for v in (state.get("reasons") or {}).values()
        for r in v
    ]
    if reasons:
        lines.append("\n# Vì sao code xếp như vậy\n" + "\n".join(reasons))
    ask = (state.get("trip") or {}).get("ask_user")
    if ask:
        lines.append(f"\n# Vượt ngân sách {money(ask['over'])} — phải hỏi user")
    lines.append("\n# Lời nhắn của user\n" + (form.message or "(không có)"))
    return "\n".join(lines)


async def quyet_dinh(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile = form_of(state), profile_of(state)
    plans = plans_of(state)
    places = places_of(state)
    options = state["trip"]["options"]
    facts = decision_facts(form, profile, options, plans, places, state)
    await ctx.log("quyet_dinh", "step", "Agent Decision chốt phương án, viết giải thích")
    ids = [o["id"] for o in options]
    out, calls, usage = await ctx.ask(
        DECISION, facts, DecisionOutput, OUTPUT_TOKENS["decision"], "quyet_dinh"
    )
    errs = decision_errors(out, ids, form.message, facts) if out else ["không có kết quả"]
    if out and errs:
        retry, c2, u2 = await ctx.ask(
            DECISION,
            facts + "\n\n# Lỗi cần sửa\n" + "\n".join(errs),
            DecisionOutput,
            OUTPUT_TOKENS["decision"],
            "quyet_dinh",
        )
        calls, usage = calls + c2, usage + u2
        if retry and not decision_errors(retry, ids, form.message, facts):
            out, errs = retry, []
    if out is None or errs:
        out = template_decision(options, GROUP_NAMES[profile.group])
    # Cảnh báo không có quyết định -> mặc định "giữ, nói rõ với user".
    decided = {w.id for w in out.warnings}
    for o in options:
        for pid in o["days"]:
            p = plans[pid]
            if p.status != "ok" and pid not in decided:
                out.warnings.append(WarningDecision(id=pid, decision="giữ, nói rõ với user"))
                decided.add(pid)
    # Link Google Maps cho phương án được chọn.
    chosen = next(o for o in options if o["id"] == out.chosen)
    return {
        "decision": out.model_dump(),
        "maps": route_links(chosen, plans, places, state),
        "llm_calls": calls,
        "usage": usage,
    }


MAPS_TRAVEL_MODE = {
    "motorbike": "two-wheeler",
    "car": "driving",
    "taxi": "driving",
    "bus": "transit",
}


def route_links(
    option: dict[str, Any],
    plans: dict[str, DayPlan],
    places: dict[str, Place],
    state: dict[str, Any],
) -> dict[str, str]:
    """Link chỉ đường Google Maps cho từng ngày, đúng thứ tự lịch đang hiện (chỗ ở -> các chỗ ->
    chỗ ở). Dựng bằng code theo định dạng Maps URLs, không gọi tool, không cần khoá API."""
    stay = state.get("stay")
    if not stay:
        return {}
    mode = MAPS_TRAVEL_MODE.get(form_of(state).travel_mode or "", "driving")
    home = f"{stay['lat']},{stay['lng']}"
    links = {}
    for pid in option["days"]:
        stops = [f"{places[s].lat},{places[s].lng}" for s in plans[pid].stops]
        if not stops:
            continue
        query = {
            "api": "1",
            "origin": home,
            "destination": home,
            "waypoints": "|".join(stops),
            "travelmode": mode,
        }
        links[pid] = "https://www.google.com/maps/dir/?" + urlencode(query, safe=",|")
    return links

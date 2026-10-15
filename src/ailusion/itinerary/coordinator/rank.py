"""Làn Coordinator — bước 12–15: xếp hạng lịch từng ngày, ghép tối đa 3 phương án.

Toàn bộ là code, không cộng trọng số tự đặt:
- Bước 13 (`mark_beaten`): gắn nhãn lịch kém hơn mọi mặt so với một lịch khác cùng ngày.
- Bước 14 (`rank`): xếp như từ điển theo thứ tự nhóm khách coi trọng (`SORT_ORDER`).
- Bước 15 (`assemble`): có ngân sách thì ghép lịch các ngày sao cho tổng ước (vé, ăn, đi lại,
  chỗ ở, phát sinh — đúng cách màn hình tính) nằm trong ±`BUDGET_BAND` ngân sách, các ngày tiêu đều
  nhau (ngày lên thì ngày khác xuống), rồi mới tới hạng theo nhóm khách (`fit_budget`). Không có
  ngân sách / thiếu giá: PA1 = hạng 1 mọi ngày, PA2/PA3 = hạng 2, 3. Không vừa ±5% thì ghi rõ thiếu
  / vượt bao nhiêu; vượt thì `ask_user` theo mức chi, máy không tự cắt.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from ..agents.decision import INTERNAL_PAIR
from ..coordinator.simulate import EXTRA_MID
from ..shared.context import Ctx
from ..shared.form import SORT_ORDER, TripForm, money
from ..shared.math import dominates
from ..shared.models import (
    DayPlan,
    ItineraryState,
    Place,
    TripProfile,
    dump_plans,
    form_of,
    place_from_dict,
    places_of,
    plans_of,
    profile_of,
)


def compare_dims(group: str) -> list[tuple[str, str]]:
    """Mặt đem so ở bước 13: mặt nhóm coi trọng + tiền, phút ngồi xe, phút chờ, hợp ý, số sao."""
    dims = dict(SORT_ORDER[group])
    for name in ("cost", "ride_min", "wait_min"):
        dims.setdefault(name, "min")
    for name in ("num_matches", "quality"):
        dims.setdefault(name, "max")
    if "num_stops" in dims:
        dims["num_stops"] = "max" if group == "P1" else "min"
    return list(dims.items())


def _signed(plan: DayPlan, dims: list[tuple[str, str]]) -> list[float | None]:
    out: list[float | None] = []
    for name, direction in dims:
        v = plan.metrics.get(name)
        out.append(None if v is None else (-v if direction == "min" else v))
    return out


def mark_beaten(plans: list[DayPlan], group: str) -> None:
    """Bước 13: B kém hơn A nếu A không thua B ở mặt nào và hơn ít nhất 1 mặt. Không xoá B."""
    dims = compare_dims(group)
    alive = [p for p in plans if p.status != "fail"]
    for b in alive:
        for a in alive:
            if a is not b and dominates(_signed(a, dims), _signed(b, dims)):
                b.beaten_by = a.id
                break


def rank(plans: list[DayPlan], sort_order: list[tuple[str, str]]) -> list[DayPlan]:
    """Bước 14: không bị hơn hẳn trước, "kém hơn" sau, "không đi được" cuối; trong mỗi nhóm so
    như từ điển theo thứ tự nhóm coi trọng; thiếu số ở mặt đang so thì xếp sau."""

    def key(p: DayPlan) -> tuple[Any, ...]:
        band = 2 if p.status == "fail" else 1 if p.beaten_by else 0
        parts: list[Any] = [band]
        for name, direction in sort_order:
            v = p.metrics.get(name)
            if v is None:
                parts += [1, 0]
            else:
                parts += [0, v if direction == "min" else -v]
        return tuple(parts)

    return sorted(plans, key=key)


def rank_reason(a: DayPlan, b: DayPlan, sort_order: list[tuple[str, str]]) -> str | None:
    """Câu "L1 trước L2: ngồi xe 50 so với 55 phút" — mặt đầu tiên hai lịch khác nhau."""
    names = {
        "cost": ("tiền", "đ"),
        "ride_min": ("ngồi xe", "phút"),
        "num_stops": ("số chỗ", "chỗ"),
        "num_matches": ("chỗ hợp ý", "chỗ"),
        "quality": ("số sao", "sao"),
        "popularity": ("độ nổi tiếng", "review"),
        "needs_met": ("tiện nghi", "mục"),
    }
    for name, _ in sort_order:
        x, y = a.metrics.get(name), b.metrics.get(name)
        if x is not None and y is not None and x != y:
            label, unit = names.get(name, (name, ""))
            return f"{a.id} trước {b.id}: {label} {_fmt(x)} so với {_fmt(y)} {unit}".strip()
    return None


def _fmt(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}".replace(",", ".")
    return f"{v:.1f}".replace(".", ",")


def stay_cost(stay: Place | None, form: TripForm) -> int | None:
    """Tiền chỗ ở cả đoàn: giá 1 đêm × số đêm × số phòng (2 người 1 phòng)."""
    if stay is None or form.stay_booked:
        return 0 if form.stay_booked else None
    if form.num_nights == 0:
        return 0
    rooms = math.ceil(form.group_size / 2)
    return stay.price * form.num_nights * rooms if stay.price else None


BUDGET_BAND = 0.05  # tổng ước lệch ngân sách tối đa ±5% (user yêu cầu)
CANDIDATES_PER_DAY = 5  # số lịch tốt nhất mỗi ngày đem ghép (5^7 = 78k tổ hợp cho 7 ngày)


def trip_estimate(days: list[DayPlan], s_cost: int | None, form: TripForm) -> int | None:
    """Tổng ước cả chuyến, cả đoàn: (tiền các ngày + chỗ ở) × (1 + phát sinh) + vé đã mua."""
    spends = [p.metrics.get("spend") for p in days]
    if s_cost is None or any(v is None for v in spends):
        return None
    core = sum(spends) + s_cost  # type: ignore[arg-type]
    return int(round(core * (1 + EXTRA_MID) / 1000) * 1000) + form.prepaid_tickets * form.group_size


def fit_budget(
    ranked: dict[int, list[DayPlan]],
    s_cost: int | None,
    form: TripForm,
    budget: int,
    places_kind: dict[str, str],
    want: int = 3,
) -> list[tuple[list[DayPlan], int]]:
    """Tối đa `want` tổ hợp lịch (mỗi ngày 1 lịch) sát ngân sách nhất. Thứ tự so: ít chỗ chơi lặp
    giữa các ngày → tổng lệch ngân sách ngoài ±5% ít nhất → các ngày tiêu đều (lệch so với phần
    chia đều ngoài ±5% ít nhất) → tổng hạng các lịch nhỏ nhất (điều nhóm khách coi trọng)."""
    days = sorted(ranked)
    cand: dict[int, list[DayPlan]] = {}
    for d in days:
        ok = [p for p in ranked[d] if p.status != "fail" and p.metrics.get("spend") is not None]
        if not ok:
            return []
        cand[d] = ok[:CANDIDATES_PER_DAY]
    fixed = (s_cost or 0) + form.prepaid_tickets * form.group_size / (1 + EXTRA_MID)
    per_day = (budget / (1 + EXTRA_MID) - fixed) / len(days)
    scored = []
    for combo in itertools.product(*(cand[d] for d in days)):
        total = trip_estimate(list(combo), s_cost, form)
        if total is None:
            continue
        seen: set[str] = set()
        repeats = 0
        for p in combo:
            repeats += sum(s in seen and places_kind.get(s) != "meal" for s in p.stops)
            seen |= set(p.stops)
        off = max(0.0, abs(total - budget) / budget - BUDGET_BAND)
        spread = (
            max(abs(p.metrics["spend"] - per_day) for p in combo) / per_day  # type: ignore[operator]
            if per_day > 0
            else 0.0
        )
        ranks = sum(cand[d].index(p) for d, p in zip(days, combo, strict=True))
        key = (repeats, round(off, 3), round(max(0.0, spread - BUDGET_BAND), 2), ranks)
        scored.append((key, list(combo), total))
    scored.sort(key=lambda t: t[0])
    return [(combo, total) for _key, combo, total in scored[:want]]


def budget_note(estimate: int | None, budget: int | None) -> str | None:
    """Tổng ước không vừa ±5% ngân sách: nói rõ lệch bao nhiêu (vượt thì bước 15 hỏi user)."""
    if estimate is None or not budget:
        return None
    gap = estimate - budget
    if abs(gap) <= budget * BUDGET_BAND:
        return None
    pct = round(abs(gap) / budget * 100)
    if gap < 0:
        return (
            f"Lộ trình ước khoảng {money(estimate)}, thấp hơn ngân sách {money(budget)} "
            f"khoảng {pct}%: điểm đến chưa đủ chỗ đúng nhu cầu để dùng hết ngân sách. Có thể nâng "
            "chỗ ở, thêm ngày hoặc thêm hoạt động có phí."
        )
    return f"Lộ trình ước khoảng {money(estimate)}, vượt ngân sách {money(budget)} khoảng {pct}%."


def assemble(
    ranked: dict[int, list[DayPlan]],
    stay: Place | None,
    form: TripForm,
    profile: TripProfile,
    places_kind: dict[str, str] | None = None,
) -> dict[str, Any]:
    """3 phương án: PA1 = lịch hạng 1 mỗi ngày, PA2 = hạng 2, PA3 = hạng 3 (ngày ít lịch hơn thì
    dùng lịch đi được tốt nhất còn lại). Trong một phương án không đi lại chỗ đã đi ở ngày trước
    nếu còn lịch khác. Vượt ngân sách thì đưa user 2 lựa chọn theo mức chi.

    `places_kind`: place_id -> "visit" | "meal", để ưu tiên không lặp chỗ chơi hơn quán ăn.
    """
    places_kind = places_kind or {}
    s_cost = stay_cost(stay, form)
    options = []
    budget = profile.trip_budget
    fitted = (
        fit_budget(ranked, s_cost, form, budget, places_kind)
        if budget and form.spend_style != "unlimited"
        else []
    )
    for k, (days_fit, est) in enumerate(fitted):
        day_costs = [p.metrics.get("cost") for p in days_fit]
        total = None
        if all(c is not None for c in day_costs) and s_cost is not None:
            total = int(sum(day_costs) + s_cost + form.prepaid_tickets * form.group_size)  # type: ignore[arg-type]
        options.append({"id": f"PA{k + 1}", "days": days_fit, "total_cost": total, "estimate": est})
    for k in range(0 if not fitted else 3, 3):
        days: list[DayPlan] = []
        visited: set[str] = set()
        for _day, plans in sorted(ranked.items()):
            usable = [p for p in plans if p.status != "fail"] or plans
            start = min(k, len(usable) - 1)
            # Lấy lịch hạng k nếu nó không đi lại chỗ đã có ở ngày trước; không thì lịch lặp ít
            # nhất — trước hết ít chỗ chơi lặp, rồi ít quán lặp, hoà thì theo hạng (từ hạng k).
            order = usable[start:] + usable[:start]
            pick = min(
                order,
                key=lambda p: (
                    sum(s in visited and places_kind.get(s) != "meal" for s in p.stops),
                    sum(s in visited for s in p.stops),
                    order.index(p),
                ),
            )
            days.append(pick)
            visited |= set(pick.stops)
        day_costs = [p.metrics.get("cost") for p in days]
        total = None
        if all(c is not None for c in day_costs) and s_cost is not None:
            total = int(sum(day_costs) + s_cost + form.prepaid_tickets * form.group_size)  # type: ignore[arg-type]
        options.append(
            {
                "id": f"PA{k + 1}",
                "days": days,
                "total_cost": total,
                "estimate": trip_estimate(days, s_cost, form),
            }
        )

    # Bỏ phương án trùng hệt phương án trước (ngày nào cũng chỉ có 1 lịch đi được).
    unique, seen = [], set()
    for o in options:
        sig = tuple(p.id for p in o["days"])
        if sig not in seen:
            seen.add(sig)
            unique.append(o)

    ask = None
    best = unique[0]
    measured = best["estimate"] if best.get("estimate") is not None else best["total_cost"]
    limit = budget * (1 + BUDGET_BAND) if budget is not None and best.get("estimate") else budget
    if (
        budget is not None
        and measured is not None
        and limit is not None
        and measured > limit
        and form.spend_style != "unlimited"
    ):
        cheapest_days = []
        for day, plans in sorted(ranked.items()):
            ok = [p for p in plans if p.status != "fail" and p.metrics.get("cost") is not None]
            cheapest_days.append(
                min(ok, key=lambda p: p.metrics["cost"]) if ok else best["days"][day - 1]
            )
        ask = {
            "over": measured - budget,
            "spend_style": form.spend_style,
            "cheaper_first": profile.group == "P1",
            "cheaper_days": [p.id for p in cheapest_days],
        }
    note = budget_note(best.get("estimate"), budget) if form.spend_style != "unlimited" else None
    return {
        "options": unique,
        "stay_cost": s_cost,
        "trip_budget": budget,
        "ask_user": ask,
        "budget_note": note,
    }


def better_at(options: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Mỗi phương án hơn 2 cái kia ở đâu (cộng các ngày)."""
    labels = {
        "ride_min": ("ít ngồi xe nhất", "min"),
        "cost": ("rẻ nhất", "min"),
        "num_matches": ("nhiều chỗ đúng sở thích nhất", "max"),
        "quality": ("chỗ được đánh giá tốt nhất", "max"),
        "num_stops": ("đi được nhiều chỗ nhất", "max"),
        "popularity": ("nhiều chỗ nổi tiếng nhất", "max"),
    }
    totals: dict[str, dict[str, float]] = {}
    for o in options:
        for name in labels:
            values = [p.metrics.get(name) for p in o["days"]]
            if all(v is not None for v in values):
                totals.setdefault(name, {})[o["id"]] = (
                    sum(values) / len(values) if name in ("quality", "popularity") else sum(values)  # type: ignore[arg-type]
                )
    out: dict[str, list[str]] = {o["id"]: [] for o in options}
    for name, (label, direction) in labels.items():
        row = totals.get(name, {})
        if len(row) < 2:
            continue
        best = min(row.values()) if direction == "min" else max(row.values())
        winners = [k for k, v in row.items() if v == best]
        if len(winners) == 1:
            out[winners[0]].append(label)
    return out


async def xep_hang(state: ItineraryState, ctx: Ctx) -> dict:
    profile = profile_of(state)
    plans = plans_of(state)
    ranked: dict[str, list[str]] = {}
    reasons: dict[str, list[str]] = {}
    for day in sorted({p.day for p in plans.values()}):
        day_plans = [p for p in plans.values() if p.day == day]
        mark_beaten(day_plans, profile.group)
        order = rank(day_plans, profile.sort_order)
        ranked[str(day)] = [p.id for p in order]
        reasons[str(day)] = [
            r
            for a, b in zip(order, order[1:], strict=False)
            if (r := rank_reason(a, b, profile.sort_order))
        ][:3]
    await ctx.log(
        "xep_hang",
        "finding",
        f"Xếp hạng theo điều nhóm {profile.group} coi trọng: "
        # Nhật ký hiện cho user xem: "Ngày 1: tiền 400.000 so với …", không lộ mã lịch.
        + "; ".join(INTERNAL_PAIR.sub(r"Ngày \1:", r) for v in reasons.values() for r in v[:1]),
    )
    return {
        "plans": dump_plans(plans),
        "ranked": ranked,
        "reasons": reasons,
    }


async def ghep(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile = form_of(state), profile_of(state)
    plans = plans_of(state)
    ranked = {int(d): [plans[i] for i in ids] for d, ids in state["ranked"].items()}
    stay = place_from_dict(state["stay"]) if state.get("stay") else None
    kinds = {pid: p.kind for pid, p in places_of(state).items()}
    trip = assemble(ranked, stay, form, profile, kinds)
    labels = better_at(trip["options"])
    serial = {
        "options": [
            {
                "id": o["id"],
                "days": [p.id for p in o["days"]],
                "total_cost": o["total_cost"],
                "estimate": o.get("estimate"),
                "better_at": labels[o["id"]],
            }
            for o in trip["options"]
        ],
        "stay_cost": trip["stay_cost"],
        "trip_budget": trip["trip_budget"],
        "ask_user": trip["ask_user"],
    }
    text = " · ".join(
        f"{o['id']} ~{money(o['estimate'] if o.get('estimate') is not None else o['total_cost'])}"
        for o in serial["options"]
    )
    if trip["trip_budget"]:
        text += f" (ngân sách {money(trip['trip_budget'])}, cho phép ±{int(BUDGET_BAND * 100)}%)"
    await ctx.log(
        "ghep",
        "finding",
        text + (" · vượt ngân sách, hỏi user" if trip["ask_user"] else ""),
    )
    notes = list(state.get("notes") or [])
    if trip["budget_note"]:
        notes.append(trip["budget_note"])
    return {"trip": serial, "notes": notes}

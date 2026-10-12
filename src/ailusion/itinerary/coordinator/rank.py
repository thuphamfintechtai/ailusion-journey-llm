"""Làn Coordinator — bước 12–15: xếp hạng lịch từng ngày, ghép tối đa 3 phương án.

Toàn bộ là code, không cộng trọng số tự đặt:
- Bước 13 (`mark_beaten`): gắn nhãn lịch kém hơn mọi mặt so với một lịch khác cùng ngày.
- Bước 14 (`rank`): xếp như từ điển theo thứ tự nhóm khách coi trọng (`SORT_ORDER`).
- Bước 15 (`assemble`): PA1 = hạng 1 mọi ngày, PA2/PA3 = đổi ngày có lịch hạng 2 gần nhất; kiểm
  ngân sách theo mức chi — vượt thì `ask_user`, máy không tự cắt.
"""

from __future__ import annotations

import math
from typing import Any

from ..agents.decision import INTERNAL_PAIR
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
    for k in range(3):
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
        options.append({"id": f"PA{k + 1}", "days": days, "total_cost": total})

    # Bỏ phương án trùng hệt phương án trước (ngày nào cũng chỉ có 1 lịch đi được).
    unique, seen = [], set()
    for o in options:
        sig = tuple(p.id for p in o["days"])
        if sig not in seen:
            seen.add(sig)
            unique.append(o)

    budget = profile.trip_budget
    ask = None
    best = unique[0]
    if (
        budget is not None
        and best["total_cost"] is not None
        and best["total_cost"] > budget
        and form.spend_style != "unlimited"
    ):
        cheapest_days = []
        for day, plans in sorted(ranked.items()):
            ok = [p for p in plans if p.status != "fail" and p.metrics.get("cost") is not None]
            cheapest_days.append(
                min(ok, key=lambda p: p.metrics["cost"]) if ok else best["days"][day - 1]
            )
        ask = {
            "over": best["total_cost"] - budget,
            "spend_style": form.spend_style,
            "cheaper_first": profile.group == "P1",
            "cheaper_days": [p.id for p in cheapest_days],
        }
    return {"options": unique, "stay_cost": s_cost, "trip_budget": budget, "ask_user": ask}


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
                "better_at": labels[o["id"]],
            }
            for o in trip["options"]
        ],
        "stay_cost": trip["stay_cost"],
        "trip_budget": trip["trip_budget"],
        "ask_user": trip["ask_user"],
    }
    text = " · ".join(f"{o['id']} {money(o['total_cost'])}" for o in serial["options"])
    await ctx.log(
        "ghep",
        "finding",
        text + (" · vượt ngân sách, hỏi user" if trip["ask_user"] else ""),
    )
    return {"trip": serial}

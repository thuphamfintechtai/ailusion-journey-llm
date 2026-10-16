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
from ..shared.context import Ctx
from ..shared.form import (
    BUDGET_BAND,
    EXTRA_MID,
    SORT_ORDER,
    TripForm,
    budget_band,
    money,
    trip_budget,
)
from ..shared.math import dominates, km
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
from .score import STAY_MAX_KM


def compare_dims(group: str) -> list[tuple[str, str]]:
    """Mặt đem so ở bước 13: mặt nhóm coi trọng + tiền, phút ngồi xe, phút chờ, hợp ý, số sao."""
    dims = dict(SORT_ORDER[group])
    for name in ("cost", "ride_min", "wait_min"):
        dims.setdefault(name, "min")
    for name in ("num_matches", "quality"):
        dims.setdefault(name, "max")
    if "num_stops" in dims:
        dims["num_stops"] = "max" if group in ("P1", "P5") else "min"
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


CANDIDATES_PER_DAY = 5  # số lịch tốt nhất mỗi ngày đem ghép (5^7 = 78k tổ hợp cho 7 ngày)


def money_band(form: TripForm) -> tuple[int | None, int | None] | None:
    """Khoảng ngân sách các luật tiền dùng (shared/form.budget_band); "không giới hạn" = None."""
    return None if form.spend_style == "unlimited" else budget_band(form)


def net_of_prepaid(total: int, form: TripForm) -> int:
    """Tổng ước bỏ vé đi về đã mua: ngân sách (`budget_band`, `trip_budget`) đã trừ vé đó, so
    tổng ước còn vé là tính vé 2 lần."""
    return total - form.prepaid_tickets * form.group_size


def band_gap(total: int, band: tuple[int | None, int | None], form: TripForm) -> int:
    """Tổng ước (đã bỏ vé đã mua) nằm ngoài khoảng ngân sách bao nhiêu: âm = thấp hơn mức dưới,
    dương = vượt mức trên, 0 = trong khoảng."""
    low, high = band
    cmp = net_of_prepaid(total, form)
    if low and cmp < low:
        return cmp - low
    if high and cmp > high:
        return cmp - high
    return 0


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
    band: tuple[int | None, int | None] | None,
    places_kind: dict[str, str],
    want: int = 3,
    shared: set[str] | None = None,
    wanted: dict[str, str] | None = None,
    avoid: set[str] | None = None,
) -> list[tuple[list[DayPlan], int | None]]:
    """Tối đa `want` phương án (mỗi ngày 1 lịch), KHÁC NHAU thật sự.

    PA1: ít chỗ chơi lặp giữa các ngày → tổng ước nằm ngoài khoảng ngân sách (`band`: ±5% số user
    nhập, khoảng user chọn, hoặc không vượt khi "sang trong ngân sách") ít nhất → các ngày tiêu
    đều → tổng hạng các lịch nhỏ nhất (điều nhóm khách coi trọng). PA2, PA3: cùng thứ tự, nhưng
    sau luật ngân sách thì ưu tiên tổ hợp có ít chỗ chơi trùng với các phương án đã chọn nhất,
    để 3 phương án cho 3 lựa chọn khác nhau chứ không chỉ lệch 1 lịch. `shared`: chỗ phương án nào
    cũng phải có (user chọn), không tính là trùng. `band` None: bỏ qua luật tiền.
    `wanted`: place_id -> thứ user nhắc (VD "bún bò"); phương án thiếu thứ nào xếp sau (ngay sau
    luật không lặp chỗ, trước luật tiền). `avoid`: chỗ chơi phương án trước (vòng trước) đã dùng;
    phương án đầu tiên trả về cũng ưu tiên ít trùng với chúng (ngay sau luật ngân sách)."""
    shared = shared or set()
    wanted = wanted or {}
    asked = set(wanted.values())
    days = sorted(ranked)
    cand: dict[int, list[DayPlan]] = {}
    for d in days:
        ok = [
            p
            for p in ranked[d]
            if p.status != "fail" and (band is None or p.metrics.get("spend") is not None)
        ]
        if not ok:
            return []
        cand[d] = ok[:CANDIDATES_PER_DAY]
    per_day = 0.0
    ref = trip_budget(form) if band else None  # mốc (đã bỏ vé đã mua) để chia tiền mỗi ngày
    if ref:
        per_day = (ref / (1 + EXTRA_MID) - (s_cost or 0)) / len(days)
    rows = []
    for combo in itertools.product(*(cand[d] for d in days)):
        total = trip_estimate(list(combo), s_cost, form)
        if band and total is None:
            continue
        seen: set[str] = set()
        repeats = meal_repeats = 0
        for p in combo:
            repeats += sum(s in seen and places_kind.get(s) != "meal" for s in p.stops)
            meal_repeats += sum(s in seen and places_kind.get(s) == "meal" for s in p.stops)
            seen |= set(p.stops)
        visits = {s for s in seen if places_kind.get(s) != "meal" and s not in shared}
        missing = len(asked - {wanted[s] for s in seen if s in wanted})
        off = abs(band_gap(total, band, form)) / ref if band and ref and total else 0.0
        spread = (
            max(abs(p.metrics["spend"] - per_day) for p in combo) / per_day  # type: ignore[operator]
            if per_day > 0
            else 0.0
        )
        ranks = sum(cand[d].index(p) for d, p in zip(days, combo, strict=True))
        rows.append(
            {
                "combo": list(combo),
                "total": total,
                "visits": visits,
                # Quán lặp giữa các ngày (VD cùng 1 quán ngày 1 và ngày 2): xét sau luật tiền.
                "base": (repeats, missing, round(off, 3), meal_repeats),
                "rest": (round(max(0.0, spread - BUDGET_BAND), 2), ranks),
            }
        )
    if not rows:
        return []
    avoid = (avoid or set()) - shared

    def avoided(r: dict[str, Any]) -> float:
        return round(len(r["visits"] & avoid) / max(1, len(r["visits"])), 2)

    picked = [min(rows, key=lambda r: (r["base"], avoided(r), r["rest"]))]
    while len(picked) < want:
        used = avoid.union(*(r["visits"] for r in picked))
        sigs = {tuple(p.id for p in r["combo"]) for r in picked}
        left = [r for r in rows if tuple(p.id for p in r["combo"]) not in sigs]
        if not left:
            break

        def overlap(r: dict[str, Any], used: set[str] = used) -> float:
            return round(len(r["visits"] & used) / max(1, len(r["visits"])), 2)

        picked.append(min(left, key=lambda r: (r["base"], overlap(r), r["rest"])))
    return [(r["combo"], r["total"]) for r in picked]


def budget_note(estimate: int | None, form: TripForm) -> str | None:
    """Tổng ước nằm ngoài khoảng ngân sách: nói rõ lệch bao nhiêu (vượt thì bước 15 hỏi user).
    User nhập 1 con số thì so với con số đó; chọn khoảng thì so với mức dưới / mức trên."""
    band = money_band(form)
    if estimate is None or band is None:
        return None
    gap = band_gap(estimate, band, form)
    if gap == 0:
        return None
    low, high = band
    ranged = form.budget_min is not None
    bound = (low if gap < 0 else high) if ranged else trip_budget(form)
    if not bound:
        return None
    cmp = net_of_prepaid(estimate, form)
    pct = round(abs(cmp - bound) / bound * 100)
    what = (
        ("mức dưới của ngân sách" if gap < 0 else "mức trên của ngân sách")
        if ranged
        else ("ngân sách")
    )
    if gap < 0:
        return (
            f"Lộ trình ước khoảng {money(estimate)}, thấp hơn {what} {money(bound)} khoảng {pct}%: "
            "điểm đến chưa đủ chỗ đúng nhu cầu để dùng hết ngân sách."
        )
    return f"Lộ trình ước khoảng {money(estimate)}, vượt {what} {money(bound)} khoảng {pct}%."


def band_text(form: TripForm) -> str | None:
    """Khoảng ngân sách để ghi nhật ký / lời giải thích."""
    band = money_band(form)
    if band is None:
        return None
    low, high = band
    if form.budget_min is not None:
        if low and high:
            return f"ngân sách {money(low)}–{money(high)}"
        return f"ngân sách từ {money(low)}" if low else f"ngân sách tối đa {money(high)}"
    if form.spend_style == "luxury_within_budget":
        return f"ngân sách {money(high)}, không vượt"
    return f"ngân sách {money(trip_budget(form))}, cho phép ±{int(BUDGET_BAND * 100)}%"


def assemble(
    ranked: dict[int, list[DayPlan]],
    stay: Place | None,
    form: TripForm,
    profile: TripProfile,
    places_kind: dict[str, str] | None = None,
    shared: set[str] | None = None,
    wanted: dict[str, str] | None = None,
) -> dict[str, Any]:
    """3 phương án: PA1 = lịch hạng 1 mỗi ngày, PA2 = hạng 2, PA3 = hạng 3 (ngày ít lịch hơn thì
    dùng lịch đi được tốt nhất còn lại). Trong một phương án không đi lại chỗ đã đi ở ngày trước
    nếu còn lịch khác. Vượt ngân sách thì đưa user 2 lựa chọn theo mức chi.

    `places_kind`: place_id -> "visit" | "meal", để ưu tiên không lặp chỗ chơi hơn quán ăn.
    """
    places_kind = places_kind or {}
    s_cost = stay_cost(stay, form)
    options = []
    fitted = fit_budget(
        ranked, s_cost, form, money_band(form), places_kind, shared=shared, wanted=wanted
    )
    for k, (days_fit, est) in enumerate(fitted):
        options.append(
            {
                "id": f"PA{k + 1}",
                "days": days_fit,
                "total_cost": total_cost(days_fit, s_cost, form),
                "estimate": est if est is not None else trip_estimate(days_fit, s_cost, form),
            }
        )
    for k in range(0 if not fitted else 3, 3):
        days = by_rank(ranked, k, places_kind)
        options.append(
            {
                "id": f"PA{k + 1}",
                "days": days,
                "total_cost": total_cost(days, s_cost, form),
                "estimate": trip_estimate(days, s_cost, form),
            }
        )
    return finish_trip(options, ranked, s_cost, form, profile)


def by_rank(
    ranked: dict[int, list[DayPlan]],
    k: int,
    places_kind: dict[str, str],
    avoid: set[str] | None = None,
) -> list[DayPlan]:
    """Mỗi ngày lịch hạng k nếu nó không đi lại chỗ đã có ở ngày trước (hay ở `avoid`); không thì
    lịch lặp ít nhất — trước hết ít chỗ chơi lặp, rồi ít quán lặp, hoà thì theo hạng (từ hạng k)."""
    days: list[DayPlan] = []
    visited: set[str] = set(avoid or ())
    for _day, plans in sorted(ranked.items()):
        usable = [p for p in plans if p.status != "fail"] or plans
        start = min(k, len(usable) - 1)
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
    return days


def total_cost(days: list[DayPlan], s_cost: int | None, form: TripForm) -> int | None:
    day_costs = [p.metrics.get("cost") for p in days]
    if any(c is None for c in day_costs) or s_cost is None:
        return None
    return int(sum(day_costs) + s_cost + form.prepaid_tickets * form.group_size)  # type: ignore[arg-type]


def finish_trip(
    options: list[dict[str, Any]],
    ranked: dict[int, list[DayPlan]],
    s_cost: int | None,
    form: TripForm,
    profile: TripProfile,
) -> dict[str, Any]:
    """Bỏ phương án trùng, kiểm ngân sách của phương án đầu (vượt thì hỏi user theo mức chi)."""
    budget = profile.trip_budget
    # Bỏ phương án trùng hệt phương án trước (ngày nào cũng chỉ có 1 lịch đi được).
    unique, seen = [], set()
    for o in options:
        sig = tuple(p.id for p in o["days"])
        if sig not in seen:
            seen.add(sig)
            unique.append(o)

    ask = None
    best = unique[0]
    band = money_band(form)
    measured = best["estimate"] if best.get("estimate") is not None else best["total_cost"]
    over = band_gap(measured, band, form) if band and measured is not None else 0
    if over > 0:
        # Lịch rẻ nhất mỗi ngày theo tiền cả ngày (`spend`: vé + ăn + đi lại), không chỉ vé + ăn:
        # lịch vé rẻ mà đi xa có thể tốn hơn.
        cheapest_days = []
        for day, plans in sorted(ranked.items()):
            ok = [p for p in plans if p.status != "fail" and day_money(p) is not None]
            cheapest_days.append(min(ok, key=day_money) if ok else best["days"][day - 1])
        ask = {
            "over": over,
            "spend_style": form.spend_style,
            "cheaper_first": profile.group == "P1",
            "cheaper_days": [p.id for p in cheapest_days],
        }
    note = budget_note(best.get("estimate"), form)
    return {
        "options": unique,
        "stay_cost": s_cost,
        "trip_budget": budget,
        "ask_user": ask,
        "budget_note": note,
    }


UPGRADE_STAYS = 2  # gợi ý tối đa ngần này chỗ ở tốt hơn khi lịch tiêu thấp hơn ngân sách
UPGRADE_VISITS = 3  # và ngần này chỗ chơi có phí chưa có trong lịch


def money_notes(
    state: dict[str, Any], best: dict[str, Any] | None, form: TripForm, profile: TripProfile
) -> list[str]:
    """Ghi chú tiền ngoài lệch ngân sách (ngân sách thấp so với mặt bằng giá của điểm đến: thông
    báo riêng ở đầu kết quả, coordinator/levels.py):
    - bao nhiêu phần tổng ước là giá ƯỚC (chưa có giá thật) — điểm đến ít giá thật thì tổng ước
      chủ yếu là số tự đặt;
    - lịch tiêu thấp hơn mức dưới ngân sách: gợi ý cụ thể chỗ ở tốt hơn (vừa phần tiền còn dư) và
      chỗ chơi có phí chưa có trong lịch, thay vì chỉ nói "chưa đủ chỗ"."""
    out: list[str] = []
    n = form.group_size
    if best is None or best.get("estimate") is None:
        return out
    places = places_of(state)
    stops = [s for p in best["days"] for s in p.stops if s in places]
    guessed = sum(
        (places[s].price or 0) * n for s in stops if (places[s].price_by or "").startswith("ước")
    )
    if guessed and best["estimate"]:
        pct = round(guessed / best["estimate"] * 100)
        if pct:
            out.append(f"Khoảng {pct}% tổng ước dựa trên giá ước (chỗ chưa có giá thật).")
    band = money_band(form)
    gap = band_gap(best["estimate"], band, form) if band else 0
    if gap >= 0:
        return out
    spare = -gap / (1 + EXTRA_MID)  # tiền còn dư so với mức dưới, trước phát sinh
    hints = []
    stay = place_from_dict(state["stay"]) if state.get("stay") else None
    if stay and stay.price and not form.stay_booked and form.num_nights:
        rooms = -(-n // 2)
        up_to = stay.price + spare / form.num_nights / rooms
        center = state.get("center")
        better = sorted(
            (
                place_from_dict(s)
                for s in (state.get("stays") or {}).values()
                if s.get("price")
                and stay.price < s["price"] <= up_to
                # Chỗ ở bước chọn chỗ ở đã bỏ vì xa trung tâm thì không gợi ý lại (từng gợi ý
                # "nâng chỗ ở: Laguna Lăng Cô" cho chuyến Huế, ngay dưới dòng "bỏ … hơn 30 km").
                and (not center or km(tuple(center), (s["lat"], s["lng"])) <= STAY_MAX_KM)
            ),
            key=lambda s: -(s.rating_floor or 0),
        )[:UPGRADE_STAYS]
        if better:
            hints.append(
                "nâng chỗ ở: " + ", ".join(f"{s.name} ({money(s.price)}/đêm)" for s in better)
            )
    used = set(stops)
    paid = sorted(
        (
            p
            for p in places.values()
            if p.kind == "visit"
            and p.place_id not in used
            and p.price
            and not (p.price_by or "").startswith("ước")
            and p.price * n <= spare
        ),
        key=lambda p: -(p.reviews or 0),
    )[:UPGRADE_VISITS]
    if paid:
        hints.append(
            "thêm hoạt động có phí: "
            + ", ".join(f"{p.name} ({money(p.price)}/người)" for p in paid)
        )
    if hints:
        out.append("Còn dư ngân sách, có thể " + "; ".join(hints) + ".")
    return out


def day_money(p: DayPlan) -> float | None:
    """Tiền cả ngày để so lịch rẻ / đắt: `spend` (vé + ăn + đi lại), thiếu thì `cost` (vé + ăn)."""
    v = p.metrics.get("spend")
    return v if v is not None else p.metrics.get("cost")


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


def pick_option(
    state: dict[str, Any], k: int, avoid: set[str]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]:
    """Phương án k từ lịch của nhánh k (state riêng của nhánh, đã qua bước xếp hạng): vừa ngân
    sách ±5%, ít trùng chỗ chơi với `avoid` (chỗ của phương án đã chọn + chỗ dành cho nhánh khác).
    Trả (phương án, lịch của nhánh với mã "V{k}-…", chỗ phương án dùng)."""
    form = form_of(state)
    stay = place_from_dict(state["stay"]) if state.get("stay") else None
    places = places_of(state)
    kinds = {pid: p.kind for pid, p in places.items()}
    musts = {pid for pid, p in places.items() if p.must}
    wanted = {pid: p.wanted for pid, p in places.items() if p.wanted and not p.must}
    plans = plans_of(state)
    ranked = {int(d): [plans[i] for i in ids] for d, ids in state["ranked"].items()}
    s_cost = stay_cost(stay, form)
    fitted = fit_budget(
        ranked,
        s_cost,
        form,
        money_band(form),
        kinds,
        want=1,
        shared=musts,
        wanted=wanted,
        avoid=avoid,
    )
    days, est = fitted[0] if fitted else (by_rank(ranked, 0, kinds, avoid=avoid), None)
    prefix = f"V{k}-"
    kept = {prefix + pid: {**d, "id": prefix + pid} for pid, d in dump_plans(plans).items()}
    option = {
        "id": f"PA{k}",
        "days": [prefix + p.id for p in days],
        "total_cost": total_cost(days, s_cost, form),
        "estimate": est if est is not None else trip_estimate(days, s_cost, form),
    }
    return option, kept, {s for p in days for s in p.stops if s not in musts}


async def ghep(state: ItineraryState, ctx: Ctx) -> dict:
    """Bước 15: ghép phương án của các nhánh, kiểm ngân sách của phương án đầu."""
    form, profile = form_of(state), profile_of(state)
    stay = place_from_dict(state["stay"]) if state.get("stay") else None
    plans = plans_of({"plans": state.get("kept_plans") or {}})
    options = [{**o, "days": [plans[i] for i in o["days"]]} for o in state.get("options") or []]
    ranked = {
        int(d): [plans[i] for i in ids] for d, ids in (state.get("first_ranked") or {}).items()
    }
    trip = finish_trip(options, ranked, stay_cost(stay, form), form, profile)
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
    if band := band_text(form):
        text += f" ({band})"
    await ctx.log(
        "ghep",
        "finding",
        text + (" · vượt ngân sách, hỏi user" if trip["ask_user"] else ""),
    )
    notes = list(state.get("notes") or [])
    if trip["budget_note"]:
        notes.append(trip["budget_note"])
    notes += money_notes(state, trip["options"][0] if trip["options"] else None, form, profile)
    return {
        "trip": serial,
        # Các vòng ghi lại cùng một ghi chú (VD chỗ ở, ngày ghim bị dời): giữ 1 lần.
        "notes": list(dict.fromkeys(notes)),
        "plans": dump_plans(plans),
        "ranked": state.get("first_ranked") or {},
        "reasons": state.get("first_reasons") or state.get("reasons") or {},
    }

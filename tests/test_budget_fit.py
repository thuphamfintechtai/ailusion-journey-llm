"""Tổng ước cả chuyến bám ngân sách ±5%, các ngày tiêu đều (user yêu cầu)."""

from __future__ import annotations

from typing import Any

from ailusion.itinerary.agents.planner import day_target
from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.coordinator.rank import BUDGET_BAND, assemble, budget_note, trip_estimate
from ailusion.itinerary.shared.form import TripForm
from ailusion.itinerary.shared.models import DayPlan, Place


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Hội An",
        "start_date": "2026-10-22",
        "end_date": "2026-10-24",
        "group_size": 2,
        "budget": 5_000_000,
        "budget_unit": "group_trip",
        "priority": "saving",
    }
    data.update(over)
    return TripForm.model_validate(data)


def _plan(day: int, pid: str, spend: int, stops: list[str]) -> DayPlan:
    return DayPlan(
        id=f"N{day}-{pid}",
        day=day,
        leave_stay="08:00",
        stops=stops,
        metrics={"cost": spend // 2, "spend": spend},
    )


def _ranked(levels: list[int]) -> dict[int, list[DayPlan]]:
    # Mỗi ngày có các lịch tiêu `levels`, xếp hạng rẻ trước (như nhóm tiết kiệm xếp).
    return {
        d: [_plan(d, f"L{i}", v, [f"d{d}-{i}"]) for i, v in enumerate(sorted(levels))]
        for d in (1, 2, 3)
    }


STAY = Place(
    place_id="h", name="Homestay", category="x", kind="stay", lat=15.88, lng=108.33, price=400_000
)


def _within(total: int, budget: int) -> bool:
    return abs(total - budget) <= budget * BUDGET_BAND


def test_saving_group_still_uses_the_budget_not_a_third_of_it() -> None:
    form = _form()
    profile = profile_trip(form, [], [])
    trip = assemble(_ranked([300_000, 600_000, 900_000, 1_200_000, 1_500_000]), STAY, form, profile)
    best = trip["options"][0]
    assert _within(best["estimate"], 5_000_000), best["estimate"]
    # Hạng 1 mỗi ngày (rẻ nhất) chỉ khoảng 2 triệu — cách cũ — thì không đạt.
    cheapest = trip_estimate([_ranked([300_000])[d][0] for d in (1, 2, 3)], 1_600_000, form)
    assert cheapest is not None and cheapest < 5_000_000 * (1 - BUDGET_BAND)
    assert trip["budget_note"] is None and trip["ask_user"] is None


def test_comfortable_group_does_not_go_far_over_the_budget() -> None:
    form = _form(budget=7_000_000, priority="comfort")
    profile = profile_trip(form, [], [])
    # Nhóm đỡ mệt xếp lịch đắt (ít ngồi xe) lên đầu: cách cũ ra ~10 triệu.
    ranked = {
        d: list(reversed(v))
        for d, v in _ranked([500_000, 900_000, 1_300_000, 1_800_000, 2_400_000]).items()
    }
    trip = assemble(ranked, STAY, form, profile)
    assert _within(trip["options"][0]["estimate"], 7_000_000)


def test_days_spend_evenly_and_options_differ() -> None:
    form = _form()
    trip = assemble(
        _ranked([300_000, 600_000, 900_000, 1_200_000, 1_500_000]),
        STAY,
        form,
        profile_trip(form, [], []),
    )
    spends = [p.metrics["spend"] for p in trip["options"][0]["days"]]
    assert max(spends) - min(spends) <= 300_000  # không dồn tiền vào 1 ngày
    assert (
        len({tuple(p.id for p in o["days"]) for o in trip["options"]}) == len(trip["options"]) == 3
    )


def test_not_enough_places_to_spend_the_budget_is_said_plainly() -> None:
    note = budget_note(2_000_000, 5_000_000)
    assert note and "thấp hơn ngân sách" in note and "60%" in note
    assert budget_note(5_100_000, 5_000_000) is None  # trong ±5%: không nói gì
    assert "vượt" in (budget_note(10_000_000, 7_000_000) or "")


def test_planner_gets_an_even_daily_target() -> None:
    form = _form()
    state = {
        "form": form.model_dump(mode="json"),
        "profile": profile_trip(form, [], []).model_dump(mode="json"),
        "stay": {"price": 400_000},
    }
    # 5.000.000 ÷ 1,125 − 400.000 × 2 đêm × 1 phòng = 3.644.444 → chia 3 ngày ≈ 1.215.000
    assert day_target(state) == 1_215_000
    lux = {**state, "form": _form(spend_style="unlimited").model_dump(mode="json")}
    assert day_target(lux) is None


def _diverse_ranked() -> dict[int, list[DayPlan]]:
    # Mỗi ngày 4 lịch cùng mức tiền; lịch "a" và "b" chỉ lệch 1 chỗ, lịch "c", "d" khác hẳn.
    out = {}
    for d in (1, 2, 3):
        out[d] = [
            _plan(d, "a", 1_000_000, [f"x{d}", f"y{d}", "must"]),
            _plan(d, "b", 1_000_000, [f"x{d}", f"z{d}", "must"]),
            _plan(d, "c", 1_000_000, [f"p{d}", f"q{d}", "must"]),
            _plan(d, "d", 1_000_000, [f"r{d}", f"s{d}", "must"]),
        ]
    return out


def test_three_options_visit_different_places() -> None:
    from ailusion.itinerary.coordinator.rank import fit_budget

    form = _form(budget=None)
    kinds = {f"{c}{d}": "visit" for c in "xyzpqrs" for d in (1, 2, 3)} | {"must": "visit"}
    picked = fit_budget(_diverse_ranked(), 0, form, None, kinds, shared={"must"})
    visits = [{s for p in combo for s in p.stops} - {"must"} for combo, _ in picked]
    assert len(picked) == 3
    # PA2, PA3 không trùng chỗ chơi nào với PA1 (còn lịch khác hẳn để chọn); "must" không tính.
    assert not visits[0] & visits[1] and not visits[0] & visits[2] and not visits[1] & visits[2]


def test_planner_may_not_just_reorder_the_same_places() -> None:
    from ailusion.itinerary.agents.planner import PlanDraft, PlannerOutput, plan_errors

    out = PlannerOutput(
        days=[
            {
                "day": 1,
                "plans": [
                    PlanDraft(id="L1", leave_stay="08:00", stops=["D1", "Q1", "D2"]),
                    PlanDraft(
                        id="L2", leave_stay="08:00", stops=["D2", "D1", "Q2"]
                    ),  # đổi thứ tự / quán
                    PlanDraft(id="L3", leave_stay="08:00", stops=["D3", "Q1"]),
                ],
            }
        ]
    )
    allowed = {1: ["D1", "D2", "D3", "Q1", "Q2"]}
    errors = plan_errors(out, {"D1", "D2", "D3", "Q1", "Q2"}, {1: "08:00"}, None, allowed)
    assert errors[1] == [
        "L2: cùng bộ chỗ chơi với một lịch khác (chỉ đổi thứ tự / quán), đổi ít nhất 1 chỗ chơi"
    ]
    few = {1: ["D1", "D2", "Q1", "Q2"]}  # ngày chỉ có 2 chỗ chơi: không bắt (không đổi được)
    assert 1 not in plan_errors(
        PlannerOutput(
            days=[
                {
                    "day": 1,
                    "plans": out.days[0].plans[:2]
                    + [PlanDraft(id="L3", leave_stay="08:00", stops=["D1"])],
                }
            ]
        ),
        {"D1", "D2", "Q1", "Q2"},
        {1: "08:00"},
        None,
        few,
    )

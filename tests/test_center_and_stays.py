"""Toạ độ điểm đến trùng tên, chỗ ở xa / vượt ngân sách, ngày không còn chỗ nào."""

from __future__ import annotations

from typing import Any

from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.coordinator.score import STAY_MAX_KM, usable_stays
from ailusion.itinerary.coordinator.search import checked_center
from ailusion.itinerary.coordinator.simulate import check_plan
from ailusion.itinerary.shared.form import TripForm
from ailusion.itinerary.shared.models import DayPlan, Place

HOI_AN = (15.8801, 108.3380)
HO_TRAM = (10.4870, 107.4960)


def _p(pid: str, at: tuple[float, float], kind: str = "visit", **kw: Any) -> Place:
    return Place(place_id=pid, name=pid, category="x", kind=kind, lat=at[0], lng=at[1], **kw)


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Hội An",
        "start_date": "2026-10-22",
        "end_date": "2026-10-24",
        "group_size": 2,
        "budget": 5_000_000,
        "priority": "saving",
    }
    data.update(over)
    return TripForm.model_validate(data)


def test_wrong_geocode_is_replaced_by_where_the_places_are() -> None:
    places = {f"q{i}": _p(f"q{i}", (HOI_AN[0] + i / 1000, HOI_AN[1])) for i in range(6)}
    center, note = checked_center(list(HO_TRAM), places, "Hội An")
    assert center and abs(center[0] - HOI_AN[0]) < 0.01 and note and "trùng tên" in note
    same, none = checked_center(list(HOI_AN), places, "Hội An")
    assert same == list(HOI_AN) and none is None  # geocode đúng: giữ nguyên
    few = {"a": _p("a", HOI_AN)}
    assert checked_center(list(HO_TRAM), few, "Hội An") == (
        list(HO_TRAM),
        None,
    )  # ít chỗ: chưa đủ tin


def test_far_and_over_budget_stays_are_dropped() -> None:
    stays = [
        _p("Sala Hồ Tràm", HO_TRAM, kind="stay", price=485_000),
        _p("Resort sang", HOI_AN, kind="stay", price=4_000_000),
        _p("Homestay phố cổ", HOI_AN, kind="stay", price=400_000),
    ]
    form = _form()
    kept, notes = usable_stays(stays, list(HOI_AN), form, trip_budget=5_000_000)
    assert [s.name for s in kept] == ["Homestay phố cổ"]
    assert any(f"hơn {STAY_MAX_KM} km" in n for n in notes) and any("vượt 50%" in n for n in notes)
    # Mức chi "không giới hạn": không chặn theo tiền.
    lux, _ = usable_stays(
        stays, list(HOI_AN), _form(spend_style="unlimited"), trip_budget=5_000_000
    )
    assert {s.name for s in lux} == {"Resort sang", "Homestay phố cổ"}


def test_a_day_without_any_place_is_not_reported_as_fine() -> None:
    form = _form()
    profile = profile_trip(form, [], [])
    plan = DayPlan(id="N2-D1", day=2, leave_stay="08:00", stops=[])
    reason = check_plan(plan, {}, {}, profile.days[1], profile, form)
    assert plan.status == "fail" and reason and "không còn chỗ nào" in reason

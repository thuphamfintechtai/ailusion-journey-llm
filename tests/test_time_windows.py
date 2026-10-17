"""Phần "Khung giờ" của form: giờ đến / giờ về / giờ dậy / giờ về tối, nghỉ trưa, lịch bận.

Các ca từng hỏng: lịch bận giữa ngày (dòng thời gian không nhảy qua khung bận, lịch nào cũng "chạm
giờ bận"), ngày gần như không có giờ rảnh (tới nơi 19:45 / rời chỗ ở 08:00 để về: lịch trống bị báo
"dữ liệu thiếu"), giờ về sát bữa tối (về trước 18:30: bữa tối bắt buộc mà không xếp được).
"""

from __future__ import annotations

from typing import Any

from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.coordinator.score import capacity, idle_days
from ailusion.itinerary.coordinator.simulate import (
    check_plan,
    drop_bad_meals,
    meal_time_issues,
    simulate,
)
from ailusion.itinerary.shared.form import (
    BUSY_TRAVEL_MIN,
    TripForm,
    required_meals,
    skipped_meals,
    to_minutes,
)
from ailusion.itinerary.shared.models import STAY, DayPlan, DayWindow, Place

OPEN = {d: (6 * 60, 23 * 60) for d in range(1, 4)}


def _place(pid: str, stay: int = 60) -> Place:
    meal = pid.startswith("M")
    return Place(
        place_id=pid,
        name=pid,
        category="Nhà hàng" if meal else "Bảo tàng",
        kind="meal" if meal else "visit",
        lat=16.0,
        lng=108.2,
        hours=OPEN,
        stay_low=45 if meal else stay,
        stay_high=45 if meal else stay,
    )


PLACES = {k: _place(k) for k in ("M1", "M2", "M3", "A", "B", "C", "D", "E", "F")}
MATRIX = {(a, b): 10.0 for a in [STAY, *PLACES] for b in [STAY, *PLACES] if a != b}


def _window(start: str = "08:00", end: str = "20:45", **over: Any) -> DayWindow:
    return DayWindow(day=2, day_start=start, day_end=end, free_minutes=600, **over)


def _run(window: DayWindow, stops: list[str], leave: str = "08:00") -> Any:
    plan = DayPlan(id="L", day=2, leave_stay=leave, stops=stops)
    return simulate(plan, PLACES, MATRIX, window, [], long_stay=False)


def _hhmm(run: Any, pid: str) -> str:
    start = next(s.start for s in run.stops if s.place_id == pid)
    return f"{start // 60:02d}:{start % 60:02d}"


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Đà Nẵng",
        "start_date": "2026-10-24",
        "end_date": "2026-10-26",
        "group_size": 2,
        "group_type": "couple",
        "budget": 8_000_000,
        "night_return_time": "20:45",
        "nap": "none",
    }
    data.update(over)
    return TripForm.model_validate(data)


# ---------------------------------------------------------------- lịch bận
def test_busy_afternoon_is_inserted_and_the_day_goes_on_after_it() -> None:
    # Bận 14:00–16:00 (khung chặn 13:45–16:00 đã gồm đường tới chỗ bận): chỗ D không kịp xong
    # trước giờ bận -> đi bận trước, 16:00 + 15 phút đường mới tới D.
    run = _run(_window(blocked_slots=["13:45–16:00"]), ["M1", "A", "B", "M2", "C", "D", "E", "M3"])
    assert run.issues == []
    assert run.busy == [(5, to_minutes("13:45"), to_minutes("16:00"))]
    assert _hhmm(run, "D") == "16:15"
    assert next(s.travel for s in run.stops if s.place_id == "D") == BUSY_TRAVEL_MIN


def test_busy_mid_morning_keeps_breakfast_first() -> None:
    run = _run(_window(blocked_slots=["09:45–12:00"]), ["M1", "A", "M2", "B", "C", "D", "E", "M3"])
    assert run.issues == []
    assert _hhmm(run, "M1") == "08:10" and _hhmm(run, "A") == "12:15"


def test_busy_after_the_last_stop_sets_the_return_time() -> None:
    # Đi buổi sáng rồi bận tới tối (không có giờ về trễ): về sau giờ xong việc bận.
    window = _window(start="08:00", end="20:45", blocked_slots=["17:45–21:00"])
    run = _run(window, ["M1", "A", "M2", "B", "C"])
    assert run.busy == [(5, to_minutes("17:45"), to_minutes("21:00"))]
    assert run.back == to_minutes("21:00") + BUSY_TRAVEL_MIN
    assert not any("quá giờ" in i for i in run.issues)
    # Bận xong sớm (16:00) mà không ăn tối: vẫn là thiếu bữa (còn rảnh tới 20:45).
    free_after = _run(_window(blocked_slots=["13:45–16:00"]), ["M1", "A", "B", "M2", "C"])
    assert any("thiếu bữa tối" in i for i in free_after.issues)


def test_no_nap_when_busy_starts_during_it() -> None:
    # Ăn trưa 11:30–12:15, nghỉ trưa sẽ tới 13:55 mà 13:15 đã phải đi bận: không nghỉ, đi bận.
    window = _window(nap=True, nap_at="stay", blocked_slots=["13:15–15:00"])
    run = _run(window, ["M1", "A", "B", "M2", "C", "M3"])
    assert _hhmm(run, "M2") == "11:30"
    assert run.rest is None and run.busy == [(4, to_minutes("13:15"), to_minutes("15:00"))]
    assert _hhmm(run, "C") == "15:15" and run.issues == []
    # Không có giờ bận thì vẫn nghỉ trưa như cũ.
    assert _run(_window(nap=True, nap_at="stay"), ["M1", "A", "B", "M2", "C", "M3"]).rest


def test_busy_already_running_when_leaving_is_still_an_error() -> None:
    # Rời chỗ ở 09:00 giữa khung bận 08:30–10:00: không chèn được, chỗ trong khung bận là lỗi.
    run = _run(_window(blocked_slots=["08:30–10:00"]), ["A", "M2"], leave="09:00")
    assert any("chạm giờ bận" in i for i in run.issues)


def test_busy_outside_the_day_window_is_dropped() -> None:
    # Bận 07:00–09:00 mà tới nơi buổi chiều: trước đây ra khung ngược "14:45–09:00".
    form = _form(
        arrival_time="afternoon",
        busy_slots=[{"day": 1, "busy_start": "07:00", "busy_end": "09:00"}],
    )
    day1 = profile_trip(form).days[0]
    assert day1.blocked_slots == [] and day1.free_minutes == 360
    kept = profile_trip(_form(busy_slots=[{"day": 2, "busy_start": "14:00", "busy_end": "16:00"}]))
    assert kept.days[1].blocked_slots == ["13:45–16:00"]


# ---------------------------------------------------------------- bữa bắt buộc
def test_meal_required_only_when_the_shortest_meal_fits() -> None:
    assert "tối" not in required_meals("08:00", "18:30")  # 18:00–18:30: 30 phút, bữa cần 45
    assert "tối" in required_meals("08:00", "19:00")
    assert required_meals("08:00", "20:45") == ["sáng", "trưa", "tối"]  # 08:00 vẫn bắt ăn sáng
    assert "Không xếp bữa tối: ngày kết thúc 18:30" in skipped_meals("08:00", "18:30")
    # Tới nơi 19:45: trước đây không xếp bữa tối mà không ghi gì.
    assert "Không xếp bữa tối: ngày bắt đầu 19:45" in skipped_meals("19:45", "20:45")


def test_last_meal_that_cannot_fit_is_dropped_not_reported_missing() -> None:
    # Về chỗ ở trước 18:45: ăn tối 18:00–18:45 + 10 phút về là trễ -> bỏ bữa tối, ghi chú.
    window = _window(end="18:45")
    plan = DayPlan(
        id="L", day=2, leave_stay="08:00", stops=["M1", "A", "B", "M2", "C", "D", "E", "F", "M3"]
    )
    assert drop_bad_meals(plan, PLACES, MATRIX, window, []) == 1 and "M3" not in plan.stops
    run = simulate(plan, PLACES, MATRIX, window, [], long_stay=False)
    assert not any("thiếu bữa tối" in i for i in run.issues)
    assert any("không đủ giờ ăn tối" in u for u in run.unchecked)
    # Ngày còn đủ giờ mà không ăn tối: vẫn là lỗi.
    full = _window(end="20:45")
    assert any(
        "thiếu bữa tối" in i for i in meal_time_issues(plan, run.stops, PLACES, 17 * 60, full)
    )


# ---------------------------------------------------------------- ngày không đủ giờ
def test_day_without_free_time_gets_no_places_and_an_empty_plan_is_fine() -> None:
    evening = DayWindow(day=1, day_start="19:45", day_end="20:45", free_minutes=60)
    full = DayWindow(day=2, day_start="08:00", day_end="20:45", free_minutes=675)
    last = DayWindow(day=3, day_start="08:00", day_end="08:00", free_minutes=0)
    caps = capacity([evening, full, last], 60, 20)
    assert caps == [0, 8, 0]
    assert idle_days([evening, full, last], caps, PLACES) == [1, 3]
    # Ngày có bữa bắt buộc (11:30–12:30, ăn trưa) không phải ngày trống dù không chứa chỗ chơi.
    lunch = DayWindow(day=3, day_start="11:30", day_end="12:30", free_minutes=60)
    assert idle_days([lunch], capacity([lunch], 60, 20), PLACES) == []
    form = _form(arrival_time="evening")
    profile = profile_trip(form)
    empty = DayPlan(id="L", day=1, leave_stay="19:45", stops=[])
    assert check_plan(empty, PLACES, MATRIX, profile.days[0], profile, form) is None
    assert empty.status == "ok"
    # Ngày trọn vẹn mà lịch trống: vẫn là lỗi (dữ liệu thiếu thật).
    lazy = DayPlan(id="L", day=2, leave_stay="08:00", stops=[])
    assert "không còn chỗ nào hợp lệ" in (
        check_plan(lazy, PLACES, MATRIX, profile.days[1], profile, form) or ""
    )

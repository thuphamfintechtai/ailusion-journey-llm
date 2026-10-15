"""Mỗi ngày đủ bữa theo giờ rảnh (sáng / trưa / tối) và đêm nào cũng có chỗ ngủ."""

from __future__ import annotations

from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.coordinator.simulate import check_plan, meal_time_issues
from ailusion.itinerary.shared.form import TripForm, required_meals
from ailusion.itinerary.shared.models import DayPlan, DayWindow


def test_meals_follow_when_the_day_starts_and_ends() -> None:
    assert required_meals("09:00", "21:00") == ["sáng", "trưa", "tối"]  # đi 9h: phải ăn sáng
    assert required_meals("09:30", "21:00") == ["sáng", "trưa", "tối"]
    assert required_meals("10:00", "21:00") == ["trưa", "tối"]  # đi 10h: không cần bữa sáng
    assert required_meals("13:00", "21:00") == ["trưa", "tối"]  # đi 13h vẫn kịp bữa trưa
    assert required_meals("14:00", "21:00") == ["tối"]  # đi chiều: chỉ bữa tối
    assert required_meals("08:00", "18:15") == ["sáng", "trưa"]  # về trước 18:30: không bữa tối
    assert required_meals("08:00", "12:30") == ["sáng", "trưa"]  # ngày cuối về trưa


def test_missing_meal_is_reported_but_a_party_counts_as_dinner() -> None:
    day = DayWindow(day=2, day_start="08:00", day_end="21:00", free_minutes=780)
    plan = DayPlan(id="L1", day=2, leave_stay="08:00", stops=[])
    issues = meal_time_issues(plan, [], {}, 21 * 60, day)
    assert [i.split(" (")[0] for i in issues] == [
        "thiếu bữa sáng",
        "thiếu bữa trưa",
        "thiếu bữa tối",
    ]
    party = day.model_copy(update={"meal_only_slots": ["16:45–21:00"]})  # dự tiệc từ 16:45
    issues = meal_time_issues(plan, [], {}, 21 * 60, party)
    assert not any("bữa tối" in i for i in issues)


def test_every_night_needs_a_place_to_sleep() -> None:
    form = TripForm.model_validate(
        {
            "destination": "Huế",
            "start_date": "2026-10-22",
            "end_date": "2026-10-24",
            "group_size": 2,
        }
    )
    profile = profile_trip(form, [], [])
    night = DayPlan(id="N1", day=1, leave_stay="11:00", stops=[])
    check_plan(night, {}, {}, profile.days[0], profile, form, has_stay=False)
    assert night.status == "fail" and any("chỗ ngủ" in i for i in night.issues)
    last = DayPlan(id="N3", day=3, leave_stay="08:00", stops=[])
    check_plan(last, {}, {}, profile.days[2], profile, form, has_stay=False)
    assert not any("chỗ ngủ" in i for i in last.issues)  # ngày cuối không ngủ lại

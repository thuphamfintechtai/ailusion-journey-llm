"""Nhu cầu bắt buộc ở phần "Đi cùng ai": nhu cầu ăn uống (món chay, ghế cao cho bé...) chỉ xét ở
quán; nhu cầu khác (lối xe lăn, chỗ đỗ ô tô) xét mọi chỗ. Không gọi mạng."""

from __future__ import annotations

from ailusion.itinerary.agents.planner import need_label, needs_line
from ailusion.itinerary.coordinator.search import place_calls
from ailusion.itinerary.shared.form import meets_needs, needs_for, unknown_needs
from ailusion.itinerary.shared.models import Place

NEEDS = ["vegetarian", "high_chair", "wheelchair", "car_parking"]


def _place(kind: str, **status: str) -> Place:
    return Place(
        place_id="p", name="Chỗ", category="", kind=kind, lat=16.0, lng=108.2, need_status=status
    )


def test_meal_needs_only_count_for_restaurants() -> None:
    assert needs_for("meal", NEEDS) == NEEDS
    assert needs_for("visit", NEEDS) == ["wheelchair", "car_parking"]
    assert needs_for("stay", NEEDS) == ["wheelchair", "car_parking"]
    beach = _place("visit", wheelchair="yes", car_parking="yes")
    assert meets_needs(beach.kind, beach.need_status, NEEDS)
    assert unknown_needs(beach.kind, beach.need_status, NEEDS) == []
    shop = _place("meal", vegetarian="yes", wheelchair="yes", car_parking="yes")
    assert not meets_needs(shop.kind, shop.need_status, NEEDS)
    assert unknown_needs(shop.kind, shop.need_status, NEEDS) == ["high_chair"]


def test_planner_sees_which_need_each_place_meets() -> None:
    shop = _place("meal", vegetarian="yes", wheelchair="no")
    assert need_label(shop, NEEDS) == (
        " | đáp ứng: có món chay | không có: lối vào cho xe lăn"
        " | chưa rõ: ghế cao cho bé, có chỗ đỗ ô tô"
    )
    assert need_label(_place("visit"), ["vegetarian"]) == ""
    assert needs_line(NEEDS) == (
        "# Đoàn cần (bắt buộc): có món chay, ghế cao cho bé (quán Q…); "
        "lối vào cho xe lăn, có chỗ đỗ ô tô (mọi chỗ)"
    )
    assert needs_line([]) is None


def test_search_by_needs_finds_restaurants_and_places_separately() -> None:
    calls = place_calls({"destination": "Huế"}, [], "", NEEDS)
    by_needs = [a["needs"] for _, a in calls if a.get("needs")]
    assert by_needs == [NEEDS, ["wheelchair", "car_parking"]]
    # Chỉ nhu cầu ăn uống: một lượt tìm quán, không lượt tìm chỗ chơi theo nhu cầu.
    only_food = place_calls({"destination": "Huế"}, [], "", ["vegan"])
    assert [a["needs"] for _, a in only_food if a.get("needs")] == [["vegan"]]

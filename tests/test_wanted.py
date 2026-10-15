"""Thứ user nhắc trong lời nhắn (VD "bún bò") phải vào lộ trình, kể cả khi ô sở thích đã điền."""

from __future__ import annotations

from typing import Any

from ailusion.itinerary.agents.intake import Extracted, Extraction, apply_extracted
from ailusion.itinerary.coordinator.rank import fit_budget
from ailusion.itinerary.shared.form import TripForm
from ailusion.itinerary.shared.models import DayPlan, Place, wanted_match, wanted_words


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Huế",
        "start_date": "2026-10-22",
        "end_date": "2026-10-23",
        "group_size": 2,
        "interests": "Chụp ảnh, check-in",
        "message": "có bún  bò ngon",
    }
    data.update(over)
    return TripForm.model_validate(data)


def test_message_is_added_to_filled_interests_not_dropped() -> None:
    form = _form()
    out = Extraction(items=[Extracted(field="interests", value="bún bò", quote="bún  bò ngon")])
    new, kept = apply_extracted(form, out, sent={"interests", "destination"})
    assert new.interests == "Chụp ảnh, check-in, bún bò"  # cộng thêm, không ghi đè
    assert kept and kept[0]["field"] == "interests"
    again, _ = apply_extracted(new, out, sent={"interests"})
    assert again.interests == new.interests  # không thêm trùng


def test_only_specific_asks_count_and_match_without_accents() -> None:
    assert wanted_words("Chụp ảnh, check-in, bún bò, Biển") == ["bún bò"]
    quan = Place(
        place_id="q",
        name="Bún Bò Huế O Cương Chú Điệp",
        category="Nhà hàng",
        kind="meal",
        lat=16.4,
        lng=107.6,
    )
    other = Place(
        place_id="x", name="Bùn Lầy Cafe", category="Quán cà phê", kind="visit", lat=16.4, lng=107.6
    )
    assert wanted_match(quan, ["bún bò"]) == "bún bò"
    assert wanted_match(other, ["bún bò"]) is None


def test_options_without_the_asked_dish_rank_last() -> None:
    def plan(day: int, pid: str, stops: list[str]) -> DayPlan:
        return DayPlan(
            id=f"N{day}-{pid}",
            day=day,
            leave_stay="08:00",
            stops=stops,
            metrics={"cost": 0, "spend": 0},
        )

    ranked = {
        1: [plan(1, "L1", ["dai-noi", "com-hen"]), plan(1, "L2", ["dai-noi", "bun-bo"])],
        2: [plan(2, "L1", ["lang", "che"]), plan(2, "L2", ["chua", "che"])],
    }
    kinds = {"com-hen": "meal", "bun-bo": "meal", "che": "meal"}
    picked = fit_budget(ranked, 0, _form(), None, kinds, wanted={"bun-bo": "bún bò"})
    first = [s for p in picked[0][0] for s in p.stops]
    assert "bun-bo" in first  # hạng 1 ngày 1 không có bún bò nhưng phương án đề xuất phải có


def test_free_text_needs_become_need_codes_and_keep_chosen_ones() -> None:
    from ailusion.itinerary.agents.intake import ExtractedNeed

    form = _form(
        message="", needs_note="nhà mình ăn chay, cần chỗ đỗ ô tô", required_needs=["kids"]
    )
    out = Extraction(
        required_needs=[
            ExtractedNeed(code="vegetarian", quote="ăn chay"),
            ExtractedNeed(code="car_parking", quote="cần chỗ đỗ ô tô"),
            ExtractedNeed(code="spa", quote="ăn chay"),  # mã lạ: bỏ
            ExtractedNeed(code="wifi", quote="có wifi"),  # câu không có: bỏ
        ]
    )
    new, kept = apply_extracted(form, out, sent={"required_needs"})
    assert new.required_needs == ["kids", "vegetarian", "car_parking"]
    assert [k["value"] for k in kept] == ["có món chay", "có chỗ đỗ ô tô"]


def test_many_places_priority_prefers_more_stops() -> None:
    from ailusion.itinerary.coordinator.profile import profile_trip

    profile = profile_trip(_form(priority="many_places"), [], [])
    assert profile.group == "P5" and profile.sort_order[0] == ("num_stops", "max")


def test_purpose_keyword_needs_a_whole_word():
    from ailusion.itinerary.coordinator.filter import keyword_match

    def place(name: str, category: str) -> Place:
        return Place(place_id=name, name=name, category=category, kind="visit", lat=16.0, lng=108.2)

    assert keyword_match(place("Sân bóng đá Làng Đại Học", "Sân bóng đá"), ["culture"]) is None
    assert keyword_match(place("Làng cổ Phước Tích", "Điểm du lịch"), ["culture"]) == "làng cổ"
    assert keyword_match(place("Barber King", "Tiệm cắt tóc"), ["fun"]) is None
    assert keyword_match(place("Sky Bar 36", "Bar"), ["fun"]) == "bar"


def test_kind_follows_the_category_before_the_name():
    from ailusion.itinerary.shared.tools import kind_of

    assert kind_of("Chùa phật giáo", "Chùa Quán Thế Âm") == "visit"
    assert kind_of("Nhà hàng", "Khói Garden") == "meal"
    assert kind_of("", "Quán bún bò Bà Diệu") == "meal"


def test_trip_budget_is_not_rounded_through_per_day():
    from ailusion.itinerary.shared.form import trip_budget

    form = TripForm(
        destination="Đà Nẵng",
        start_date="2026-10-22",
        end_date="2026-10-24",
        group_size=2,
        budget=8_000_000,
    )
    assert trip_budget(form) == 8_000_000
    assert (
        trip_budget(form.model_copy(update={"budget_unit": "person_day", "budget": 1_000_000}))
        == 6_000_000
    )


def test_numeric_category_codes_fall_back_to_the_name():
    from ailusion.itinerary.shared.tools import kind_of

    assert kind_of("4004, 4004-2", "Bún Bò Huế Gốc Bún") == "meal"  # mã loại của Vietmap
    assert kind_of("4004, 4004-2", "Bãi Đá Ông Địa") == "visit"

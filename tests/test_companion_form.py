"""Phần "Đi cùng ai" của form: loại nhóm khớp số người, số trẻ em / người từ 65 / xe lăn, suy loại
nhóm khi user chưa chọn, số phòng, ô "Nhu cầu khác"."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ailusion.api.app import form_error_text
from ailusion.itinerary.agents.intake import foods_in_note
from ailusion.itinerary.coordinator.profile import QUESTIONS, profile_trip, questions_for
from ailusion.itinerary.shared.form import TripForm, rooms_for


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Đà Nẵng",
        "start_date": "2026-10-24",
        "end_date": "2026-10-26",
        "group_size": 2,
        "budget": 10_000_000,
        "night_return_time": "20:45",
        "purposes": ["relax"],
    }
    data.update(over)
    return TripForm.model_validate(data)


def test_group_and_counts_must_match_the_number_of_people() -> None:
    # Trước đây "Một mình" mà 3 người vẫn lập (ghi "hợp đi một mình", tính 2 phòng); 3 bé trong
    # đoàn 2 người chỉ tính tiền 2 người.
    cases = [
        (
            {"group_type": "solo", "group_size": 3},
            'Đi một mình nhưng số người là 3: sửa "Số người" hoặc "Đi cùng ai"',
        ),
        (
            {"group_type": "family", "group_size": 1},
            'Gia đình nhưng số người là 1: sửa "Số người" hoặc "Đi cùng ai"',
        ),
        (
            {"group_type": "family", "group_size": 2, "kids_under_6": 3},
            "Số trẻ em và người từ 65 tuổi (3) nhiều hơn số người (2)",
        ),
        (
            {"group_type": "solo", "group_size": 1, "kids_under_6": 1},
            "Có 1 trẻ em thì số người (1) phải gồm cả người lớn đi cùng",
        ),
        (
            {"group_type": "family", "group_size": 2, "wheelchair_users": 3},
            "Số người ngồi xe lăn (3) nhiều hơn số người (2)",
        ),
    ]
    for over, text in cases:
        with pytest.raises(ValidationError) as err:
            _form(**over)
        assert form_error_text(err.value.errors()) == text
    # Hợp lệ: 2 cặp đôi đi chung; ông bà đưa cháu đi (người từ 65 là người lớn đi cùng).
    _form(group_type="couple", group_size=4)
    _form(group_type="family", group_size=3, seniors_65=2, kids_under_6=1)


def test_group_is_inferred_from_the_counts_and_not_asked_again() -> None:
    # 1 người: một mình; có trẻ em / người từ 65: gia đình — trước đây vẫn hỏi "Bạn đi cùng ai?",
    # bấm "Gia đình có trẻ nhỏ" còn ghi đè 2 bé user nhập thành 1.
    alone = _form(group_size=1)
    assert "H1" not in questions_for(alone, profile_trip(alone))
    assert "solo_dining" in profile_trip(alone).nice_to_have_needs
    kids = _form(group_size=4, kids_under_6=2)
    assert "H1" not in questions_for(kids, profile_trip(kids))
    unknown = _form(group_size=3)
    assert "H1" in questions_for(unknown, profile_trip(unknown))
    # Hỏi H1 thì form ≥ 2 người: "Một mình" sửa luôn số người, form không bị từ chối.
    solo = dict(QUESTIONS["H1"]["choices"])["Một mình"]
    assert _form(**{**solo}).group_size == 1


def test_kids_under_6_share_the_parents_room() -> None:
    # Trước đây gia đình 2 người lớn + 2 bé dưới 6 tuổi tính 2 phòng.
    assert rooms_for(_form(group_type="family", group_size=4, kids_under_6=2)) == 1
    assert rooms_for(_form(group_type="family", group_size=4, kids_6_12=2)) == 2
    assert rooms_for(_form(group_type="friends", group_size=5)) == 3
    assert rooms_for(_form(group_type="solo", group_size=1)) == 1


def test_allergy_in_the_needs_note_is_not_a_food_to_have() -> None:
    # "bé dị ứng hải sản" từng thành món lịch PHẢI có (tìm quán hải sản riêng).
    assert foods_in_note("bé dị ứng hải sản") == []
    assert foods_in_note("mẹ không ăn được hành, muốn ăn bún bò") == ["bún bò"]
    assert foods_in_note("ít cay") == [] and foods_in_note("thịt nướng") == ["thịt nướng"]

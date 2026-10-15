"""Lấy thông tin chuyến đi từ cuộc trò chuyện để điền sẵn form."""

from ailusion.chat.trip_info import TripInfo, transcript_text


def test_cleaned_drops_nonsense_numbers_and_dates() -> None:
    info = TripInfo(
        days=0,
        adults=-1,
        children=2,
        budget_million=0,
        start_date="20/12",
        destination="  ",
        interests=["Biển", "Biển"],
    ).cleaned()
    assert info.days is None
    assert info.adults is None
    assert info.children == 2
    assert info.budget_million is None
    assert info.start_date is None
    assert info.destination is None
    assert info.interests == ["Biển"]


def test_cleaned_keeps_valid_values() -> None:
    info = TripInfo(days=3, adults=2, start_date="2026-12-20", budget_million=15).cleaned()
    assert (info.days, info.adults, info.start_date, info.budget_million) == (
        3,
        2,
        "2026-12-20",
        15,
    )


def test_transcript_labels_speakers_and_skips_blanks() -> None:
    text = transcript_text(
        [("human", "Đi Đà Lạt 3 ngày"), ("ai", "  "), ("system", "x"), ("ai", "Ok!")]
    )
    assert text == "Người dùng: Đi Đà Lạt 3 ngày\n\nTrợ lý: Ok!"


def test_group_type_is_kept_across_turns() -> None:
    from ailusion.chat.trip_info import merge

    first = TripInfo(destination="Hội An", adults=2, group_type="couple")
    later = TripInfo(budget_million=6)  # lượt sau không nhắc lại "2 vợ chồng"
    merged = merge(first, later)
    assert merged.group_type == "couple" and merged.budget_million == 6


def test_named_places_are_cleaned_and_kept_across_turns() -> None:
    from ailusion.chat.graph import form_text
    from ailusion.chat.trip_info import merge

    info = TripInfo.model_validate(
        {
            "days": 3,
            "must_visits": [
                {"name": " Bà Nà Hills ", "day": 5, "time": "sáng"},  # ngày, giờ sai: bỏ trống
                {"name": "bà nà hills"},  # trùng tên
                {"name": "Bếp Hên", "day": 2, "time": "19:00"},
                {"name": "  "},
            ],
        }
    ).cleaned()
    assert [(m.name, m.day, m.time) for m in info.must_visits] == [
        ("Bà Nà Hills", None, None),
        ("Bếp Hên", 2, "19:00"),
    ]
    later = merge(info, TripInfo(budget_million=8))
    assert [m.name for m in later.must_visits] == ["Bà Nà Hills", "Bếp Hên"]
    assert "Địa điểm muốn đi: Bà Nà Hills, Bếp Hên (ngày 2, 19:00)" in form_text(later)

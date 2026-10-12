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

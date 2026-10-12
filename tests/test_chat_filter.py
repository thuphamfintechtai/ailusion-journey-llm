"""Bộ lọc luật: câu nào đáng gọi LLM để cập nhật form chuyến đi."""

import pytest

from ailusion.chat.filter import check, fold


def test_fold_strips_vietnamese_marks() -> None:
    assert fold("Đà Lạt  3N2Đ") == "da lat 3n2d"


@pytest.mark.parametrize(
    ("message", "signal"),
    [
        ("Đi Đà Lạt 3 ngày 2 đêm", "days"),
        ("lịch 3N2Đ nha", "days"),
        ("đi cuối tuần này", "days"),
        ("2 vợ chồng với 1 bé", "people"),
        ("nhà mình đi cùng ông bà", "people"),
        ("giá cỡ 1tr", "budget"),
        ("tầm 500k thôi", "budget"),
        ("ngân sách eo hẹp lắm", "budget"),
        ("đi ngày 20/12", "date"),
        ("thứ 7 tuần sau đi", "date"),
        ("bé dị ứng tôm cua", "access"),
        ("ông đi lại khó, sợ leo dốc", "access"),
        ("muốn đi chậm chậm ngắm cảnh", "style"),
        ("thích biển với cà phê", "interest"),
        ("thuê xe máy tự lái", "transport"),
        ("hay đi Phú Quốc nhỉ", "place"),
        ("di da lat", "place"),
    ],
)
def test_trip_details_pass_the_filter(message: str, signal: str) -> None:
    result = check(message)
    assert signal in result.signals
    assert result.should_extract


@pytest.mark.parametrize(
    "message",
    ["cảm ơn nha", "quán nào ngon nhất?", "kể thêm về chỗ đó đi", "hay quá", "ủa vậy hả"],
)
def test_small_talk_is_skipped(message: str) -> None:
    assert not check(message).should_extract


@pytest.mark.parametrize(
    "message", ["lên lịch trình giúp mình", "lập lộ trình đi", "Lên kế hoạch luôn nha", "mở form"]
)
def test_plan_intent(message: str) -> None:
    result = check(message)
    assert result.plan_intent
    assert result.should_extract


def test_short_agreement_counts_only_after_a_plan_offer() -> None:
    offer = "Bạn có muốn mình lập lộ trình chi tiết không?"
    assert check("ok", last_ai=offer).plan_intent
    assert check("Được luôn", last_ai=offer).plan_intent
    assert not check("ok", last_ai="Hồ Xuân Hương đẹp nhất lúc sáng sớm.").plan_intent
    assert not check("ok").plan_intent


def test_long_message_starting_with_ok_is_not_an_agreement() -> None:
    offer = "Bạn có muốn mình lập lộ trình không?"
    long = "ok nhưng mà mình muốn hỏi thêm về mấy quán ăn ngon ở khu chợ đêm trước đã"
    assert not check(long, last_ai=offer).plan_intent

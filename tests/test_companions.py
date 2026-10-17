"""Người đi cùng (shared/companions.py): món phải tránh trong ô "Nhu cầu khác", lời dặn từng chỗ
khi đi cùng trẻ em."""

from __future__ import annotations

from ailusion.itinerary.agents.intake import ExtractedNeed, Extraction, apply_extracted
from ailusion.itinerary.shared.companions import avoid_match, avoided_foods, kid_tips, kid_unfit
from ailusion.itinerary.shared.form import TripForm
from ailusion.itinerary.shared.models import Place


def _place(name: str, category: str, kind: str = "meal") -> Place:
    return Place(place_id=name, name=name, category=category, kind=kind, lat=16.0, lng=108.2)


def test_allergies_and_diets_in_the_needs_note() -> None:
    assert [p for p, _ in avoided_foods("bé dị ứng hải sản")] == ["bé dị ứng hải sản"]
    tom_cua = dict(avoided_foods("con dị ứng tôm, cua và mực"))
    assert set(tom_cua["con dị ứng tôm, cua, mực"]) >= {"tôm", "cua", "ghẹ", "mực"}
    # Món không nhận ra (cay, đậu phộng): không lọc được quán, chữ rỗng.
    assert avoided_foods("không ăn cay; bé dị ứng đậu phộng") == [
        ("không ăn cay", ()),
        ("bé dị ứng đậu phộng", ()),
    ]
    assert "cua" in dict(avoided_foods("kiêng đồ biển"))["kiêng đồ biển"]
    assert avoided_foods("muốn ăn bún bò") == [] and avoided_foods("ăn chay") == []


def test_restaurants_with_the_food_are_matched_by_name_or_type() -> None:
    # Chạy thật Nha Trang: "Bánh Canh Cua Linh" từng được xếp 2 lần dù bé dị ứng hải sản.
    avoid = avoided_foods("bé dị ứng hải sản")
    assert avoid_match(_place("Bánh Canh Cua Linh", "Nhà hàng Việt Nam"), avoid) == (
        "bé dị ứng hải sản",
        "cua",
    )
    assert avoid_match(_place("Hải sản Bé Mặn", "Nhà hàng hải sản"), avoid)
    # Khớp nguyên chữ có dấu: "cá" không khớp "cà phê", "các"; chỗ chơi không xét.
    assert avoid_match(_place("Cà phê Muối", "Quán cà phê"), avoid) is None
    assert avoid_match(_place("Các món Việt", "Nhà hàng"), avoid) is None
    assert avoid_match(_place("Chợ hải sản", "Chợ", kind="visit"), avoid) is None


def test_kid_tips_say_why_and_what_to_do() -> None:
    form = TripForm.model_validate(
        {
            "destination": "Nha Trang",
            "start_date": "2026-10-24",
            "end_date": "2026-10-26",
            "group_size": 4,
            "kids_under_6": 2,
            "budget": 12_000_000,
        }
    )
    places = [
        _place("Khu du lịch Đảo Khỉ", "Điểm thu hút khách du lịch", "visit"),
        _place("Bãi biển Nha Trang", "Điểm thu hút khách du lịch", "visit"),
        _place("Tour Hòn Tằm", "Điểm thu hút khách du lịch", "visit"),
        _place("Thủy cung Vinpearl", "Công viên thủy cung", "visit"),
        _place("Bánh Canh Cua Linh", "Nhà hàng Việt Nam"),
    ]
    assert kid_tips(places, form) == [
        "Khu du lịch Đảo Khỉ: thú có thể giật đồ, cào — giữ trẻ sát bên, không cho trẻ tự cho "
        "thú ăn",
        "Bãi biển Nha Trang: trông trẻ sát khi xuống nước, mang phao tay cho trẻ, che nắng",
        "Tour Hòn Tằm: đi tàu / cano — hỏi trước có áo phao cỡ trẻ em",
    ]
    adults = form.model_copy(update={"kids_under_6": 0})
    assert kid_tips(places, adults) == []
    # Hồ bơi như bãi biển; hải đăng nhiều bậc (chạy thật: "I-Resort Swimming Pool", "Hải đăng Hòn
    # Lớn" chưa có lời dặn).
    more = [
        _place("I-Resort Swimming Pool", "Điểm thu hút khách du lịch", "visit"),
        _place("Hải đăng Hòn Lớn", "Điểm thu hút khách du lịch", "visit"),
    ]
    assert [x.split(": ", 1)[1] for x in kid_tips(more, form)] == [
        "trông trẻ sát khi xuống nước, mang phao tay cho trẻ, che nắng",
        "dốc, nhiều bậc — mang địu (xe đẩy khó đi)",
    ]


def test_places_unfit_for_kids_under_6() -> None:
    # Chạy thật: "Lặn Biển LabiXa" từng được xếp cho đoàn có 2 bé dưới 6.
    assert kid_unfit(_place("Lặn Biển LabiXa", "Điểm thu hút khách du lịch", "visit"))
    assert kid_unfit(_place("GRAND SPA", "Spa mát xa", "visit"))
    assert kid_unfit(_place("Sailing Club", "Quán bar", "visit"))
    assert not kid_unfit(_place("Thủy cung Vinpearl", "Công viên thủy cung", "visit"))
    assert not kid_unfit(_place("Quán Spa Café", "Quán cà phê"))  # quán ăn uống, không xét


def test_an_allergy_is_not_read_as_vegetarian() -> None:
    # Chạy thật: Intake đổi "bé dị ứng hải sản" ra "có món chay" (mọi quán bị ghi "chưa rõ đáp
    # ứng: có món chay"). Câu có chữ "chay" thì vẫn nhận.
    form = TripForm.model_validate(
        {
            "destination": "Nha Trang",
            "start_date": "2026-10-24",
            "end_date": "2026-10-26",
            "group_size": 2,
            "budget": 8_000_000,
            "needs_note": "bé dị ứng hải sản, mẹ ăn chay",
        }
    )
    wrong = ExtractedNeed(code="vegetarian", quote="bé dị ứng hải sản")
    right = ExtractedNeed(code="vegetarian", quote="mẹ ăn chay")
    new, _ = apply_extracted(form, Extraction(required_needs=[wrong]), sent=set())
    assert new.required_needs == []
    new, _ = apply_extracted(form, Extraction(required_needs=[wrong, right]), sent=set())
    assert new.required_needs == ["vegetarian"]

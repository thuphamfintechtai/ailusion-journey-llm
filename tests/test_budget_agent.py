"""Agent Budget: code đưa đúng đoạn có giá, kiểm lại trên trang (lịch Hội An / Huế 10/2026: 20 lượt
tìm, 0 giá được nhận)."""

from __future__ import annotations

from ailusion.itinerary.agents.budget import (
    PriceFind,
    accept_price,
    hit_text,
    price_windows,
    trusted_finds,
)
from ailusion.itinerary.coordinator.filter import refresh_estimates
from ailusion.itinerary.shared.llm import money_in
from ailusion.itinerary.shared.models import Place

# Đầu trang thật: menu, logo, ảnh base64; giá nằm sâu bên dưới, in đậm markdown.
PAGE = (
    "[![Logo](https://mia.vn/logo.png)](https://mia.vn) [Trang chủ](https://mia.vn) "
    "![](data:image/webp;base64,UklGRnAtAABXRUJQVlA4WAoAAAAgAAAA) "
    * 5
    + "\n\n# Bảo tàng Văn hoá Dân gian Hội An\n\n•62 Bạch Đằng, Hội An\n\n"
    "**Thời gian tham quan:** 7 giờ sáng – 21 giờ tối hàng ngày\n\n"
    "**Vé tham quan:** 80.000 VNĐ / người (dành cho khách Việt Nam) – 150.000 VNĐ / người "
    "(dành cho khách nước ngoài)\n"
)


def test_page_markdown_does_not_break_the_quote() -> None:
    find = PriceFind(
        code="V1",
        price=80_000,
        url="https://mia.vn/bao-tang",
        quote="Vé tham quan: 80.000 VNĐ / người (dành cho khách Việt Nam)",
    )
    assert accept_price(find, PAGE)  # trang có "**Vé tham quan:**"
    assert not accept_price(
        find.model_copy(update={"price": 150_000}), PAGE
    )  # không có trong trích
    assert not accept_price(find.model_copy(update={"price": 115_000}), PAGE)  # số giữa: loại


def test_free_entry_and_millions_are_read() -> None:
    page = "Chùa Cầu: **Miễn phí** vào cửa cho khách tham quan bên ngoài."
    free = PriceFind(code="V1", price=0, url="https://x", quote="Miễn phí vào cửa")
    assert accept_price(free, page)
    assert not accept_price(free.model_copy(update={"quote": "khách tham quan bên ngoài"}), page)
    assert money_in("Vé cáp treo: 1,2 triệu/người") == [1_200_000]
    assert money_in("trọn gói 1.5tr") == [1_500_000]
    assert money_in("trẻ em trên 10 tuổi") == []  # "tr" đầu chữ "trẻ", "trên" không phải triệu


def test_agent_sees_the_price_not_the_page_header() -> None:
    windows = price_windows(PAGE)
    assert windows and "80.000 VNĐ" in windows[0]
    assert "base64" not in " ".join(windows)
    text = hit_text(
        {"url": "https://mia.vn/bao-tang", "snippet": "Giá vé 80.000 đ", "content": PAGE}
    )
    assert "Giá vé 80.000 đ" in text and "Vé tham quan: 80.000 VNĐ" in text


def test_link_must_be_the_places_own_result_and_one_quote_one_place() -> None:
    codes = {"V1": "museum-a", "V2": "museum-b", "V3": "temple"}
    hits = {"V1": {"https://mia.vn/a"}, "V2": {"https://mia.vn/a"}, "V3": {"https://x.vn/t"}}
    same = "Vé tham quan: 80.000 VNĐ / người"
    finds = [
        PriceFind(code="V1", price=80_000, url="https://mia.vn/a", quote=same),
        PriceFind(code="V2", price=80_000, url="https://mia.vn/a/", quote=same),  # cùng câu trích
        PriceFind(
            code="V3", price=30_000, url="https://bia.vn/khac", quote="Vé 30.000đ"
        ),  # link lạ
    ]
    assert trusted_finds(finds, codes, hits) == []
    finds[1] = finds[1].model_copy(update={"quote": "Bảo tàng B: 50.000 VNĐ"})
    assert [f.code for f in trusted_finds(finds, codes, hits)] == ["V1", "V2"]


def test_estimates_are_refreshed_after_real_prices_arrive() -> None:
    def place(pid: str, price: int | None, by: str | None) -> Place:
        return Place(
            place_id=pid,
            name=pid,
            category="Bảo tàng",
            kind="visit",
            lat=15.88,
            lng=108.33,
            price=price,
            price_by=by,
        )

    # Bước 7 chưa có giá thật nào: bảo tàng ước 50k theo bảng. Agent Budget nhận 2 giá thật.
    places = {
        "a": place("a", 80_000, "Agent Budget: https://x"),
        "b": place("b", 120_000, "Agent Budget: https://y"),
        "c": place("c", 50_000, "ước theo loại"),
    }
    refresh_estimates(places)
    assert places["c"].price == 100_000 and places["c"].price_by == "ước theo chỗ cùng loại"


def test_blank_price_does_not_break_the_whole_answer() -> None:
    from ailusion.itinerary.agents.budget import PriceFinds

    # Model trả price "" cho mã không có giá: trước đây cả câu trả lời hỏng, mất mọi giá.
    out = PriceFinds.model_validate(
        {
            "items": [
                {
                    "code": "V1",
                    "price": 35000,
                    "url": "https://x",
                    "quote": "Người lớn: 35.000 VNĐ",
                },
                {"code": "V2", "price": "", "url": "", "quote": ""},
                {"code": "V3", "price": "80.000", "url": "https://y", "quote": "80.000đ"},
            ]
        }
    )
    assert [i.price for i in out.items] == [35_000, None, 80_000]


def test_price_page_must_name_the_place() -> None:
    from ailusion.itinerary.agents.budget import core_name, name_on_page

    ticket = (
        "Giá vé tham quan phố cổ Hội An. Vé tham quan Hội An được quy định dành cho khách Việt "
        "Nam: **Giá vé dành cho khách Việt Nam: 80.000đ/người**. Chùa Cầu nằm trong 21 điểm."
    )
    assert name_on_page("Chùa Cầu", "Hội An", ticket)
    assert not name_on_page(
        "Chợ Bà Lê", "Hội An", ticket
    )  # "cho", "le" có trên trang, cụm thì không
    assert core_name("CSO Gallery Hội An", "Hội An") == "cso gallery"
    assert core_name("Chùa Huyền Không (Theravāda)", "Huế") == "chua huyen khong"
    assert core_name("Hội An", "Hội An") == "hoi an"  # tên chính là điểm đến: giữ nguyên
    assert name_on_page("Bảo tàng Văn hóa Dân gian Hội An", "Hội An", "Bảo tàng Văn hoá Dân gian")


def test_quote_joined_with_ellipsis_checks_each_part() -> None:
    page = (
        "Opening hours 8:00-20:00. Ticket price: Free. ... "
        + "Precious Heritage Art Gallery Museum Hoi An"
    )
    ok = PriceFind(
        code="V1",
        price=0,
        url="https://x",
        quote="Ticket price: Free [...] Precious Heritage Art Gallery Museum Hoi An",
    )
    assert accept_price(ok, page)
    bad = ok.model_copy(update={"quote": "Ticket price: Free [...] Bảo tàng khác"})
    assert not accept_price(bad, page)

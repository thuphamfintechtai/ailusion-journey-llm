"""Số liệu các tab: âm lịch, ngày lễ, mức đông, thời tiết theo buổi, chặng đi, chi phí, hành lý."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from ailusion.itinerary.response import (
    Day,
    DayCost,
    Item,
    ItineraryResponse,
    Option,
    Session,
    add_extras,
    cost_breakdown,
    day_cost,
    itinerary_markdown,
)
from ailusion.itinerary.shared.form import TripForm
from ailusion.itinerary.shared.lunar import crowd_of, holiday_of, to_lunar
from ailusion.itinerary.shared.models import STAY
from ailusion.itinerary.tabs import (
    day_weather,
    leg_cost,
    leg_km,
    leg_range,
    packing,
    price_range,
    ride_alt,
    travel_alt,
)


@pytest.mark.parametrize(
    ("solar", "lunar", "leap"),
    [
        (date(2026, 2, 17), (1, 1, 2026), False),  # Tết Bính Ngọ
        (date(2025, 1, 29), (1, 1, 2025), False),  # Tết Ất Tỵ
        (date(1985, 1, 21), (1, 1, 1985), False),  # Việt Nam (+7) sớm hơn Trung Quốc 1 tháng
        (date(2025, 7, 25), (1, 6, 2025), True),  # tháng 6 nhuận Ất Tỵ
        (date(2023, 3, 22), (1, 2, 2023), True),  # tháng 2 nhuận Quý Mão
        (date(2023, 3, 21), (30, 2, 2023), False),
        (date(2025, 10, 6), (15, 8, 2025), False),  # Trung Thu
        (date(2026, 4, 26), (10, 3, 2026), False),  # Giỗ Tổ
        (date(2026, 1, 1), (13, 11, 2025), False),  # đầu năm dương còn năm âm trước
    ],
)
def test_lunar_conversion(solar: date, lunar: tuple[int, int, int], leap: bool) -> None:
    got = to_lunar(solar)
    assert (got.day, got.month, got.year) == lunar and got.leap is leap


def test_lunar_text_and_year_name() -> None:
    tet = to_lunar(date(2026, 2, 17))
    assert str(tet) == "1/1" and tet.year_name == "Bính Ngọ"


def test_holidays_solar_lunar_and_local_festival() -> None:
    assert holiday_of(date(2026, 9, 2)) == "Lễ 2/9"
    assert holiday_of(date(2026, 2, 18)) == "Mùng 2 Tết"
    assert holiday_of(date(2025, 9, 6)) == "Lễ Vu Lan"
    assert holiday_of(date(2025, 7, 25)) is None  # 1/6 nhuận, không phải ngày lễ
    fest = [{"name": "Lễ hội pháo hoa", "start": "2026-06-01", "end": "2026-06-03"}]
    assert holiday_of(date(2026, 6, 2), fest) == "Lễ hội pháo hoa"
    assert holiday_of(date(2026, 6, 4), fest) is None


def test_crowd_levels() -> None:
    assert crowd_of(date(2026, 9, 2)) == ("high", "Lễ 2/9, rất đông")
    assert crowd_of(date(2026, 10, 14))[0] == "low"  # thứ Tư thường
    assert crowd_of(date(2026, 10, 17)) == ("medium", "Cuối tuần, đông vừa")
    assert crowd_of(date(2026, 5, 2))[0] == "high"  # thứ Bảy sát 30/4 – 1/5
    fest = [{"name": "Lễ hội pháo hoa", "start": "2026-06-03", "end": None}]
    assert crowd_of(date(2026, 6, 3), fest) == ("high", "Có Lễ hội pháo hoa, đông")


def _weather() -> dict[str, Any]:
    hours = [
        {"time": "06:00", "chance_of_rain_pct": 0, "condition": "Nắng"},
        {"time": "09:00", "chance_of_rain_pct": 20, "condition": "Có mây vài nơi"},
        {"time": "12:00", "chance_of_rain_pct": 40, "condition": "Nhiều mây"},
        {"time": "15:00", "chance_of_rain_pct": 85, "condition": "Mưa to"},
        {"time": "18:00", "chance_of_rain_pct": 60, "condition": "Dông có mưa"},
        {"time": "21:00", "chance_of_rain_pct": 30, "condition": "Sương mù"},
    ]
    return {
        "weather": [
            {"date": "2026-10-20", "temp_min_c": 15.2, "temp_max_c": 22, "hours": hours},
            {"date": "2026-10-21", "temp_min_c": 17, "temp_max_c": 25},  # chỉ có số ngày
        ]
    }


def test_sessions_from_hourly_forecast() -> None:
    lo, hi, sessions = day_weather(_weather(), 1, "2026-10-20")
    assert (lo, hi) == (15.2, 22)
    assert [(s["part"], s["label"]) for s in sessions] == [
        ("morning", "Sáng"),
        ("afternoon", "Chiều"),
        ("evening", "Tối"),
    ]
    assert [s["rain_chance"] for s in sessions] == [20, 85, 60]  # lớn nhất trong khung
    assert [s["icon"] for s in sessions] == ["cloud", "rain", "storm"]
    assert sessions[1]["condition"] == "Mưa lớn"
    # Ngày chỉ có số theo ngày: có nhiệt độ, không có buổi; ngày ngoài tầm dự báo: trống.
    assert day_weather(_weather(), 2, "2026-10-21") == (17, 25, [])
    assert day_weather(_weather(), 3, "2026-10-22") == (None, None, [])


def test_rain_chance_alone_marks_rain() -> None:
    state = {"weather": [{"hours": [{"time": "09:00", "chance_of_rain_pct": 70}]}]}
    _, _, sessions = day_weather(state, 1, "2026-10-20")  # dữ liệu cũ không có `date`
    assert sessions == [
        {
            "part": "morning",
            "label": "Sáng",
            "condition": "Có mưa rào",
            "rain_chance": 70,
            "icon": "rain",
        }
    ]


def test_leg_km_prefers_matrix_then_straight_line() -> None:
    pos = {STAY: (16.06, 108.22), "a": (16.06, 108.25)}
    assert leg_km(STAY, "a", pos, {f"{STAY}|a": 4.26}) == 4.3
    straight = leg_km(STAY, "a", pos, {})
    assert straight == pytest.approx(3.2 * 1.3, abs=0.1)
    assert leg_km(STAY, "zz", pos, {}) is None


def test_leg_cost_by_mode() -> None:
    # Gọi xe: trung bình các hãng cùng cỡ xe trong shared/fares.py.
    assert leg_cost(10, "taxi", 4) == 132_000
    assert leg_cost(10, "taxi", 5) == 149_000  # 1 xe 7 chỗ (gồm phí nền tảng Grab)
    assert leg_cost(10, "taxi", 4, 20, "hn") == 131_000
    assert leg_cost(1, "taxi", 2) == 19_000  # chỉ giá mở cửa
    assert leg_cost(40, "taxi", 4) < 4 * leg_cost(10, "taxi", 4)  # qua mốc km thì rẻ hơn
    # Tự lái không tính theo chặng: tiền thuê + xăng tính theo ngày (`rental_of`).
    assert leg_cost(10, "motorbike", 3) is None and leg_cost(10, "car", 4) is None
    assert leg_cost(10, "bus", 4) is None
    assert leg_cost(10, None, 4) is None
    assert leg_cost(None, "taxi", 4) is None


def test_leg_range_by_mode() -> None:
    # Gọi xe: hãng rẻ nhất / đắt nhất cùng cỡ xe; số giữa (trung bình các hãng) nằm trong khoảng.
    assert leg_range(10, "taxi", 4) == (96_000, 167_000)  # Sông Nhuệ 6.000 + 9 km × 10.000
    assert leg_range(10, "taxi", 5) == (137_000, 155_000)  # 1 xe 7 chỗ, Grab + 2.000 phí
    low, high = leg_range(10, "taxi", 9)  # type: ignore[misc]
    assert (low, high) == (233_000, 322_000)  # 1 xe 7 chỗ + 1 xe 4 chỗ (Grab + 2.000 phí)
    assert low <= leg_cost(10, "taxi", 9) <= high  # type: ignore[operator]
    assert leg_range(10, "motorbike", 3) is None and leg_range(10, "car", 8) is None
    assert leg_range(10, "bus", 4) is None and leg_range(None, "taxi", 4) is None


def test_price_range() -> None:
    assert price_range(300_000, "tool") == (300_000, 300_000)
    assert price_range(300_000, None) == (300_000, 300_000)
    assert price_range(300_000, "ước theo loại") == (225_000, 375_000)
    assert price_range(1_100, "ước theo loại") == (0, 2_000)  # luôn kẹp giá ước
    assert price_range(None, "ước theo loại") == (None, None)
    item = Item(type="visit", start="09:00", end="10:00", name="A", price=200_000, price_by="ước")
    assert (item.price_min, item.price_max) == (150_000, 250_000)
    unknown = Item(type="visit", start="09:00", end="10:00", name="B")
    assert (unknown.price_min, unknown.price_max) == (None, None)


def _item(kind: str, name: str, price: int | None = None, **kw: Any) -> Item:
    return Item(type=kind, start="09:00", end="10:00", name=name, price=price, **kw)  # type: ignore[arg-type]


def _day(n: int, items: list[Item], night: int, **kw: Any) -> Day:
    return Day(
        day=n,
        date=f"2026-10-{19 + n}",
        weekday="",
        plan_id=f"p{n}",
        status="ok",
        metrics={},
        items=items,
        cost=day_cost(items, night),
        **kw,
    )


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Đà Lạt",
        "start_date": "2026-10-20",
        "end_date": "2026-10-21",
        "group_size": 3,
    }
    return TripForm.model_validate({**data, **over})


def test_day_cost_and_breakdown_add_up() -> None:
    d1 = _day(
        1,
        [
            _item("visit", "Chùa Linh Phước", 0, travel_cost=30_000),
            _item("meal", "Bánh căn", 90_000, travel_cost=25_000),
            _item("visit", "Thung lũng Tình Yêu", 300_000, travel_cost=None),
            _item("back", "Về chỗ ở", travel_cost=25_000),
        ],
        night=1_000_000,
    )
    d2 = _day(2, [_item("meal", "Lẩu gà", None, travel_cost=40_000)], night=0)
    assert d1.cost == DayCost(
        stay=1_000_000, tickets=300_000, food=90_000, transport=80_000, total=1_470_000,
        total_min=1_470_000, total_max=1_470_000,
    )  # fmt: skip
    cb = cost_breakdown([d1, d2], _form(), budget=3_000_000, estimated=True)
    assert cb.total == d1.cost.total + d2.cost.total == 1_510_000
    assert (cb.stay, cb.tickets, cb.food, cb.transport) == (1_000_000, 300_000, 90_000, 120_000)
    assert cb.per_person == 503_333 and cb.budget == 3_000_000 and cb.estimated


def _trip(mode: str | None, market: bool = False) -> tuple[list[Day], TripForm]:
    """2 ngày, 3 người: ngày 1 có 1 chỗ giá ước và 1 chặng có khoảng giá; ngày 2 quán chưa giá."""
    d1 = _day(
        1,
        [
            _item("visit", "Chùa Linh Phước", 0, travel_cost=30_000),
            _item(
                "meal",
                "Chợ đêm Đà Lạt" if market else "Bánh căn",
                90_000,
                travel_cost=25_000,
                travel_cost_min=20_000,
                travel_cost_max=31_000,
            ),
            _item("visit", "Thung lũng Tình Yêu", 300_000, price_by="ước theo loại"),
            _item("back", "Về chỗ ở", travel_cost=25_000),
        ],
        night=1_000_000,
    )
    d2 = _day(2, [_item("meal", "Lẩu gà", None, travel_cost=40_000)], night=0)
    form = _form(travel_mode=mode) if mode else _form()
    add_extras([d1, d2], form)
    return [d1, d2], form


def _extras(day: Day) -> dict[str, tuple[int, int, str]]:
    assert day.cost
    return {e.label: (e.min, e.max, e.reason) for e in day.cost.extra_items}


def test_day_extras_is_a_share_of_the_day_with_reasons() -> None:
    (d1, d2), form = _trip("motorbike")
    ((label, (low, high, reason)),) = _extras(d1).items()
    assert label == "Chi phí phát sinh"
    # 10–15% của ở + vé + ăn + đi lại + thuê và xăng 2 xe máy (giữa 175.000 × 2):
    # 1.000.000 + 300.000 + 90.000 + 80.000 + 350.000.
    assert (low, high) == (182_000, 273_000)
    assert reason.startswith("10–15% chi phí ngày: Thuế, phí dịch vụ")
    assert "hư xe" in reason and "Thung lũng Tình Yêu" in reason
    assert "Chùa Linh Phước" not in reason  # giá thật

    c = d1.cost
    assert c and c.extras == round((low + high) / 2 / 1000) * 1000
    assert c.total == c.stay + c.tickets + c.food + c.transport + c.rental + c.extras
    assert c.total_min <= c.total <= c.total_max

    cb = cost_breakdown([d1, d2], form, budget=None, estimated=True)
    days = [d.cost for d in (d1, d2) if d.cost]
    assert cb.total == sum(x.total for x in days)
    assert cb.total_min == sum(x.total_min for x in days)
    assert cb.total_max == sum(x.total_max for x in days)
    assert cb.extras == sum(x.extras for x in days)
    assert cb.per_person_min <= cb.per_person <= cb.per_person_max
    assert [e.label for e in cb.extra_items] == ["Chi phí phát sinh"]
    assert 0.09 < cb.extras_pct_min <= 0.10 and 0.14 < cb.extras_pct_max <= 0.15
    assert cb.extras_notes[0].startswith("Thuế") and len(cb.extras_notes) == len(
        set(cb.extras_notes)
    )


def test_day_extras_more_on_rainy_or_peak_days() -> None:
    from ailusion.itinerary.tabs import day_extras, extra_pct

    assert extra_pct(False, False) == (0.10, 0.15)
    assert extra_pct(True, False) == (0.10, 0.20)
    assert extra_pct(True, True) == (0.10, 0.25)
    (d1, _), form = _trip("taxi", market=True)
    (row,) = day_extras(d1.items, form, 1_000_000, rainy=True, peak=True)
    assert (row["min"], row["max"]) == (100_000, 250_000)
    for note in ("Giá gọi xe tăng", "Trời mưa", "Lễ, cao điểm", "Quà, đặc sản"):
        assert note in row["reason"]
    assert "hư xe" not in row["reason"]
    assert day_extras(d1.items, form, 0) == []


def test_markdown_shows_total_as_range() -> None:
    (d1, d2), form = _trip("motorbike")
    cb = cost_breakdown([d1, d2], form, budget=None, estimated=True)
    resp = ItineraryResponse(
        thread_id="t",
        status="ok",
        destination="Đà Lạt",
        start_date="2026-10-20",
        days=2,
        options=[Option(id="PA1", name="Phương án 1", days=[d1, d2], cost_breakdown=cb)],
        chosen="PA1",
    )
    text = itinerary_markdown(resp)
    low, high = f"{cb.total_min:,}".replace(",", "."), f"{cb.total_max:,}".replace(",", ".")
    assert f"💰 **Tổng:** khoảng {low}–{high} đ cả đoàn" in text
    assert "phát sinh" in text


def _items(*names: tuple[str, str, str]) -> list[Item]:
    return [_item(kind, name, category=cat) for kind, name, cat in names]


def test_packing_rules_with_reasons() -> None:
    rainy = Session(
        part="afternoon", label="Chiều", condition="Mưa lớn", rain_chance=90, icon="rain"
    )
    days = [
        _day(
            1,
            _items(
                ("visit", "Chùa Linh Phước", "Chùa"),
                ("meal", "Chợ đêm Đà Lạt", "Chợ"),
                ("visit", "Hồ Xuân Hương", "Hồ"),
                ("visit", "Ga Đà Lạt", "Điểm mốc"),
                ("visit", "Dinh Bảo Đại", "Di tích"),
            ),
            0,
            temp_min=14.6,
            sessions=[rainy],
        ),
        _day(
            2,
            _items(("visit", "Đồi chè Cầu Đất", "Nông trại"), ("visit", "Langbiang", "Núi")),
            0,
            sessions=[rainy],
        ),
    ]
    form = _form(travel_mode="motorbike", kids_under_6=1)
    out = packing(form, days, ["adventure"])
    by = {p["item"]: p for p in out}
    assert out[0]["item"] == "CCCD / hộ chiếu" and len(out) <= 14
    assert [p["group"] for p in out] == sorted(
        (p["group"] for p in out), key=["Giấy tờ", "Quần áo", "Sức khỏe", "Thiết bị"].index
    )
    assert "Bằng lái A1, đăng ký xe" in by and "Mũ bảo hiểm" in by
    assert "Chợ đêm Đà Lạt" in by["Tiền mặt mệnh giá nhỏ"]["reason"]
    assert by["Áo khoác ấm"]["reason"] == "Nhiệt độ thấp nhất khoảng 15°C"
    assert by["Áo mưa / ô"]["reason"] == "Dự báo mưa chiều ngày 1, chiều ngày 2"
    assert "Dép / giày chống trơn" in by
    assert by["Trang phục kín vai, gối"]["reason"] == "Viếng Chùa Linh Phước"
    assert "Giày thể thao" in by and "Đồ cho trẻ em" in by
    assert "Kem chống nắng, áo chống nắng, khẩu trang" in by
    assert by["Sạc dự phòng"]["reason"] == "Dùng bản đồ và gọi xe cả ngày"
    assert "Máy ảnh / điện thoại trống bộ nhớ" in by  # 6 điểm tham quan
    assert all(p["reason"] for p in out)


def test_packing_minimal_trip() -> None:
    day = _day(1, _items(("visit", "Bảo tàng", "Bảo tàng")), 0)
    items = {p["item"] for p in packing(_form(end_date="2026-10-20"), [day], [])}
    assert items == {"CCCD / hộ chiếu", "Thuốc cá nhân", "Sạc dự phòng"}


def test_fare_matrix() -> None:
    from ailusion.itinerary.shared.fares import (
        SERVICES,
        cars_for,
        city_of,
        compare,
        fare_of,
        ride_price,
    )

    grab = next(s for s in SERVICES if s.service == "GrabCar 4 chỗ")
    # 28.473 + 8 km × 9.818 + 442 đ/phút × 20 phút × 8/10 (phút sau 2 km đầu)
    assert round(ride_price(fare_of(grab, "hn"), 10, 20)) == 114_089
    xanh = next(s for s in SERVICES if s.service == "Xanh SM Car")
    # Bậc: 2 km đầu 30.500, km 2–12 15.300, km 12–25 14.000, từ km 25 13.000 (Đà Nẵng)
    assert round(ride_price(fare_of(xanh, "dn"), 30)) == 30_500 + 153_000 + 182_000 + 65_000
    assert ride_price(fare_of(xanh, "dn"), 1.5) == 30_500
    assert city_of("Đà Nẵng") == "dn" and city_of("TP. Hồ Chí Minh") == "hcm"
    assert city_of("Hội An") == "hcm"  # nơi khác dùng giá phổ biến
    assert cars_for(4) == ["car4"] and cars_for(6) == ["car7"] and cars_for(9) == ["car7", "car4"]

    rows = compare([(5, 15, False), (10, 25, False)], 2, "dn")
    assert [r["total"] for r in rows] == sorted(r["total"] for r in rows)
    assert {r["kind"] for r in rows} == {"car", "bike"}
    bike = next(r for r in rows if r["service"] == "GrabBike")
    assert bike["vehicles"] == 2  # mỗi khách một xe máy


def _alt(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {r["mode"]: r["minutes"] for r in rows}


def test_travel_alt_from_speed_when_no_matrix() -> None:
    # 1,5 km chưa có phút bản đồ: km / tốc độ phố; taxi +3 phút chờ; đi bộ vì ≤ 2 km.
    assert _alt(travel_alt(1.5, "motorbike")) == {"car": 4, "taxi": 7, "walk": 20}
    # Chưa chọn phương tiện: ước cả xe máy.
    assert _alt(travel_alt(5.0, None)) == {"motorbike": 12, "car": 14, "taxi": 17}
    assert travel_alt(None, "car") == []


def test_travel_alt_scales_matrix_minutes() -> None:
    # Bản đồ đo 20 phút theo ô tô (đã chọn taxi): xe máy = 20 × 22/25.
    assert _alt(travel_alt(8.0, "taxi", 20)) == {"motorbike": 18, "car": 20}
    # Đo theo xe máy (chọn xe máy): ô tô = 10 × 25/22, taxi thêm 3 phút.
    assert _alt(travel_alt(1.8, "motorbike", 10)) == {"car": 11, "taxi": 14, "walk": 56}
    # Bus: không ước bus, ước đủ xe máy / ô tô / taxi; > 2 km thì không có đi bộ.
    assert [r["mode"] for r in travel_alt(3.0, "bus", 12)] == ["motorbike", "car", "taxi"]


def test_ride_alt_sums_modes_present_on_every_leg() -> None:
    legs = [travel_alt(1.0, "car"), travel_alt(3.0, "car"), []]
    # Xe máy 2 + 7, taxi 6 + 11; đi bộ thiếu ở chặng 3 km nên không cộng.
    assert _alt(ride_alt(legs)) == {"motorbike": 9, "taxi": 17}
    assert ride_alt([[], []]) == []


def test_day_and_item_carry_travel_alt() -> None:
    item = _item("visit", "Hồ", travel_alt=[{"mode": "car", "minutes": 5}])
    day = _day(1, [item], 0, ride_alt=ride_alt([[{"mode": "car", "minutes": 5}]]))
    assert day.items[0].travel_alt[0].minutes == 5
    assert day.model_dump()["ride_alt"] == [{"mode": "car", "minutes": 5}]


def test_grab_fees_and_night_surcharge() -> None:
    from ailusion.itinerary.shared.fares import SERVICES, fare_of, is_night, service_price

    grab = next(s for s in SERVICES if s.service == "GrabCar 4 chỗ")
    base = service_price(grab, fare_of(grab, "hn"), 10, 20)
    assert base == pytest.approx(114_089 + 2_000)  # phí nền tảng 2.000 đ/chuyến
    assert service_price(grab, fare_of(grab, "hn"), 10, 20, night=True) == pytest.approx(
        base + 10_000
    )
    assert is_night(22 * 60) and is_night(5 * 60 + 59) and not is_night(6 * 60)
    assert leg_cost(10, "taxi", 4, night=True) > leg_cost(10, "taxi", 4)


def test_rental_for_self_drive() -> None:
    from ailusion.itinerary.tabs import rental_of

    low, high, note = rental_of("motorbike", 3) or (0, 0, "")
    # 2 xe máy × (thuê 100.000–150.000 + xăng 40.000–60.000) mỗi ngày
    assert (low, high) == (280_000, 420_000)
    assert note.startswith("2 xe máy × (thuê")
    assert rental_of("taxi", 3) is None and rental_of(None, 3) is None
    (d1, _), _ = _trip("motorbike")
    assert d1.cost and (d1.cost.rental_min, d1.cost.rental_max) == (280_000, 420_000)
    assert d1.cost.total_min <= d1.cost.total <= d1.cost.total_max

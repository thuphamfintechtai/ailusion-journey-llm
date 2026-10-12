"""Bảng giá cước gọi xe các hãng ở Việt Nam, để ước tiền đi lại từng chặng và so sánh các hãng.

Mỗi giá: (giá mở cửa, số km mở cửa đã gồm, [(tới km, đồng/km), ...], đồng/phút). Bậc cuối có
"tới km" = None nghĩa là từ đó trở đi. Giá theo thành phố: "hn", "hcm", "dn"; nơi khác dùng
`OTHER_CITY`. Không gồm phụ phí giờ cao điểm, phí chờ, khuyến mãi.

Nguồn (tra 12/10/2026):
- Grab: grab.com/vn/blog/bang-thong-tin-cac-dich-vu-tren-ung-dung-grab (cập nhật 08/07/2026,
  đã gồm VAT 8%). Phí nền tảng GrabCar 2.000 đ/chuyến, phụ thu đêm 22:00–05:59 10.000 đ/chuyến
  (GrabCar, GrabBike); hãng khác chưa công bố phụ thu cố định nên không cộng.
- Xanh SM: greensm.com/vn-vi/news/gia-taxi-xanh-sm-bao-nhieu; Xanh SM Bike theo báo chí lúc ra mắt
  (chưa gồm phí ứng dụng 6,7%, đã cộng vào đây).
- be: báo chí về đợt tăng giá beCar 05/2026; beBike Hà Nội.
- Mai Linh, Vinasun: bảng giá taxi 02/2025 (sunparadiseland.com); G7, Taxi Group, Sông Nhuệ,
  Thành Công, Group Eco: thuexe247.vn/gia-taxi-bao-nhieu-tien-1km (07/03/2025).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Fare = tuple[int, float, list[tuple[float | None, int]], int]


@dataclass(frozen=True)
class Service:
    brand: str
    service: str
    kind: str  # "car4" | "car7" | "bike"
    seats: int  # khách mỗi xe
    by_city: dict[str, Fare]  # "hn" | "hcm" | "dn" | "*" (mọi nơi)
    trip_fee: int = 0  # phí cố định mỗi chuyến (phí nền tảng)
    night_fee: int = 0  # phụ thu chuyến đêm (`NIGHT_HOURS`)


# Giờ tính phụ thu đêm của Grab: 22:00 – 05:59.
NIGHT_HOURS = (22, 6)


def is_night(minute: float | None) -> bool:
    """`minute` = phút kể từ 0h của lúc đi."""
    if minute is None:
        return False
    hour = int(minute // 60) % 24
    return hour >= NIGHT_HOURS[0] or hour < NIGHT_HOURS[1]


def _same(fare: Fare) -> dict[str, Fare]:
    return {"*": fare}


SERVICES: list[Service] = [
    Service(
        "Grab",
        "GrabCar 4 chỗ",
        "car4",
        4,
        {
            "hn": (28_473, 2, [(None, 9_818)], 442),
            "hcm": (28_473, 2, [(None, 9_818)], 442),
            "dn": (27_000, 2, [(None, 12_175)], 442),
        },
        trip_fee=2_000,
        night_fee=10_000,
    ),
    Service(
        "Grab",
        "GrabCar 7 chỗ",
        "car7",
        7,
        _same((33_382, 2, [(None, 12_764)], 540)),
        trip_fee=2_000,
        night_fee=10_000,
    ),
    Service(
        "Grab",
        "GrabBike",
        "bike",
        1,
        {
            "hn": (13_255, 2, [(None, 4_222)], 344),
            "*": (12_273, 2, [(None, 4_222)], 344),
        },
        night_fee=10_000,
    ),
    Service(
        "Xanh SM",
        "Xanh SM Car",
        "car4",
        4,
        {
            "hn": (30_500, 2, [(12, 14_700), (25, 13_800), (None, 11_900)], 0),
            "dn": (30_500, 2, [(12, 15_300), (25, 14_000), (None, 13_000)], 0),
            "hcm": (30_500, 2, [(12, 15_200), (25, 14_700), (None, 13_300)], 0),
        },
    ),
    Service(
        "Xanh SM",
        "Xanh SM Luxury",
        "car4",
        4,
        {
            "hn": (34_400, 2, [(12, 16_000), (25, 14_900), (None, 12_900)], 0),
            "dn": (34_400, 2, [(12, 16_800), (25, 15_400), (None, 14_300)], 0),
            "hcm": (34_400, 2, [(12, 16_500), (25, 16_000), (None, 14_400)], 0),
        },
    ),
    Service(
        "Xanh SM",
        "Xanh SM Bike",
        "bike",
        1,
        {
            "hn": (14_725, 2, [(None, 5_122)], 0),  # 13.800 / 4.800 + phí ứng dụng 6,7%
            "*": (13_338, 2, [(None, 5_122)], 0),  # 12.500 / 4.800 + 6,7%
        },
    ),
    Service(
        "be",
        "beCar 4 chỗ",
        "car4",
        4,
        {
            "hn": (31_704, 2, [(12, 10_568), (None, 10_040)], 0),
            "*": (31_501, 2, [(12, 11_805), (None, 10_779)], 0),
        },
    ),
    Service("be", "beBike", "bike", 1, _same((14_472, 2, [(None, 4_918)], 0))),
    Service(
        "Mai Linh", "Taxi 4 chỗ", "car4", 4, _same((20_000, 1, [(25, 14_000), (None, 10_000)], 0))
    ),
    Service(
        "Mai Linh", "Taxi 7 chỗ", "car7", 7, _same((20_000, 1, [(25, 15_000), (None, 12_000)], 0))
    ),
    Service(
        "Vinasun", "Taxi 4 chỗ", "car4", 4, _same((11_000, 0.5, [(25, 16_400), (None, 13_500)], 0))
    ),
    Service(
        "G7", "Taxi Toyota Vios", "car4", 4, _same((20_000, 1, [(20, 13_500), (None, 11_000)], 0))
    ),
    Service(
        "Taxi Group", "Taxi 4 chỗ", "car4", 4, _same((10_000, 1, [(32, 14_000), (None, 12_000)], 0))
    ),
    Service(
        "Taxi Group", "Taxi 7 chỗ", "car7", 7, _same((10_000, 1, [(32, 16_000), (None, 13_000)], 0))
    ),
    Service(
        "Sông Nhuệ", "Taxi 4 chỗ", "car4", 4, _same((6_000, 1, [(20, 10_000), (None, 8_500)], 0))
    ),
    Service(
        "Thành Công", "Taxi 4 chỗ", "car4", 4, _same((6_000, 1, [(20, 11_000), (None, 9_000)], 0))
    ),
    Service(
        "Group Eco",
        "Taxi Hyundai i10",
        "car4",
        4,
        _same((6_000, 1, [(20, 11_500), (None, 9_500)], 0)),
    ),
]
OTHER_CITY = "hcm"  # Grab áp giá TP.HCM cho phần lớn tỉnh; các hãng khác cũng gần mức này
CITY_WORDS = {
    "hn": ("hà nội", "ha noi", "hanoi"),
    "hcm": ("hồ chí minh", "ho chi minh", "tp.hcm", "tphcm", "sài gòn", "sai gon", "saigon"),
    "dn": ("đà nẵng", "da nang", "danang"),
}


def city_of(destination: str) -> str:
    low = (destination or "").lower()
    return next((c for c, words in CITY_WORDS.items() if any(w in low for w in words)), OTHER_CITY)


def fare_of(service: Service, city: str) -> Fare | None:
    return service.by_city.get(city) or service.by_city.get("*") or service.by_city.get(OTHER_CITY)


def ride_price(fare: Fare, km: float, minutes: float = 0) -> float:
    """Cước một xe cho một chặng: giá mở cửa + từng bậc km + theo phút (sau phần mở cửa)."""
    opening, opening_km, tiers, per_min = fare
    price, done = float(opening), opening_km
    for upto, rate in tiers:
        end = km if upto is None else min(km, upto)
        if end > done:
            price += (end - done) * rate
            done = end
        if upto is None or km <= upto:
            break
    if km > opening_km and minutes:
        price += per_min * minutes * (km - opening_km) / km
    return price


def service_price(
    s: Service, fare: Fare, km: float, minutes: float = 0, night: bool = False
) -> float:
    """Cước một xe của hãng `s`: `ride_price` + phí cố định mỗi chuyến + phụ thu đêm."""
    return ride_price(fare, km, minutes) + s.trip_fee + (s.night_fee if night else 0)


def cars_for(group_size: int) -> list[str]:
    """Loại xe cần gọi cho đoàn: xe 7 chỗ cho đoàn đông, lẻ ≤ 4 người thì thêm xe 4 chỗ."""
    cars, left = [], max(group_size, 1)
    while left > 7:
        cars.append("car7")
        left -= 7
    cars.append("car4" if left <= 4 else "car7")
    return cars


def services(kind: str, city: str) -> list[tuple[Service, Fare]]:
    return [(s, f) for s in SERVICES if s.kind == kind and (f := fare_of(s, city)) is not None]


def car_leg_price(
    km: float, minutes: float, group_size: int, city: str, night: bool = False
) -> int:
    """Ước tiền gọi ô tô một chặng cho cả đoàn: trung bình các hãng cùng cỡ xe, làm tròn nghìn."""
    total = 0.0
    for kind in cars_for(group_size):
        prices = [service_price(x, f, km, minutes, night) for x, f in services(kind, city)]
        total += sum(prices) / len(prices)
    return int(round(total / 1000) * 1000)


def car_leg_range(
    km: float, minutes: float, group_size: int, city: str, night: bool = False
) -> tuple[int, int]:
    """(rẻ nhất, đắt nhất) gọi ô tô một chặng cho cả đoàn: cộng theo từng xe của `cars_for`,
    mỗi xe lấy hãng rẻ nhất / đắt nhất cùng cỡ; làm tròn nghìn."""
    lo = hi = 0.0
    for kind in cars_for(group_size):
        prices = [service_price(x, f, km, minutes, night) for x, f in services(kind, city)]
        lo, hi = lo + min(prices), hi + max(prices)
    return int(round(lo / 1000) * 1000), int(round(hi / 1000) * 1000)


def compare(
    legs: list[tuple[float, float, bool]], group_size: int, city: str
) -> list[dict[str, object]]:
    """Tổng tiền đi các chặng (km, phút, đi đêm) của cả chuyến theo từng hãng, rẻ trước.

    Ô tô: theo số xe cần gọi (`cars_for`), hãng không có xe 7 chỗ thì gọi thêm xe 4 chỗ.
    Xe máy: mỗi khách một xe.
    """
    rows: list[dict[str, object]] = []
    need7 = cars_for(group_size).count("car7")
    for s, fare in services("car4", city) + services("bike", city):
        if s.kind == "bike":
            vehicles = group_size
            per_leg = [service_price(s, fare, km, mins, nt) * vehicles for km, mins, nt in legs]
        else:
            seven = next(((x, f) for x, f in services("car7", city) if x.brand == s.brand), None)
            if need7 and seven is not None:
                fleet = [seven] * need7 + [(s, fare)] * (len(cars_for(group_size)) - need7)
            else:
                fleet = [(s, fare)] * math.ceil(group_size / 4)
            vehicles = len(fleet)
            per_leg = [
                sum(service_price(x, f, km, mins, nt) for x, f in fleet) for km, mins, nt in legs
            ]
        rows.append(
            {
                "brand": s.brand,
                "service": s.service,
                "kind": "bike" if s.kind == "bike" else "car",
                "vehicles": vehicles,
                "total": int(round(sum(per_leg) / 1000) * 1000),
            }
        )
    return sorted(rows, key=lambda r: r["total"])  # type: ignore[arg-type, return-value]

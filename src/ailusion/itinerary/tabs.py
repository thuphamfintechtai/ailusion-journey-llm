"""Số liệu cho các tab Bản đồ, Chi phí, Thời tiết, Hành lý — code thuần từ dữ liệu đã có, không
gọi thêm LLM hay tool.

- `leg_km` / `leg_cost` / `leg_range`: km và tiền đi của một chặng (cả đoàn) theo phương tiện
  trong form; `leg_range` là khoảng (thấp, cao), `leg_cost` là số giữa.
- `travel_alt` / `ride_alt`: phút đi một chặng (và cả ngày) nếu đổi sang phương tiện khác.
- `day_weather`: nhiệt độ thấp/cao và thời tiết theo buổi (sáng / chiều / tối) từ các mốc giờ
  6h, 9h, 12h, 15h, 18h, 21h của `weather_forecast(hourly=True)`.
- `packing`: danh sách hành lý theo luật, mỗi món kèm lý do lấy từ chính chuyến này.
- `price_range` / `day_extras`: giá theo khoảng và chi phí phát sinh từng ngày (theo % chi phí)
  (gửi xe, nước uống, vé chưa rõ giá, quà, dự phòng), luật cố định, lý do lấy từ chính ngày đó.
"""

from __future__ import annotations

import math
from typing import Any

from .shared.fares import OTHER_CITY, car_leg_price, car_leg_range
from .shared.form import TripForm
from .shared.math import km
from .shared.models import CATEGORY_GROUPS

# Số người mỗi xe khi tự lái: xe máy 2, ô tô tối đa 7. Gọi xe tính theo `shared/fares.py`;
# bus: chưa có giá -> None.
SEATS_PER_VEHICLE: dict[str, int] = {"motorbike": 2, "car": 7}
# Tự lái (xe máy / ô tô) không tính tiền theo chặng mà theo ngày: thuê xe + đổ xăng mỗi xe.
# Thuê xe máy (xe số / xe ga phổ thông, Đà Lạt): 100.000–150.000 đ/ngày (vietnamtourism.com,
# mytour.vn 03/2026; lễ Tết +20–50%); xăng xe máy đổ khoảng 50.000 đ/xe/ngày. Ô tô tự lái và
# xăng ô tô: số tạm (tu_dat). Đoàn đã có xe riêng thì bỏ phần thuê.
RENTAL: dict[str, tuple[int, int]] = {
    "motorbike": (100_000, 150_000),
    "car": (800_000, 1_200_000),
}
FUEL_PER_DAY: dict[str, tuple[int, int]] = {
    "motorbike": (40_000, 60_000),
    "car": (300_000, 500_000),
}
RENTAL_NAMES = {"motorbike": "xe máy", "car": "ô tô tự lái"}


def _vnd(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def rental_of(mode: str | None, group_size: int) -> tuple[int, int, str] | None:
    """(thấp, cao, ghi chú) tiền thuê xe + xăng một ngày cho cả đoàn; không tự lái thì None."""
    if mode not in RENTAL:
        return None
    (r_low, r_high), (f_low, f_high) = RENTAL[mode], FUEL_PER_DAY[mode]  # type: ignore[index]
    n = vehicles_of(mode, group_size)
    note = (
        f"{group_size} người · {n} {RENTAL_NAMES[mode]} × (thuê {_vnd(r_low)}–{_vnd(r_high)} đ"  # type: ignore[index]
        f" + xăng {_vnd(f_low)}–{_vnd(f_high)} đ)/ngày; có xe riêng thì chỉ tính xăng"
    )
    return (r_low + f_low) * n, (r_high + f_high) * n, note


ROAD_FACTOR = 1.3  # km đường chim bay × hệ số đường vòng khi không có km thật (tu_dat)


def leg_km(
    a: str, b: str, pos: dict[str, tuple[float, float]], km_matrix: dict[str, float]
) -> float | None:
    """Km đường đi a -> b: số của distance_matrix nếu có, không thì chim bay × `ROAD_FACTOR`."""
    if (real := km_matrix.get(f"{a}|{b}")) is not None:
        return round(real, 1)
    if a in pos and b in pos:
        return round(km(pos[a], pos[b]) * ROAD_FACTOR, 1)
    return None


def _thousand(v: float) -> int:
    return int(round(v / 1000) * 1000)


def vehicles_of(mode: str | None, group_size: int) -> int:
    """Số xe tự đi (xe máy / ô tô) của đoàn; phương tiện khác -> 0."""
    if mode not in SEATS_PER_VEHICLE:
        return 0
    return math.ceil(group_size / SEATS_PER_VEHICLE[mode])  # type: ignore[index]


def leg_range(
    distance: float | None,
    mode: str | None,
    group_size: int,
    minutes: float = 0,
    city: str = OTHER_CITY,
    night: bool = False,
) -> tuple[int, int] | None:
    """(thấp, cao) tiền đi một chặng cho cả đoàn, làm tròn nghìn; không rõ phương tiện / km thì
    None. Gọi xe: hãng rẻ nhất / đắt nhất cùng cỡ xe; tự lái tính theo ngày nên None."""
    if distance is None:
        return None
    if mode == "taxi":
        return car_leg_range(distance, minutes, group_size, city, night)
    return None  # tự lái: tính theo ngày (`rental_of`), không theo chặng


def leg_cost(
    distance: float | None,
    mode: str | None,
    group_size: int,
    minutes: float = 0,
    city: str = OTHER_CITY,
    night: bool = False,
) -> int | None:
    """Tiền đi một chặng cho cả đoàn, làm tròn nghìn; không rõ phương tiện / km thì None.

    Gọi xe (taxi): trung bình giá các hãng trong `shared/fares.py` theo thành phố và cỡ xe;
    tự lái tính theo ngày (thuê + xăng, `rental_of`) nên None.
    """
    if distance is None:
        return None
    if mode == "taxi":
        return car_leg_price(distance, minutes, group_size, city, night)
    return None  # tự lái: tính theo ngày (`rental_of`), không theo chặng


# Số tạm (tu_dat): tốc độ trung bình trong phố, km/h. Gọi taxi cộng thêm phút chờ xe tới đón.
# Không ước xe buýt / phương tiện công cộng (tuyến, giờ chờ không đoán được từ km).
CITY_SPEED: dict[str, float] = {"motorbike": 25.0, "car": 22.0, "taxi": 22.0, "walk": 4.5}
TAXI_PICKUP_MIN = 3
WALK_MAX_KM = 2.0


def travel_alt(
    distance: float | None, mode: str | None, minutes: float | None = None
) -> list[dict[str, Any]]:
    """Phút đi chặng này bằng các phương tiện khác phương tiện đã chọn.

    Có phút của distance_matrix (`minutes`, đo theo xe máy nếu chọn xe máy, còn lại theo ô tô)
    thì nhân theo tỉ lệ tốc độ; không có thì km / tốc độ. Đi bộ chỉ khi chặng ≤ 2 km.
    Chưa chọn phương tiện: ước cả xe máy (mặc định).
    """
    if not minutes and distance is None:
        return []
    # Tốc độ của bản đồ đã đo `minutes` (form.vehicle: xe máy -> motorcycle, còn lại -> car).
    ref = CITY_SPEED["motorbike" if mode == "motorbike" else "car"]
    out: list[dict[str, Any]] = []
    for alt, speed in CITY_SPEED.items():
        if alt == mode:
            continue
        if alt == "walk" and (distance is None or distance > WALK_MAX_KM):
            continue
        drive = minutes * ref / speed if minutes else distance / speed * 60  # type: ignore[operator]
        if alt == "taxi":
            drive += TAXI_PICKUP_MIN
        out.append({"mode": alt, "minutes": max(1, round(drive))})
    return out


def ride_alt(legs: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Tổng phút cả ngày theo từng phương tiện khác. Chỉ giữ phương tiện có số ở mọi chặng
    (đi bộ mà có một chặng > 2 km thì không cộng)."""
    legs = [x for x in legs if x]
    if not legs:
        return []
    out = []
    for alt in CITY_SPEED:
        mins = [a["minutes"] for x in legs for a in x if a["mode"] == alt]
        if len(mins) == len(legs):
            out.append({"mode": alt, "minutes": sum(mins)})
    return out


ESTIMATE_SPREAD = 0.25  # giá ước (price_by "ước ...") ±25%


def price_range(price: int | None, price_by: str | None) -> tuple[int | None, int | None]:
    """Khoảng giá cả đoàn: giá thật -> (giá, giá); giá ước -> ±25% (thấp làm tròn xuống, cao làm
    tròn lên nghìn, nên luôn kẹp giá); chưa có giá -> (None, None)."""
    if price is None:
        return None, None
    if not (price_by or "").startswith("ước"):
        return price, price
    low = math.floor(price * (1 - ESTIMATE_SPREAD) / 1000) * 1000
    return low, math.ceil(price * (1 + ESTIMATE_SPREAD) / 1000) * 1000


# Chi phí phát sinh: không tính trước được từng khoản (thuế, mưa làm giá xe tăng, hư xe, gửi xe,
# nước, ăn vặt...), nên để dành theo % tiền ở + vé + ăn + đi lại của ngày. Số tạm (tu_dat).
EXTRA_LABEL = "Chi phí phát sinh"
EXTRA_PCT = (0.10, 0.15)
EXTRA_PCT_RAIN = 0.05  # ngày có buổi mưa: thêm vào mức cao
EXTRA_PCT_PEAK = 0.05  # lễ / cao điểm: thêm vào mức cao
EXTRA_PCT_CAP = 0.25
RAIN_PCT = 50  # một buổi có khả năng mưa từ mức này thì coi ngày có mưa
MARKET_WORDS = CATEGORY_GROUPS["chợ"]


def extra_pct(rainy: bool, peak: bool) -> tuple[float, float]:
    high = EXTRA_PCT[1] + (EXTRA_PCT_RAIN if rainy else 0) + (EXTRA_PCT_PEAK if peak else 0)
    return EXTRA_PCT[0], min(high, EXTRA_PCT_CAP)


def extra_notes(items: list[Any], form: TripForm, rainy: bool, peak: bool) -> list[str]:
    """Vì sao cần để dành phát sinh, theo đúng ngày / chuyến này (hiện cho người dùng)."""
    notes = [
        "Thuế, phí dịch vụ, phụ thu không ghi trong giá niêm yết",
        "Nước uống, ăn vặt, đồ dùng lặt vặt",
    ]
    mode = form.travel_mode
    if mode in ("motorbike", "car"):
        notes.append("Gửi xe, đổ thêm xăng, hư xe hoặc thủng lốp dọc đường")
    elif mode == "taxi":
        notes.append("Giá gọi xe tăng giờ cao điểm hoặc khi trời mưa, phí chờ")
    else:
        notes.append("Gửi xe, đi lại phát sinh ngoài lịch")
    if rainy:
        notes.append("Trời mưa: gọi xe giá cao hơn, mua áo mưa, đổi sang điểm trong nhà")
    if peak:
        notes.append("Lễ, cao điểm: giá phòng, đồ ăn, vé có thể tăng")
    unknown = [
        i.name
        for i in items
        if i.type == "visit" and (i.price is None or (i.price_by or "").startswith("ước"))
    ]
    if unknown:
        notes.append(f"Vé, dịch vụ tại điểm chưa rõ giá (cáp treo, thuê đồ…): {_join(unknown)}")
    if any(
        i.type in ("visit", "meal")
        and any(w in f"{i.category or ''} {i.name}".lower() for w in MARKET_WORDS)
        for i in items
    ):
        notes.append("Quà, đặc sản mua ở chợ")
    return notes


def day_extras(
    items: list[Any], form: TripForm, subtotal: int, rainy: bool = False, peak: bool = False
) -> list[dict[str, Any]]:
    """Phát sinh một ngày cho cả đoàn = % của `subtotal` (ở + vé + ăn + đi lại, số giữa):
    [{label, min, max, reason}] — một khoản, lý do là các ví dụ của ngày đó."""
    if subtotal <= 0:
        return []
    low, high = extra_pct(rainy, peak)
    return [
        {
            "label": EXTRA_LABEL,
            "min": _thousand(subtotal * low),
            "max": _thousand(subtotal * high),
            "reason": f"{round(low * 100)}–{round(high * 100)}% chi phí ngày: "
            + "; ".join(extra_notes(items, form, rainy, peak)),
        }
    ]


SESSIONS = [("morning", "Sáng", 6, 12), ("afternoon", "Chiều", 12, 18), ("evening", "Tối", 18, 22)]
# Đọc mô tả thời tiết (WeatherAPI lang=vi, hoặc tiếng Anh) -> (icon, câu ngắn), nặng nhất trước.
SKY = [
    ("storm", "Có dông", ("dông", "sấm", "thunder")),
    ("rain", "Mưa lớn", ("mưa to", "mưa lớn", "mưa nặng hạt", "heavy rain", "torrential")),
    ("rain", "Có mưa rào", ("mưa", "rain", "drizzle", "shower")),
    ("fog", "Sương mù", ("sương", "fog", "mist")),
    ("cloud", "Nhiều mây", ("mây", "u ám", "cloud", "overcast")),
    ("sun", "Nắng ráo", ("nắng", "quang", "sunny", "clear")),
]
RAINY_PCT = 50  # khả năng mưa từ mức này coi là có mưa dù mô tả không nói


def _sky(texts: list[str], rain: int | None) -> tuple[str, str]:
    low = " ".join(texts).lower()
    hits = [(icon, label) for icon, label, words in SKY if any(w in low for w in words)]
    if rain is not None and rain >= RAINY_PCT and not any(i in ("storm", "rain") for i, _ in hits):
        hits.insert(0, ("rain", "Có mưa rào"))
    if hits:
        return hits[0]
    return ("sun", "Nắng ráo") if (rain or 0) < 20 else ("cloud", "Nhiều mây")


def _weather_day(state: dict[str, Any], day: int, iso: str) -> dict[str, Any] | None:
    days = state.get("weather") or []
    by_date = {str(d.get("date"))[:10]: d for d in days if d.get("date")}
    if by_date:
        return by_date.get(iso)
    return days[day - 1] if 1 <= day <= len(days) else None


def day_weather(
    state: dict[str, Any], day: int, iso: str
) -> tuple[float | None, float | None, list[dict[str, Any]]]:
    """(nhiệt độ thấp, cao, các buổi). Ngày ngoài tầm dự báo -> (None, None, [])."""
    w = _weather_day(state, day, iso)
    if not w:
        return None, None, []
    sessions = []
    hours = [h for h in w.get("hours") or [] if h.get("time")]
    for part, label, a, b in SESSIONS:
        inside = [h for h in hours if a <= int(str(h["time"])[:2]) < b]
        if not inside:
            continue
        chances = [
            h["chance_of_rain_pct"] for h in inside if h.get("chance_of_rain_pct") is not None
        ]
        rain = int(max(chances)) if chances else None
        icon, condition = _sky([str(h.get("condition") or "") for h in inside], rain)
        if part == "evening" and icon == "sun":
            condition = "Trời quang"
        sessions.append(
            {
                "part": part,
                "label": label,
                "condition": condition,
                "rain_chance": rain,
                "icon": icon,
            }
        )
    return w.get("temp_min_c"), w.get("temp_max_c"), sessions


PACK_GROUPS = ["Giấy tờ", "Quần áo", "Sức khỏe", "Thiết bị"]
MAX_PACKING = 14
COLD_C = 18
RELIGIOUS = ("chùa", "đền", "lăng", "nhà thờ", "miếu", "thiền viện", "tu viện")
CASH_PLACES = ("chợ", "quán", "ăn vặt", "vỉa hè", "bánh", "bún", "phở", "mì", "cơm")
MANY_STOPS = 4  # chỗ chơi trong một ngày từ mức này thì cần giày êm
MANY_PHOTOS = 6  # tổng chỗ chơi cả chuyến từ mức này thì nhắc dọn bộ nhớ


def _join(names: list[str], n: int = 3) -> str:
    names = list(dict.fromkeys(names))
    return ", ".join(names[:n]) + (f" và {len(names) - n} chỗ khác" if len(names) > n else "")


def packing(form: TripForm, days: list[Any], purposes: list[str]) -> list[dict[str, str]]:
    """Hành lý theo luật; `days` là các `Day` của phương án được chọn."""
    out: list[dict[str, str]] = []

    def add(group: str, item: str, reason: str) -> None:
        out.append({"group": group, "item": item, "reason": reason})

    stops = [(d, i) for d in days for i in d.items if i.type in ("visit", "meal")]
    visits = [i for _, i in stops if i.type == "visit"]
    text = {id(i): f"{i.category or ''} {i.name}".lower() for _, i in stops}
    sessions = [(d, s if isinstance(s, dict) else s.model_dump()) for d in days for s in d.sessions]
    mins = [d.temp_min for d in days if d.temp_min is not None]

    add(
        "Giấy tờ",
        "CCCD / hộ chiếu",
        "Nhận phòng khách sạn, mua vé tham quan" if form.num_nights else "Mua vé tham quan",
    )
    if form.travel_mode == "motorbike":
        add("Giấy tờ", "Bằng lái A1, đăng ký xe", "Đi xe máy suốt chuyến")
    elif form.travel_mode == "car":
        add("Giấy tờ", "Bằng lái B, đăng ký xe", "Tự lái ô tô suốt chuyến")
    cash = [i.name for _, i in stops if any(w in text[id(i)] for w in CASH_PLACES)]
    if cash:
        add(
            "Giấy tờ",
            "Tiền mặt mệnh giá nhỏ",
            f"Chợ, quán nhỏ hay chỉ nhận tiền mặt: {_join(cash)}",
        )

    if mins and min(mins) <= COLD_C:
        add("Quần áo", "Áo khoác ấm", f"Nhiệt độ thấp nhất khoảng {min(mins):.0f}°C")
    rainy = [
        (d, s)
        for d, s in sessions
        if (s["rain_chance"] or 0) >= RAINY_PCT or s["icon"] in ("rain", "storm")
    ]
    if rainy:
        when = _join([f"{s['label'].lower()} ngày {d.day}" for d, s in rainy], 4)
        add("Quần áo", "Áo mưa / ô", f"Dự báo mưa {when}")
    heavy = [d.day for d, s in sessions if s["icon"] == "storm" or s["condition"] == "Mưa lớn"]
    if heavy:
        days_txt = _join([f"ngày {n}" for n in heavy])
        add("Quần áo", "Dép / giày chống trơn", f"Mưa lớn hoặc dông {days_txt}")
    holy = [i.name for i in visits if any(w in text[id(i)] for w in RELIGIOUS)]
    if holy:
        add("Quần áo", "Trang phục kín vai, gối", f"Viếng {_join(holy)}")
    per_day = max((sum(i.type == "visit" for i in d.items) for d in days), default=0)
    if "adventure" in purposes:
        add("Quần áo", "Giày thể thao", "Chuyến có leo núi, đi bộ đường dài")
    elif per_day >= MANY_STOPS:
        add("Quần áo", "Giày thể thao", f"Có ngày đi {per_day} điểm, đi bộ nhiều")

    sunny = [d.day for d, s in sessions if s["icon"] == "sun"]
    if form.travel_mode == "motorbike":
        add(
            "Sức khỏe",
            "Kem chống nắng, áo chống nắng, khẩu trang",
            "Đi xe máy ngoài trời, bụi và nắng",
        )
    elif sunny:
        add("Sức khỏe", "Kem chống nắng", f"Dự báo nắng {_join([f'ngày {n}' for n in sunny])}")
    add(
        "Sức khỏe",
        "Thuốc cá nhân",
        "Có người từ 65 tuổi, mang đủ thuốc dùng hằng ngày"
        if form.seniors_65
        else "Thuốc say xe, đau bụng, thuốc dùng riêng",
    )
    if form.kids_under_6 or form.kids_6_12:
        kids = form.kids_under_6 + form.kids_6_12
        add("Sức khỏe", "Đồ cho trẻ em", f"Đi cùng {kids} trẻ: sữa, bỉm, khăn ướt, quần áo thay")

    if form.travel_mode == "motorbike":
        add("Thiết bị", "Mũ bảo hiểm", "Đi xe máy, bắt buộc đội mũ")
    add("Thiết bị", "Sạc dự phòng", "Dùng bản đồ và gọi xe cả ngày")
    if len({i.name for i in visits}) >= MANY_PHOTOS:
        add(
            "Thiết bị",
            "Máy ảnh / điện thoại trống bộ nhớ",
            f"{len({i.name for i in visits})} điểm tham quan để chụp ảnh",
        )
    out.sort(key=lambda x: PACK_GROUPS.index(x["group"]))
    return out[:MAX_PACKING]

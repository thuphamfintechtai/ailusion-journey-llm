"""Âm lịch Việt Nam, ngày lễ, mức đông.

- `to_lunar`: dương -> âm, thuật toán Hồ Ngọc Đức (tính điểm sóc và trung khí theo Jean Meeus,
  "Astronomical Algorithms" 1998), múi giờ +7 — lịch Việt khác lịch Trung Quốc (+8) ở vài năm.
- `holiday_of`: ngày lễ / dịp lớn (dương và âm lịch) và lễ hội của điểm đến pipeline đã tìm.
- `crowd_of`: mức đông theo luật cố định (lễ, cuối tuần sát lễ, lễ hội địa phương, cuối tuần).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

TZ = 7.0
SYNODIC = 29.530588853  # ngày của một tháng âm trung bình
CAN = ["Giáp", "Ất", "Bính", "Đinh", "Mậu", "Kỷ", "Canh", "Tân", "Nhâm", "Quý"]
CHI = ["Tý", "Sửu", "Dần", "Mão", "Thìn", "Tỵ", "Ngọ", "Mùi", "Thân", "Dậu", "Tuất", "Hợi"]


@dataclass(frozen=True)
class LunarDate:
    day: int
    month: int
    year: int
    leap: bool = False

    @property
    def year_name(self) -> str:
        return f"{CAN[(self.year + 6) % 10]} {CHI[(self.year + 8) % 12]}"

    def __str__(self) -> str:
        return f"{self.day}/{self.month}"


def _jd(d: date) -> int:
    return d.toordinal() + 1721425  # số ngày Julius (lịch Gregory)


def _new_moon(k: int) -> int:
    """Ngày Julius (theo giờ +7) của điểm sóc thứ k kể từ 1/1/1900."""
    t = k / 1236.85
    t2, t3 = t * t, t * t * t
    dr = math.pi / 180
    jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * t2 - 0.000000155 * t3
    jd1 += 0.00033 * math.sin((166.56 + 132.87 * t - 0.009173 * t2) * dr)
    m = 359.2242 + 29.10535608 * k - 0.0000333 * t2 - 0.00000347 * t3
    mpr = 306.0253 + 385.81691806 * k + 0.0107306 * t2 + 0.00001236 * t3
    f = 21.2964 + 390.67050646 * k - 0.0016528 * t2 - 0.00000239 * t3
    c1 = (0.1734 - 0.000393 * t) * math.sin(m * dr) + 0.0021 * math.sin(2 * dr * m)
    c1 = c1 - 0.4068 * math.sin(mpr * dr) + 0.0161 * math.sin(dr * 2 * mpr)
    c1 = c1 - 0.0004 * math.sin(dr * 3 * mpr)
    c1 = c1 + 0.0104 * math.sin(dr * 2 * f) - 0.0051 * math.sin(dr * (m + mpr))
    c1 = c1 - 0.0074 * math.sin(dr * (m - mpr)) + 0.0004 * math.sin(dr * (2 * f + m))
    c1 = c1 - 0.0004 * math.sin(dr * (2 * f - m)) - 0.0006 * math.sin(dr * (2 * f + mpr))
    c1 = c1 + 0.0010 * math.sin(dr * (2 * f - mpr)) + 0.0005 * math.sin(dr * (2 * mpr + m))
    if t < -11:
        delta = 0.001 + 0.000839 * t + 0.0002261 * t2 - 0.00000845 * t3 - 0.000000081 * t * t3
    else:
        delta = -0.000278 + 0.000265 * t + 0.000262 * t2
    return math.floor(jd1 + c1 - delta + 0.5 + TZ / 24)


def _sun_longitude(jdn: int) -> int:
    """Kinh độ mặt trời lúc 0h ngày `jdn`, chia 12 phần (0..11) — để biết trung khí."""
    t = (jdn - 2451545.5 - TZ / 24) / 36525
    t2 = t * t
    dr = math.pi / 180
    m = 357.52910 + 35999.05030 * t - 0.0001559 * t2 - 0.00000048 * t * t2
    l0 = 280.46645 + 36000.76983 * t + 0.0003032 * t2
    dl = (1.914600 - 0.004817 * t - 0.000014 * t2) * math.sin(dr * m)
    dl += (0.019993 - 0.000101 * t) * math.sin(dr * 2 * m) + 0.000290 * math.sin(dr * 3 * m)
    lon = (l0 + dl) * dr
    lon -= 2 * math.pi * math.floor(lon / (2 * math.pi))
    return math.floor(lon / math.pi * 6)


def _month11(year: int) -> int:
    """Ngày đầu tháng 11 âm (tháng chứa Đông chí) của năm dương `year`."""
    off = _jd(date(year, 12, 31)) - 2415021
    k = math.floor(off / SYNODIC)
    nm = _new_moon(k)
    return _new_moon(k - 1) if _sun_longitude(nm) >= 9 else nm


def _leap_offset(a11: int) -> int:
    """Tháng nhuận = tháng đầu tiên sau tháng 11 không có trung khí."""
    k = math.floor((a11 - 2415021.076998695) / SYNODIC + 0.5)
    i = 1
    arc = _sun_longitude(_new_moon(k + i))
    last = -1
    while arc != last and i < 14:
        last = arc
        i += 1
        arc = _sun_longitude(_new_moon(k + i))
    return i - 1


def to_lunar(d: date) -> LunarDate:
    day_number = _jd(d)
    k = math.floor((day_number - 2415021.076998695) / SYNODIC)
    month_start = _new_moon(k + 1)
    if month_start > day_number:
        month_start = _new_moon(k)
    a11 = _month11(d.year)
    b11 = a11
    if a11 >= month_start:
        year = d.year
        a11 = _month11(d.year - 1)
    else:
        year = d.year + 1
        b11 = _month11(d.year + 1)
    diff = math.floor((month_start - a11) / 29)
    month, leap = diff + 11, False
    if b11 - a11 > 365:
        leap_diff = _leap_offset(a11)
        if diff >= leap_diff:
            month = diff + 10
            leap = diff == leap_diff
    if month > 12:
        month -= 12
    if month >= 11 and diff < 4:
        year -= 1
    return LunarDate(day_number - month_start + 1, month, year, leap)


# Ngày lễ (quy ước sản phẩm): nghỉ lễ chính thức và các dịp lớn thường đông.
SOLAR_HOLIDAYS = {
    (1, 1): "Tết Dương lịch",
    (30, 4): "Lễ 30/4",
    (1, 5): "Lễ 1/5",
    (2, 9): "Lễ 2/9",
}
LUNAR_HOLIDAYS = {
    (1, 1): "Mùng 1 Tết",
    (2, 1): "Mùng 2 Tết",
    (3, 1): "Mùng 3 Tết",
    (10, 3): "Giỗ Tổ Hùng Vương",
    (15, 1): "Rằm tháng Giêng",
    (15, 7): "Lễ Vu Lan",
    (15, 8): "Tết Trung Thu",
}
# Dịp lớn nhưng không phải ngày nghỉ: đông ở chùa / phố, không đông bằng ngày nghỉ lễ.
NOT_DAY_OFF = {"Rằm tháng Giêng", "Lễ Vu Lan", "Tết Trung Thu"}
NEAR_HOLIDAY_DAYS = 2  # cuối tuần cách ngày nghỉ lễ ngần này ngày coi là "sát lễ"


def national_holiday(d: date) -> str | None:
    if name := SOLAR_HOLIDAYS.get((d.day, d.month)):
        return name
    lunar = to_lunar(d)
    return None if lunar.leap else LUNAR_HOLIDAYS.get((lunar.day, lunar.month))


def festival_on(d: date, festivals: list[dict[str, Any]] | None) -> str | None:
    """Lễ hội của điểm đến (từ find_festivals) diễn ra đúng ngày `d`; chỉ xét lễ hội có ngày."""
    for f in festivals or []:
        try:
            start = date.fromisoformat(str(f.get("start"))[:10])
            end = date.fromisoformat(str(f.get("end") or f.get("start"))[:10])
        except ValueError:
            continue
        if start <= d <= end and f.get("name"):
            return str(f["name"])
    return None


def holiday_of(d: date, festivals: list[dict[str, Any]] | None = None) -> str | None:
    return national_holiday(d) or festival_on(d, festivals)


def crowd_of(d: date, festivals: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    """(mức, câu ghi chú). Cao: ngày nghỉ lễ, cuối tuần sát lễ, có lễ hội địa phương; vừa: cuối
    tuần, dịp lớn không nghỉ; thấp: ngày thường."""
    weekend = d.weekday() >= 5
    name = national_holiday(d)
    if name and name not in NOT_DAY_OFF:
        return "high", f"{name}, rất đông"
    if fest := festival_on(d, festivals):
        return "high", f"Có {fest}, đông"
    if weekend:
        near = [
            national_holiday(d + timedelta(days=k))
            for k in range(-NEAR_HOLIDAY_DAYS, NEAR_HOLIDAY_DAYS + 1)
            if k
        ]
        near = [n for n in near if n and n not in NOT_DAY_OFF]
        if near:
            return "high", f"Cuối tuần sát {near[0]}, rất đông"
        if name:
            return "high", f"{name} vào cuối tuần, đông"
        return "medium", "Cuối tuần, đông vừa"
    if name:
        return "medium", f"{name}, chùa và phố đông hơn"
    return "low", "Ngày thường, ít đông"

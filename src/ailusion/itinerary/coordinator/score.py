"""Làn Coordinator — bước 7–8: chấm điểm, chọn chỗ ở, chốt danh sách đưa Planner.

- Bước 7 (`cham`): thời gian ở lại (DB; thiếu thì ước theo loại chỗ `STAY_BY_TYPE`); chọn chỗ ở:
  so 4 mặt (tổng tiền, phút ngồi xe tới các chỗ chơi, sao tin được, tiện ích), lấy tầng không bị
  trội, rồi xếp theo điều nhóm khách coi trọng.
- Bước 8 (`chot_danh_sach`): ma trận phút đi giữa chỗ ở và các chỗ đáng xét; sức chứa từng ngày
  = phút rảnh ÷ (thời gian ở lại phổ biến + thời gian đi phổ biến); lấy từ tầng không bị trội cho
  tới khi đủ, đặt mã ngắn D1, D2… (chỗ chơi), Q1, Q2… (quán) cho Planner; chia chỗ cho từng
  ngày theo cụm gần nhau (`split_by_day`) để một ngày không chạy từ đầu này sang đầu kia.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any

from ..shared.context import Ctx
from ..shared.form import (
    MEAL_STAY,
    NEAR_MIN,
    day_allowance,
    meets_needs,
    money,
    needed_meals,
    required_meals,
    rooms_for,
    to_minutes,
)
from ..shared.math import km, median, pareto_tiers
from ..shared.models import (
    STAY,
    DayWindow,
    ItineraryState,
    Place,
    dump_places,
    form_of,
    place_from_dict,
    place_to_dict,
    places_of,
    profile_of,
    same_brand,
    stays_of,
)
from ..shared.tools import tool_ok
from .levels import below_level


class NoPlacesError(Exception):
    """Không có điểm tham quan nào ở điểm đến (DB lẫn tool tìm kiếm đều trống)."""

    def __init__(self, destination: str) -> None:
        super().__init__(f"Chưa có dữ liệu địa điểm cho {destination}")
        self.destination = destination


# Thời gian ở lại theo loại — chỉ dùng khi Google không có dòng "Mọi người thường dành …".
# Số tạm (nguồn "tu_dat"), kết quả ghi rõ "ước theo loại".
STAY_BY_TYPE: list[tuple[tuple[str, ...], int, int]] = [
    (("bảo tàng",), 60, 90),
    (("chùa", "nhà thờ", "đền", "lăng"), 30, 60),
    (("bãi biển", "biển"), 90, 180),
    (("khu vui chơi", "công viên giải trí"), 120, 240),
    (("chợ",), 45, 90),
    (("cà phê", "quán cà phê"), 45, 90),
    (("spa", "massage"), 60, 120),
    (("núi", "thác", "rừng", "hang"), 90, 180),
]
DEFAULT_STAY = (60, 90)

# Giá ước mỗi người (VND) cho chỗ chưa có giá thật sau khi đã hỏi DB và Agent Budget. Ưu tiên
# trung vị giá của các chỗ cùng loại có giá thật trong chính chuyến này (`fill_price_estimates`);
# không có thì theo bảng dưới — số tạm (nguồn "tu_dat"), kết quả ghi rõ "ước". Từ khoá so theo thứ
# tự: "công viên giải trí" phải đứng trước "công viên".
PRICE_BY_TYPE: list[tuple[tuple[str, ...], int]] = [
    (("công viên giải trí", "khu vui chơi", "sun world", "vinwonders", "công viên nước"), 700_000),
    (("spa", "massage", "mát xa"), 400_000),
    (("suối khoáng", "tắm bùn", "suối nước nóng"), 300_000),
    (("bảo tàng", "di tích", "triển lãm", "thành cổ", "lăng"), 50_000),
    (("cà phê", "cafe", "coffee"), 60_000),
    # Chỉ loại gần như luôn miễn phí. Không đoán theo "cầu", "hồ": Cầu Rồng miễn phí nhưng Cầu Vàng
    # nằm trong khu Bà Nà phải mua vé.
    (
        (
            "bãi biển",
            "bãi tắm",
            "chùa",
            "đền",
            "nhà thờ",
            "hành hương",
            "thánh địa",
            "tượng",
            "đài tưởng niệm",
            "quảng trường",
            "phố đi bộ",
            "công viên",
            "bán đảo",
            "chợ",
        ),
        0,
    ),
]
VISIT_PRICE = 100_000  # chỗ chơi không khớp loại nào
MEAL_PRICE = 150_000  # một bữa mỗi người khi không quán nào trong chuyến có giá
MIN_SAME_TYPE_PRICES = 2  # cần ít nhất ngần này chỗ cùng loại có giá thật mới lấy trung vị


def fill_price_estimates(places: dict[str, Place]) -> None:
    """Điền giá ước cho chỗ chơi / quán chưa có giá, ghi nguồn vào `price_by` ("ước …")."""
    known: dict[str, list[float]] = {}
    for p in places.values():
        if p.price is not None and not (p.price_by or "").startswith("ước"):
            known.setdefault("meal" if p.kind == "meal" else p.group, []).append(float(p.price))
    # Chỗ chơi không khớp loại nào: trung vị giá thật của mọi CHỖ CHƠI trong chuyến (dữ liệu của
    # chính điểm đến) trước khi dùng số tự đặt VISIT_PRICE. Không lấy quán cà phê nghỉ trưa
    # (kind "rest") hay quán ăn: giá ly cà phê không phải giá vé.
    visit_prices = [
        float(p.price)
        for p in places.values()
        if p.kind == "visit" and p.price is not None and not (p.price_by or "").startswith("ước")
    ]
    for p in places.values():
        if p.price is not None:
            continue
        same = known.get("meal" if p.kind == "meal" else p.group, [])
        if len(same) >= MIN_SAME_TYPE_PRICES:
            p.price, p.price_by = int(median(same) or 0), "ước theo chỗ cùng loại"
        elif p.kind == "meal":
            p.price, p.price_by = MEAL_PRICE, "ước theo loại"
        else:
            typed = next((v for words, v in PRICE_BY_TYPE if any(w in p.text for w in words)), None)
            if typed is None and len(visit_prices) >= MIN_SAME_TYPE_PRICES:
                p.price, p.price_by = int(median(visit_prices) or 0), "ước theo chỗ chơi khác"
            else:
                p.price = VISIT_PRICE if typed is None else typed
                p.price_by = "ước theo loại"


def fill_stay_estimate(place: Place) -> None:
    if place.stay_low is not None:
        return
    if place.kind == "meal":
        place.stay_low, place.stay_high = MEAL_STAY
    else:
        low, high = next(
            ((lo, hi) for words, lo, hi in STAY_BY_TYPE if any(w in place.text for w in words)),
            DEFAULT_STAY,
        )
        place.stay_low, place.stay_high = low, high
    place.stay_by = "ước theo loại"


MAX_DETAILS = 10  # get_place_details (tốn quota SerpApi) để lấy thời gian ở lại
MAX_STAYS_COMPARED = 6
# Tool distance_matrix nhận tối đa 25 điểm (đi + đến) mỗi lượt (MCP `_TOI_DA_DIEM`). Nhiều điểm
# hơn thì chia thành ô TILE điểm đi × TILE điểm đến, gọi song song rồi ghép (`matrix_pairs`).
TOOL_MATRIX_POINTS = 25
TILE = 12
# Số điểm tối đa (chỗ ở + chỗ chơi + quán) đem vào danh sách của một phương án: 45 điểm = 16 ô.
MAX_MATRIX_POINTS = 45


async def matrix_pairs(
    ctx: Ctx, points: list[Place], vehicle: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mọi cặp (from, to) giữa `points` (index trong `points`) -> (cặp, nhật ký tool). Một ô lỗi
    thì các cặp của ô đó thiếu, chỗ gọi ước theo đường chim bay."""
    coords = [f"{p.lat},{p.lng}" for p in points]
    if len(points) <= TOOL_MATRIX_POINTS:
        data, log = await ctx.tool("distance_matrix", origins=coords, vehicle=vehicle)
        return (data.get("pairs") or []) if tool_ok(data) else [], [log]
    chunks = [list(range(i, min(i + TILE, len(points)))) for i in range(0, len(points), TILE)]
    tiles = [(a, b) for a in chunks for b in chunks]
    calls = [
        (
            "distance_matrix",
            {"origins": [coords[i] for i in a], "vehicle": vehicle}
            if a is b
            else {
                "origins": [coords[i] for i in a],
                "destinations": [coords[j] for j in b],
                "vehicle": vehicle,
            },
        )
        for a, b in tiles
    ]
    results, logs = await ctx.many(calls)
    pairs = []
    for (a, b), data in zip(tiles, results, strict=True):
        if not tool_ok(data):
            continue
        # Tool đánh số điểm đến tiếp sau điểm đi (ô vuông thì dùng chung số).
        offset = 0 if a is b else len(a)
        for x in data.get("pairs") or []:
            pairs.append({**x, "from": a[x["from"]], "to": b[x["to"] - offset]})
    return pairs, logs


# Ước phút đi khi distance_matrix không trả được cặp nào đó: km đường chim bay × hệ số đường vòng
# ÷ tốc độ trong phố, cộng phút gửi xe. Số tạm (nguồn "tu_dat"), kết quả ghi rõ là ước.
DETOUR = 1.4
CITY_KMH = {"motorbike": 25, "car": 25, "taxi": 25, "bus": 15}
PARKING_MIN = 3


def estimate_minutes(a: Place, b: Place, travel_mode: str | None) -> float:
    speed = CITY_KMH.get(travel_mode or "", 25)
    return round(km((a.lat, a.lng), (b.lat, b.lng)) * DETOUR / speed * 60 + PARKING_MIN)


def capacity(days: list[DayWindow], typical_stay: float, typical_hop: float) -> list[int]:
    """Số chỗ chơi mỗi ngày chứa được. Ngày không đủ giờ cho 1 chỗ (tới nơi 19:45, về chỗ ở 20:45;
    rời chỗ ở 08:00 để kịp chuyến về) là 0 — trước đây ít nhất 1, ngày đó vẫn bị chia chỗ."""
    per = max(1.0, typical_stay + typical_hop)
    return [max(0, math.floor(d.free_minutes / per)) for d in days]


def idle_days(days: list[DayWindow], caps: list[int], places: dict[str, Place]) -> list[int]:
    """Ngày không xếp lịch: không đủ giờ cho 1 chỗ chơi (`capacity` 0), không có bữa bắt buộc,
    không có chỗ user ghim vào ngày đó. Code tự tạo lịch trống, không đưa Planner."""
    return [
        d.day
        for d, cap in zip(days, caps, strict=True)
        if cap == 0
        and not needed_meals(d)
        and not any(p.must and p.must_day == d.day for p in places.values())
    ]


SPARE_VISITS = 2  # mỗi ngày thêm ngần này chỗ chơi dư để Planner có lựa chọn khi xếp lịch khác nhau


def meal_points(want_visits: int, want_meals: int, room: int) -> int:
    """Số điểm của ma trận dành cho quán (phần còn lại cho chỗ chơi). Đủ chỗ cho cả hai: quán lấy
    đủ, chỗ chơi lấy phần còn lại. Không đủ: chia theo tỉ lệ nhu cầu — trước đây quán lấy trước,
    chuyến dài hết chỗ chơi (Đà Lạt 5 ngày: 31 quán, chỗ chơi còn 13 điểm -> ngày 1 chỗ chơi)."""
    if want_visits + want_meals <= room:
        return want_meals
    return round(room * want_meals / (want_visits + want_meals))


def visit_need(days: list[DayWindow], visits: list[Place], travel_mode: str | None) -> int:
    """Số chỗ chơi cả chuyến cần khi CHƯA có ma trận (để chia điểm của ma trận): sức chứa các ngày
    như `pick_pool` dùng, thời gian ở lại là trung vị các chỗ, phút đi ước theo đường chim bay
    (`estimate_minutes`) giữa các chỗ; + SPARE_VISITS mỗi ngày."""
    stay = median([float(p.stay_low) for p in visits if p.stay_low]) or 60.0
    hop = median([float(estimate_minutes(a, b, travel_mode)) for a, b in combinations(visits, 2)])
    return sum(capacity(days, stay, hop or 20.0)) + SPARE_VISITS * len(days)


def place_vector(center: tuple[float, float] | None) -> Any:
    """5 mặt so chỗ (bước 8), đổi về "lớn hơn là tốt hơn": sao tin được, độ nổi tiếng (số
    review), hợp mục đích, giá (âm), khoảng cách tới trung tâm (âm). Thiếu độ nổi tiếng thì chỗ
    4,3★ / 29.000 review (Chùa Cầu) bị phòng tranh 4,8★ / 88 review hơn hẳn, rơi khỏi danh sách."""

    def vector(p: Place) -> list[float | None]:
        return [
            p.rating_floor,
            float(p.reviews or 0),
            1.0 if p.matches_purpose else 0.0,
            -p.price if p.price is not None else None,
            -km(center, (p.lat, p.lng)) if center else 0.0,
        ]

    return vector


# Loại nên thưa hơn "mỗi ngày 1 chỗ": số chỗ tối đa cả chuyến = số ngày × tỉ lệ (ít nhất 1).
# Spa: 1 chỗ mỗi 2 ngày — mục đích "nghỉ dưỡng" có từ khoá spa nên dễ kéo spa vào mọi ngày.
TYPE_SHARE = {"spa": 0.5}


def type_cap(group: str, days: int) -> int:
    """Số chỗ tối đa của một loại trong cả chuyến."""
    return max(1, math.ceil(days * TYPE_SHARE.get(group, 1.0)))


def pick_pool(
    visits: list[Place],
    meals: list[Place],
    need_visits: int,
    need_meals: int,
    center: Any,
    per_group: int,
    used: set[str] | None = None,
    costly: set[str] | None = None,
) -> tuple[list[Place], list[list[str]]]:
    """Lấy hết tầng 1, rồi tầng 2… cho tới khi đủ số chỗ cả chuyến cần. Quán ăn chọn cùng cách.
    `costly`: chỗ đắt hơn phần tiền của nó (`costly_ids`) — xếp sau mọi tầng của chỗ vừa túi tiền,
    chỉ lấy khi thiếu.

    Chỗ chơi cùng một loại (`Place.group`: spa, chùa…) lấy tối đa `type_cap` chỗ (`per_group` =
    số ngày; spa thưa hơn): không thì "nghỉ dưỡng" kéo toàn spa vào, ngày nào cũng spa.
    Chỗ chơi dành cho phương án khác (`used`) chỉ lấy khi chỗ của phương án này chưa đủ: chia
    tầng chung thì chỗ nổi tiếng nhất (Chùa Cầu) luôn ở tầng 1, phương án nào cũng có, ngày 1 của
    3 phương án giống hệt nhau.
    """
    used, costly = used or set(), costly or set()
    chosen: list[Place] = []
    tiers_out: list[list[str]] = []
    own = [p for p in visits if p.place_id not in used and p.place_id not in costly]
    others = [p for p in visits if p.place_id in used and p.place_id not in costly]
    dear = [p for p in visits if p.place_id in costly]
    cheap_meals = [m for m in meals if m.place_id not in costly]
    dear_meals = [m for m in meals if m.place_id in costly]
    for items, need, cap in ((own, need_visits, per_group), (cheap_meals, need_meals, None)):
        taken = 0
        per: dict[str, int] = {}
        tiers = pareto_tiers(items, place_vector(center))
        if items is own:  # phần của phương án khác xếp sau mọi tầng của phương án này
            tiers += pareto_tiers(others, place_vector(center))
        # Chỗ đắt hơn túi tiền xếp sau cùng, rẻ nhất trước (không theo sao: ngân sách 5 triệu
        # Phú Quốc từng lấy Vua Hải Sản 1 triệu/người trước quán 250k).
        over = dear if items is own else dear_meals
        tiers += [[p] for p in sorted(over, key=lambda p: p.price or 0)]
        for tier in tiers:
            if taken >= need:
                break
            kept = []
            for p in sorted(tier, key=lambda p: -(p.rating_floor or 0)):
                if cap is not None and per.get(p.type_key, 0) >= type_cap(p.type_key, cap):
                    continue
                per[p.type_key] = per.get(p.type_key, 0) + 1
                kept.append(p)
            chosen += kept
            taken += len(kept)
            tiers_out.append([p.place_id for p in kept])
    return chosen, tiers_out


def costly_ids(
    places: list[Place], allowance: int | None, group_size: int
) -> tuple[set[str], int | None, int | None]:
    """Chỗ đắt hơn phần tiền của nó, theo tiền nên tiêu mỗi ngày (`day_allowance`, đúng số
    Planner nhận): quán có giá mỗi người hơn 1/3 tiền mỗi người mỗi ngày (3 bữa), chỗ chơi có vé
    mỗi người hơn cả tiền một ngày của một người. Chỗ user chọn / thứ user nhắc không tính.
    -> (place_id, trần một bữa, trần một vé). Không có ngân sách thì không chỗ nào."""
    if not allowance:
        return set(), None, None
    per_person = allowance / max(1, group_size)
    meal_cap, visit_cap = int(per_person / MEALS_PER_DAY), int(per_person)
    out = {
        p.place_id
        for p in places
        if not p.must
        and not p.wanted
        and p.price is not None
        and p.price > (meal_cap if p.kind == "meal" else visit_cap)
    }
    return out, meal_cap, visit_cap


def one_per_brand(meals: list[Place], center: tuple[float, float] | None = None) -> list[Place]:
    """Mỗi thương hiệu / chuỗi quán chỉ giữ 1 chi nhánh: sao tin được cao nhất, rồi gần trung tâm
    nhất. Không thì cả chuyến ăn 3 bữa ở 3 chi nhánh "Lẩu Gà Lá É Tao Ngộ"."""
    best = sorted(
        meals,
        key=lambda p: (-(p.rating_floor or 0), km(center, (p.lat, p.lng)) if center else 0.0),
    )
    kept: list[Place] = []
    for p in best:
        if not any(same_brand(p, k) for k in kept):
            kept.append(p)
    return kept


def varied_meals(meals: list[Place]) -> list[Place]:
    """Xếp quán cho đa dạng món: mỗi lượt lấy tối đa `MEALS_PER_CUISINE` quán mỗi loại (giữ thứ tự
    có sẵn trong loại), quán cùng loại thừa ra xuống lượt sau."""
    seen: dict[str, int] = {}
    keyed = []
    for i, p in enumerate(meals):
        keyed.append((seen.get(p.group, 0) // MEALS_PER_CUISINE, i, p))
        seen[p.group] = seen.get(p.group, 0) + 1
    return [p for _, _, p in sorted(keyed, key=lambda t: t[:2])]


MEALS_PER_CUISINE = 2
MEAL_CHOICES = 2  # số quán cho mỗi bữa bắt buộc đưa Planner (chỉ 1 thì quán lặp giữa các ngày)
MEALS_PER_DAY = 3  # sáng, trưa, tối: đủ ngần này quán mỗi ngày thì mỗi ngày có danh sách quán riêng
WANTED_MEALS = 3  # mỗi món user nhắc: giữ tối đa ngần này quán trong danh sách đưa Planner
WANTED_VISITS = 4  # chỗ chơi khớp thứ user nhắc giữ chắc trong danh sách
# Quán cùng loại món với quán đã có của ngày: coi như xa thêm ngần này phút khi chia ngày.
SAME_CUISINE_MIN = 15


# Mỏ neo chỉ chọn trong ngần này × số ngày chỗ nổi tiếng nhất: không để một chỗ ít người biết
# nằm lẻ ngoài rìa thành mỏ neo chỉ vì nó xa.
ANCHOR_POOL = 2


def split_by_day(
    pool: list[Place],
    days: list[int],
    minutes: dict[str, float],
    travel_mode: str | None = None,
    windows: dict[int, tuple[int, int]] | None = None,
    used: set[str] | None = None,
) -> dict[int, list[str]]:
    """Chia chỗ cho từng ngày TRƯỚC khi đưa Planner, theo cụm gần nhau, không ngày nào đi lại chỗ
    ngày khác.

    Chỗ chơi: ngày 1 lấy chỗ nổi tiếng nhất còn mở cửa làm "mỏ neo"; mỗi ngày sau lấy mỏ neo xa
    các mỏ neo trước nhất (phút đi gần nhất tới mỏ neo đã có × hệ số nổi tiếng 0,5–1), để mỗi ngày
    là một vùng. Các chỗ còn lại (nổi tiếng trước) vào ngày có phút đi TRUNG BÌNH tới các chỗ đã
    có của ngày ít nhất, miễn là ngày đó mở cửa, chưa có chỗ cùng loại và chưa đầy. Không ngày nào
    nhận được thì nới luật cùng loại, rồi nới số lượng. Ngày có chỗ chơi cách chỗ đó ≤ `NEAR_MIN`
    phút được xét trước (kể cả khi đã có chỗ cùng loại), ngày gần đã đầy mới sang ngày xa: không
    trộn 2 vùng trong một ngày khi không cần (chuyến Đà Nẵng: chùa thứ 2 ở Hội An vào ngày ở Đà
    Nẵng), mà không ngày nào thiếu chỗ (trần mỗi ngày giữ nguyên). Quán ăn: đủ nhiều (≥
    `MEALS_PER_DAY` quán mỗi ngày) thì vào ngày có cụm chỗ chơi gần nhất, ngày gần trước; ít thì
    ngày nào cũng dùng chung.
    `minutes`: "id_a|id_b" -> phút đi (ma trận bước 8); thiếu cặp thì ước theo đường chim bay.
    `windows`: ngày -> (phút bắt đầu, phút kết thúc) của ngày; có thì "mở cửa" còn phải là giờ mở
    giao khung ngày đủ thời gian ở (show Ký Ức Hội An 16:00–22:00 không vào ngày cuối về 12:30).
    `used`: chỗ của phương án khác (mượn khi thiếu) — không làm mỏ neo khi còn chỗ của mình, không
    thì chỗ nổi tiếng nhất (Chùa Cầu) neo ngày 1 của cả 3 phương án.
    """
    visits = sorted((p for p in pool if p.kind != "meal"), key=lambda p: -(p.reviews or 0))
    meals = sorted((p for p in pool if p.kind == "meal"), key=lambda p: -(p.reviews or 0))
    out: dict[int, list[Place]] = {d: [] for d in days}

    def is_open(p: Place, day: int) -> bool:
        span = p.hours.get(day, "chưa rõ")
        if span is None:
            return False
        if span == "chưa rõ" or not windows or day not in windows:
            return True  # chưa rõ giờ thì coi như mở
        start, end = windows[day]
        return min(span[1], end) - max(span[0], start) >= (p.stay_low or 0)

    def gap(a: Place, b: Place) -> float:
        known = [
            v
            for v in (
                minutes.get(f"{a.place_id}|{b.place_id}"),
                minutes.get(f"{b.place_id}|{a.place_id}"),
            )
            if v is not None
        ]
        return sum(known) / len(known) if known else estimate_minutes(a, b, travel_mode)

    def far(p: Place, day: int) -> float:
        members = [q for q in out[day] if q.kind != "meal"]
        return sum(gap(p, q) for q in members) / len(members) if members else 999.0

    top = math.log1p(max((p.reviews or 0 for p in visits), default=0))

    def fame(p: Place) -> float:
        return 0.5 + 0.5 * math.log1p(p.reviews or 0) / top if top else 1.0

    # Chỗ user chọn: có ngày thì vào đúng ngày đó trước và làm mỏ neo của ngày; không ngày thì
    # được chia trước mọi chỗ khác.
    pinned = [p for p in visits if p.must and p.must_day in out]
    anchors: list[Place] = []
    for p in pinned:
        out[p.must_day].append(p)  # type: ignore[index]
        anchors.append(p)
    rest = [p for p in visits if p not in pinned]
    rest.sort(key=lambda p: not p.must)  # sort ổn định: chỗ user chọn lên đầu, còn lại giữ thứ tự
    famous = {p.place_id for p in visits[: ANCHOR_POOL * len(days)]}
    for day in days:
        if out[day]:
            continue
        open_ = [p for p in rest if is_open(p, day)]
        own = [p for p in open_ if p.place_id not in (used or set())] or open_
        options = [p for p in own if p.place_id in famous] or own
        if not options:
            continue
        anchor = (
            max(options, key=lambda p: min(gap(p, a) for a in anchors) * fame(p))
            if anchors
            else options[0]
        )
        out[day].append(anchor)
        anchors.append(anchor)
        rest.remove(anchor)

    def near(p: Place, day: int) -> bool:
        """Cách một chỗ chơi đang có của ngày ≤ NEAR_MIN phút (ngày chưa có chỗ nào: gần)."""
        members = [q for q in out[day] if q.kind != "meal"]
        return not members or min(gap(p, q) for q in members) <= NEAR_MIN

    per_day = math.ceil(len(visits) / max(1, len(days)))
    for p in rest:
        open_days = [d for d in days if is_open(p, d)] or (list(days) if p.must else [])
        close = [d for d in open_days if near(p, d)]
        # Ngày gần trước, kể cả khi đã có chỗ cùng loại (mỗi LỊCH vẫn chỉ 1 chỗ mỗi loại): đẩy sang
        # ngày ở vùng khác chỉ vì cùng loại từng trộn 2 vùng (chùa thứ 2 ở Hội An vào ngày ở Đà
        # Nẵng). Ngày gần đã đầy mới sang ngày xa — giữ trần số chỗ mỗi ngày để không ngày nào
        # thiếu chỗ chơi.
        for choices, typed in (
            (close, True),
            (close, False),
            (open_days, True),
            (open_days, False),
        ):
            fits = [
                d
                for d in choices
                if len(out[d]) < per_day
                and (not typed or all(q.type_key != p.type_key for q in out[d]))
            ]
            if fits:
                out[min(fits, key=lambda d: (far(p, d), len(out[d])))].append(p)
                break
        else:  # mọi ngày mở cửa đã đầy: nới số lượng
            if open_days:
                out[min(open_days, key=lambda d: (far(p, d), len(out[d])))].append(p)
    pinned_meals = [m for m in meals if m.must and m.must_day in out]
    for m in pinned_meals:
        out[m.must_day].append(m)  # type: ignore[index]
    meals = sorted((m for m in meals if m not in pinned_meals), key=lambda m: not m.must)
    if len(meals) >= MEALS_PER_DAY * len(days):
        per_meal = math.ceil(len(meals) / len(days))
        taken = {d: 0 for d in days}

        def cost(d: int, m: Place) -> tuple[float, int]:
            same = sum(q.kind == "meal" and q.group == m.group for q in out[d])
            return far(m, d) + SAME_CUISINE_MIN * same, taken[d]

        for m in meals:
            # Quán vào ngày có chỗ chơi gần nó trước (không ăn sáng ở Hội An trong ngày chơi Đà
            # Nẵng); ngày gần đã đủ quán mới sang ngày khác.
            fits = (
                [d for d in days if taken[d] < per_meal and near(m, d)]
                or [d for d in days if taken[d] < per_meal]
                or days
            )
            day = min(fits, key=lambda d, m=m: cost(d, m))
            out[day].append(m)
            taken[day] += 1
    else:
        for d in days:
            out[d] += meals
    return {d: [p.place_id for p in ps] for d, ps in out.items()}


def moved_closed_days(musts: list[Place], days: list[int], form: Any) -> list[str]:
    """Chỗ user chọn ghim vào ngày nó đóng cửa: dời sang ngày mở cửa gần nhất, báo lại."""
    notes = []
    for p in musts:
        day = p.must_day
        if day is None or day not in days or p.hours.get(day, "?") is not None:
            continue
        open_days = [d for d in days if p.hours.get(d, "?") is not None]
        if not open_days:
            continue
        new = min(open_days, key=lambda d: abs(d - day))
        notes.append(
            f"“{p.must_name or p.name}” đóng cửa ngày {day} ({form.day_date(day):%d/%m}), "
            f"đã chuyển sang ngày {new}."
        )
        p.must_day = new
    return notes


# Chỗ ở xa trung tâm điểm đến hơn ngần này thì bỏ (DB / SerpApi có chỗ ở gắn nhầm điểm đến, VD
# "Sala Hồ Tràm" khi đi Hội An). Số tạm ("tu_dat").
STAY_MAX_KM = 30
# Tiền phòng cả chuyến không vượt ngần này phần ngân sách cả chuyến (còn ăn, chơi, đi lại). Số tạm.
STAY_BUDGET_SHARE = 0.5
# Tiền phòng cả chuyến nên khoảng ngần này phần ngân sách (phần còn lại cho ăn, chơi, đi lại).
# Số tạm ("tu_dat"); nên thay bằng tỉ lệ thật từ các chuyến user đã chọn.
STAY_TARGET_SHARE = 0.35


def stay_target_share(form: Any) -> float:
    """Phần ngân sách nhắm cho chỗ ở: "sang nhưng trong ngân sách" nhắm tới trần
    STAY_BUDGET_SHARE (chỗ ở tốt nhất mà cả chuyến vẫn không vượt ngân sách), còn lại
    STAY_TARGET_SHARE."""
    return STAY_BUDGET_SHARE if form.spend_style == "luxury_within_budget" else STAY_TARGET_SHARE


STAY_PRICE_PICKS = 8  # chỗ ở giá sát mục tiêu nhất đem so phút đi tới các điểm chơi
STAY_PRICE_SLACK = 0.25  # lệch giá thêm tối đa ngần này (phần của mục tiêu) để được chỗ gần hơn
STAY_RIDE_VISITS = 12  # số điểm chơi nổi bật đem đo phút đi từ chỗ ở


def usable_stays(
    stays: list[Place], center: Any, form: Any, trip_budget: int | None
) -> tuple[list[Place], list[str]]:
    """Chỗ ở được xét: gần trung tâm điểm đến, tiền phòng cả chuyến vừa ngân sách (trừ khi mức chi
    "không giới hạn"). Bỏ hết thì giữ chỗ rẻ nhất trong số gần trung tâm, kèm ghi chú."""
    notes: list[str] = []
    near = [s for s in stays if not center or km(tuple(center), (s.lat, s.lng)) <= STAY_MAX_KM]
    if len(near) < len(stays):
        far = [s.name for s in stays if s not in near]
        notes.append(
            f"Bỏ {len(far)} chỗ ở cách trung tâm {form.destination} hơn {STAY_MAX_KM} km: "
            + ", ".join(far[:3])
        )
    near = near or stays
    if not trip_budget or form.spend_style == "unlimited":
        return near, notes
    rooms = rooms_for(form)
    nights = max(1, form.num_nights)
    cap = trip_budget * STAY_BUDGET_SHARE

    def total(s: Place) -> float | None:
        return s.price * nights * rooms if s.price else None

    fits = [s for s in near if total(s) is None or total(s) <= cap]  # type: ignore[operator]
    if fits:
        if len(fits) < len(near):
            notes.append(
                f"Bỏ {len(near) - len(fits)} chỗ ở có tiền phòng cả chuyến vượt "
                f"{int(STAY_BUDGET_SHARE * 100)}% ngân sách"
            )
        return fits, notes
    cheapest = min(near, key=lambda s: total(s) or 0)
    notes.append(
        f"Không có chỗ ở nào tiền phòng dưới {int(STAY_BUDGET_SHARE * 100)}% ngân sách; "
        f"chọn chỗ rẻ nhất: {cheapest.name}"
    )
    return [cheapest], notes


async def cham(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    stays = stays_of(state)
    await ctx.log("cham", "step", "Chấm sao tin được, thời gian ở lại; chọn chỗ ở")
    notes = list(state.get("notes") or [])
    # Thời gian ở lại từ Google cho chỗ đáng xét nhất (tốn quota, giới hạn MAX_DETAILS).
    top = sorted(
        (p for p in places.values() if p.kind == "visit"),
        key=lambda p: (not p.matches_purpose, -(p.rating_floor or 0)),
    )[:MAX_DETAILS]
    # source="db": DB chưa lưu `time_spent` cho chỗ nào (đã đếm 28.9k chỗ), gọi SerpApi thì
    # tốn tới 10 quota mỗi lộ trình; thiếu thì ước theo loại chỗ (STAY_BY_TYPE).
    details, logs = await ctx.many(
        [("get_place_details", {"place_id": p.place_id, "source": "db"}) for p in top]
    )
    for p, data in zip(top, details, strict=True):
        stay = ((data.get("place") or {}).get("stay_min")) if tool_ok(data) else None
        if stay and stay.get("low"):
            p.stay_low, p.stay_high, p.stay_by = int(stay["low"]), int(stay["high"]), "google"
    for p in places.values():
        fill_stay_estimate(p)
    fill_price_estimates(places)

    # Chọn chỗ ở: user đã đặt thì dùng luôn; chưa thì so 4 mặt rồi xếp theo nhóm.
    center = state.get("center")
    chosen_stay = None
    if form.stay_booked:
        geo, g_log = await ctx.tool("geocode_vn", address=f"{form.stay_booked}, {form.destination}")
        logs.append(g_log)
        top_geo = (geo.get("results") or [{}])[0] if tool_ok(geo) else {}
        lat, lng = (
            top_geo.get("lat") or (center or [0, 0])[0],
            top_geo.get("lng") or (center or [0, 0])[1],
        )
        chosen_stay = Place(
            place_id="booked",
            name=form.stay_booked,
            category="Chỗ ở đã đặt",
            kind="stay",
            lat=lat,
            lng=lng,
        )
    elif stays:
        usable, stay_notes = usable_stays(list(stays.values()), center, form, profile.trip_budget)
        notes += stay_notes
        candidates = sorted(usable, key=lambda s: -(s.rating_floor or 0))[:MAX_STAYS_COMPARED]
        visits = [p for p in top] or [p for p in places.values() if p.kind == "visit"][:10]
        ride: dict[str, float | None] = {}
        if visits:
            data, m_log = await ctx.tool(
                "distance_matrix",
                origins=[f"{s.lat},{s.lng}" for s in candidates],
                destinations=[f"{p.lat},{p.lng}" for p in visits],
                vehicle=form.vehicle,
            )
            logs.append(m_log)
            per: dict[int, list[float]] = {}
            for pair in (data.get("pairs") or []) if tool_ok(data) else []:
                if pair.get("duration_min") is not None and pair["from"] < len(candidates):
                    per.setdefault(pair["from"], []).append(pair["duration_min"])
            ride = {s.name: median(per.get(i, [])) for i, s in enumerate(candidates)}
        rooms = rooms_for(form)

        def vector(s: Place) -> list[float | None]:
            total = s.price * max(1, form.num_nights) * rooms if s.price else None
            amenities = sum(1 for n in profile.nice_to_have_needs if s.need_status.get(n) == "yes")
            return [
                -total if total else None,
                -ride[s.name] if ride.get(s.name) is not None else None,
                s.rating_floor,
                float(amenities),
            ]

        tiers = pareto_tiers(candidates, vector)
        first = tiers[0] if tiers else candidates
        key = {
            "P1": lambda s: s.price or 10**9,
            "P2": lambda s: ride.get(s.name) or 10**9,
            "P3": lambda s: -(s.rating_floor or 0),
            "P4": lambda s: ride.get(s.name) or 10**9,
            "P5": lambda s: ride.get(s.name) or 10**9,  # đi nhiều chỗ: chỗ ở gần các điểm
        }[profile.group]
        chosen_stay = sorted(first, key=key)[0]
        # Có ngân sách: lấy chỗ ở có tiền phòng cả chuyến gần `STAY_TARGET_SHARE` ngân sách nhất
        # (trong số đã qua `usable_stays`), để cả chuyến bám ngân sách chứ không rẻ quá / đắt quá.
        budget = profile.trip_budget
        # Mọi chỗ ở dùng được có giá (không chỉ vài chỗ sao cao nhất đem so đường đi): 6 chỗ đó hay
        # toàn chỗ rẻ, ngân sách 8 triệu lại chọn phòng 390 nghìn/đêm.
        priced = [x for x in usable if x.price]
        if budget and form.spend_style != "unlimited" and priced:
            target = budget * stay_target_share(form)
            nights = max(1, form.num_nights)

            def gap(x: Place) -> float:
                return abs(x.price * nights * rooms - target) / target  # type: ignore[operator]

            # Vài chỗ giá sát mục tiêu nhất (±STAY_PRICE_SLACK), rồi lấy chỗ ít phút đi tới các
            # điểm chơi nhất: không thì chọn resort ở Hội An cho chuyến Đà Nẵng, ngày nào cũng
            # 35 phút mỗi lượt về.
            close = sorted(priced, key=gap)[:STAY_PRICE_PICKS]
            close = [x for x in close if gap(x) <= gap(close[0]) + STAY_PRICE_SLACK]
            if len(close) > 1 and visits:
                data, m_log = await ctx.tool(
                    "distance_matrix",
                    origins=[f"{x.lat},{x.lng}" for x in close],
                    destinations=[f"{p.lat},{p.lng}" for p in visits[:STAY_RIDE_VISITS]],
                    vehicle=form.vehicle,
                )
                logs.append(m_log)
                per_stay: dict[int, list[float]] = {}
                for pair in (data.get("pairs") or []) if tool_ok(data) else []:
                    if pair.get("duration_min") is not None and pair["from"] < len(close):
                        per_stay.setdefault(pair["from"], []).append(pair["duration_min"])
                ride_of = {i: median(v) for i, v in per_stay.items()}
                chosen_stay = close[
                    min(
                        range(len(close)),
                        key=lambda i: (ride_of.get(i) or 10**6, gap(close[i])),
                    )
                ]
            else:
                chosen_stay = min(priced, key=lambda x: (gap(x), key(x)))
    await ctx.log(
        "cham",
        "finding",
        f"Chỗ ở: {chosen_stay.name if chosen_stay else 'chưa chọn được'}",
    )
    return {
        "places": dump_places(places),
        "stay": place_to_dict(chosen_stay) if chosen_stay else None,
        "notes": notes,
        "tool_log": logs,
    }


async def chot_danh_sach(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    center = tuple(state["center"]) if state.get("center") else None
    stay = place_from_dict(state["stay"]) if state.get("stay") else None
    visits = [p for p in places.values() if p.kind == "visit"]
    meals = [p for p in places.values() if p.kind == "meal"]
    if not visits:
        raise NoPlacesError(form.destination)
    # Ma trận phút đi giữa chỗ ở và các chỗ đáng xét (tối đa MAX_MATRIX_POINTS điểm).
    ordered_visits = sorted(visits, key=lambda p: (not p.matches_purpose, -(p.rating_floor or 0)))
    # Mỗi loại tối đa 1 chỗ mỗi ngày của chuyến; chỗ thừa cùng loại xếp xuống cuối.
    per: dict[str, int] = {}
    first, rest = [], []
    for p in ordered_visits:
        per[p.type_key] = per.get(p.type_key, 0) + 1
        (first if per[p.type_key] <= type_cap(p.type_key, len(profile.days)) else rest).append(p)
    # Chỗ dành cho phương án khác (`used`, xem coordinator/branches.py) xếp sau mọi chỗ của
    # phương án này (cùng nhóm), để 3 phương án đi chỗ khác nhau; thiếu thì vẫn dùng được ở cuối.
    used = set(state.get("used") or [])

    def unused_first(xs: list[Place]) -> list[Place]:
        return [p for p in xs if p.place_id not in used] + [p for p in xs if p.place_id in used]

    ordered_visits = (
        [p for p in first + rest if p.must]
        + unused_first([p for p in first + rest if not p.must and p.wanted])
        + unused_first([p for p in first + rest if not p.must and not p.wanted])
    )
    # Mỗi chuỗi quán 1 chi nhánh; đa dạng loại món trước khi cắt bớt. Quán user chọn luôn giữ,
    # chi nhánh khác cùng chuỗi thì bỏ.
    must_meals = [m for m in meals if m.must]
    others = [m for m in meals if not m.must and not any(same_brand(m, k) for k in must_meals)]
    # Quán khớp thứ user nhắc (VD bún bò): mỗi thứ tối đa `WANTED_MEALS` quán, đứng ngay sau quán
    # user chọn để chắc vào danh sách.
    wanted_meals: list[Place] = []
    for word in dict.fromkeys(m.wanted for m in others if m.wanted):
        same = [m for m in one_per_brand([m for m in others if m.wanted == word], center)]
        same = sorted(same, key=lambda m: (m.place_id in used, -(m.rating_floor or 0)))
        wanted_meals += same[:WANTED_MEALS]
    rest_meals = [m for m in others if m not in wanted_meals]
    ordered_meals = (
        must_meals + wanted_meals + unused_first(varied_meals(one_per_brand(rest_meals, center)))
    )
    # Ít nhất 2 quán riêng mỗi ngày (bước chia ngày cần vậy mới không phải dùng chung quán).
    # Đủ quán cho mọi bữa bắt buộc (sáng / trưa / tối theo giờ rảnh từng ngày).
    n_meal_slots = max(
        sum(len(required_meals(d.day_start, d.day_end)) for d in profile.days),
        2 * len(profile.days),
    )
    # Mỗi bữa ~MEAL_CHOICES quán để chọn: 3 lịch mỗi ngày khác quán, các ngày không ăn lại 1 quán.
    want_meals = min(len(ordered_meals), n_meal_slots * MEAL_CHOICES + 3)
    room = MAX_MATRIX_POINTS - 1
    want_visits = min(
        len(ordered_visits), visit_need(profile.days, ordered_visits[:room], form.travel_mode)
    )
    # Ngân sách thấp (5 triệu cho 2 người 4 ngày Phú Quốc): quán, vé đắt hơn phần tiền của nó xếp
    # sau, chỉ dùng khi thiếu — không thì toàn quán 250k/người, công viên nước 700k/người.
    costly, meal_cap, visit_cap = costly_ids(
        visits + meals,
        day_allowance(form, profile.trip_budget, stay.price if stay else None),
        form.group_size,
    )
    ordered_visits = sorted(ordered_visits, key=lambda p: p.place_id in costly)
    # Quán có bằng chứng đáp ứng nhu cầu ăn uống bắt buộc (món chay, ghế cao cho bé) lên trước
    # quán chưa rõ, để chắc vào danh sách đưa Planner; quán user chọn giữ chỗ.
    ordered_meals = sorted(
        ordered_meals,
        key=lambda p: (
            p.place_id in costly,
            not (p.must or meets_needs(p.kind, p.need_status, profile.required_needs)),
        ),
    )
    n_meals = meal_points(want_visits, want_meals, room)
    shortlist = ordered_visits[: room - n_meals]
    shortlist += ordered_meals[:n_meals]
    points = ([stay] if stay else []) + shortlist
    pairs, m_logs = await matrix_pairs(ctx, points, form.vehicle)
    ids = ([STAY] if stay else []) + [p.place_id for p in shortlist]
    matrix: dict[str, float] = {}
    km_matrix: dict[str, float] = {}  # km đường đi thật cùng lượt gọi, cho tab Chi phí
    for pair in pairs:
        key = f"{ids[pair['from']]}|{ids[pair['to']]}"
        if pair.get("distance_km") is not None:
            km_matrix[key] = float(pair["distance_km"])
        if pair.get("duration_min") is None:
            continue
        matrix[key] = float(pair["duration_min"])
    # Cặp tool không trả (lỗi, quá giới hạn, không có đường): ước theo đường chim bay thay vì để
    # trống — phút đi trống bị tính là 0, lịch thiếu dữ liệu lại thành lịch "ít ngồi xe nhất".
    notes = list(state.get("notes") or [])
    if costly and below_level(form, profile):
        notes.append(
            f"Ngân sách thấp so với {form.destination}: đã ưu tiên quán dưới "
            f"{money(meal_cap)} mỗi người mỗi bữa và chỗ chơi vé dưới {money(visit_cap)} mỗi "
            "người; quán, vé đắt hơn chỉ dùng khi không đủ chỗ rẻ hơn."
        )
    by_id = dict(zip(ids, points, strict=True))
    guessed = 0
    for a in ids:
        for b in ids:
            if a != b and f"{a}|{b}" not in matrix:
                matrix[f"{a}|{b}"] = float(estimate_minutes(by_id[a], by_id[b], form.travel_mode))
                guessed += 1
    if guessed:
        notes.append(
            f"Không lấy được phút đi thật cho {guessed} chặng, đang ước theo khoảng cách đường "
            "chim bay — giờ đến các chỗ có thể lệch."
        )
    hops = [v for k, v in matrix.items() if STAY not in k]
    typical_hop = median(hops) or 20.0
    typical_stay = (
        median([float(p.stay_low) for p in shortlist if p.kind == "visit" and p.stay_low]) or 60.0
    )
    caps = capacity(profile.days, typical_stay, typical_hop)
    musts = [p for p in shortlist if p.must]
    notes += moved_closed_days(musts, [d.day for d in profile.days], form)
    # Giữ chắc trong danh sách: chỗ user chọn + chỗ khớp thứ user nhắc (không qua chia tầng).
    kept = musts + [p for p in shortlist if not p.must and p in wanted_meals]
    kept += [p for p in shortlist if not p.must and p.wanted and p.kind == "visit"][:WANTED_VISITS]
    pool, tiers = pick_pool(
        [p for p in shortlist if p.kind == "visit" and p not in kept],
        [p for p in shortlist if p.kind == "meal" and p not in kept],
        sum(caps) + SPARE_VISITS * len(profile.days),
        n_meal_slots * MEAL_CHOICES,
        center,
        per_group=len(profile.days),
        used=used,
        costly=costly,
    )
    pool = kept + pool
    # Đặt mã theo độ nổi tiếng (số review giảm dần): Planner hay chọn các mã đầu danh sách, nên
    # chỗ nổi tiếng (Cầu Vàng, Bà Nà…) đứng trước spa / quán nhỏ có sao cao nhưng ít người biết.
    # Chỗ mượn của phương án khác (`used`) đặt mã sau chỗ của phương án này.
    pool = sorted(pool, key=lambda p: (p.kind == "meal", p.place_id in used, -(p.reviews or 0)))
    codes = {}
    for p in pool:
        prefix = "Q" if p.kind == "meal" else "D"
        codes[f"{prefix}{sum(c.startswith(prefix) for c in codes) + 1}"] = p.place_id
    code_of = {pid: c for c, pid in codes.items()}
    idle = idle_days(profile.days, caps, places)
    by_day = split_by_day(
        pool,
        [d.day for d in profile.days if d.day not in idle],
        matrix,
        form.travel_mode,
        {d.day: (to_minutes(d.day_start), to_minutes(d.day_end)) for d in profile.days},
        used,
    )
    day_codes = {str(d): [code_of[pid] for pid in pids] for d, pids in by_day.items()}
    day_codes.update({str(d): [] for d in idle})
    # Chỗ user chọn nằm ở ngày nào thì mọi lịch của ngày đó phải có.
    must_codes = {d: [c for c in cs if places[codes[c]].must] for d, cs in day_codes.items()}
    await ctx.log(
        "chot_danh_sach",
        "finding",
        f"Sức chứa {caps} chỗ/ngày, đưa Planner "
        f"{sum(p.kind == 'visit' for p in pool)} chỗ chơi + "
        f"{sum(p.kind == 'meal' for p in pool)} quán"
        + (
            f" (bỏ {len(meals) - len(ordered_meals)} chi nhánh trùng chuỗi quán)"
            if len(ordered_meals) < len(meals)
            else ""
        ),
    )
    return {
        "pool": [p.place_id for p in pool],
        "codes": codes,
        "day_codes": day_codes,
        "must_codes": {d: cs for d, cs in must_codes.items() if cs},
        "idle_days": idle,
        "places": dump_places(places),  # ngày ghim có thể đã dời (`moved_closed_days`)
        "matrix": matrix,
        "km_matrix": km_matrix,
        "notes": notes,
        "tool_log": m_logs,
    }

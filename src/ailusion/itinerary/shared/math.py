"""Phép tính dùng chung, mỗi phép có nguồn:

- `rating_floor`: số sao còn tin được — bất đẳng thức Hoeffding (1963), độ tin 95%. Chỉ dùng
  khi tool không trả `rating_adjusted` (sao Bayes MCP tính theo tiên nghiệm của điểm đến × loại
  hình, xem MCP src/services/rating_prior.py) — Place.rating_floor ưu tiên số của MCP.
- `dominates` / `pareto_tiers`: không bị trội / chia tầng không bị trội (Deb 2002).
- `km`: khoảng cách đường chim bay (haversine). `median`: trung vị.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

RISK = 0.05  # chấp nhận 5% khả năng sai (độ tin 95%)
RATING_RANGE = 4.0  # thang Google 1–5 sao
EARTH_KM = 6371


def rating_floor(rating: float | None, reviews: int, risk: float = RISK) -> float | None:
    """Số sao còn tin được = sao − 4 × √(ln(1/risk) ÷ (2 × số review)) (Hoeffding 1963).

    Ít review thì bị trừ nhiều: 4,8★ từ 40 review còn 4,026; 4,6★ từ 20.000 review còn 4,565.
    """
    if rating is None or reviews <= 0:
        return None
    margin = RATING_RANGE * math.sqrt(math.log(1 / risk) / (2 * reviews))
    return round(rating - margin, 3)


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lat1, lng1), (lat2, lng2) = a, b
    p = math.pi / 180
    h = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2
    )
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def dominates(a: list[float | None], b: list[float | None]) -> bool:
    """`a` hơn hẳn `b`: không thua ở mặt nào và hơn ít nhất 1 mặt. Số đo đã đổi về "lớn hơn là
    tốt hơn". Thiếu số ở một mặt thì không so được."""
    if any(x is None for x in a) or any(y is None for y in b):
        return False
    return all(x >= y for x, y in zip(a, b, strict=True)) and any(
        x > y for x, y in zip(a, b, strict=True)
    )


def pareto_tiers(items: list[Any], vector: Any) -> list[list[Any]]:
    """Chia tầng (Deb và cộng sự 2002): tầng 1 = không bị ai hơn hẳn; bỏ tầng 1 làm lại."""
    rest, tiers = list(items), []
    while rest:
        vectors = {id(x): vector(x) for x in rest}
        tier = [x for x in rest if not any(dominates(vectors[id(y)], vectors[id(x)]) for y in rest)]
        if not tier:  # không bao giờ xảy ra với quan hệ hơn hẳn, phòng vòng lặp vô hạn
            tier = rest
        tiers.append(tier)
        rest = [x for x in rest if x not in tier]
    return tiers

"""Cache kết quả tool trong Redis — giảm chi phí SerpAPI / thời tiết / OSRM và độ trễ.

Mỗi tool có TTL riêng theo độ "tươi" của dữ liệu: giá vé thay đổi từng phút, âm lịch thì
cả tháng không đổi. Tool nào không có trong bảng thì dùng TOOL_CACHE_DEFAULT_TTL_SECONDS.

    tool_cache = ToolCache()

    @tool_cache.cached("price_availability")
    async def search_hotels(city: str, checkin: str, nights: int) -> dict: ...
"""

from __future__ import annotations

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from redis.asyncio import Redis

from ailusion.config import get_settings
from ailusion.redis import get_redis, keys

P = ParamSpec("P")
R = TypeVar("R")

# TTL (giây) theo từng tool. Tên tool khớp kho tool trong sơ đồ.
TOOL_TTL_SECONDS: dict[str, int] = {
    "price_availability": 5 * 60,  # Giá & chỗ trống
    "weather": 30 * 60,  # Thời tiết (dự báo + công điện bão)
    "events": 3 * 3600,  # Sự kiện, lễ hội
    "web_search": 6 * 3600,  # Tìm kiếm web
    "reviews": 24 * 3600,  # Thu thập đánh giá
    "distance_time": 24 * 3600,  # Khoảng cách & thời gian (OSRM)
    "geocode_poi": 7 * 86400,  # SerpAPI geocode, POI, giờ mở cửa
    "terrain_access": 30 * 86400,  # Địa hình & tiếp cận
    "vn_calendar": 30 * 86400,  # Lịch Việt Nam, âm lịch
    "fx_rate": 3600,  # Đổi tiền tệ
}


class ToolCache:
    """Đọc/ghi cache tool. Dùng client Redis chung nếu không truyền vào."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    @property
    def redis(self) -> Redis:
        return self._redis or get_redis()

    def ttl_for(self, tool: str) -> int:
        """TTL của tool, hoặc TTL mặc định nếu tool chưa khai báo ở TOOL_TTL_SECONDS."""
        return TOOL_TTL_SECONDS.get(tool, get_settings().redis.tool_cache_default_ttl_seconds)

    async def get(self, tool: str, args: Any) -> Any | None:
        """Kết quả đã cache, hoặc None nếu chưa có / đã hết hạn."""
        raw = await self.redis.get(keys.tool_cache(tool, args))
        return None if raw is None else json.loads(raw)

    async def set(self, tool: str, args: Any, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        await self.redis.set(keys.tool_cache(tool, args), payload, ex=ttl or self.ttl_for(tool))

    async def invalidate(self, tool: str) -> int:
        """Xoá toàn bộ cache của một tool, trả về số key đã xoá."""
        deleted = 0
        async for key in self.redis.scan_iter(match=keys.tool_cache_pattern(tool), count=500):
            deleted += await self.redis.delete(key)
        return deleted

    def cached(
        self, tool: str, ttl: int | None = None
    ) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
        """Decorator cho hàm tool async; key cache = tên tool + toàn bộ tham số."""

        def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
            @functools.wraps(fn)
            async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                cache_args = {"args": args, "kwargs": kwargs}
                hit = await self.get(tool, cache_args)
                if hit is not None:
                    return hit
                result = await fn(*args, **kwargs)
                # None thường nghĩa là tool lỗi / không có dữ liệu — không cache lại.
                if result is not None:
                    await self.set(tool, cache_args, result, ttl)
                return result

            return wrapper

        return decorator

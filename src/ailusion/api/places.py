"""Chi tiết một địa điểm khi user bấm thẻ địa điểm trong lộ trình.

    GET /places/{place_id}?destination=...

Gọi song song hai tool MCP `get_place_details` và `get_place_reviews`, đọc DB trước
(`source="db"`); DB không có gì mới gọi SerpApi (`source="live"`, tốn quota). DB có dưới 3 ảnh
thì gọi `get_place_details` live một lần để bổ sung ảnh (MCP ghi lại ảnh vào DB nên mỗi địa điểm
chỉ tốn một lần). `review_photos=true` lấy review live (có ảnh), lỗi thì dùng review DB. Kết quả
giữ trong bộ nhớ tiến trình 10 phút để bấm lại thẻ không gọi tool lần nữa.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from langchain_core.tools import BaseTool

from ailusion.core.mcp import call_tool, load_mcp_tools, tools_by_name

from .schemas import PlaceDetail, PlaceReview

router = APIRouter(tags=["places"])

CACHE_TTL = 600  # giây
CACHE_MAX = 500
MAX_IMAGES = 10
MIN_IMAGES = 3  # DB ít ảnh hơn thì bổ sung từ SerpApi
MAX_REVIEWS = 10
_Key = tuple[str, str, bool]
_cache: dict[_Key, tuple[float, PlaceDetail]] = {}


@router.get("/places/{place_id}", response_model=PlaceDetail)
async def place_detail(
    place_id: str,
    request: Request,
    destination: str | None = None,
    review_photos: bool = False,
) -> PlaceDetail:
    place_id = place_id.strip()
    # Tool chỉ nhận place_id; destination để tách cache khi FE gửi kèm điểm đến.
    key = (place_id, (destination or "").strip().lower(), review_photos)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        tools = tools_by_name(await mcp_tools(request.app))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"Không kết nối được MCP server: {type(e).__name__}"
        ) from e
    detail = await fetch_place(tools, place_id, review_photos=review_photos)
    _remember(key, detail)
    return detail


async def mcp_tools(app: FastAPI) -> list[BaseTool]:
    """Danh sách tool MCP nạp một lần rồi giữ trên app.state (lỗi thì lần sau nạp lại)."""
    if getattr(app.state, "mcp_tools", None) is None:
        if getattr(app.state, "mcp_lock", None) is None:
            app.state.mcp_lock = asyncio.Lock()
        async with app.state.mcp_lock:
            if getattr(app.state, "mcp_tools", None) is None:
                app.state.mcp_tools = await load_mcp_tools()
    return app.state.mcp_tools


async def fetch_place(
    tools: dict[str, BaseTool], place_id: str, *, review_photos: bool = False
) -> PlaceDetail:
    if "get_place_details" not in tools:
        raise HTTPException(status_code=502, detail="MCP server thiếu tool get_place_details")
    details_tool = tools["get_place_details"]
    reviews_tool = tools.get("get_place_reviews")

    async def details(source: str) -> Any:
        return await _safe_call(details_tool, place_id=place_id, source=source)

    async def reviews(source: str) -> Any:
        if reviews_tool is None:
            return None
        return await _safe_call(reviews_tool, place_id=place_id, source=source)

    d_raw, r_raw, r_photos = await asyncio.gather(
        details("db"), reviews("db"), reviews("live") if review_photos else _none()
    )
    if _reviews(r_photos):
        r_raw = r_photos
    # DB thiếu chỗ / thiếu ảnh / thiếu review mới gọi SerpApi, hai tool vẫn chạy song song.
    db_place = _place(d_raw)
    need_d = db_place is None or len(_images(db_place)) < MIN_IMAGES
    need_r = not _reviews(r_raw) and not review_photos
    extra_images: list[str] = []
    if need_d or need_r:
        d_live, r_live = await asyncio.gather(
            details("live") if need_d else _none(), reviews("live") if need_r else _none()
        )
        if db_place is None:
            d_raw = d_live
        elif (live_place := _place(d_live)) is not None:
            extra_images = _images(live_place)
        if need_r and _reviews(r_live):
            r_raw = r_live

    place = _place(d_raw)
    if place is None:
        if isinstance(d_raw, dict) and d_raw.get("status") == "success":
            raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
        raise HTTPException(status_code=502, detail="Không lấy được chi tiết địa điểm")
    detail = to_detail(place_id, place, r_raw, extra_images=extra_images)
    detail.has_review_photos_option = reviews_tool is not None
    return detail


async def _none() -> None:
    return None


async def _safe_call(tool: BaseTool, **args: Any) -> Any:
    try:
        return await call_tool(tool, **args)
    except Exception:  # noqa: BLE001 — coi như tool không có kết quả
        return None


def _place(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and raw.get("status") == "success":
        place = raw.get("place")
        return place if isinstance(place, dict) and place else None
    return None


def _reviews(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and raw.get("status") == "success":
        return [r for r in raw.get("reviews") or [] if isinstance(r, dict)]
    return []


def to_detail(
    place_id: str,
    place: dict[str, Any],
    reviews_raw: Any,
    *,
    extra_images: list[str] | None = None,
) -> PlaceDetail:
    review_place = (reviews_raw or {}).get("place") if isinstance(reviews_raw, dict) else None
    review_place = review_place if isinstance(review_place, dict) else {}
    gps = place.get("gps") if isinstance(place.get("gps"), dict) else {}
    pid = place.get("place_id") or place_id
    price = place.get("price")
    return PlaceDetail(
        place_id=pid,
        name=place.get("name") or review_place.get("name"),
        category=place.get("type"),
        address=place.get("address") or review_place.get("address"),
        phone=place.get("phone"),
        website=place.get("website"),
        maps_url=place.get("maps_url") or f"https://www.google.com/maps/place/?q=place_id:{pid}",
        lat=gps.get("latitude"),
        lng=gps.get("longitude"),
        rating=place.get("rating") or review_place.get("rating"),
        reviews_count=place.get("reviews") or review_place.get("reviews"),
        price=str(price) if price not in (None, "") else None,
        hours=place.get("hours") if isinstance(place.get("hours"), dict | list) else None,
        description=place.get("description"),
        attributes=_attributes(place.get("attributes")),
        images=_merge_images(_images(place), extra_images or []),
        reviews=[
            PlaceReview(
                rating=r.get("rating"),
                date=str(r["date"]) if r.get("date") else None,
                text=r.get("text") or "",
                images=[u for u in r.get("images") or [] if isinstance(u, str)][:4],
            )
            for r in _reviews(reviews_raw)[:MAX_REVIEWS]
        ],
    )


def _images(place: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for url in [place.get("thumbnail"), *(place.get("images") or [])]:
        if isinstance(url, str) and url and url not in out:
            out.append(url)
    return out[:MAX_IMAGES]


def _merge_images(*lists: list[str]) -> list[str]:
    return list(dict.fromkeys(u for urls in lists for u in urls))[:MAX_IMAGES]


def _attributes(raw: Any) -> dict[str, Any] | None:
    """DB lưu dict; SerpApi (`extensions`) là list các dict một khoá -> gộp thành một dict."""
    if isinstance(raw, dict):
        return raw or None
    if isinstance(raw, list):
        merged: dict[str, Any] = {}
        for item in raw:
            if isinstance(item, dict):
                merged.update(item)
        return merged or None
    return None


def _remember(key: _Key, detail: PlaceDetail) -> None:
    now = time.monotonic()
    for k in [k for k, (t, _) in _cache.items() if now - t >= CACHE_TTL]:
        del _cache[k]
    if len(_cache) >= CACHE_MAX:
        del _cache[min(_cache, key=lambda k: _cache[k][0])]
    _cache[key] = (now, detail)

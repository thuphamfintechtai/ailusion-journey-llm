"""GET /places/{place_id}: chi tiết + review từ tool MCP giả (không gọi MCP / SerpApi thật)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ailusion.api import places
from ailusion.api.app import app


class FakeTool:
    """Tool MCP giả: trả lời theo `source`, ghi lại các lượt gọi."""

    def __init__(self, name: str, by_source: dict[str, Any]) -> None:
        self.name = name
        self.by_source = by_source
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        self.calls.append(args)
        out = self.by_source.get(args.get("source"))
        if isinstance(out, Exception):
            raise out
        return [{"type": "text", "text": json.dumps(out)}]


DB_PLACE = {
    "status": "success",
    "place": {
        "name": "Hồ Xuân Hương",
        "place_id": "ChIJ1",
        "type": "Hồ",
        "address": "Đà Lạt",
        "gps": {"latitude": 11.94, "longitude": 108.44},
        "rating": 4.6,
        "reviews": 1200,
        "price": "Miễn phí",
        "hours": {"Thứ Hai": "Mở cửa cả ngày"},
        "attributes": {"Tiếp cận": ["Lối vào cho xe lăn"]},
        "description": "Hồ giữa thành phố",
        "thumbnail": "https://img/t.jpg",
        "images": ["https://img/t.jpg", *[f"https://img/{i}.jpg" for i in range(12)]],
    },
}
DB_REVIEWS = {
    "status": "success",
    "returned": 12,
    "reviews": [
        {"rating": 5, "date": "2026-09-01", "text": f"đẹp {i}", "images": ["https://r/1.jpg"]}
        for i in range(12)
    ],
}


@pytest.fixture(autouse=True)
def _reset() -> Any:
    places._cache.clear()
    yield
    places._cache.clear()
    app.state.mcp_tools = None


def _use(*tools: FakeTool) -> None:
    app.state.mcp_tools = list(tools)


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://llm.test") as client:
        return await client.get(path)


async def test_reads_db_and_shapes_response() -> None:
    details = FakeTool("get_place_details", {"db": DB_PLACE})
    reviews = FakeTool("get_place_reviews", {"db": DB_REVIEWS})
    _use(details, reviews)

    r = await _get("/places/ChIJ1?destination=Đà Lạt")

    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Hồ Xuân Hương"
    assert body["category"] == "Hồ"
    assert (body["lat"], body["lng"]) == (11.94, 108.44)
    assert body["reviews_count"] == 1200
    assert body["hours"] == {"Thứ Hai": "Mở cửa cả ngày"}
    assert body["maps_url"].endswith("place_id:ChIJ1")
    assert body["images"][0] == "https://img/t.jpg"
    assert len(body["images"]) == 10 and len(set(body["images"])) == 10
    assert len(body["reviews"]) == 10
    assert body["reviews"][0]["images"] == ["https://r/1.jpg"]
    # DB đủ thì không gọi SerpApi.
    assert [c["source"] for c in details.calls + reviews.calls] == ["db", "db"]


async def test_cache_skips_tools_on_second_click() -> None:
    details = FakeTool("get_place_details", {"db": DB_PLACE})
    reviews = FakeTool("get_place_reviews", {"db": DB_REVIEWS})
    _use(details, reviews)

    await _get("/places/ChIJ1")
    r = await _get("/places/ChIJ1")

    assert r.status_code == 200
    assert len(details.calls) == 1 and len(reviews.calls) == 1


async def test_falls_back_to_live_only_when_db_empty() -> None:
    live_place = {
        "status": "success",
        "place": {
            "name": "Quán A",
            "place_id": "ChIJ2",
            "attributes": [{"Dịch vụ": ["Ăn tại chỗ"]}, {"Thanh toán": ["Thẻ"]}],
            "hours": [{"monday": "7–22"}],
        },
    }
    details = FakeTool(
        "get_place_details",
        {"db": {"status": "success", "place": None, "note": "DB chưa có"}, "live": live_place},
    )
    reviews = FakeTool(
        "get_place_reviews",
        {
            "db": {"status": "success", "returned": 0, "reviews": []},
            "live": {"status": "success", "reviews": [{"rating": 4, "text": "ngon"}]},
        },
    )
    _use(details, reviews)

    body = (await _get("/places/ChIJ2")).json()

    assert body["attributes"] == {"Dịch vụ": ["Ăn tại chỗ"], "Thanh toán": ["Thẻ"]}
    assert body["hours"] == [{"monday": "7–22"}]
    assert body["reviews"] == [{"rating": 4.0, "date": None, "text": "ngon", "images": []}]
    assert [c["source"] for c in details.calls] == ["db", "live"]
    assert [c["source"] for c in reviews.calls] == ["db", "live"]


async def test_not_found_is_404() -> None:
    empty = {"status": "success", "place": None}
    _use(
        FakeTool("get_place_details", {"db": empty, "live": empty}),
        FakeTool("get_place_reviews", {"db": {"status": "success", "reviews": []}}),
    )

    r = await _get("/places/ChIJx")

    assert r.status_code == 404
    assert r.json()["detail"] == "Không tìm thấy địa điểm"


async def test_tool_error_is_502_and_not_cached() -> None:
    details = FakeTool(
        "get_place_details",
        {"db": RuntimeError("down"), "live": {"status": "error", "error": "quota"}},
    )
    _use(details, FakeTool("get_place_reviews", {}))

    r = await _get("/places/ChIJy")

    assert r.status_code == 502
    assert "chi tiết địa điểm" in r.json()["detail"]
    assert not places._cache


async def test_mcp_down_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> list[Any]:
        raise ConnectionError("refused")

    monkeypatch.setattr(places, "load_mcp_tools", boom)
    app.state.mcp_tools = None

    r = await _get("/places/ChIJz")

    assert r.status_code == 502
    assert "MCP" in r.json()["detail"]


def _db_place_few_images() -> dict[str, Any]:
    place = {**DB_PLACE["place"], "images": None, "thumbnail": "https://img/t.jpg"}
    return {"status": "success", "place": place}


LIVE_IMAGES = {
    "status": "success",
    "place": {
        "name": "Tên từ SerpApi",
        "place_id": "ChIJ1",
        "thumbnail": "https://img/t.jpg",
        "images": [f"https://live/{i}.jpg" for i in range(12)],
    },
}


async def test_enriches_images_from_live_when_db_has_few() -> None:
    details = FakeTool("get_place_details", {"db": _db_place_few_images(), "live": LIVE_IMAGES})
    reviews = FakeTool("get_place_reviews", {"db": DB_REVIEWS})
    _use(details, reviews)

    body = (await _get("/places/ChIJ1")).json()

    assert [c["source"] for c in details.calls] == ["db", "live"]
    assert [c["source"] for c in reviews.calls] == ["db"]
    # Giữ dữ liệu DB, chỉ ghép thêm ảnh (bỏ trùng, tối đa 10).
    assert body["name"] == "Hồ Xuân Hương"
    assert body["images"] == ["https://img/t.jpg", *[f"https://live/{i}.jpg" for i in range(9)]]
    assert body["has_review_photos_option"] is True


async def test_enrich_failure_keeps_db_images() -> None:
    details = FakeTool(
        "get_place_details", {"db": _db_place_few_images(), "live": RuntimeError("quota")}
    )
    _use(details, FakeTool("get_place_reviews", {"db": DB_REVIEWS}))

    r = await _get("/places/ChIJ1")

    assert r.status_code == 200
    assert r.json()["images"] == ["https://img/t.jpg"]


async def test_no_live_call_when_db_has_enough_images() -> None:
    place = {**DB_PLACE["place"], "images": [f"https://img/{i}.jpg" for i in range(2)]}
    details = FakeTool("get_place_details", {"db": {"status": "success", "place": place}})
    _use(details, FakeTool("get_place_reviews", {"db": DB_REVIEWS}))

    body = (await _get("/places/ChIJ1")).json()

    assert len(body["images"]) == 3  # thumbnail + 2 ảnh
    assert [c["source"] for c in details.calls] == ["db"]


async def test_review_photos_uses_live_reviews_and_separate_cache() -> None:
    live_reviews = {
        "status": "success",
        "reviews": [{"rating": 5, "text": "có ảnh", "images": ["https://r/live.jpg"]}],
    }
    details = FakeTool("get_place_details", {"db": DB_PLACE})
    reviews = FakeTool("get_place_reviews", {"db": DB_REVIEWS, "live": live_reviews})
    _use(details, reviews)

    with_photos = (await _get("/places/ChIJ1?review_photos=true")).json()
    again = (await _get("/places/ChIJ1?review_photos=true")).json()
    plain = (await _get("/places/ChIJ1")).json()

    assert with_photos["reviews"] == [
        {"rating": 5.0, "date": None, "text": "có ảnh", "images": ["https://r/live.jpg"]}
    ]
    assert again == with_photos
    assert plain["reviews"][0]["text"] == "đẹp 0"
    # Lượt thứ hai trúng cache; lượt không review_photos là khoá cache khác nên gọi lại DB.
    assert sorted(c["source"] for c in reviews.calls) == ["db", "db", "live"]
    assert len(details.calls) == 2


async def test_review_photos_falls_back_to_db_reviews() -> None:
    reviews = FakeTool("get_place_reviews", {"db": DB_REVIEWS, "live": RuntimeError("quota")})
    _use(FakeTool("get_place_details", {"db": DB_PLACE}), reviews)

    body = (await _get("/places/ChIJ1?review_photos=true")).json()

    assert len(body["reviews"]) == 10 and body["reviews"][0]["text"] == "đẹp 0"
    assert sorted(c["source"] for c in reviews.calls) == ["db", "live"]


async def test_no_reviews_tool_disables_review_photos_option() -> None:
    _use(FakeTool("get_place_details", {"db": DB_PLACE}))

    body = (await _get("/places/ChIJ1")).json()

    assert body["has_review_photos_option"] is False
    assert body["reviews"] == []

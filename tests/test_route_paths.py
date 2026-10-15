"""Đường xe chạy thật cho bản đồ (bước 17): chia đoạn theo giới hạn tool, lịch trùng gọi 1 lần."""

from __future__ import annotations

import asyncio
from typing import Any

from ailusion.itinerary.response import MAX_ROUTE_POINTS, fill_paths, route_chunks
from ailusion.itinerary.shared.models import Place, place_to_dict


def test_route_chunks_overlap_at_the_seam() -> None:
    pts = [str(i) for i in range(23)]
    chunks = route_chunks(pts)
    assert all(len(c) <= MAX_ROUTE_POINTS for c in chunks)
    assert [c[0] for c in chunks[1:]] == [c[-1] for c in chunks[:-1]]  # đoạn sau nối đoạn trước
    assert chunks[0][0] == "0" and chunks[-1][-1] == "22"
    assert route_chunks(["a", "b"]) == [["a", "b"]] and route_chunks(["a"]) == []


class FakeCtx:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on

    async def many(self, calls: list[tuple[str, dict[str, Any]]]) -> tuple[list[Any], list[Any]]:
        out = []
        for _name, args in calls:
            self.calls.append(args)
            bad = self.fail_on and self.fail_on in args["points"]
            out.append(
                {"status": "error"}
                if bad
                else {"status": "success", "path": "enc", "path_precision": 5}
            )
        return out, [{} for _ in calls]

    async def log(self, *_a: Any) -> None:
        return None


def _state() -> dict[str, Any]:
    places = {
        pid: place_to_dict(
            Place(place_id=pid, name=pid, category="x", kind="visit", lat=16 + i / 100, lng=108.2)
        )
        for i, pid in enumerate(["a", "b", "c"])
    }
    return {
        "form": {
            "destination": "Đà Nẵng",
            "start_date": "2026-10-25",
            "end_date": "2026-10-26",
            "group_size": 2,
            "travel_mode": "motorbike",
        },
        "stay": {"lat": 16.05, "lng": 108.24},
        "places": places,
        "plans": {"N1-L1": {"stops": ["a", "b"]}, "N2-L1": {"stops": ["c"]}},
        "trip": {"options": [{"days": ["N1-L1", "N2-L1"]}, {"days": ["N1-L1", "N2-L1"]}]},
    }


def test_fill_paths_calls_once_per_plan_with_stay_loop() -> None:
    ctx = FakeCtx()
    out = asyncio.run(fill_paths(_state(), ctx))  # type: ignore[arg-type]
    assert len(ctx.calls) == 2  # 2 lịch, dù 2 phương án dùng chung
    first = ctx.calls[0]
    assert first["points"][0] == first["points"][-1] == "16.05,108.24"  # chỗ ở → … → chỗ ở
    assert first["vehicle"] == "motorcycle" and first["include_path"] is True
    assert out["paths"]["N1-L1"] == {"paths": ["enc"], "precision": 5}


def test_fill_paths_drops_a_plan_with_a_missing_leg() -> None:
    out = asyncio.run(fill_paths(_state(), FakeCtx(fail_on="16.02,108.2")))  # type: ignore[arg-type]
    assert "N2-L1" not in out["paths"] and "N1-L1" in out["paths"]

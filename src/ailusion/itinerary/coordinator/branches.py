"""Làn Coordinator — bước 8–14 chạy 3 nhánh song song, mỗi nhánh làm ra 1 phương án.

Tool distance_matrix chỉ nhận 25 điểm, nên một danh sách chung chỉ có ~24 chỗ cho cả 3 phương án
-> 3 phương án gần như cùng chỗ. Ở đây mỗi nhánh có danh sách, ma trận phút đi, lịch Planner riêng:

- Chia chỗ chơi (`split_shares`): xếp theo hợp mục đích + sao tin được, chia xen kẽ cho 3 nhánh
  (1, 2, 3, 1, 2, 3…). Nhánh k ưu tiên phần của mình, phần của nhánh khác xếp cuối (chỉ dùng khi
  thiếu). Quán ăn không chia: chia thì mỗi nhánh còn 1/3 số quán, toàn quán chỉ mở tối, lịch hỏng
  vì giờ ăn (chạy thử Đà Nẵng 3 ngày); 3 phương án giống nhau là do chỗ chơi, không phải quán. Chỗ
  user chọn và thứ user nhắc (VD bún bò) không chia: nhánh nào cũng có.
- Mỗi nhánh: chot_danh_sach -> xep_lich (Planner) -> kiem_lich -> xep_hang, chạy song song.
- Chọn phương án lần lượt PA1 -> PA3 (`pick_option`): phương án sau ít trùng chỗ chơi nhất với
  phương án đã chọn trước, rồi mới tới chỗ dành cho nhánh khác.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..agents.planner import xep_lich
from ..shared.context import Ctx
from ..shared.models import ItineraryState, Place, places_of
from .rank import pick_option, xep_hang
from .score import chot_danh_sach
from .simulate import kiem_lich

log = logging.getLogger(__name__)

BRANCHES = 3
STEPS = (chot_danh_sach, xep_lich, kiem_lich, xep_hang)
SUMMED = ("llm_calls", "usage", "tool_log")  # khoá cộng dồn (reducer) của state


def split_shares(places: dict[str, Place], n: int = BRANCHES) -> list[set[str]]:
    """Chia chỗ chơi (trừ chỗ user chọn / user nhắc) xen kẽ cho n nhánh."""
    visits = sorted(
        (p for p in places.values() if p.kind == "visit" and not p.must and not p.wanted),
        key=lambda p: (not p.matches_purpose, -(p.rating_floor or 0), -(p.reviews or 0)),
    )
    shares: list[set[str]] = [set() for _ in range(n)]
    for i, p in enumerate(visits):
        shares[i % n].add(p.place_id)
    return shares


async def run_branch(state: ItineraryState, ctx: Ctx, k: int, others: set[str]) -> dict[str, Any]:
    """Một nhánh trên bản state riêng; trả state của nhánh (khoá cộng dồn đã gộp)."""
    sub: dict[str, Any] = {**state, "round": k, "used": sorted(others)}
    for key in SUMMED:
        sub[key] = 0 if key == "llm_calls" else []
    lane = ctx.branch("lap_phuong_an", f"pa{k}")
    for step in STEPS:
        out = await step(sub, lane)  # type: ignore[arg-type]
        for key in SUMMED:
            if key in out:
                sub[key] = sub[key] + out.pop(key)
        sub.update(out)
    return sub


async def lap_phuong_an(state: ItineraryState, ctx: Ctx) -> dict:
    places = places_of(state)
    shares = split_shares(places)
    await ctx.log(
        "lap_phuong_an",
        "step",
        f"Lập {BRANCHES} phương án song song, mỗi phương án ưu tiên "
        f"~{min(len(s) for s in shares)} chỗ riêng",
    )
    runs = await asyncio.gather(
        *(
            run_branch(state, ctx, k + 1, set().union(*(s for j, s in enumerate(shares) if j != k)))
            for k in range(BRANCHES)
        ),
        return_exceptions=True,
    )
    runs = list(runs)
    for k, r in enumerate(runs):
        if isinstance(r, BaseException):  # nhánh lỗi (VD Planner trả lời hỏng): chạy lại 1 lần
            log.warning("lap_phuong_an: nhánh %d lỗi, chạy lại", k + 1, exc_info=r)
            others = set().union(*(s for j, s in enumerate(shares) if j != k))
            try:
                runs[k] = await run_branch(state, ctx, k + 1, others)
                continue
            except Exception as e:  # noqa: BLE001 - các nhánh khác vẫn ra phương án
                runs[k] = e
                log.warning("lap_phuong_an: nhánh %d lỗi lần 2", k + 1, exc_info=e)
            await ctx.branch("lap_phuong_an", f"pa{k + 1}").log(
                "lap_phuong_an", "error", f"Phương án {k + 1} lỗi: {type(r).__name__}"
            )
    done = [(k + 1, r) for k, r in enumerate(runs) if not isinstance(r, BaseException)]
    if not done:
        raise runs[0]  # type: ignore[misc]

    options: list[dict[str, Any]] = []
    kept: dict[str, dict[str, Any]] = {}
    matrix: dict[str, float] = {}
    km_matrix: dict[str, float] = {}
    notes: list[str] = []
    taken: set[str] = set()  # chỗ của các phương án đã chọn
    kinds = {pid: p.kind for pid, p in places.items()}
    for k, sub in done:
        others = set(sub["used"])
        option, plans, stops = pick_option(sub, k, taken | others)
        new = sum(kinds.get(s) != "meal" and s not in taken for s in stops)
        options.append(option)
        kept.update(plans)
        taken |= stops
        matrix.update(sub.get("matrix") or {})
        km_matrix.update(sub.get("km_matrix") or {})
        notes += [n for n in sub.get("notes") or [] if n not in notes]
        await ctx.branch("lap_phuong_an", f"pa{k}").log(
            "lap_phuong_an",
            "finding",
            f"Phương án {k}: {new} chỗ chơi không trùng phương án trước",
        )
    k1, first = done[0]
    return {
        "options": options,
        "kept_plans": kept,
        "first_ranked": {d: [f"V{k1}-{i}" for i in ids] for d, ids in first["ranked"].items()},
        "first_reasons": first.get("reasons") or {},
        "places": first["places"],  # chot_danh_sach có thể dời ngày ghim (như nhau mọi nhánh)
        "pool": first["pool"],
        "matrix": matrix,
        "km_matrix": km_matrix,
        "notes": notes,
        "llm_calls": sum(sub["llm_calls"] for _, sub in done),
        "usage": [u for _, sub in done for u in sub["usage"]],
        "tool_log": [t for _, sub in done for t in sub["tool_log"]],
    }

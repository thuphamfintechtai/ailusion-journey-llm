"""Làn Coordinator — bước 8–14 chạy 3 nhánh song song, mỗi nhánh làm ra 1 phương án.

Tool distance_matrix chỉ nhận 25 điểm, nên một danh sách chung chỉ có ~24 chỗ cho cả 3 phương án
-> 3 phương án gần như cùng chỗ. Ở đây mỗi nhánh có danh sách, ma trận phút đi, lịch Planner riêng:

- Chia chỗ chơi (`split_shares`): xếp theo hợp mục đích + sao tin được, chia xen kẽ cho 3 nhánh
  (1, 2, 3, 1, 2, 3…). Nhánh k ưu tiên phần của mình, phần của nhánh khác xếp cuối (chỉ dùng khi
  thiếu). Quán ăn không chia: chia thì mỗi nhánh còn 1/3 số quán, toàn quán chỉ mở tối, lịch hỏng
  vì giờ ăn (chạy thử Đà Nẵng 3 ngày); 3 phương án giống nhau là do chỗ chơi, không phải quán. Chỗ
  user chọn và thứ user nhắc (VD bún bò) không chia: nhánh nào cũng có.
- `chot_nhanh`: mỗi nhánh chốt danh sách chỗ đưa Planner (chot_danh_sach), chạy song song.
- `xac_minh` (coordinator/filter.py): Experience + Budget xem đúng các chỗ đó (gộp 3 nhánh, bỏ
  trùng) — 1 lượt mỗi agent, kiểm chỗ sẽ thật sự vào lịch thay vì chỗ sao cao nhất cả điểm đến.
- `lap_phuong_an`: mỗi nhánh xep_lich (Planner) -> kiem_lich -> xep_hang, chạy song song.
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
SUMMED = ("llm_calls", "usage", "tool_log")  # khoá cộng dồn (reducer) của state
# Phần state riêng của mỗi nhánh, giữ trong state["branches"] giữa chot_nhanh và lap_phuong_an.
BRANCH_KEYS = (
    "round",
    "used",
    "pool",
    "codes",
    "day_codes",
    "must_codes",
    "matrix",
    "km_matrix",
    "notes",
)


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


async def run_steps(
    state: ItineraryState, ctx: Ctx, node: str, base: dict[str, Any], steps: tuple[Any, ...]
) -> dict[str, Any]:
    """Chạy `steps` trên bản state riêng của nhánh (`base` đè lên state); khoá cộng dồn gộp lại."""
    sub: dict[str, Any] = {**state, **base}
    for key in SUMMED:
        sub[key] = 0 if key == "llm_calls" else []
    lane = ctx.branch(node, f"pa{sub['round']}")
    for step in steps:
        out = await step(sub, lane)  # type: ignore[arg-type]
        for key in SUMMED:
            if key in out:
                sub[key] = sub[key] + out.pop(key)
        sub.update(out)
    return sub


async def all_branches(
    ctx: Ctx, node: str, bases: list[dict[str, Any]], run: Any
) -> list[tuple[int, dict[str, Any]]]:
    """Chạy `run(base)` cho mọi nhánh song song; nhánh lỗi (VD Planner trả lời hỏng) chạy lại 1
    lần, vẫn lỗi thì bỏ nhánh đó (các nhánh khác vẫn ra phương án). Không nhánh nào xong: ném."""
    runs: list[Any] = list(await asyncio.gather(*(run(b) for b in bases), return_exceptions=True))
    for i, r in enumerate(runs):
        if not isinstance(r, BaseException):
            continue
        k = bases[i]["round"]
        log.warning("%s: nhánh %d lỗi, chạy lại", node, k, exc_info=r)
        try:
            runs[i] = await run(bases[i])
            continue
        except Exception as e:  # noqa: BLE001
            runs[i] = e
            log.warning("%s: nhánh %d lỗi lần 2", node, k, exc_info=e)
        await ctx.branch(node, f"pa{k}").log(
            node, "error", f"Phương án {k} lỗi: {type(r).__name__}"
        )
    done = [(bases[i]["round"], r) for i, r in enumerate(runs) if not isinstance(r, BaseException)]
    if not done:
        raise runs[0]
    return done


def summed(done: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "llm_calls": sum(sub["llm_calls"] for _, sub in done),
        "usage": [u for _, sub in done for u in sub["usage"]],
        "tool_log": [t for _, sub in done for t in sub["tool_log"]],
    }


async def chot_nhanh(state: ItineraryState, ctx: Ctx) -> dict:
    """Bước 8 của 3 nhánh: mỗi nhánh chốt danh sách chỗ (ưu tiên phần chỗ chơi của mình)."""
    shares = split_shares(places_of(state))
    await ctx.log(
        "chot_nhanh",
        "step",
        f"Chia chỗ cho {BRANCHES} phương án, mỗi phương án ưu tiên "
        f"~{min(len(s) for s in shares)} chỗ chơi riêng",
    )
    bases = [
        {"round": k + 1, "used": sorted(set().union(*(s for j, s in enumerate(shares) if j != k)))}
        for k in range(BRANCHES)
    ]
    done = await all_branches(
        ctx,
        "chot_nhanh",
        bases,
        lambda b: run_steps(state, ctx, "chot_nhanh", b, (chot_danh_sach,)),
    )
    first = done[0][1]
    return {
        "branches": [{key: sub[key] for key in BRANCH_KEYS if key in sub} for _, sub in done],
        "places": first["places"],  # chot_danh_sach có thể dời ngày ghim (như nhau mọi nhánh)
        **summed(done),
    }


async def lap_phuong_an(state: ItineraryState, ctx: Ctx) -> dict:
    """Bước 9–14 của 3 nhánh: Planner xếp lịch, chạy thử, xếp hạng; rồi chọn PA1 -> PA3."""
    places = places_of(state)
    await ctx.log("lap_phuong_an", "step", f"Lập {len(state['branches'])} phương án song song")
    done = await all_branches(
        ctx,
        "lap_phuong_an",
        list(state["branches"]),
        lambda b: run_steps(state, ctx, "lap_phuong_an", b, (xep_lich, kiem_lich, xep_hang)),
    )
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
        "pool": first["pool"],
        "matrix": matrix,
        "km_matrix": km_matrix,
        "notes": notes,
        **summed(done),
    }

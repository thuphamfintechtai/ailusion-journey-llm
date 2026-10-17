"""Làn Coordinator — bước 6: lọc chỗ không đi được, rồi gửi 2 agent xác minh.

- `loc`: bỏ chỗ đóng cửa mọi ngày, thiếu nhu cầu bắt buộc, chỗ ngoài trời khi có cảnh báo thiên
  tai mức đỏ. Chỗ có từ khoá mục đích thì code tự nhận "hợp mục đích" (`keyword_match`); chỗ còn
  lại gửi Agent Experience, chỗ chơi chưa có giá gửi Agent Budget.
- `xac_minh`: chạy 2 agent song song (agents/experience.py, agents/budget.py).
"""

from __future__ import annotations

import asyncio
import re
import unicodedata

from ..agents.budget import MAX_PRICE_FINDS, find_prices
from ..agents.experience import MAX_PURPOSE_CHECKS, check_purposes
from ..shared.companions import KID_UNFIT_REASON, avoid_match, avoided_foods, kid_unfit
from ..shared.context import Ctx
from ..shared.form import PURPOSE_KEYWORDS, need_text, needs_for, unknown_needs
from ..shared.math import km
from ..shared.models import (
    ItineraryState,
    Place,
    dump_places,
    form_of,
    places_of,
    profile_of,
    wanted_match,
    wanted_words,
)
from ..shared.tools import add_hours, add_needs
from .score import fill_price_estimates


def keyword_match(place: Place, purposes: list[str]) -> str | None:
    """Từ khoá của mục đích có trong loại / tên chỗ -> trả từ khoá đó (làm bằng chứng)."""
    for purpose in purposes:
        for word in PURPOSE_KEYWORDS.get(purpose, ()):
            if has_word(place.text, word):
                return word
    return None


def has_word(text: str, word: str) -> bool:
    """Khớp nguyên chữ: "spa" không khớp "space", "bar" không khớp "barber"."""
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


MAX_HOURS_PLACES = 90  # tra giờ mở cửa cho tối đa ngần này chỗ (chia lượt 15 chỗ; 3 phương án)
# Xa trung tâm điểm đến hơn ngần này thì bỏ: DB có chỗ gắn nhầm điểm đến (VD "VinWonders Nha
# Trang" trong danh sách Đà Nẵng). 60 km đủ gồm Hội An, Bà Nà khi đi Đà Nẵng. Số tạm ("tu_dat").
MAX_KM_FROM_CENTER = 60


def is_shop(p: Place) -> bool:
    """Cửa hàng bán lẻ (đồng hồ, quần áo, đặc sản…): chỉ là chỗ chơi khi user đi để mua sắm."""
    return p.kind == "visit" and p.category.strip().lower().startswith(
        ("cửa hàng", "cửa tiệm", "shop")
    )


# Chữ chung trong tên, không dùng để nhận ra 2 chỗ là một: "Bãi biển Mỹ Khê" và "Bãi tắm hàng
# dừa Mỹ Khê" trùng nhờ "mỹ khê"; "Cầu Rồng" và "Cầu Tình Yêu" không trùng vì "cầu" là chữ chung.
GENERIC_NAME_WORDS = {
    "bãi",
    "biển",
    "tắm",
    "chùa",
    "đền",
    "cầu",
    "công",
    "viên",
    "bảo",
    "tàng",
    "chợ",
    "khu",
    "du",
    "lịch",
    "đà",
    "nẵng",
    "hội",
    "an",
    "the",
    "spa",
    "massage",
    "và",
    "&",
    "-",
    "của",
}
SAME_PLACE_KM = 1.5


def same_words(a: str, b: str) -> bool:
    """Hai tên trùng hẳn khi bỏ dấu, chữ hoa, dấu câu ("Phú Quốc" / "phu quoc")."""

    def plain(t: str) -> str:
        t = unicodedata.normalize("NFD", t.lower().replace("đ", "d"))
        return " ".join(re.findall(r"\w+", t.encode("ascii", "ignore").decode()))

    return bool(plain(a)) and plain(a) == plain(b)


def name_words(p: Place) -> set[str]:
    name = unicodedata.normalize("NFC", p.name).lower()
    return {w for w in re.findall(r"\w+", name) if w not in GENERIC_NAME_WORDS}


def merge_duplicates(places: dict[str, Place]) -> list[str]:
    """Gộp chỗ chơi DB lưu 2 lần dưới 2 tên: cách nhau < SAME_PLACE_KM và trùng hẳn tên, hoặc
    cùng loại và tên có ≥ 2 chữ riêng trùng nhau. Giữ chỗ nhiều review hơn. Trả tên chỗ đã bỏ."""
    removed = []
    visits = sorted(
        (p for p in places.values() if p.kind == "visit"), key=lambda p: -(p.reviews or 0)
    )
    kept: list[Place] = []
    for p in visits:
        twin = next(
            (
                k
                for k in kept
                if km((k.lat, k.lng), (p.lat, p.lng)) < SAME_PLACE_KM
                and (
                    # Trùng hẳn tên (khác chữ hoa: "Phố Cổ Hội An" / "Phố cổ Hội An") thì không
                    # cần cùng loại — Google ghi 2 loại khác nhau cho cùng một nơi.
                    (name_words(k) == name_words(p) and bool(name_words(p)))
                    or (k.group == p.group and len(name_words(k) & name_words(p)) >= 2)
                )
            ),
            None,
        )
        if twin:
            removed.append(f"{p.name} (trùng {twin.name})")
            twin.thumbnail = twin.thumbnail or p.thumbnail
            if p.must and not twin.must:  # chỗ user chọn: giữ dấu trên bản còn lại
                twin.must, twin.must_day = True, p.must_day
                twin.must_time, twin.must_name = p.must_time, p.must_name
            del places[p.place_id]
        else:
            kept.append(p)
    return removed


def indoor(p: Place) -> bool:
    return any(
        w in p.text
        for w in ("bảo tàng", "trung tâm thương mại", "nhà hàng", "quán", "cà phê", "spa", "chợ")
    )


def to_date(at: str):  # noqa: ANN201
    from datetime import date

    return date.fromisoformat(at[:10])


async def loc(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    notes = list(state.get("notes") or [])
    await ctx.log("loc", "step", "Lọc chỗ không đi được: giờ mở cửa, nhu cầu bắt buộc")
    # Tra giờ mở cửa cho những chỗ đáng xét nhất, từng ngày: nửa theo sao tin được, nửa theo số
    # review — chỉ theo sao thì toàn spa / quán nhỏ, chỗ nổi tiếng (Bà Nà, Cầu Vàng) thiếu giờ.
    half = MAX_HOURS_PLACES // 2
    by_rating = sorted(places.values(), key=lambda p: -(p.rating_floor or 0))
    by_fame = sorted(places.values(), key=lambda p: -(p.reviews or 0))
    musts = [p for p in places.values() if p.must]
    candidates = list({p.place_id: p for p in musts + by_rating[:half] + by_fame}.values())[
        : MAX_HOURS_PLACES + len(musts)
    ]
    ids = [p.place_id for p in candidates]
    calls = []
    for d in range(1, form.num_days + 1):
        for i in range(0, len(ids), 15):
            calls.append(
                (
                    "check_opening_hours",
                    {
                        "places": ids[i : i + 15],
                        "at": f"{form.day_date(d).isoformat()} 10:00",
                        "destination": form.destination,
                    },
                )
            )
    if profile.required_needs:
        unknown = [
            p.place_id
            for p in candidates
            if unknown_needs(p.kind, p.need_status, profile.required_needs)
        ]
        for i in range(0, len(unknown), 15):
            calls.append(
                (
                    "check_place_needs",
                    {
                        "places": unknown[i : i + 15],
                        "needs": profile.required_needs,
                        "destination": form.destination,
                    },
                )
            )
    results, logs = await ctx.many(calls)
    for (name, args), data in zip(calls, results, strict=True):
        if name == "check_opening_hours":
            day = (to_date(args["at"]) - form.start_date).days + 1
            add_hours(places, day, data)
        else:
            add_needs(places, data)

    center = tuple(state["center"]) if state.get("center") else None
    removed: list[str] = []
    # Người đi cùng (shared/companions.py): ghi chú riêng ở đầu kết quả, không lẫn vào "Bỏ N chỗ".
    avoids = avoided_foods(form.needs_note)
    kid_out: list[str] = []
    food_out: dict[str, list[str]] = {}
    for pid, p in list(places.items()):
        reason, food = None, avoid_match(p, avoids)
        if p.hours and all(p.hours.get(d, "?") is None for d in range(1, form.num_days + 1)):
            reason = "đóng cửa mọi ngày của chuyến"
        elif lacking := [
            n for n in needs_for(p.kind, profile.required_needs) if p.need_status.get(n) == "no"
        ]:
            reason = f"thiếu nhu cầu bắt buộc: {need_text(lacking)}"
        elif state.get("alerts") and p.kind == "visit" and not indoor(p):
            reason = "cảnh báo thiên tai mức đỏ, chỗ ngoài trời"
        elif is_shop(p) and "shopping" not in profile.purposes:
            reason = "cửa hàng, chuyến đi không có mục đích mua sắm"
        elif p.kind == "visit" and same_words(p.name, form.destination):
            # "Phú Quốc" (loại Đảo) là chính điểm đến, không phải một chỗ để ghé; Agent Budget
            # từng gán cho nó giá vé máy bay Hà Nội – Phú Quốc 1.268.181 đ.
            reason = "chính là điểm đến, không phải một chỗ để ghé"
        elif center and km(center, (p.lat, p.lng)) > MAX_KM_FROM_CENTER:
            reason = f"cách trung tâm {form.destination} hơn {MAX_KM_FROM_CENTER} km"
        elif form.kids_under_6 and kid_unfit(p):
            reason = KID_UNFIT_REASON
        elif food:
            reason = f"{food[0]} ({food[1]})"
        if reason and p.must and not reason.startswith("đóng cửa"):
            # User tự chọn: vẫn giữ, chỉ nhắc điều cần biết.
            p.to_verify.append(f"Bạn chọn chỗ này dù {reason}")
            reason = None
        if reason:
            if p.must:
                notes.append(f"Không xếp được “{p.must_name or p.name}”: {reason}.")
            if reason == KID_UNFIT_REASON:
                kid_out.append(p.name)
            elif food and reason == f"{food[0]} ({food[1]})":
                food_out.setdefault(food[0], []).append(f"{p.name} ({food[1]})")
            else:
                removed.append(f"{p.name}: {reason}")
            del places[pid]
            continue
        if unsure := unknown_needs(p.kind, p.need_status, profile.required_needs):
            p.to_verify.append(f"chưa rõ đáp ứng: {need_text(unsure)} — nên hỏi trước")
    removed += [f"{r}: cùng một chỗ" for r in merge_duplicates(places)]
    if removed:
        notes.append(f"Bỏ {len(removed)} chỗ: " + "; ".join(removed[:6]))
    if kid_out:
        notes.append(
            f"Đoàn có {form.kids_under_6} bé dưới 6 tuổi: bỏ {len(kid_out)} chỗ spa, massage, "
            "quán bar, lặn biển, dù lượn (không hợp trẻ nhỏ) — " + ", ".join(kid_out[:5])
        )
    elif form.kids_under_6:
        # Không phải bỏ chỗ nào vẫn ghi, để thấy lịch đã tính tới trẻ nhỏ (chạy thật Phú Quốc 1 bé
        # dưới 6: đầu kết quả không có dòng nào về trẻ).
        notes.append(
            f"Đoàn có {form.kids_under_6} bé dưới 6 tuổi: lịch không xếp spa, massage, quán bar, "
            "lặn biển, dù lượn (không hợp trẻ nhỏ) — chỗ tìm được không có chỗ nào như vậy"
        )
    for phrase, words in avoids:
        dropped = food_out.get(phrase, [])
        if dropped:
            tail = (
                f"bỏ {len(dropped)} quán có món đó — {', '.join(dropped[:5])}; quán trong lịch "
                "vẫn nên dặn trước khi gọi món"
            )
        elif words:
            tail = "dặn quán trước khi gọi món"
        else:
            tail = "chưa lọc được quán theo món này — dặn quán trước khi gọi món"
        notes.append(f"{phrase[:1].upper()}{phrase[1:]}: {tail}")

    # Chỗ khớp thứ user nhắc (sở thích + lời nhắn, VD "bún bò"): gắn từ khoá, bước sau ưu tiên.
    words = wanted_words(form.interests)
    for p in places.values():
        p.wanted = wanted_match(p, words)
    # Chỗ có từ khoá mục đích: tự nhận hợp mục đích. Chỗ còn lại Agent Experience xem sau, khi các
    # nhánh đã chốt danh sách (`xac_minh`).
    for p in places.values():
        word = keyword_match(p, profile.purposes)
        if word:
            p.matches_purpose, p.match_by = True, f"từ khoá '{word}'"
    await ctx.log("loc", "finding", f"Còn {len(places)} chỗ")
    return {
        "places": dump_places(places),
        "notes": notes,
        "tool_log": logs,
    }


def branch_pool(state: ItineraryState) -> list[str]:
    """Chỗ các nhánh đưa Planner, gộp xen kẽ (1 chỗ nhánh 1, 1 chỗ nhánh 2…), bỏ trùng: cắt theo
    trần vẫn chia đều cho các phương án."""
    pools = [b.get("pool") or [] for b in state.get("branches") or []]
    out: list[str] = []
    for i in range(max((len(p) for p in pools), default=0)):
        for pool in pools:
            if i < len(pool) and pool[i] not in out:
                out.append(pool[i])
    return out


async def xac_minh(state: ItineraryState, ctx: Ctx) -> dict:
    """Bước 6a + 6b, sau khi các nhánh chốt danh sách: Agent Experience xem chỗ chơi chưa rõ hợp
    mục đích, Agent Budget tìm giá có nguồn cho chỗ chơi đang dùng giá ước — chỉ trong các chỗ sắp
    đưa Planner (chỗ user chọn / user nhắc đứng đầu danh sách mỗi nhánh), song song."""
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    pool = [pid for pid in branch_pool(state) if pid in places and places[pid].kind == "visit"]
    to_exp = [pid for pid in pool if profile.purposes and places[pid].matches_purpose is None][
        :MAX_PURPOSE_CHECKS
    ]
    to_bud = [
        pid
        for pid in pool
        if places[pid].price is None or (places[pid].price_by or "").startswith("ước")
    ][:MAX_PRICE_FINDS]
    await ctx.log(
        "xac_minh",
        "step",
        f"{len(pool)} chỗ chơi sắp đưa Planner; gửi Experience {len(to_exp)}, Budget {len(to_bud)}",
    )
    a, b = await asyncio.gather(
        check_purposes(ctx, places, to_exp, profile.purposes),
        find_prices(ctx, places, to_bud, form.destination),
    )
    refresh_estimates(places)
    return {"places": dump_places(places), **a.add(b).state()}


def refresh_estimates(places: dict[str, Place]) -> None:
    """Giá ước (bước 7) tính TRƯỚC Agent Budget, theo trung vị giá thật của chỗ cùng loại: Agent
    Budget nhận thêm giá thật thì các giá ước đó cũ. Xoá mọi giá "ước …" rồi ước lại một lượt."""
    for p in places.values():
        if (p.price_by or "").startswith("ước"):
            p.price = p.price_by = None
            p.price_low = p.price_high = None
    fill_price_estimates(places)

"""Agent Planner — bước 9: xếp các lịch đi khác nhau cho từng ngày.

Vào: khung giờ từng ngày, danh sách chỗ (mã D/Q, giờ mở cửa, thời gian ở lại, giá), phút đi giữa
các chỗ. Ra: `PlannerOutput` (≥ 2 lịch mỗi ngày, mỗi lượt tối đa `DAYS_PER_PLANNER_CALL` ngày).
Planner KHÔNG chấm lịch nào hay hơn — code chạy thử và xếp hạng. Code kiểm (`plan_errors`): chỉ
dùng mã có trong danh sách, đủ số lịch; sai thì trả sửa 1 lần. Không trả được lịch nào thì
`fallback_plans` tự ghép mỗi ngày 1 lịch, gắn nhãn dự phòng.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ..shared.context import Ctx
from ..shared.form import (
    DINNER_FREE_MAX,
    GROUP_NAMES,
    LONG_WAIT_MAX,
    MIN_VISITS,
    MUST_LATE_MAX,
    NAP_MIN,
    PURPOSE_NAMES,
    day_allowance,
    hhmm,
    must_time_text,
    required_meals,
    skipped_meals,
)
from ..shared.llm import OUTPUT_TOKENS
from ..shared.models import (
    STAY,
    DayPlan,
    ItineraryState,
    Place,
    form_of,
    matrix_of,
    opens_late,
    places_of,
    plan_to_dict,
    profile_of,
    weather_text,
)

PLANNER = """Bạn là Agent Planner trong hệ thống lập lộ trình du lịch Việt Nam. Bạn xếp các lịch đi khác nhau cho từng ngày. Bạn KHÔNG chấm lịch nào hay hơn: hệ thống tự chạy thử từng lịch theo đồng hồ, đo tiền, phút ngồi xe, số chỗ hợp ý… rồi xếp hạng.

# Quy tắc cứng

1. Chỉ dùng mã chỗ trong danh sách (D… chỗ đi chơi, Q… quán ăn). Không bịa chỗ.
2. Giờ rời chỗ ở (`leave_stay`) không sớm hơn giờ bắt đầu ngày.
3. Cả lịch (kể cả thời gian ở lại và phút đi về chỗ ở) phải nằm trong giờ rảnh của ngày; không chạm khung bị chặn. Khung "chỉ ăn uống" chỉ được xếp quán Q….
4. Không xếp chỗ "đóng cửa" ngày đó; tới nơi trong giờ mở cửa.
5. Mỗi ngày ít nhất 2 lịch, không có 2 lịch giống hệt nhau.
6. Bữa ăn: dòng "Ngày …" ghi bữa BẮT BUỘC của ngày (và bữa tuỳ chọn / không xếp), tính sẵn theo giờ bắt đầu ngày. Mỗi bữa bắt buộc 1 quán Q… trong `stops`, đặt gần đúng chỗ (bữa sáng đầu ngày nếu có, trưa giữa ngày, tối cuối ngày) — hệ thống tự dời quán vào đúng giờ ăn và bỏ quán thừa / sai giờ. Giữa 2 bữa là chỗ chơi, không xếp 2 quán liền nhau; mọi chỗ bán đồ ăn (kể cả "khu ăn uống", "ăn vặt") tính là 1 bữa; quán loại "bữa sáng" chỉ làm bữa sáng; thiếu bữa bắt buộc là lỗi. `leave_stay` = giờ bắt đầu ngày.
7. Ngày kéo tới tối: có chỗ chơi buổi sáng VÀ buổi chiều (sau bữa trưa / nghỉ trưa) rồi mới tới bữa tối — không kết thúc ngày sau bữa trưa; ngày dài ít nhất {min_visits} chỗ chơi. Đừng nhồi tới mức về trễ: lịch quá dài hệ thống bỏ bớt chỗ. Spa, massage, xông hơi xếp buổi chiều / tối (từ 13:00). Chỗ ghi "đi buổi tối" (show, chợ đêm, bar…) xếp sau bữa tối nếu còn mở.
8. Có dòng "Tiêu khoảng … mỗi ngày": các lịch của ngày có mức tiền khác nhau quanh con số đó — một lịch thấp hơn, một lịch cao hơn một chút (lịch thứ 3 nếu có thì sát con số), để hệ thống ghép được cả chuyến sát ngân sách. Không chọn toàn chỗ rẻ khi ngân sách còn dư nhiều, cũng không dồn chỗ đắt.
9. Chỗ ghi "user nhắc: …" là đúng thứ user viết trong lời nhắn / sở thích (VD "bún bò"): mỗi thứ phải có ít nhất 1 lần trong mỗi phương án (rải ra vài ngày), nhưng MỖI NGÀY TỐI ĐA 1 BỮA cho cùng một món; quán thì vào đúng bữa trưa / tối, ngày vẫn phải có chỗ đi chơi.
10. Ngày ghi "nghỉ trưa": sau bữa trưa hệ thống tự chèn {nap} phút nghỉ (chỗ ở / quán cà phê / khách sạn gần đó), không ghi vào `stops`. Buổi chiều sau đó tới bữa tối PHẢI có chỗ chơi: tới một chỗ phải chờ quá {wait} phút là lỗi; riêng bữa tối là điểm cuối ngày thì được trống tối đa {dinner_free} phút trước đó (thời gian tự do). "rảnh … phút" đã trừ giờ nghỉ trưa.

# Làm các lịch khác nhau thật sự

Các lịch của một ngày nên khác nhau về kiểu: một lịch ít chỗ, ít ngồi xe; một lịch nhiều chỗ hợp sở thích; một lịch chọn chỗ được đánh giá cao nhất; gom chỗ gần nhau (xem phút đi) để đỡ chạy qua lại.

Khác nhau ở CHỖ CHƠI, không chỉ ở thứ tự hay quán ăn: 2 lịch cùng ngày không được có cùng một bộ chỗ chơi D…; cố để mỗi lịch có ít nhất một nửa chỗ chơi khác các lịch kia, dùng cả các chỗ ít được chọn trong danh sách của ngày. Hệ thống ghép 3 phương án từ các lịch này nên lịch giống nhau thì 3 phương án cũng giống nhau. (Chỗ BẮT BUỘC thì lịch nào cũng có, không tính.)
"""


for key, value in {
    "must_late": MUST_LATE_MAX,
    "min_visits": MIN_VISITS,
    "nap": NAP_MIN,
    "wait": LONG_WAIT_MAX,
    "dinner_free": DINNER_FREE_MAX,
}.items():
    PLANNER = PLANNER.replace("{" + key + "}", str(value))


PLANNER_FIX = """

# Sửa lịch

Hệ thống chạy thử các lịch dưới đây và thấy lỗi. Chỉ trả lại đúng các ngày có lỗi, mỗi ngày đủ ít nhất 2 lịch (giữ lịch không lỗi, sửa hoặc thay lịch lỗi), giữ nguyên mã lịch của lịch được sửa.
"""


DAYS_PER_PLANNER_CALL = 4
# 2 lịch mỗi ngày đủ: 3 phương án đã khác nhau nhờ 3 nhánh song song (coordinator/branches.py).
MIN_PLANS_PER_DAY = 2
# Lượt Planner sửa lịch (sai luật / chạy thử lệch) tốn ~2/3 token của cả lộ trình mà code đã tự
# sửa phần lớn (bỏ mã lặp, sắp thứ tự, đặt quán ăn, bỏ bớt chỗ khi quá dài, chèn nghỉ trưa): tắt.
PLANNER_FIXES = False
DISTINCT_POOL = 3  # ngày có từ ngần này chỗ chơi mới bắt 2 lịch phải khác bộ chỗ chơi
# Bảng phút đi đưa Planner: mỗi chỗ chỉ ghi ngần này chỗ gần nhất (đủ để gom chỗ gần nhau).
PAIRS_PER_PLACE = 4


class PlanDraft(BaseModel):
    id: str = Field(description="Mã lịch, VD 'L1'")
    leave_stay: str = Field(description="Giờ rời chỗ ở 'HH:MM'")
    stops: list[str] = Field(description="Mã chỗ theo thứ tự đi, gồm cả quán ăn đúng giờ ăn")


class DayDrafts(BaseModel):
    day: int = Field(ge=1)
    plans: list[PlanDraft]


class PlannerOutput(BaseModel):
    days: list[DayDrafts]


def plan_errors(
    out: PlannerOutput,
    codes: set[str],
    days: dict[int, str],
    groups: dict[str, str] | None = None,
    allowed: dict[int, list[str]] | None = None,
    required: dict[int, list[str]] | None = None,
) -> dict[int, list[str]]:
    """Lỗi theo ngày: mã ngoài danh sách, rời chỗ ở sớm hơn giờ bắt đầu, < 2 lịch, lịch trùng,
    mã lặp trong một lịch, 2 chỗ chơi cùng loại trong một lịch, mã không thuộc ngày đó.

    `days`: ngày -> giờ bắt đầu ngày ("HH:MM"). `groups`: mã chỗ chơi D… -> loại (Place.group).
    `allowed`: ngày -> mã được dùng ngày đó (bước 8 chia sẵn); None = mọi mã.
    `required`: ngày -> mã chỗ user chọn, mọi lịch ngày đó phải có.
    """
    groups = groups or {}
    errors: dict[int, list[str]] = {}
    seen_days = {d.day: d for d in out.days}
    for day, start in days.items():
        d = seen_days.get(day)
        errs: list[str] = []
        if d is None:
            errors[day] = [f"thiếu ngày {day}"]
            continue
        sigs = set()
        visit_sets: set[frozenset[str]] = set()
        dup_sigs: set[tuple[str, ...]] = set()
        for p in d.plans:
            bad = [c for c in p.stops if c not in codes]
            if bad:
                errs.append(f"{p.id}: mã {', '.join(bad)} không có trong danh sách")
            if not re.fullmatch(r"\d{2}:\d{2}", p.leave_stay) or p.leave_stay < start:
                errs.append(f"{p.id}: giờ rời chỗ ở {p.leave_stay} sớm hơn {start}")
            if not p.stops:
                errs.append(f"{p.id}: không có chỗ nào")
            if len(set(p.stops)) < len(p.stops):
                errs.append(f"{p.id}: một chỗ xuất hiện 2 lần")
            kinds = [groups[c] for c in p.stops if c in groups]
            twice = sorted({k for k in kinds if kinds.count(k) > 1})
            if twice:
                errs.append(f"{p.id}: 2 chỗ cùng loại {', '.join(twice)} trong một ngày")
            other = [c for c in p.stops if c in codes and allowed and c not in allowed.get(day, [])]
            if other:
                errs.append(f"{p.id}: {', '.join(other)} không thuộc danh sách ngày {day}")
            lacking = [c for c in (required or {}).get(day, []) if c not in p.stops]
            if lacking:
                errs.append(f"{p.id}: thiếu chỗ BẮT BUỘC {', '.join(lacking)}")
            sig = tuple(p.stops)
            if sig in sigs:
                errs.append(f"{p.id}: trùng hệt một lịch khác")
            sigs.add(sig)
            # Cùng bộ chỗ chơi, chỉ đổi thứ tự / quán: chỉ bắt khi ngày có đủ chỗ để đổi.
            must = set((required or {}).get(day, []))
            own = frozenset(c for c in p.stops if c.startswith("D") and c not in must)
            pool = [
                c for c in (allowed or {}).get(day, codes) if c.startswith("D") and c not in must
            ]
            if own and len(pool) >= DISTINCT_POOL and own in visit_sets and sig not in dup_sigs:
                errs.append(
                    f"{p.id}: cùng bộ chỗ chơi với một lịch khác (chỉ đổi thứ tự / quán), "
                    "đổi ít nhất 1 chỗ chơi"
                )
            visit_sets.add(own)
            dup_sigs.add(sig)
        if len(d.plans) < MIN_PLANS_PER_DAY:
            errs.append(f"chỉ có {len(d.plans)} lịch, cần ít nhất {MIN_PLANS_PER_DAY}")
        if errs:
            errors[day] = errs
    return errors


def hours_text(p: Place, day: int) -> str:
    """Giờ mở cửa một ngày cho Planner: "07:00–11:00 + 13:00–17:00", "đóng cửa", "chưa rõ"."""
    if day not in p.hours:
        return "chưa rõ"
    span = p.hours[day]
    if span is None:
        return "đóng cửa"
    edges = [span[0]] + [t for gap in p.breaks.get(day, []) for t in gap] + [span[1]]
    return " + ".join(f"{hhmm(a)}–{hhmm(b)}" for a, b in zip(edges[::2], edges[1::2], strict=False))


def day_target(state: dict[str, Any]) -> int | None:
    """Tiền nên tiêu mỗi ngày cho cả đoàn (vé, ăn, đi lại; chưa gồm chỗ ở và phát sinh) —
    shared/form.day_allowance, cùng số bước chạy thử dùng để đánh dấu vượt ngân sách ngày."""
    form, profile = form_of(state), profile_of(state)
    return day_allowance(form, profile.trip_budget, (state.get("stay") or {}).get("price"))


def must_codes_of(state: dict[str, Any]) -> dict[int, list[str]]:
    """Ngày -> mã chỗ user chọn mà mọi lịch ngày đó phải có (bước 8)."""
    return {int(d): list(cs) for d, cs in (state.get("must_codes") or {}).items()}


def must_text(p: Place) -> str:
    """Nhãn chỗ user chọn cho Planner: " | BẮT BUỘC | user muốn tới lúc 19:00"."""
    if not p.must:
        return ""
    when = must_time_text(p.must_time)
    return " | BẮT BUỘC" + (f" | user muốn tới {when}" if when else "")


def day_codes_of(state: dict[str, Any]) -> dict[int, list[str]]:
    """Ngày -> mã chỗ được dùng ngày đó (bước 8 chia sẵn). State cũ chưa chia thì ngày nào cũng
    dùng toàn bộ danh sách."""
    days = [d.day for d in profile_of(state).days]
    split = state.get("day_codes") or {}
    return {d: split.get(str(d), list(state["codes"])) for d in days}


MAX_PAIRS_PER_DAY = 300  # trần an toàn; một ngày ~12 chỗ chỉ có ~66 cặp


def day_pairs(
    day_codes: list[str], codes: dict[str, str], matrix: dict[tuple[str, str], float]
) -> list[str]:
    """Phút đi giữa từng cặp chỗ CỦA MỘT NGÀY ("D1–D3 12", trung bình 2 chiều), gần trước: Planner
    chỉ ghép chỗ trong ngày nên không cần cặp khác ngày, và không cặp nào của ngày bị cắt mất."""
    out = []
    for i, a in enumerate(day_codes):
        for b in day_codes[i + 1 :]:
            ways = [
                v
                for v in (matrix.get((codes[a], codes[b])), matrix.get((codes[b], codes[a])))
                if v is not None
            ]
            if ways:
                out.append((sum(ways) / len(ways), a, b))
    # Mỗi chỗ chỉ giữ PAIRS_PER_PLACE cặp gần nhất (cặp được giữ nếu gần với 1 trong 2 đầu).
    near: dict[str, int] = {}
    kept = []
    for minutes, a, b in sorted(out):
        if near.get(a, 0) < PAIRS_PER_PLACE or near.get(b, 0) < PAIRS_PER_PLACE:
            near[a], near[b] = near.get(a, 0) + 1, near.get(b, 0) + 1
            kept.append(f"{a}–{b} {round(minutes)}")
    return kept[:MAX_PAIRS_PER_DAY]


def planner_input(
    state: dict[str, Any], days: list[int], fix: dict[int, list[str]] | None = None
) -> str:
    """Dữ liệu đưa Planner: khung giờ, rồi danh sách chỗ RIÊNG của từng ngày, phút đi."""
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    codes = state["codes"]
    by_day = day_codes_of(state)
    matrix = matrix_of(state)
    lines = []
    for d in profile.days:
        if d.day not in days:
            continue
        extra = []
        if d.blocked_slots:
            extra.append("bị chặn " + ", ".join(d.blocked_slots))
        if d.meal_only_slots:
            extra.append("chỉ ăn uống " + ", ".join(d.meal_only_slots))
        weather = weather_text(state, d.day)
        meals = required_meals(d.day_start, d.day_end)
        extra += skipped_meals(d.day_start, d.day_end)
        if d.nap:
            where = {
                "cafe": "ở quán cà phê gần quán trưa",
                "hotel": "ở khách sạn gần quán trưa",
                "stay": "tại chỗ ở",
            }.get(d.nap_at, "tại chỗ ở hoặc quán cà phê gần quán trưa")
            extra.append(f"nghỉ trưa {NAP_MIN} phút {where} sau bữa trưa (hệ thống tự chèn)")
        lines.append(
            f"# Ngày {d.day} ({form.day_date(d.day):%d/%m}): {d.day_start}–{d.day_end}, "
            f"rảnh {d.free_minutes} phút, bữa bắt buộc: {', '.join(meals) or 'không'}"
            + (f"; {'; '.join(extra)}" if extra else "")
            + (f"; {weather}" if weather else "")
        )
        lines.append(
            "Chỗ dùng được ngày này (mã | tên | loại | giờ mở cửa | ở lại | giá/người | "
            "phút từ chỗ ở):"
        )
        for code in by_day[d.day]:
            p = places[codes[code]]
            stay = f"{p.stay_low}–{p.stay_high} phút" if p.stay_low else "chưa rõ"
            price = f"{p.price:,} đ".replace(",", ".") if p.price is not None else "chưa có giá"
            from_stay = matrix.get((STAY, p.place_id))
            lines.append(
                f"{code} | {p.name} | {p.group} | {hours_text(p, d.day)} | {stay} | {price} | "
                f"{int(from_stay) if from_stay is not None else '?'} phút"
                + (" | hợp mục đích" if p.matches_purpose else "")
                + (" | đi buổi tối" if p.kind != "meal" and opens_late(p, d.day) else "")
                + must_text(p)
                + (f" | user nhắc: {p.wanted}" if p.wanted and not p.must else "")
            )
        pairs = day_pairs(by_day[d.day], codes, matrix)
        if pairs:
            lines.append(f"Phút đi giữa các chỗ của ngày {d.day}: " + ", ".join(pairs))
        lines.append("")
    lines.append(
        f"\n# Nhóm khách: {profile.group} {GROUP_NAMES[profile.group]}; mục đích: "
        + (", ".join(PURPOSE_NAMES[p] for p in profile.purposes) or "chưa rõ")
    )
    if form.needs_note.strip():
        lines.append(
            f"# Nhu cầu bắt buộc user tự ghi (phải tôn trọng khi chọn chỗ, quán): "
            f"{form.needs_note.strip()}"
        )
    per_day = day_target(state)
    if per_day:
        lines.append(
            f"# Tiêu khoảng {per_day:,} đ mỗi ngày cho cả đoàn (vé + ăn + đi lại, chưa gồm chỗ ở)".replace(
                ",", "."
            )
        )
    if fix:
        lines.append("\n# Lỗi cần sửa")
        for day, errs in sorted(fix.items()):
            lines.append(f"Ngày {day}: " + "; ".join(errs))
    return "\n".join(lines)


async def call_planner(
    ctx: Ctx,
    state: dict[str, Any],
    days: list[int],
    fix: dict[int, list[str]] | None,
    node: str,
) -> tuple[PlannerOutput | None, int, list]:
    system = PLANNER + (PLANNER_FIX if fix else "")
    tokens = OUTPUT_TOKENS["planner"] + 500 * len(days)
    human = planner_input(state, days, fix)
    return await ctx.ask(system, human, PlannerOutput, tokens, node)


def visit_groups(state: dict[str, Any]) -> dict[str, str]:
    """Mã chỗ chơi D… -> loại (Place.group), để kiểm luật 7–8."""
    places = places_of(state)
    return {c: places[pid].group for c, pid in state["codes"].items() if c.startswith("D")}


def errors_of(plan_id: str, day_errors: list[str]) -> list[str]:
    """Lỗi của đúng một lịch: lỗi ghi mã lịch đó ("L2: …") và lỗi chung của cả ngày."""
    return [e for e in day_errors if e.startswith(f"{plan_id}:") or not re.match(r"^\w+: ", e)]


def fallback_plans(
    state: dict[str, Any], days: set[int] | None = None
) -> dict[str, dict[str, Any]]:
    """Ngày Planner không trả được lịch nào: mỗi ngày 1 lịch code tự ghép từ danh sách của ngày,
    gắn nhãn để người dùng biết đây là lịch dự phòng. `days`: chỉ ghép cho các ngày này."""
    profile, places = profile_of(state), places_of(state)
    by_day = day_codes_of(state)
    out = {}
    used: set[str] = set()
    for d in profile.days:
        if days is not None and d.day not in days:
            continue
        pids = [state["codes"][c] for c in by_day[d.day]]
        visits = [pid for pid in pids if places[pid].kind == "visit"]
        meals = [pid for pid in pids if places[pid].kind == "meal"]
        # Chỗ user chọn luôn có; thêm cho đủ 2 chỗ chơi khác loại, chưa dùng ở ngày dự phòng khác.
        take: list[str] = [pid for pid in visits if places[pid].must]
        for pid in visits:
            if len(take) >= 2:
                break
            if (
                pid not in used
                and pid not in take
                and all(places[pid].group != places[t].group for t in take)
            ):
                take.append(pid)
        take = take or visits[:1]
        used.update(take)
        must_meals = [m for m in meals if places[m].must]
        meal = next((m for m in meals if m not in used), meals[0] if meals else None)
        chosen = must_meals or ([meal] if meal else [])
        used.update(chosen)
        stops = take[:1] + chosen + take[1:]
        plan = DayPlan(id=f"N{d.day}-D1", day=d.day, leave_stay=d.day_start, stops=stops)
        plan.unchecked.append("lịch dự phòng do code ghép, Planner không trả được lịch")
        out[plan.id] = plan_to_dict(plan)
    return out


async def xep_lich(state: ItineraryState, ctx: Ctx) -> dict:
    profile = profile_of(state)
    codes = set(state["codes"])
    groups = visit_groups(state)
    allowed = day_codes_of(state)
    required = must_codes_of(state)
    all_days = [d.day for d in profile.days]
    starts = {d.day: d.day_start for d in profile.days}
    plans: dict[str, dict[str, Any]] = {}
    calls_total, usage_total = 0, []
    for i in range(0, len(all_days), DAYS_PER_PLANNER_CALL):
        batch = all_days[i : i + DAYS_PER_PLANNER_CALL]
        await ctx.log("xep_lich", "step", f"Planner xếp lịch ngày {batch[0]}–{batch[-1]}")
        out, calls, usage = await call_planner(ctx, state, batch, None, "xep_lich")
        calls_total += calls
        usage_total += usage
        if out is None:
            continue
        errors = plan_errors(out, codes, {d: starts[d] for d in batch}, groups, allowed, required)
        if errors and PLANNER_FIXES:
            await ctx.log("xep_lich", "finding", "Lịch sai luật, trả Planner sửa 1 lần")
            fixed, calls, usage = await call_planner(ctx, state, sorted(errors), errors, "xep_lich")
            calls_total += calls
            usage_total += usage
            if fixed:
                keep = {d.day: d for d in out.days}
                keep.update({d.day: d for d in fixed.days if d.day in errors})
                out = PlannerOutput(days=list(keep.values()))
        remaining = plan_errors(
            out, codes, {d: starts[d] for d in batch}, groups, allowed, required
        )
        for d in out.days:
            if d.day not in starts:
                continue
            for p in d.plans:
                # Planner đôi khi ghi 1 mã 2 lần (VD "Mỹ Sơn → Mỹ Sơn"): giữ lần đầu.
                stops = list(
                    dict.fromkeys(state["codes"][c] for c in p.stops if c in state["codes"])
                )
                if not stops:
                    continue
                plan = DayPlan(
                    id=f"N{d.day}-{p.id}",
                    day=d.day,
                    leave_stay=p.leave_stay if len(p.leave_stay) == 5 else starts[d.day],
                    stops=stops,
                )
                own = errors_of(p.id, remaining.get(d.day, []))
                if own:
                    plan.unchecked.append("Planner chưa sửa hết lỗi: " + "; ".join(own[:2]))
                plans[plan.id] = plan_to_dict(plan)
    # Ngày nào Planner bỏ sót (kể cả sau lượt sửa) thì ghép lịch dự phòng cho riêng ngày đó, để
    # phương án không mất ngày.
    missing = set(all_days) - {p["day"] for p in plans.values()}
    if missing:
        plans.update(fallback_plans(state, missing))
    return {"plans": plans, "llm_calls": calls_total, "usage": usage_total}

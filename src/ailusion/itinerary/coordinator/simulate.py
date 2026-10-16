"""Làn Coordinator — bước 10–11: chạy thử từng lịch Planner xếp, theo đồng hồ.

- `reorder_visits`: sắp lại thứ tự chỗ chơi Planner xếp cho ít phút ngồi xe nhất (chỗ ở -> các
  chỗ -> chỗ ở); chỉ nhận khi chạy thử không thêm lỗi (giờ mở cửa, giờ ăn…).
- `simulate`: chạy dòng thời gian một ngày (giờ đi, giờ đến, chờ mở cửa, giờ về).
- `check_plan`: chạy 2 lần (ở ít / ở lâu) -> ok / risky / warn / fail; lỗi cứng, sát giờ, vượt tiền
  xử lý theo nhóm khách. Trả lý do để Planner sửa.
- `kiem_lich`: lịch lệch thì trả Planner sửa 1 lần, vẫn lệch thì giữ kèm nhãn; đo 8 số của từng
  lịch (`measure`) cho bước xếp hạng.
"""

from __future__ import annotations

import contextvars
import heapq
import math
from itertools import combinations, permutations

from ..agents.planner import (
    PLANNER_FIXES,
    PlannerOutput,
    call_planner,
    day_codes_of,
    errors_of,
    must_codes_of,
    plan_errors,
    visit_groups,
)
from ..shared.context import Ctx
from ..shared.fares import city_of
from ..shared.form import (
    AFTERNOON_FROM,
    AFTERNOON_GROUPS,
    BREAKFAST,
    BREAKFAST_FIRST_BY,
    BREAKFAST_IF_START_BY,
    BREAKFAST_TARGET,
    DINNER,
    DINNER_FREE_MAX,
    FULL_DAY_MIN,
    LONG_WAIT_MAX,
    LUNCH,
    LUNCH_TARGET,
    MEAL_MIN_OVERLAP,
    MEALS,
    MIN_MEAL_GAP,
    MIN_VISITS,
    NAP_HOME_MAX,
    NAP_MIN,
    NAP_SPOT_KM,
    NEAR_MIN,
    TripForm,
    day_allowance,
    hhmm,
    must_time_text,
    must_window,
    required_meals,
    to_minutes,
)
from ..shared.math import km, median
from ..shared.models import (
    STAY,
    DayPlan,
    DayWindow,
    ItineraryState,
    Place,
    Run,
    Stop,
    TripProfile,
    dump_plans,
    form_of,
    matrix_of,
    opens_late,
    places_of,
    plans_of,
    profile_of,
    same_brand,
)
from ..tabs import leg_cost, rental_of
from .score import estimate_minutes

MEAL_WAIT_MAX = 45  # tới quán sớm hơn giờ ăn tối đa ngần này phút thì chờ (số tạm, "tu_dat")
# Mốc (giờ đẹp) của từng bữa, theo khung giờ ăn `MEALS`.
MEAL_TARGETS = {"sáng": BREAKFAST_TARGET, "trưa": LUNCH_TARGET, "tối": DINNER}


def meal_of(minute: int) -> str | None:
    """Khung giờ ăn chứa phút này ("sáng" / "trưa" / "tối"), ngoài mọi khung thì None."""
    return next(
        (n for n, (a, b) in MEALS.items() if to_minutes(a) <= minute <= to_minutes(b)), None
    )


# Chờ mở cửa / chờ hết giờ nghỉ lâu hơn ngần này phút là lỗi (VD tới chợ đêm 13:48, chờ 270 phút).
MAX_ORDERINGS_PER_SET = 240  # trần số cách chèn cho mỗi bộ quán được thử của 1 lịch
MAX_SPARE_MEALS = 4  # quán dự phòng của ngày được thử thêm để bù bữa thiếu
EXACT_VISITS = 7  # tới ngần này chỗ chơi thì thử mọi thứ tự; nhiều hơn: láng giềng gần + 2-opt
REORDER_TRIES = 4  # số thứ tự ngắn nhất đem chạy thử, lần lượt tới khi có cái không thêm lỗi


def ride_of(order: list[str], matrix: dict[tuple[str, str], float]) -> float:
    """Phút ngồi xe chỗ ở -> các chỗ theo thứ tự -> chỗ ở (thiếu cặp tính 0 như `simulate`)."""
    path = [STAY, *order, STAY]
    return sum(matrix.get((a, b), 0) for a, b in zip(path, path[1:], strict=False))


def _two_opt(visits: list[str], matrix: dict[tuple[str, str], float]) -> list[str]:
    """Láng giềng gần nhất từ chỗ ở, rồi đảo đoạn (2-opt) tới khi không ngắn hơn được nữa."""
    order, left, prev = [], list(visits), STAY
    while left:
        prev = min(left, key=lambda v: matrix.get((prev, v), 0))
        order.append(prev)
        left.remove(prev)
    best = ride_of(order, matrix)
    improved = True
    while improved:
        improved = False
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                trial = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                if (r := ride_of(trial, matrix)) < best - 1e-9:
                    order, best, improved = trial, r, True
    return order


def shorter_orders(
    visits: list[str], matrix: dict[tuple[str, str], float], tail: list[str] | None = None
) -> list[list[str]]:
    """Các thứ tự chỗ chơi ngồi xe ít hơn thứ tự đang có, ít nhất trước (tối đa REORDER_TRIES).
    `tail`: chỗ buổi tối luôn đi cuối (giữ thứ tự), thứ tự trả về đã gồm chúng."""
    tail = tail or []
    now = ride_of(visits + tail, matrix)
    if len(visits) <= EXACT_VISITS:
        orders = heapq.nsmallest(
            REORDER_TRIES,
            (list(o) + tail for o in permutations(visits)),
            key=lambda o: ride_of(o, matrix),
        )
    else:
        orders = [_two_opt(visits + tail, matrix)]
        orders = [[v for v in orders[0] if v not in tail] + tail]
    return [o for o in orders if ride_of(o, matrix) < now - 1e-9]


def evening_last(visits: list[str], places: dict[str, Place], day: int) -> list[str]:
    """Dời chỗ chỉ đi buổi tối (chợ đêm, phố đi bộ, bar, mở cửa từ 16:00) xuống cuối ngày, mở sớm
    trước; các chỗ khác giữ thứ tự."""
    late = [v for v in visits if opens_late(places[v], day)]
    late.sort(key=lambda v: (places[v].hours.get(day) or (0, 0))[0])
    return [v for v in visits if v not in late] + late


def reorder_visits(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    spare_meals: list[str],
    required_needs: list[str],
) -> bool:
    """Sắp lại thứ tự chỗ chơi cho ít ngồi xe, để một ngày không chạy qua lại giữa hai đầu thành
    phố; chỗ buổi tối (chợ đêm, bar…) luôn xếp cuối để không tới trước giờ mở rồi chờ hàng giờ.
    Mỗi thứ tự mới được đặt lại quán ăn (`place_meals`) rồi chạy thử: nhận thứ tự ít lỗi hơn (ở
    ít, rồi ở lâu: giờ mở cửa, chờ lâu, giờ ăn, giờ về), cùng số lỗi thì phải ngồi xe ít hơn. Đổi
    `plan.stops` tại chỗ; trả True nếu có đổi."""
    visits = [s for s in plan.stops if places[s].kind != "meal"]
    meals = [s for s in plan.stops if places[s].kind == "meal"]
    if len(visits) < 2:
        return False

    def tried(stops: list[str]) -> tuple[DayPlan, tuple[int, int], float]:
        trial = DayPlan(id=plan.id, day=plan.day, leave_stay=plan.leave_stay, stops=list(stops))
        place_meals(trial, places, matrix, window, spare_meals)
        short = simulate(trial, places, matrix, window, required_needs, long_stay=False)
        long = simulate(trial, places, matrix, window, required_needs, long_stay=True)
        return trial, (len(short.issues), len(long.issues)), _ride_min(trial, short)

    _, now_issues, now_ride = tried(plan.stops)
    late = [v for v in evening_last(visits, places, plan.day) if opens_late(places[v], plan.day)]
    early = [v for v in visits if v not in late]
    orders = shorter_orders(early, matrix, late)
    if late and early + late != visits:
        orders.append(early + late)  # thứ tự Planner, chỉ dời chỗ buổi tối xuống cuối
    # Spa, massage… (hợp buổi chiều): thử dời ra sau các chỗ ban ngày khác.
    noon = [v for v in early if places[v].group in AFTERNOON_GROUPS and not places[v].must]
    if noon:
        rest = [v for v in early if v not in noon]
        for order in [rest + noon + late, *[[*o] for o in orders]]:
            moved = [v for v in order if v not in noon and v not in late] + noon + late
            if moved not in orders:
                orders.append(moved)
    best = None
    for order in orders:
        trial, issues, ride = tried(order + meals)
        if (issues, ride) < (now_issues, now_ride) and (best is None or (issues, ride) < best[1]):
            best = (trial, (issues, ride))
    if best is None:
        return False
    plan.stops = best[0].stops
    return True


def drop_extra_meals(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    required_needs: list[str],
) -> int:
    """Mỗi khung sáng / trưa / tối chỉ 1 quán (Planner hay xếp 4 quán liền trong giờ trưa khi
    chuyến "ẩm thực"): giữ quán user chọn, rồi thứ user nhắc, rồi quán tới trước. Quán đang ngoài
    giờ ăn thì giữ (bước đặt quán xếp lại giờ). Đổi `plan.stops` tại chỗ; trả số quán bỏ."""
    dropped = 0
    for _ in range(3):  # bỏ quán làm giờ các chỗ sau đổi: chạy lại tới khi ổn
        run = simulate(plan, places, matrix, window, required_needs, long_stay=False)
        by_window: dict[str | None, list[str]] = {}
        for st in run.stops:
            p = places[st.place_id]
            if p.kind != "meal":
                continue
            name = next(
                (n for n, (a, b) in MEALS.items() if to_minutes(a) <= st.start <= to_minutes(b)),
                None,
            )
            by_window.setdefault(name, []).append(st.place_id)
        extra: list[str] = []
        for name, pids in by_window.items():
            if name is None:
                continue
            ranked = sorted(pids, key=lambda q: (not places[q].must, not places[q].wanted))
            extra += [q for q in ranked[1:] if not places[q].must]
        # Cùng một món user nhắc (VD bún bò) tối đa 1 bữa mỗi ngày: giữ bữa đầu.
        seen_wanted: set[str] = set()
        for st in run.stops:
            p = places[st.place_id]
            if p.kind != "meal" or not p.wanted or p.must or st.place_id in extra:
                continue
            if p.wanted in seen_wanted:
                extra.append(st.place_id)
            seen_wanted.add(p.wanted)
        if not extra:
            break
        plan.stops = [q for q in plan.stops if q not in extra]
        dropped += len(extra)
    return dropped


def drop_bad_meals(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    required_needs: list[str],
) -> int:
    """Quán ăn sai giờ sau khi đã đặt lại: ngoài mọi khung giờ ăn (VD 15:00), hoặc tới phải đứng
    chờ mở cửa lâu (quán sushi 9:30 mới mở làm bữa sáng). Bỏ — trừ quán user chọn / thứ user nhắc —
    để bước đặt quán chèn quán dự phòng đúng giờ. Đổi `plan.stops` tại chỗ; trả số quán bỏ."""
    run = simulate(plan, places, matrix, window, required_needs, long_stay=False)
    bad = []
    prev_meal: int | None = None  # giờ bắt đầu bữa trước còn giữ
    # Còn chỗ hẹn giờ phía sau: chờ tới giờ ăn là thời gian tự do (như `simulate`), không bỏ quán.
    timed = [i for i, s in enumerate(plan.stops) if places[s].must and places[s].must_time]
    for k, st in enumerate(run.stops):
        p = places[st.place_id]
        if p.kind != "meal":
            continue
        close = prev_meal is not None and st.start - prev_meal < MIN_MEAL_GAP
        if p.must or p.wanted:
            prev_meal = st.start
            continue
        if close:  # sát bữa trước: bỏ, bước đặt quán xếp bữa này muộn hơn
            bad.append(st.place_id)
            continue
        # Quán chỉ bán bữa sáng mà rơi vào trưa / tối: bỏ, bước đặt quán chèn quán khác.
        meal_windows = [BREAKFAST] if p.breakfast_only else MEALS.values()
        in_window = any(to_minutes(a) <= st.start <= to_minutes(b) for a, b in meal_windows)
        # Bữa tối cuối ngày được trống tới DINNER_FREE_MAX trước giờ ăn (thời gian tự do).
        last = k == len(run.stops) - 1
        waits_ok = last or any(i > k for i in timed)
        if not in_window or (st.wait > MEAL_WAIT_MAX and not waits_ok):
            bad.append(st.place_id)
        else:
            prev_meal = st.start
    plan.stops = [q for q in plan.stops if q not in bad]
    return len(bad)


def drop_same_group(plan: DayPlan, places: dict[str, Place]) -> int:
    """Mỗi ngày tối đa 1 chỗ chơi mỗi loại (2 spa, 2 chùa… trong một ngày): giữ chỗ đầu (chỗ user
    chọn luôn giữ). Đổi `plan.stops` tại chỗ; trả số chỗ bỏ."""
    seen: set[str] = set()
    cut = []
    for q in plan.stops:
        p = places[q]
        if p.kind == "meal":
            continue
        if p.type_key in seen and not p.must:
            cut.append(q)
        seen.add(p.type_key)
    plan.stops = [q for q in plan.stops if q not in cut]
    plan.unchecked += [
        f"Bỏ {places[q].name}: đã có 1 chỗ {places[q].group} trong ngày" for q in cut
    ]
    return len(cut)


def drop_unfit_afternoon(plan: DayPlan, places: dict[str, Place], window: DayWindow) -> int:
    """Spa, massage… (chỉ từ AFTERNOON_FROM) trong ngày kết thúc trước khi kịp đi (ngày cuối về
    12:30): bỏ, ghi rõ. Đổi `plan.stops` tại chỗ; trả số chỗ bỏ."""
    end = to_minutes(window.day_end)
    cut = [
        q
        for q in plan.stops
        if places[q].kind != "meal"
        and places[q].group in AFTERNOON_GROUPS
        and not places[q].must
        and to_minutes(AFTERNOON_FROM) + (places[q].stay_low or 60) > end
    ]
    plan.stops = [q for q in plan.stops if q not in cut]
    plan.unchecked += [
        f"Bỏ {places[q].name}: {places[q].group} đi từ {AFTERNOON_FROM}, ngày kết thúc "
        f"{window.day_end}"
        for q in cut
    ]
    return len(cut)


def add_missing_musts(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    required: list[str],
    spare_meals: list[str],
    required_needs: list[str],
) -> int:
    """Lịch thiếu chỗ user chọn của ngày (Planner bỏ sót — A/B: 1–2 lần mỗi 15 lượt): chèn vào chỗ
    ít lỗi nhất (đặt lại quán, chạy thử ở ít rồi ở lâu, rồi ít ngồi xe). Không bỏ chỗ nào; lịch
    quá dài thì bước bỏ bớt lo (không bao giờ bỏ chỗ user chọn). Đổi `plan.stops` tại chỗ; trả số
    chỗ đã chèn."""
    added = 0
    for pid in required:
        if pid in plan.stops or pid not in places:
            continue
        visits = [s for s in plan.stops if places[s].kind != "meal"]
        meals = [s for s in plan.stops if places[s].kind == "meal"]
        if places[pid].kind == "meal":  # quán user chọn: bước đặt quán xếp đúng giờ
            orders = [visits + meals + [pid]]
        else:
            orders = [visits[:i] + [pid] + visits[i:] + meals for i in range(len(visits) + 1)]
        best: tuple[list[str], tuple[int, int, int]] | None = None
        for stops in orders:
            trial = DayPlan(id=plan.id, day=plan.day, leave_stay=plan.leave_stay, stops=stops)
            place_meals(trial, places, matrix, window, spare_meals)
            short = simulate(trial, places, matrix, window, required_needs, long_stay=False)
            long = simulate(trial, places, matrix, window, required_needs, long_stay=True)
            score = (len(short.issues), len(long.issues), _ride_min(trial, short))
            if best is None or score < best[1]:
                best = (trial.stops, score)
        if best is not None:
            plan.stops = best[0]
            added += 1
    return added


MAX_FILLS = 3


def fill_visits(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    day_visits: list[str],
    spare_meals: list[str],
    required_needs: list[str],
) -> int:
    """Ngày dài mà lịch gần như chỉ có quán (Planner đưa toàn quán bún bò, bỏ bữa trùng xong còn
    trống), hoặc lịch xong từ trưa mà ngày còn bữa tối bắt buộc (chiều trống, không chèn được bữa
    tối): thêm chỗ chơi của ngày đó (`day_visits`), chỗ gần các điểm đang có nhất trước, chỉ nhận
    khi không làm lịch thêm lỗi. Đổi `plan.stops` tại chỗ; trả số chỗ thêm."""
    # Ngày ngắn (ngày cuối về trưa…): ít nhất 1 chỗ chơi; ngày dài: MIN_VISITS.
    need_visits = MIN_VISITS if window.free_minutes >= FULL_DAY_MIN else 1
    added = 0

    def issues(stops: list[str]) -> tuple[DayPlan, int]:
        trial = DayPlan(id=plan.id, day=plan.day, leave_stay=plan.leave_stay, stops=list(stops))
        place_meals(trial, places, matrix, window, spare_meals)
        run = simulate(trial, places, matrix, window, required_needs, long_stay=False)
        return trial, len(run.issues)

    dinner = "tối" in required_meals(window.day_start, window.day_end)

    def ends_early() -> bool:
        run = simulate(plan, places, matrix, window, required_needs, long_stay=False)
        last = run.stops[-1].leave if run.stops else to_minutes(plan.leave_stay)
        return dinner and last < to_minutes(DINNER[0]) - DINNER_FREE_MAX

    def idle_gap() -> int | None:
        """Vị trí chỗ phải chờ lâu (> LONG_WAIT_MAX) trước khi vào — VD nghỉ trưa xong trống tới
        16:00 chờ chỗ buổi tối mở: chèn chỗ chơi vào khoảng trống đó."""
        run = simulate(plan, places, matrix, window, required_needs, long_stay=False)
        # Chỉ chỗ chơi: chờ tới giờ ăn (trống trước bữa tối) là thời gian tự do có chủ ý.
        return next(
            (
                i
                for i, s in enumerate(run.stops)
                if places[s.place_id].kind != "meal" and s.start - s.arrive > LONG_WAIT_MAX
            ),
            None,
        )

    def meal_gap() -> int | None:
        """Vị trí bữa thứ 2 của cặp bữa liền nhau cách ≥ MIN_MEAL_GAP mà ở giữa không có gì."""
        stops = plan.stops
        for i in range(1, len(stops)):
            if places[stops[i]].kind == "meal" and places[stops[i - 1]].kind == "meal":
                return i
        return None

    _, now = issues(plan.stops)
    while added < MAX_FILLS + 2:
        visits = [q for q in plan.stops if places[q].kind != "meal"]
        idle = idle_gap()
        if len(visits) >= need_visits and not ends_early() and meal_gap() is None and idle is None:
            break
        anchors = plan.stops or [STAY]
        groups = {places[v].type_key for v in visits}
        free = [
            q
            for q in day_visits
            if q not in plan.stops
            and q in places
            and places[q].type_key not in groups  # mỗi ngày 1 chỗ mỗi loại (không 2 spa)
            and min(matrix.get((a, q), 999.0) for a in anchors) <= NEAR_MIN
        ]
        if not free:
            break
        # Gần các điểm đang có nhất trước (sort ổn định: chỗ của ngày đứng trước khi bằng nhau).
        free.sort(key=lambda q: min(matrix.get((a, q), 999.0) for a in anchors))
        best = None
        for q in free[:5]:
            # Chèn sau chỗ gần nhất đang có, hoặc cuối lịch (buổi chiều); quán ăn bước place_meals
            # xếp lại đúng giờ.
            near = (
                min(range(len(plan.stops)), key=lambda i: matrix.get((plan.stops[i], q), 999.0))
                if plan.stops
                else -1
            )
            gap = meal_gap()
            for stops in (
                plan.stops[: near + 1] + [q] + plan.stops[near + 1 :],
                plan.stops + [q],
                *([plan.stops[:gap] + [q] + plan.stops[gap:]] if gap else []),
                *([plan.stops[:idle] + [q] + plan.stops[idle:]] if idle is not None else []),
            ):
                trial, n = issues(stops)
                if n <= now and (best is None or n < best[1]):
                    best = (trial, n)
        if best is None:
            break
        plan.stops, now = best[0].stops, best[1]
        added += 1
    return added


MAX_TRIMS = 3  # bỏ bớt tối đa ngần này chỗ chơi khỏi một lịch quá dài
# Lỗi do lịch nhồi quá nhiều chỗ: bỏ bớt 1 chỗ chơi thường hết (về trễ, chờ mở cửa lâu, ở quá giờ
# đóng cửa, chạm giờ bận).
OVERFULL = ("quá giờ kết thúc", "phải chờ", "đóng cửa lúc", "chạm giờ bận")


def trim_visits(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    spare_meals: list[str],
    required_needs: list[str],
) -> int:
    """Lịch quá dài (về trễ, phải chờ lâu…): bỏ bớt từng chỗ chơi (không bỏ chỗ user chọn / thứ
    user nhắc, giữ ít nhất 1 chỗ chơi), đặt lại quán ăn, nhận cách bỏ làm ít lỗi nhất. Đổi
    `plan.stops` tại chỗ; trả số chỗ đã bỏ."""

    def score(stops: list[str]) -> tuple[DayPlan, tuple[int, int, int]]:
        trial = DayPlan(id=plan.id, day=plan.day, leave_stay=plan.leave_stay, stops=list(stops))
        place_meals(trial, places, matrix, window, spare_meals)
        short = simulate(trial, places, matrix, window, required_needs, long_stay=False)
        long = simulate(trial, places, matrix, window, required_needs, long_stay=True)
        # Cùng số lỗi thì về sớm hơn là gần đạt hơn (bỏ tiếp 1 chỗ nữa có thể hết trễ).
        late = max(0, short.back - to_minutes(window.day_end))
        return trial, (len(short.issues), len(long.issues), late)

    trimmed = 0
    _, now = score(plan.stops)
    for _ in range(MAX_TRIMS):
        run = simulate(plan, places, matrix, window, required_needs, long_stay=False)
        if not any(k in i for i in run.issues for k in OVERFULL):
            break
        visits = [s for s in plan.stops if places[s].kind != "meal"]
        drop = [s for s in visits if not places[s].must and not places[s].wanted]
        if len(visits) <= 1 or not drop:
            break
        best = min((score([x for x in plan.stops if x != s]) for s in drop), key=lambda t: t[1])
        if best[1] >= now:
            break
        cut = [q for q in plan.stops if q not in best[0].stops and places[q].kind != "meal"]
        plan.stops, now = best[0].stops, best[1]
        trimmed += 1
        # Nêu rõ chỗ bị bỏ (bộ quy tắc: rút gọn thì phải nói bỏ gì).
        plan.unchecked += [f"Bỏ {places[q].name}: không đủ thời gian trong ngày" for q in cut]
    return trimmed


def rest_spot(
    lunch: Place, places: dict[str, Place], matrix: dict[tuple[str, str], float], mode: str
) -> tuple[str, int]:
    """Chỗ nghỉ trưa sau quán `lunch` -> (STAY hoặc place_id, phút đi tới). stay: về chỗ ở; cafe /
    hotel: chỗ loại đó gần quán trưa nhất (≤ NAP_SPOT_KM, sao cao trước); auto: chỗ ở cách quán
    trưa ≤ NAP_HOME_MAX phút thì về, xa hơn thì quán cà phê gần đó. Không có chỗ hợp thì về."""
    home = round(matrix.get((lunch.place_id, STAY)) or 0)
    if mode == "stay" or (mode == "auto" and home <= NAP_HOME_MAX):
        return STAY, home
    kind = "hotel" if mode == "hotel" else "cafe"
    near = [
        (km((lunch.lat, lunch.lng), (q.lat, q.lng)), q)
        for q in places.values()
        if q.kind == "rest" and q.rest_of == kind
    ]
    near = [(d, q) for d, q in near if d <= NAP_SPOT_KM]
    if not near:
        return STAY, home
    _d, best = min(near, key=lambda x: (round(x[0] * 2) / 2, -(x[1].rating_floor or 0)))
    return best.place_id, round(estimate_minutes(lunch, best, None))


def place_meals(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    spare_meals: list[str],
) -> bool:
    """Code tự đặt quán ăn vào đúng giờ: giữ thứ tự chỗ chơi Planner xếp, thử mọi chỗ chèn quán
    (và thêm 1 quán dự phòng của ngày khi thiếu bữa, hoặc bỏ bớt quán thừa), chọn cách ít lỗi
    giờ ăn nhất -> ít lỗi khác -> ít ngồi xe. Planner chỉ cần chọn chỗ, không phải canh giờ ăn.
    Đổi `plan.stops` tại chỗ; trả True nếu có đổi."""
    visits = [s for s in plan.stops if places[s].kind != "meal"]
    meals = [s for s in plan.stops if places[s].kind == "meal"]
    eaten_wanted = {places[k].wanted for k in meals if places[k].wanted}
    spares = [
        m
        for m in spare_meals
        if m not in plan.stops
        and not any(same_brand(places[m], places[k]) for k in meals)
        # Món user nhắc đã có trong ngày: không chèn thêm quán cùng món (bún bò sáng lẫn trưa).
        and places[m].wanted not in eaten_wanted
    ][:MAX_SPARE_MEALS]
    # Bỏ bớt quán: không bao giờ bỏ quán user chọn.
    meal_sets = [meals] + [
        meals[:i] + meals[i + 1 :] for i in range(len(meals)) if not places[meals[i]].must
    ]

    def one_brand_twice(chosen: list[str]) -> bool:
        return any(
            same_brand(places[a], places[b]) for i, a in enumerate(chosen) for b in chosen[i + 1 :]
        )

    # Không ăn 2 bữa cùng chuỗi quán trong một ngày (Planner chọn vậy thì bỏ 1 quán, bù quán khác).
    bases = [c for c in meal_sets if not one_brand_twice(c)] or meal_sets
    # Bù quán dự phòng cho đủ số bữa bắt buộc của ngày, vào MỌI bộ quán gốc (kể cả bộ đã bỏ quán
    # trùng chuỗi), rồi lọc lại để không bộ nào ăn 2 bữa cùng chuỗi.
    target = max(2, len(required_meals(window.day_start, window.day_end)))
    meal_sets = list(bases)
    for base in bases:
        for k in range(1, target - len(base) + 1):
            meal_sets += [base + list(extra) for extra in combinations(spares, k)]
    meal_sets = [c for c in meal_sets if not one_brand_twice(c)] or meal_sets
    meal_sets = list({tuple(c): c for c in meal_sets}.values())
    candidates: list[list[str]] = []
    for chosen in meal_sets:
        # Trần theo từng bộ quán: không thì bộ đầu đã chạm trần, bộ có quán bù bữa không được thử.
        candidates += _insertions(visits, chosen)[:MAX_ORDERINGS_PER_SET]

    def head(stops: list[str]) -> tuple[tuple[int, ...], int]:
        """Ít lỗi giờ ăn -> ít lỗi khác -> ít bỏ quán Planner chọn -> ít thêm quán -> ít bữa cùng
        loại món; sau đó (vòng 2) ít lỗi giờ ăn khi ở lâu -> về sớm. Bỏ quán là cách cuối cùng,
        không phải để về sớm hơn. Trả (5 tiêu chí đầu, giờ về)."""
        trial = DayPlan(id=plan.id, day=plan.day, leave_stay=plan.leave_stay, stops=stops)
        run = simulate(trial, places, matrix, window, [], long_stay=False)
        dropped = sum(m not in stops for m in meals)
        added = sum(m not in meals for m in stops if places[m].kind == "meal")
        kinds = [places[m].group for m in stops if places[m].kind == "meal"]
        return (
            sum(map(is_meal_issue, run.issues)),
            len(run.issues),
            len(evening_after_dinner(trial, run.stops, places)),
            dropped,
            added,
            len(kinds) - len(set(kinds)),
        ), run.back

    def long_meal_issues(stops: list[str]) -> int:
        trial = DayPlan(id=plan.id, day=plan.day, leave_stay=plan.leave_stay, stops=stops)
        return sum(map(is_meal_issue, simulate(trial, places, matrix, window, [], True).issues))

    # Vòng 2 (chạy thử ở lâu) chỉ cho các cách hoà nhau ở mọi tiêu chí trước: cùng kết quả với
    # chấm đủ một lượt, mà bớt gần nửa số lần chạy thử.
    pool = candidates or [plan.stops]
    heads = [head(c) for c in pool]
    top = min(h for h, _ in heads)
    ties = [(c, back) for c, (h, back) in zip(pool, heads, strict=True) if h == top]
    best = min(ties, key=lambda t: (long_meal_issues(t[0]), t[1]))[0]
    changed = best != plan.stops
    plan.stops = best
    return changed


def evening_after_dinner(plan: DayPlan, stops: list[Stop], places: dict[str, Place]) -> list[str]:
    """Chỗ buổi tối (mở từ chiều muộn: show, chợ đêm, phố đi bộ, bar…) xếp TRƯỚC bữa tối mà sau
    bữa tối vẫn còn mở đủ thời gian ở: nên đi sau bữa tối. "Đảo Ký Ức Hội An" mở 16:00, show
    diễn buổi tối — tới 16:00 ở 1 tiếng rồi đi ăn là bỏ lỡ show. Không phải lỗi mà là ưu tiên
    khi bước đặt quán (place_meals) chọn thứ tự: sau lỗi giờ ăn và lỗi khác — thiếu bữa tối hay
    chờ 3 tiếng tới bữa tối còn tệ hơn đi show trước khi ăn."""
    dinner = next(
        (
            s
            for s in stops
            if places[s.place_id].kind == "meal"
            and to_minutes(DINNER[0]) <= s.start <= to_minutes(DINNER[1])
        ),
        None,
    )
    if dinner is None:
        return []
    out = []
    for s in stops:
        p = places[s.place_id]
        if s is dinner:
            break
        if p.kind == "meal" or p.must or not opens_late(p, plan.day):
            continue
        span = p.hours.get(plan.day)
        if span and span[1] >= dinner.leave + (p.stay_low or 0):
            out.append(f"{p.name} mở tới {hhmm(span[1])}: nên đi sau bữa tối")
    return out


def is_meal_issue(issue: str) -> bool:
    """Lỗi do `meal_time_issues` sinh ra (câu luôn có "giờ ăn" hoặc "bữa")."""
    return "giờ ăn" in issue or "bữa" in issue


def _insertions(visits: list[str], meals: list[str]) -> list[list[str]]:
    """Mọi cách chèn `meals` (giữ thứ tự) vào giữa / quanh `visits` (giữ thứ tự)."""
    out: list[list[str]] = [list(visits)]
    for meal in meals:
        out = [seq[:i] + [meal] + seq[i:] for seq in out for i in range(len(seq) + 1)]
    return out


def _slots(text_slots: list[str]) -> list[tuple[int, int]]:
    out = []
    for s in text_slots:
        a, b = s.split("–")
        out.append((to_minutes(a), to_minutes(b)))
    return out


# Kết quả chạy thử trong 1 lượt kiem_lich (cùng places, ma trận, khung ngày): đặt quán, sắp thứ
# tự, bù chỗ, bỏ bớt thử lại nhiều lịch y hệt nhau (đo trên chuyến Huế: 39% lượt chạy thử là lặp).
# Mỗi nhánh có bộ nhớ riêng (contextvars theo task); đầy thì xoá cho nhẹ RAM.
_RUNS: contextvars.ContextVar[dict[tuple, Run] | None] = contextvars.ContextVar(
    "_RUNS", default=None
)
MAX_CACHED_RUNS = 20_000


def _copy_run(run: Run) -> Run:
    """Bản sao để người gọi sửa (check_plan chèn lỗi vào `issues`) không làm hỏng bản đã nhớ."""
    return Run(list(run.stops), run.back, list(run.issues), list(run.unchecked), run.rest)


def simulate(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    required_needs: list[str],
    long_stay: bool,
) -> Run:
    """Chạy dòng thời gian 1 lần (xem `_simulate`); trong kiem_lich thì nhớ kết quả theo lịch."""
    cache = _RUNS.get()
    if cache is None:
        return _simulate(plan, places, matrix, window, required_needs, long_stay)
    key = (plan.day, plan.leave_stay, tuple(plan.stops), long_stay, tuple(required_needs))
    run = cache.get(key)
    if run is None:
        if len(cache) >= MAX_CACHED_RUNS:
            cache.clear()
        run = cache[key] = _simulate(plan, places, matrix, window, required_needs, long_stay)
    return _copy_run(run)


def _simulate(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    required_needs: list[str],
    long_stay: bool,
) -> Run:
    """Chạy dòng thời gian 1 lần. Lỗi cứng: tới lúc đóng cửa, chạm khung chặn, về quá giờ kết
    thúc, thiếu nhu cầu bắt buộc. Thiếu giờ mở cửa / thời gian ở lại / phút đi thì ghi
    "chưa kiểm được", không tự điền."""
    blocked = _slots(window.blocked_slots)
    meal_only = _slots(window.meal_only_slots)
    end = to_minutes(window.day_end)
    t = to_minutes(plan.leave_stay)
    prev = STAY
    stops, issues, unchecked = [], [], []
    eaten: set[str] = set()  # khung giờ ăn đã có bữa (theo giờ bắt đầu ăn)
    prev_meal: int | None = None  # giờ bắt đầu bữa trước
    rest: tuple[int, int, int] | None = None
    # Còn chỗ hẹn giờ phía sau: tới quán sớm thì ngồi chờ tới giờ ăn cũng được (đằng nào cũng
    # phải đợi giờ hẹn), không coi là lệch giờ ăn.
    timed = [i for i, pid in enumerate(plan.stops) if places[pid].must and places[pid].must_time]
    for k, pid in enumerate(plan.stops):
        p = places[pid]
        travel = matrix.get((prev, pid))
        if travel is None and prev in places and places[prev].kind == "rest":
            travel = estimate_minutes(
                places[prev], p, None
            )  # quán nghỉ trưa không có trong ma trận
        if travel is None:
            unchecked.append(f"chưa có phút đi tới {p.name}")
            travel = 0
        travel = round(travel)  # ma trận trả phút lẻ (5.9); lịch tính theo phút nguyên
        arrive = t + travel
        span = p.hours.get(plan.day, "missing")
        if span is None:
            issues.append(f"{p.name} đóng cửa ngày {plan.day}")
            open_, close = 0, 0
        elif span == "missing":
            unchecked.append(f"chưa có giờ mở cửa của {p.name}")
            open_, close = 0, 24 * 60
        else:
            open_, close = span  # type: ignore[misc]
        # Spa, massage… hợp buổi chiều / tối: coi như mở từ AFTERNOON_FROM (tới sớm thì chờ, chờ
        # lâu là lỗi -> bước sắp thứ tự dời ra chiều). User tự chọn chỗ thì theo user.
        # Chỉ chỗ chơi: quán "The Garden Coffee, Food & Spa" là quán ăn, không phải spa.
        if p.kind != "meal" and p.group in AFTERNOON_GROUPS and not p.must:
            open_ = max(open_, to_minutes(AFTERNOON_FROM))
        start = max(arrive, open_)
        meal_wait = 0  # chờ tới giờ ăn (có chủ ý, ≤ MEAL_WAIT_MAX) không tính là chờ mở cửa
        if p.kind == "meal":  # tới quán sớm một chút trước giờ ăn thì chờ tới giờ ăn
            wait_max = 24 * 60 if any(i > k for i in timed) else MEAL_WAIT_MAX
            # Bữa tối là điểm cuối ngày (điểm đến ít chỗ, chơi xong từ chiều): trống tới giờ ăn
            # tối đa DINNER_FREE_MAX là thời gian tự do (tắm biển, nghỉ), còn hơn thiếu bữa tối.
            if k == len(plan.stops) - 1:
                wait_max = max(wait_max, DINNER_FREE_MAX)
            now_meal = meal_of(start)
            if now_meal is not None and now_meal not in eaten:
                # Đang trong khung của một bữa chưa ăn (quán sáng 08:05): ăn luôn, chỉ chờ mốc của
                # CHÍNH bữa này (tới 10:50 thì chờ mốc trưa 11:30). Chỗ hẹn giờ phía sau (Chợ
                # Đông Ba 16:30) từng kéo bữa sáng 08:05 qua mốc trưa tới mốc tối (chờ 595 phút).
                first = to_minutes(MEAL_TARGETS[now_meal][0])
                target = first if first > start else None
            else:  # ngoài khung giờ ăn / bữa này đã ăn: chờ mốc KẾ TIẾP, không nhảy qua mốc sau
                target = next(
                    (to_minutes(a) for a, _b in MEAL_TARGETS.values() if to_minutes(a) > start),
                    None,
                )
            if target is not None and target - start <= wait_max:
                # Chờ tới mốc thì về chỗ ở quá giờ kết thúc ngày (ngày cuối về 12:30, quán cách
                # chỗ ở 46 phút): chỉ chờ cho đủ cách bữa trước MIN_MEAL_GAP, rồi ăn (vẫn trễ thì
                # trễ ít hơn, và không thêm lỗi 2 bữa quá sát).
                home = matrix.get((pid, STAY))
                eat = (p.stay_high if long_stay else p.stay_low) or 0
                ends = round(home) + eat if home is not None else None
                if now_meal is not None and ends is not None and target + ends > end:
                    soonest = max(start, (prev_meal or 0) + MIN_MEAL_GAP)
                    if soonest < target:
                        meal_wait, start = soonest - start, soonest
                else:
                    meal_wait, start = target - start, target
        # Chỗ user hẹn giờ: tới sớm thì chờ tới giờ đó; trễ quá khung là lỗi.
        window_ = must_window(p.must_time) if p.must else None
        early = window_ is not None and start < window_[0]
        if early:
            start = window_[0]  # type: ignore[index]
        breaks = sorted(p.breaks.get(plan.day, []))
        for a, b in breaks:  # tới đúng giờ nghỉ giữa ngày thì chờ mở lại
            if a <= start < b:
                start = b
        # Chờ trước giờ hẹn của user là thời gian tự do, không phải lỗi; chờ mở cửa / chờ
        # hết giờ nghỉ quá lâu mới là lỗi.
        # Chỗ buổi tối (show, chợ đêm, bar… mở từ chiều muộn) đã xếp sau mọi chỗ chơi ban ngày:
        # khoảng trống trước đó là thời gian tự do (về nghỉ, dạo), không phải đứng chờ mở cửa.
        # Xếp giữa ngày (còn chỗ chơi ban ngày phía sau) thì vẫn là lỗi chờ.
        evening = (
            p.kind != "meal"
            and opens_late(p, plan.day)
            and all(
                places[q].kind == "meal" or opens_late(places[q], plan.day)
                for q in plan.stops[k + 1 :]
            )
        )
        if not early and not evening and start - arrive - meal_wait > LONG_WAIT_MAX:
            issues.append(
                f"tới {p.name} lúc {hhmm(arrive)}, phải chờ {start - arrive} phút tới "
                f"{hhmm(start)} mới vào được"
            )
        if window_ is not None and start > window_[1]:
            issues.append(
                f"tới {p.name} lúc {hhmm(start)}, trễ so với giờ bạn muốn "
                f"({must_time_text(p.must_time)})"
            )
        stay = p.stay_high if long_stay else p.stay_low
        if stay is None:
            unchecked.append(f"chưa có thời gian ở lại của {p.name}")
            stay = 0
        leave = start + stay
        if span not in (None, "missing") and leave > close:
            issues.append(f"{p.name} đóng cửa lúc {hhmm(close)}, ở tới {hhmm(leave)}")
        for a, b in breaks:
            if start < a < leave:
                issues.append(f"{p.name} nghỉ {hhmm(a)}–{hhmm(b)}, ở tới {hhmm(leave)}")
        for a, b in blocked:
            if start < b and leave > a:
                issues.append(f"{p.name} chạm giờ bận {hhmm(a)}–{hhmm(b)}")

        if p.kind != "meal":
            for a, _b in meal_only:
                if leave > a:
                    issues.append(f"{p.name} chạm khung chỉ ăn uống từ {hhmm(a)}")
        for need in required_needs:
            if p.need_status.get(need) == "no":
                issues.append(f"{p.name} thiếu nhu cầu bắt buộc: {need}")
        if p.kind == "meal":
            if (name := meal_of(start)) is not None:
                eaten.add(name)
            prev_meal = start
        stops.append(Stop(pid, arrive, start, leave, int(travel), max(0, start - arrive)))
        t, prev = leave, pid
        # Nghỉ trưa: ngày dài, vừa ăn trưa xong và còn chỗ phía sau -> nghỉ NAP_MIN phút (ở chỗ
        # ở, hay quán cà phê / khách sạn gần quán trưa — `rest_spot`) rồi đi tiếp từ đó.
        lunch = to_minutes(LUNCH[0]) <= start <= to_minutes(LUNCH[1])
        if window.nap and rest is None and p.kind == "meal" and lunch and k < len(plan.stops) - 1:
            spot, go = rest_spot(p, places, matrix, window.nap_at)
            rest = (k, leave + go, leave + go + NAP_MIN, spot)
            t, prev = rest[2], spot
    back_travel = matrix.get((prev, STAY))
    if back_travel is None and plan.stops:
        unchecked.append("chưa có phút đi về chỗ ở")
        back_travel = 0
    back = t + round(back_travel or 0)
    if back > end:
        issues.append(f"về chỗ ở lúc {hhmm(back)}, quá giờ kết thúc ngày {hhmm(end)}")
    issues += meal_time_issues(plan, stops, places, back, window)
    return Run(stops, back, list(dict.fromkeys(issues)), list(dict.fromkeys(unchecked)), rest)


def meal_time_issues(
    plan: DayPlan,
    stops: list[Stop],
    places: dict[str, Place],
    back: int,
    window: DayWindow | None = None,
) -> list[str]:
    """Quán ăn phải vào khung sáng / trưa / tối (`MEALS`), mỗi khung 1 bữa. Bữa bắt buộc theo
    giờ rảnh của NGÀY (`required_meals`): ngày trọn vẹn đủ 3 bữa, ngày đến trưa thì trưa + tối…
    — thiếu là lỗi, trừ khi đang ở một chỗ trùm cả khung (khu vui chơi 4 tiếng: ăn trong đó).
    Không có `window` thì chỉ bắt bữa lịch đi trọn khung (cách cũ)."""
    out = []
    windows = MEALS
    need = set(required_meals(window.day_start, window.day_end)) if window else set()
    # Đang bận / dự tiệc phủ giờ ăn (VD tiệc cưới từ 16:45): ăn ở đó, không bắt xếp thêm quán.
    if window:
        busy = _slots(window.blocked_slots + window.meal_only_slots)
        for name, (m0, m1) in MEALS.items():
            a0, a1 = to_minutes(m0), to_minutes(m1)
            if any(min(b1, a1) - max(b0, a0) >= MEAL_MIN_OVERLAP for b0, b1 in busy):
                need.discard(name)
    # Bắt đầu ngày sớm (≤ 08:00): ăn sáng là việc đầu tiên, trước BREAKFAST_FIRST_BY.
    if (
        window
        and "sáng" in need
        and to_minutes(window.day_start) <= to_minutes(BREAKFAST_IF_START_BY)
        and stops
    ):
        first = stops[0]
        if places[first.place_id].kind != "meal" or first.start > to_minutes(BREAKFAST_FIRST_BY):
            out.append(
                f"ngày bắt đầu {window.day_start}: bữa sáng phải là việc đầu tiên, "
                f"trước {BREAKFAST_FIRST_BY}"
            )
    eaten: dict[str, int] = {}
    last_meal: Stop | None = None
    for s in stops:
        p = places[s.place_id]
        if p.kind != "meal":
            continue
        # Hai bữa liền nhau quá sát (ăn sáng 8:08 rồi 9:30 lại ăn): ưu tiên cách 3–4 tiếng.
        if last_meal is not None and s.start - last_meal.start < MIN_MEAL_GAP:
            out.append(
                f"{p.name} ăn lúc {hhmm(s.start)}, chỉ cách bữa trước "
                f"{s.start - last_meal.start} phút (cần ≥ {MIN_MEAL_GAP} phút)"
            )
        last_meal = s
        name = next(
            (n for n, (a, b) in windows.items() if to_minutes(a) <= s.start <= to_minutes(b)), None
        )
        if name is None and not (p.must and p.must_time):  # user tự hẹn giờ ăn thì theo user
            out.append(
                f"{p.name} ăn lúc {hhmm(s.start)}, ngoài giờ ăn sáng {BREAKFAST[0]}–"
                f"{BREAKFAST[1]}, trưa {LUNCH[0]}–{LUNCH[1]} và tối {DINNER[0]}–{DINNER[1]}"
            )
        elif name is not None:
            if p.breakfast_only and name != "sáng":
                out.append(f"{p.name} chỉ bán bữa sáng, không làm bữa {name} ({hhmm(s.start)})")
            eaten[name] = eaten.get(name, 0) + 1
    leave = to_minutes(plan.leave_stay)
    for name, (a, b) in windows.items():
        if eaten.get(name, 0) > 1:
            out.append(f"{eaten[name]} bữa {name} trong một ngày")
        elif not eaten.get(name) and not any(
            s.start <= to_minutes(a) and s.leave >= to_minutes(b) for s in stops
        ):
            if name in need:
                out.append(f"thiếu bữa {name} ({a}–{b}): ngày nào cũng phải đủ bữa")
            elif leave <= to_minutes(a) and back >= to_minutes(b):
                out.append(f"đi suốt giờ ăn {name} ({a}–{b}) mà không có quán ăn")
    return out


def plan_cost(plan: DayPlan, places: dict[str, Place], group_size: int) -> int | None:
    prices = [places[pid].price for pid in plan.stops]
    if any(p is None for p in prices):
        return None
    return int(round(sum(prices) * group_size / 1000) * 1000)  # type: ignore[arg-type]


def check_plan(
    plan: DayPlan,
    places: dict[str, Place],
    matrix: dict[tuple[str, str], float],
    window: DayWindow,
    profile: TripProfile,
    form: TripForm,
    required: list[str] | None = None,
    has_stay: bool = True,
    allowance: int | None = None,
    km_matrix: dict[str, float] | None = None,
) -> str | None:
    """Bước 11: gắn trạng thái; trả lý do cần Planner sửa (None = không cần). `required`:
    place_id chỗ user chọn mà lịch ngày này phải có. `has_stay`: chuyến đã có chỗ ngủ (chọn được
    hoặc user đặt sẵn) — không có thì mọi đêm của chuyến là lỗi. `allowance`: tiền nên tiêu mỗi
    ngày cho cả đoàn (shared/form.day_allowance — đúng số Planner nhận); lịch tiêu (`day_spend`:
    vé + ăn + đi lại) hơn số này là "vượt ngân sách ngày"."""
    short = simulate(plan, places, matrix, window, profile.required_needs, long_stay=False)
    plan.rest_after = short.rest[0] if short.rest else None
    plan.rest_at = short.rest[3] if short.rest else None
    if not has_stay and plan.day <= form.num_nights:
        short.issues.insert(0, "chưa có chỗ ngủ đêm nay (không tìm được chỗ ở hợp lệ)")
    lacking = [places[pid].name for pid in required or [] if pid not in plan.stops]
    if lacking:
        short.issues.insert(0, f"thiếu chỗ bạn chọn: {', '.join(lacking)}")
    if not plan.stops:
        short.issues.insert(
            0, "ngày này không còn chỗ nào hợp lệ để xếp (dữ liệu điểm đến thiếu hoặc bị lọc hết)"
        )
    long = simulate(plan, places, matrix, window, profile.required_needs, long_stay=True)
    plan.run = short
    # Gộp, không gán đè: nhãn bước 9 gắn ("Planner chưa sửa hết lỗi") phải còn tới kết quả.
    plan.unchecked = list(dict.fromkeys(plan.unchecked + short.unchecked))
    if to_minutes(plan.leave_stay) < to_minutes(window.day_start):
        short.issues.insert(0, f"rời chỗ ở {plan.leave_stay}, sớm hơn giờ bắt đầu ngày")
    spend = day_spend(plan, places, form, km_matrix) if allowance else None
    over = spend is not None and allowance is not None and spend > allowance
    if short.issues:
        plan.status, plan.issues = "fail", short.issues
        return "lỗi cứng: " + "; ".join(short.issues)
    if long.issues:
        plan.status, plan.issues = "risky", [f"nếu ở lâu: {i}" for i in long.issues]
        if profile.group in ("P2", "P3"):
            return "sát giờ khi ở lâu, bớt 1 chỗ: " + "; ".join(long.issues)
    if over:
        gap = spend - allowance  # type: ignore[operator]
        plan.status = "warn" if plan.status == "ok" else plan.status
        plan.issues.append(f"vượt ngân sách ngày {gap:,} đ".replace(",", "."))
        if profile.group == "P1":
            return f"vượt ngân sách ngày {gap:,} đ, đổi chỗ rẻ hơn".replace(",", ".")
    return None


def day_spend(
    plan: DayPlan,
    places: dict[str, Place],
    form: TripForm,
    km_matrix: dict[str, float] | None = None,
) -> int | None:
    """Tiền cả ngày cho cả đoàn, CHƯA gồm chỗ ở và phát sinh: vé + ăn + đi lại (gọi xe: giá các
    hãng theo km thật; tự lái: thuê xe + xăng theo ngày). Thiếu giá chỗ nào thì None."""
    cost = plan_cost(plan, places, form.group_size)
    if cost is None:
        return None
    spot = places.get(plan.rest_at or "")
    if spot is not None and spot.price:
        cost += spot.price * form.group_size  # ly cà phê / phòng nghỉ theo giờ lúc nghỉ trưa
    city = city_of(form.destination)
    transport = 0
    prev = STAY
    run = plan.run
    path = plan.stops + ([STAY] if plan.stops else [])
    for i, pid in enumerate(path):
        dist = (km_matrix or {}).get(f"{prev}|{pid}")
        if dist is None and prev in places and pid in places:
            a, b = places[prev], places[pid]
            dist = km((a.lat, a.lng), (b.lat, b.lng)) * 1.4
        minutes = run.stops[i].travel if run and i < len(run.stops) else 0
        transport += leg_cost(dist, form.travel_mode, form.group_size, minutes, city) or 0
        prev = pid
    rent = rental_of(form.travel_mode, form.group_size)
    rental = (rent[0] + rent[1]) // 2 if rent else 0
    return int(round((cost + transport + rental) / 1000) * 1000)


def measure(
    plan: DayPlan,
    places: dict[str, Place],
    profile: TripProfile,
    form: TripForm,
    km_matrix: dict[str, float] | None = None,
) -> dict[str, float | None]:
    run = plan.run
    visits = [places[pid] for pid in plan.stops if places[pid].kind != "meal"]
    every = [places[pid] for pid in plan.stops]
    floors = [p.rating_floor for p in every]
    reviews = [p.reviews for p in every if p.reviews]
    met = {
        need
        for need in profile.nice_to_have_needs
        if any(p.need_status.get(need) == "yes" for p in every)
    }
    matches = sum(
        1 for p in every if p.matches_purpose and (p.kind != "meal" or "food" in profile.purposes)
    )
    return {
        "cost": plan_cost(plan, places, form.group_size),
        "ride_min": _ride_min(plan, run) if run else None,
        "wait_min": sum(s.wait for s in run.stops) if run else None,
        "num_stops": len(visits),
        "num_matches": matches,
        "quality": math.floor(sum(floors) / len(floors) * 10) / 10  # type: ignore[arg-type]
        if floors and all(f is not None for f in floors)
        else None,
        "popularity": median([float(r) for r in reviews]),
        "needs_met": len(met),
        "spend": day_spend(plan, places, form, km_matrix),
    }


def _ride_min(plan: DayPlan, run: Run) -> int:
    """Cộng phút đi mọi chặng, kể cả chặng về chỗ ở."""
    last = run.stops[-1].leave if run.stops else to_minutes(plan.leave_stay)
    return sum(s.travel for s in run.stops) + (run.back - last)


def meals_by_day(state: dict, places: dict[str, Place]) -> dict[int, list[str]]:
    """Ngày -> quán dự phòng để bù bữa còn thiếu: quán trong danh sách của ngày đó (bước 8) trước,
    rồi các quán khác của chuyến (ngày ở cụm xa ít quán thì vẫn bù được bữa trưa / tối)."""
    by_day = {
        day: [state["codes"][c] for c in codes if places[state["codes"][c]].kind == "meal"]
        for day, codes in day_codes_of(state).items()
    }
    every = [pid for pid in state["codes"].values() if places[pid].kind == "meal"]
    return {day: own + [m for m in every if m not in own] for day, own in by_day.items()}


async def kiem_lich(state: ItineraryState, ctx: Ctx) -> dict:
    token = _RUNS.set({})
    try:
        return await _kiem_lich(state, ctx)
    finally:
        _RUNS.reset(token)


async def _kiem_lich(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    matrix = matrix_of(state)
    windows = {d.day: d for d in profile.days}
    plans = plans_of(state)
    await ctx.log("kiem_lich", "step", f"Chạy thử {len(plans)} lịch theo đồng hồ, 2 lần")
    spare = meals_by_day(state, places)
    must_codes = must_codes_of(state)
    has_stay = bool(state.get("stay")) or bool(form.stay_booked)
    # Tiền nên tiêu mỗi ngày (đúng số Planner nhận), để đánh dấu lịch vượt ngân sách ngày.
    allowance = day_allowance(form, profile.trip_budget, (state.get("stay") or {}).get("price"))
    money_args = {"allowance": allowance, "km_matrix": state.get("km_matrix")}
    required = {d.day: [state["codes"][c] for c in must_codes.get(d.day, [])] for d in profile.days}
    # Spa / massage trong ngày kết thúc trước 13:00 (ngày cuối): bỏ trước khi xếp gì khác.
    for p in plans.values():
        if drop_same_group(p, places) + drop_unfit_afternoon(p, places, windows[p.day]):
            place_meals(p, places, matrix, windows[p.day], meals_by_day(state, places)[p.day])
    needs = profile.required_needs
    # Chỗ user chọn mà Planner bỏ sót: code chèn vào, không để lịch fail "thiếu chỗ bạn chọn".
    musts = sum(
        add_missing_musts(p, places, matrix, windows[p.day], required[p.day], spare[p.day], needs)
        for p in plans.values()
    )
    if musts:
        await ctx.log("kiem_lich", "finding", f"Chèn {musts} chỗ bạn chọn Planner bỏ sót")
    # Thứ tự chỗ chơi: code sắp lại cho ít ngồi xe (Planner hay xếp chạy qua lại), trước khi đặt
    # quán ăn để bữa ăn rơi đúng chỗ trên thứ tự mới.
    shorter = sum(
        reorder_visits(p, places, matrix, windows[p.day], spare[p.day], needs)
        for p in plans.values()
    )
    if shorter:
        await ctx.log(
            "kiem_lich", "finding", f"Sắp lại thứ tự cho đỡ chạy qua lại ở {shorter} lịch"
        )
    # Quán ăn: code tự đặt vào khung trưa / tối (Planner hay xếp lệch giờ ăn).
    moved = sum(
        place_meals(p, places, matrix, windows[p.day], spare[p.day]) for p in plans.values()
    )
    if moved:
        await ctx.log("kiem_lich", "finding", f"Đặt lại quán ăn cho đúng giờ ở {moved} lịch")
    # Quán thừa (2–4 bữa trong cùng giờ trưa…): mỗi khung giờ ăn giữ 1 quán. Rồi quán còn sai giờ
    # (ngoài giờ ăn, chờ mở cửa lâu) bỏ để chèn quán dự phòng đúng giờ.
    extra_meals = 0
    for p in plans.values():
        extra = drop_extra_meals(p, places, matrix, windows[p.day], needs)
        if extra:
            place_meals(p, places, matrix, windows[p.day], spare[p.day])
        bad = drop_bad_meals(p, places, matrix, windows[p.day], needs)
        if bad:
            place_meals(p, places, matrix, windows[p.day], spare[p.day])
            # Quán dự phòng vẫn sai giờ thì bỏ hẳn (thiếu 1 bữa còn hơn ăn 15:00).
            drop_bad_meals(p, places, matrix, windows[p.day], needs)
        # Lượt cuối: đặt lại quán có thể chèn lại quán trùng giờ ăn / trùng món, bỏ lần nữa.
        extra_meals += extra + bad + drop_extra_meals(p, places, matrix, windows[p.day], needs)
    if extra_meals:
        await ctx.log("kiem_lich", "finding", f"Bỏ {extra_meals} quán thừa / sai giờ ăn")
    # Ngày dài còn ít chỗ chơi (sau khi bỏ quán thừa): bù chỗ chơi của ngày.
    # Chỗ của ngày trước, rồi chỗ chơi khác trong danh sách của nhánh (ngày chỉ được chia 2–3 chỗ,
    # thêm nghỉ trưa thì chiều hết chỗ, quán tối tới quá sớm bị bỏ, ngày kết thúc sau bữa trưa).
    all_visits = [pid for pid in state["codes"].values() if places[pid].kind != "meal"]
    day_visits = {
        day: (own := [state["codes"][c] for c in cs if places[state["codes"][c]].kind != "meal"])
        + [v for v in all_visits if v not in own]
        for day, cs in day_codes_of(state).items()
    }
    # Không bù chỗ đã có trong lịch của ngày khác: phương án ghép 1 lịch mỗi ngày, bù chỗ ngày
    # khác là đi lại 1 chỗ 2 ngày (Nhà thờ tộc Trần ngày 1 lẫn ngày 2).
    used_on: dict[int, set[str]] = {}
    for p in plans.values():
        used_on.setdefault(p.day, set()).update(p.stops)
    filled = 0
    for p in plans.values():
        taken = set().union(*(s for d, s in used_on.items() if d != p.day))
        free = [v for v in day_visits.get(p.day, []) if v not in taken]
        filled += fill_visits(p, places, matrix, windows[p.day], free, spare[p.day], needs)
        used_on[p.day].update(p.stops)
    if filled:
        await ctx.log("kiem_lich", "finding", f"Bù {filled} chỗ chơi cho ngày còn trống")
        # Bù chỗ có đặt lại quán: lọc lại quán sai giờ / trùng giờ ăn một lượt cuối.
        for p in plans.values():
            if drop_bad_meals(p, places, matrix, windows[p.day], needs) + drop_extra_meals(
                p, places, matrix, windows[p.day], needs
            ):
                place_meals(p, places, matrix, windows[p.day], spare[p.day])
                drop_bad_meals(p, places, matrix, windows[p.day], needs)
    # Lịch nhồi quá nhiều chỗ (về trễ, chờ mở cửa lâu): code bỏ bớt chỗ chơi trước khi trả Planner.
    trimmed = sum(
        trim_visits(p, places, matrix, windows[p.day], spare[p.day], needs) for p in plans.values()
    )
    if trimmed:
        await ctx.log("kiem_lich", "finding", f"Bỏ bớt {trimmed} chỗ ở các lịch quá dài")
    fix: dict[int, list[str]] = {}
    for plan in plans.values():
        reason = check_plan(
            plan,
            places,
            matrix,
            windows[plan.day],
            profile,
            form,
            required[plan.day],
            has_stay,
            **money_args,
        )
        if reason:
            fix.setdefault(plan.day, []).append(f"{plan.id.split('-', 1)[1]}: {reason}")
    calls_total, usage_total = 0, []
    if fix and not PLANNER_FIXES:
        await ctx.log(
            "kiem_lich",
            "finding",
            f"{sum(len(v) for v in fix.values())} lịch còn lệch sau khi code sửa",
        )
    if fix and PLANNER_FIXES:
        await ctx.log(
            "kiem_lich",
            "finding",
            f"{sum(len(v) for v in fix.values())} lịch lệch, trả Planner sửa 1 lần",
        )
        groups = visit_groups(state)
        out, calls, usage = await call_planner(ctx, state, sorted(fix), fix, "kiem_lich")
        calls_total, usage_total = calls, usage
        fixed_days = [d for d in out.days if d.day in fix] if out else []
        # Lịch sửa xong cũng phải qua luật của Planner (mã có thật, mỗi ngày 1 chỗ mỗi loại…).
        rule_errors = plan_errors(
            PlannerOutput(days=fixed_days),
            set(state["codes"]),
            {d.day: windows[d.day].day_start for d in fixed_days},
            groups,
            day_codes_of(state),
            must_codes,
        )
        for d in fixed_days:
            for p in d.plans:
                # Một mã ghi 2 lần (VD ăn 2 bữa cùng 1 quán): giữ lần đầu.
                stops = list(
                    dict.fromkeys(state["codes"][c] for c in p.stops if c in state["codes"])
                )
                if not stops:
                    continue
                pid = f"N{d.day}-{p.id}"
                stops = [q for q in stops if q in places]
                new = DayPlan(
                    id=pid,
                    day=d.day,
                    leave_stay=p.leave_stay if len(p.leave_stay) == 5 else windows[d.day].day_start,
                    stops=stops,
                    fixed=True,
                )
                reorder_visits(new, places, matrix, windows[d.day], spare[d.day], needs)
                place_meals(new, places, matrix, windows[d.day], spare[d.day])
                drop_extra_meals(new, places, matrix, windows[d.day], needs)
                if drop_bad_meals(new, places, matrix, windows[d.day], needs):
                    place_meals(new, places, matrix, windows[d.day], spare[d.day])
                trim_visits(new, places, matrix, windows[d.day], spare[d.day], needs)
                # sau sửa: gắn nhãn
                check_plan(
                    new,
                    places,
                    matrix,
                    windows[d.day],
                    profile,
                    form,
                    required[d.day],
                    has_stay,
                    **money_args,
                )
                own = errors_of(p.id, rule_errors.get(d.day, []))
                if own:
                    new.unchecked.append("Planner chưa sửa hết lỗi: " + "; ".join(own[:2]))
                plans[pid] = new
    for plan in plans.values():
        if plan.run is None:
            check_plan(
                plan,
                places,
                matrix,
                windows[plan.day],
                profile,
                form,
                required[plan.day],
                has_stay,
                **money_args,
            )
        plan.metrics = measure(plan, places, profile, form, state.get("km_matrix"))
    statuses = {
        s: sum(p.status == s for p in plans.values()) for s in ("ok", "risky", "warn", "fail")
    }
    await ctx.log("kiem_lich", "finding", ", ".join(f"{k}: {v}" for k, v in statuses.items()))
    return {
        "plans": dump_plans(plans),
        "llm_calls": calls_total,
        "usage": usage_total,
    }

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

import heapq
import math
from itertools import permutations

from ..agents.planner import (
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
from ..shared.form import DINNER, LUNCH, TripForm, hhmm, must_time_text, must_window, to_minutes
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
from ..tabs import EXTRA_PCT, leg_cost, rental_of

MEAL_WAIT_MAX = 45  # tới quán sớm hơn giờ ăn tối đa ngần này phút thì chờ (số tạm, "tu_dat")
# Chờ mở cửa / chờ hết giờ nghỉ lâu hơn ngần này phút là lỗi (VD tới chợ đêm 13:48, chờ 270 phút).
LONG_WAIT_MAX = 45
MAX_ORDERINGS = 400  # trần số cách chèn quán được thử cho 1 lịch
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
    best = None
    for order in orders:
        trial, issues, ride = tried(order + meals)
        if (issues, ride) < (now_issues, now_ride) and (best is None or (issues, ride) < best[1]):
            best = (trial, (issues, ride))
    if best is None:
        return False
    plan.stops = best[0].stops
    return True


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
    spares = [
        m
        for m in spare_meals
        if m not in plan.stops and not any(same_brand(places[m], places[k]) for k in meals)
    ][:3]
    # Bỏ bớt quán: không bao giờ bỏ quán user chọn.
    meal_sets = [meals] + [
        meals[:i] + meals[i + 1 :] for i in range(len(meals)) if not places[meals[i]].must
    ]

    def one_brand_twice(chosen: list[str]) -> bool:
        return any(
            same_brand(places[a], places[b]) for i, a in enumerate(chosen) for b in chosen[i + 1 :]
        )

    # Không ăn 2 bữa cùng chuỗi quán trong một ngày (Planner chọn vậy thì bỏ 1 quán, bù quán khác).
    twice = one_brand_twice(meals)
    meal_sets = [c for c in meal_sets if not one_brand_twice(c)] or meal_sets
    if len(meals) < 2:
        meal_sets += [meals + [m] for m in spares]
    elif twice:
        meal_sets += [c + [m] for c in meal_sets if len(c) == 1 for m in spares]
    candidates: list[list[str]] = []
    for chosen in meal_sets:
        for order in _insertions(visits, chosen):
            candidates.append(order)
            if len(candidates) >= MAX_ORDERINGS:
                break

    def score(stops: list[str]) -> tuple[int, ...]:
        """Ít lỗi giờ ăn -> ít lỗi khác -> ít bỏ quán Planner chọn -> ít thêm quán -> ít bữa cùng
        loại món -> ít lỗi giờ ăn khi ở lâu -> về sớm. Bỏ quán là cách cuối cùng, không phải để về
        sớm hơn."""
        trial = DayPlan(id=plan.id, day=plan.day, leave_stay=plan.leave_stay, stops=stops)
        run = simulate(trial, places, matrix, window, [], long_stay=False)
        long = simulate(trial, places, matrix, window, [], long_stay=True)
        dropped = sum(m not in stops for m in meals)
        added = sum(m not in meals for m in stops if places[m].kind == "meal")
        kinds = [places[m].group for m in stops if places[m].kind == "meal"]
        return (
            sum(map(is_meal_issue, run.issues)),
            len(run.issues),
            dropped,
            added,
            len(kinds) - len(set(kinds)),
            sum(map(is_meal_issue, long.issues)),
            run.back,
        )

    best = min(candidates or [plan.stops], key=score)
    changed = best != plan.stops
    plan.stops = best
    return changed


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


def simulate(
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
    # Còn chỗ hẹn giờ phía sau: tới quán sớm thì ngồi chờ tới giờ ăn cũng được (đằng nào cũng
    # phải đợi giờ hẹn), không coi là lệch giờ ăn.
    timed = [i for i, pid in enumerate(plan.stops) if places[pid].must and places[pid].must_time]
    for k, pid in enumerate(plan.stops):
        p = places[pid]
        travel = matrix.get((prev, pid))
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
        start = max(arrive, open_)
        meal_wait = 0  # chờ tới giờ ăn (có chủ ý, ≤ MEAL_WAIT_MAX) không tính là chờ mở cửa
        if p.kind == "meal":  # tới quán sớm một chút trước giờ ăn thì chờ tới giờ ăn
            wait_max = 24 * 60 if any(i > k for i in timed) else MEAL_WAIT_MAX
            for a, _b in (LUNCH, DINNER):
                if 0 < to_minutes(a) - start <= wait_max:
                    meal_wait, start = to_minutes(a) - start, to_minutes(a)
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
        if not early and start - arrive - meal_wait > LONG_WAIT_MAX:
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
        stops.append(Stop(pid, arrive, start, leave, int(travel), max(0, start - arrive)))
        t, prev = leave, pid
    back_travel = matrix.get((prev, STAY))
    if back_travel is None and plan.stops:
        unchecked.append("chưa có phút đi về chỗ ở")
        back_travel = 0
    back = t + round(back_travel or 0)
    if back > end:
        issues.append(f"về chỗ ở lúc {hhmm(back)}, quá giờ kết thúc ngày {hhmm(end)}")
    issues += meal_time_issues(plan, stops, places, back)
    return Run(stops, back, list(dict.fromkeys(issues)), list(dict.fromkeys(unchecked)))


def meal_time_issues(
    plan: DayPlan, stops: list[Stop], places: dict[str, Place], back: int
) -> list[str]:
    """Quán ăn phải vào khung trưa / tối (`LUNCH`, `DINNER`), mỗi khung 1 bữa; lịch ở ngoài cả
    khung trưa (hoặc tối) thì phải có bữa đó — trừ khi đang ở một chỗ trùm cả khung (khu vui
    chơi 4 tiếng: ăn trong đó). Bữa sáng ăn ở chỗ ở nên không xếp quán."""
    out = []
    windows = {"trưa": LUNCH, "tối": DINNER}
    eaten: dict[str, int] = {}
    for s in stops:
        p = places[s.place_id]
        if p.kind != "meal":
            continue
        name = next(
            (n for n, (a, b) in windows.items() if to_minutes(a) <= s.start <= to_minutes(b)), None
        )
        if name is None and not (p.must and p.must_time):  # user tự hẹn giờ ăn thì theo user
            out.append(
                f"{p.name} ăn lúc {hhmm(s.start)}, ngoài giờ ăn trưa {LUNCH[0]}–"
                f"{LUNCH[1]} và tối {DINNER[0]}–{DINNER[1]}"
            )
        else:
            eaten[name] = eaten.get(name, 0) + 1
    leave = to_minutes(plan.leave_stay)
    for name, (a, b) in windows.items():
        if eaten.get(name, 0) > 1:
            out.append(f"{eaten[name]} bữa {name} trong một ngày")
        elif (
            not eaten.get(name)
            and leave <= to_minutes(a)
            and back >= to_minutes(b)
            and not any(s.start <= to_minutes(a) and s.leave >= to_minutes(b) for s in stops)
        ):
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
) -> str | None:
    """Bước 11: gắn trạng thái; trả lý do cần Planner sửa (None = không cần). `required`:
    place_id chỗ user chọn mà lịch ngày này phải có."""
    short = simulate(plan, places, matrix, window, profile.required_needs, long_stay=False)
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
    cost = plan_cost(plan, places, form.group_size)
    over = (
        cost is not None
        and profile.daily_budget is not None
        and form.spend_style != "unlimited"
        and cost > profile.daily_budget * form.group_size
    )
    if short.issues:
        plan.status, plan.issues = "fail", short.issues
        return "lỗi cứng: " + "; ".join(short.issues)
    if long.issues:
        plan.status, plan.issues = "risky", [f"nếu ở lâu: {i}" for i in long.issues]
        if profile.group in ("P2", "P3"):
            return "sát giờ khi ở lâu, bớt 1 chỗ: " + "; ".join(long.issues)
    if over:
        gap = cost - profile.daily_budget * form.group_size  # type: ignore[operator]
        plan.status = "warn" if plan.status == "ok" else plan.status
        plan.issues.append(f"vượt ngân sách ngày {gap:,} đ".replace(",", "."))
        if profile.group == "P1":
            return f"vượt ngân sách ngày {gap:,} đ, đổi chỗ rẻ hơn".replace(",", ".")
    return None


# Phát sinh lấy số giữa của khoảng `tabs.EXTRA_PCT` (10–15%) khi ước tiền cả ngày để ghép theo
# ngân sách; màn hình vẫn hiện đủ khoảng thấp – cao.
EXTRA_MID = sum(EXTRA_PCT) / 2


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
    """Ngày -> place_id các quán trong danh sách của ngày đó (bước 8), để bù bữa còn thiếu."""
    return {
        day: [state["codes"][c] for c in codes if places[state["codes"][c]].kind == "meal"]
        for day, codes in day_codes_of(state).items()
    }


async def kiem_lich(state: ItineraryState, ctx: Ctx) -> dict:
    form, profile, places = form_of(state), profile_of(state), places_of(state)
    matrix = matrix_of(state)
    windows = {d.day: d for d in profile.days}
    plans = plans_of(state)
    await ctx.log("kiem_lich", "step", f"Chạy thử {len(plans)} lịch theo đồng hồ, 2 lần")
    spare = meals_by_day(state, places)
    must_codes = must_codes_of(state)
    required = {d.day: [state["codes"][c] for c in must_codes.get(d.day, [])] for d in profile.days}
    # Thứ tự chỗ chơi: code sắp lại cho ít ngồi xe (Planner hay xếp chạy qua lại), trước khi đặt
    # quán ăn để bữa ăn rơi đúng chỗ trên thứ tự mới.
    needs = profile.required_needs
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
    fix: dict[int, list[str]] = {}
    for plan in plans.values():
        reason = check_plan(
            plan, places, matrix, windows[plan.day], profile, form, required[plan.day]
        )
        if reason:
            fix.setdefault(plan.day, []).append(f"{plan.id.split('-', 1)[1]}: {reason}")
    calls_total, usage_total = 0, []
    if fix:
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
                stops = [state["codes"][c] for c in p.stops if c in state["codes"]]
                if not stops:
                    continue
                pid = f"N{d.day}-{p.id}"
                new = DayPlan(
                    id=pid,
                    day=d.day,
                    leave_stay=p.leave_stay if len(p.leave_stay) == 5 else windows[d.day].day_start,
                    stops=stops,
                    fixed=True,
                )
                reorder_visits(new, places, matrix, windows[d.day], spare[d.day], needs)
                place_meals(new, places, matrix, windows[d.day], spare[d.day])
                # sau sửa: gắn nhãn
                check_plan(new, places, matrix, windows[d.day], profile, form, required[d.day])
                own = errors_of(p.id, rule_errors.get(d.day, []))
                if own:
                    new.unchecked.append("Planner chưa sửa hết lỗi: " + "; ".join(own[:2]))
                plans[pid] = new
    for plan in plans.values():
        if plan.run is None:
            check_plan(plan, places, matrix, windows[plan.day], profile, form, required[plan.day])
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

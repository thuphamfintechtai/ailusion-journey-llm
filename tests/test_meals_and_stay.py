"""Mỗi ngày đủ bữa theo giờ rảnh (sáng / trưa / tối) và đêm nào cũng có chỗ ngủ."""

from __future__ import annotations

from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.coordinator.simulate import check_plan, meal_time_issues
from ailusion.itinerary.shared.form import TripForm, required_meals
from ailusion.itinerary.shared.models import DayPlan, DayWindow


def test_meals_follow_when_the_day_starts_and_ends() -> None:
    # Bộ quy tắc ăn uống: dậy / bắt đầu ≤ 08:00 đủ 3 bữa; 08:00–11:00 bữa sáng tuỳ chọn; 11:00–13:00
    # bữa trưa là bữa đầu; sau 13:00 không bắt buộc bữa trưa.
    assert required_meals("07:00", "21:00") == ["sáng", "trưa", "tối"]
    assert required_meals("08:00", "21:00") == ["sáng", "trưa", "tối"]
    assert required_meals("09:00", "21:00") == ["trưa", "tối"]  # ăn sáng muộn: tuỳ
    assert required_meals("11:30", "21:00") == ["trưa", "tối"]
    assert required_meals("13:00", "21:00") == ["tối"]  # bắt đầu 13h: không ép bữa trưa
    assert required_meals("14:00", "21:00") == ["tối"]
    assert required_meals("08:00", "18:15") == ["sáng", "trưa"]  # về trước 18:30: không bữa tối
    assert required_meals("08:00", "12:30") == ["sáng", "trưa"]  # ngày cuối về trưa


def test_missing_meal_is_reported_but_a_party_counts_as_dinner() -> None:
    day = DayWindow(day=2, day_start="08:00", day_end="21:00", free_minutes=780)
    plan = DayPlan(id="L1", day=2, leave_stay="08:00", stops=[])
    issues = meal_time_issues(plan, [], {}, 21 * 60, day)
    assert [i.split(" (")[0] for i in issues] == [
        "thiếu bữa sáng",
        "thiếu bữa trưa",
        "thiếu bữa tối",
    ]
    party = day.model_copy(update={"meal_only_slots": ["16:45–21:00"]})  # dự tiệc từ 16:45
    issues = meal_time_issues(plan, [], {}, 21 * 60, party)
    assert not any("bữa tối" in i for i in issues)


def test_every_night_needs_a_place_to_sleep() -> None:
    form = TripForm.model_validate(
        {
            "destination": "Huế",
            "start_date": "2026-10-22",
            "end_date": "2026-10-24",
            "group_size": 2,
        }
    )
    profile = profile_trip(form, [], [])
    night = DayPlan(id="N1", day=1, leave_stay="11:00", stops=[])
    check_plan(night, {}, {}, profile.days[0], profile, form, has_stay=False)
    assert night.status == "fail" and any("chỗ ngủ" in i for i in night.issues)
    last = DayPlan(id="N3", day=3, leave_stay="08:00", stops=[])
    check_plan(last, {}, {}, profile.days[2], profile, form, has_stay=False)
    assert not any("chỗ ngủ" in i for i in last.issues)  # ngày cuối không ngủ lại


async def test_matrix_is_tiled_when_points_exceed_the_tool_limit():
    from ailusion.itinerary.coordinator.score import TILE, matrix_pairs
    from ailusion.itinerary.shared.models import Place

    class Ctx:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def many(self, calls):
            out = []
            for _name, args in calls:
                self.calls.append(args)
                o, d = args["origins"], args.get("destinations")
                pts = o + (d or [])
                dst = range(len(o)) if d is None else range(len(o), len(pts))
                pairs = [
                    {
                        "from": i,
                        "to": j,
                        "duration_min": float(pts[i].split(",")[0]) * 100
                        + float(pts[j].split(",")[0]),
                    }
                    for i in range(len(o))
                    for j in dst
                    if i != j
                ]
                out.append({"status": "success", "pairs": pairs})
            return out, [{"tool": "distance_matrix", "ok": True}] * len(calls)

    pts = [
        Place(place_id=f"p{i}", name=f"p{i}", category="", kind="visit", lat=float(i), lng=0.0)
        for i in range(30)
    ]
    ctx = Ctx()
    pairs, logs = await matrix_pairs(ctx, pts, "car")  # type: ignore[arg-type]
    assert len(ctx.calls) == 9 and all(
        len(c["origins"]) + len(c.get("destinations") or []) <= 25 for c in ctx.calls
    )
    assert len(pairs) == 30 * 29 and len(logs) == 9
    # Mỗi cặp trỏ đúng điểm: phút giả = 100 × lat điểm đi + lat điểm đến.
    assert all(p["duration_min"] == p["from"] * 100 + p["to"] for p in pairs)
    assert TILE * 2 <= 25


def test_long_day_rests_at_the_stay_after_lunch():
    from ailusion.itinerary.coordinator.simulate import simulate
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str, stay: int) -> Place:
        return Place(
            place_id=pid,
            name=pid,
            category="Nhà hàng" if kind == "meal" else "Bảo tàng",
            kind=kind,
            lat=16.0,
            lng=108.2,
            stay_low=stay,
            stay_high=stay,
        )

    places = {
        "b": place("b", "meal", 45),
        "v1": place("v1", "visit", 120),
        "l": place("l", "meal", 45),
        "v2": place("v2", "visit", 210),
        "d": place("d", "meal", 45),
    }
    ids = [*places, STAY]
    m = {(a, b): 15.0 for a in ids for b in ids if a != b}
    long_day = DayWindow(day=2, day_start="07:00", day_end="22:00", nap=True, free_minutes=600)
    plan = DayPlan(id="L1", day=2, leave_stay="07:00", stops=["b", "v1", "l", "v2", "d"])
    run = simulate(plan, places, m, long_day, [], long_stay=False)
    # Tới quán trưa 10:30 (sớm hơn mốc 11:30 quá 45 phút: ăn luôn, vẫn trong khung dịch 1 tiếng)
    # -> về chỗ ở 11:30, nghỉ tới 13:00 -> chỗ chiều tính từ chỗ ở (13:15).
    assert run.rest == (2, 11 * 60 + 30, 13 * 60, STAY)
    v2 = run.stops[3]
    assert v2.arrive == 13 * 60 + 15
    assert not run.issues  # chiều 13:45–17:15, tới quán tối 17:30 chờ 30 phút
    # Chiều bỏ trống (chỗ chiều ngắn): không còn coi trống tới 18:00 là tự do -> lỗi giờ ăn tối.
    places["v2"].stay_low = 60
    gap = simulate(plan, places, m, long_day, [], False).issues
    assert any("ngoài giờ ăn" in i or "thiếu bữa tối" in i for i in gap)
    places["v2"].stay_low = 210
    # Ngày ngắn: không nghỉ trưa.
    short = long_day.model_copy(update={"nap": False})
    assert simulate(plan, places, m, short, [], long_stay=False).rest is None


def test_nap_can_be_at_a_nearby_cafe_or_hotel_or_skipped():
    from ailusion.itinerary.coordinator.simulate import rest_spot, simulate
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str, lat: float, **kw) -> Place:
        return Place(place_id=pid, name=pid, category="", kind=kind, lat=lat, lng=108.2, **kw)

    lunch = place("l", "meal", 16.00)
    cafe_far = place("c_far", "rest", 16.10, rest_of="cafe")  # ~11 km: quá NAP_SPOT_KM
    cafe = place("c", "rest", 16.005, rest_of="cafe", price=50_000)  # ~0,5 km
    hotel = place("h", "rest", 16.01, rest_of="hotel")
    places = {p.place_id: p for p in (lunch, cafe_far, cafe, hotel)}
    near_home = {("l", STAY): 10.0}
    far_home = {("l", STAY): 35.0}
    assert rest_spot(lunch, places, near_home, "auto")[0] == STAY  # chỗ ở gần: về
    assert rest_spot(lunch, places, far_home, "auto")[0] == "c"  # xa: quán cà phê gần nhất
    assert rest_spot(lunch, places, near_home, "cafe")[0] == "c"
    assert rest_spot(lunch, places, far_home, "hotel")[0] == "h"
    assert rest_spot(lunch, places, far_home, "stay") == (STAY, 35)
    # Không có quán nào gần: về chỗ ở.
    alone = {"l": lunch, "c_far": cafe_far}
    assert rest_spot(lunch, alone, far_home, "cafe")[0] == STAY

    # Chặng sau nghỉ trưa tính từ quán cà phê (ước theo khoảng cách, không có trong ma trận).
    v = place("v", "visit", 16.006, stay_low=60, stay_high=60)
    allp = {**places, "v": v}
    m = {(a, b): 10.0 for a in ("l", "v", STAY) for b in ("l", "v", STAY) if a != b}
    m[("l", STAY)] = 35.0
    w = DayWindow(
        day=2, day_start="10:30", day_end="22:00", nap=True, nap_at="auto", free_minutes=600
    )
    run = simulate(
        DayPlan(id="L", day=2, leave_stay="10:30", stops=["l", "v"]), allp, m, w, [], False
    )
    assert run.rest is not None and run.rest[3] == "c"
    assert run.stops[1].arrive > run.rest[2] and run.stops[1].travel > 0

    form = TripForm(
        destination="Đà Nẵng",
        start_date="2026-10-22",
        end_date="2026-10-24",
        group_size=2,
        budget=8_000_000,
        wake_time="06:00",
        nap="none",
    )
    assert not any(d.nap for d in profile_trip(form, [], []).days)


def test_overfull_short_day_drops_a_visit_but_keeps_user_picks():
    from ailusion.itinerary.coordinator.simulate import simulate, trim_visits
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str = "visit", stay: int = 60, **kw) -> Place:
        cat = "Nhà hàng" if kind == "meal" else "Bảo tàng"
        return Place(
            place_id=pid,
            name=pid,
            category=cat,
            kind=kind,
            lat=16.0,
            lng=108.2,
            stay_low=stay,
            stay_high=stay,
            **kw,
        )

    places = {
        "b": place("b", "meal", 45),
        "a": place("a", must=True),
        "x": place("x"),
        "y": place("y"),
        "l": place("l", "meal", 45),
    }
    ids = [*places, STAY]
    m = {(p, q): 15.0 for p in ids for q in ids if p != q}
    # Ngày cuối 08:00–12:30: ăn sáng + 3 chỗ + ăn trưa thì về trễ.
    w = DayWindow(day=3, day_start="08:00", day_end="12:30", free_minutes=270)
    plan = DayPlan(id="L", day=3, leave_stay="08:00", stops=["b", "a", "x", "y", "l"])
    assert any("quá giờ kết thúc" in i for i in simulate(plan, places, m, w, [], False).issues)
    assert trim_visits(plan, places, m, w, [], []) >= 1
    assert "a" in plan.stops  # chỗ user chọn không bị bỏ
    assert not simulate(plan, places, m, w, [], False).issues


def test_one_restaurant_per_meal_time_even_on_a_food_trip():
    from ailusion.itinerary.coordinator.simulate import drop_extra_meals, simulate
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str = "meal", stay: int = 45, **kw) -> Place:
        cat = "Nhà hàng" if kind == "meal" else "Chợ"
        return Place(
            place_id=pid,
            name=pid,
            category=cat,
            kind=kind,
            lat=16.0,
            lng=108.2,
            stay_low=stay,
            stay_high=stay,
            **kw,
        )

    # Như lịch thật: ăn sáng, rồi 4 quán liền trong giờ trưa (11:00, 11:47, 12:35, 13:22).
    places = {
        "b": place("b"),
        "v": place("v", "visit", 120),
        "q1": place("q1"),
        "q2": place("q2", wanted="bún cá"),
        "q3": place("q3"),
        "q4": place("q4"),
        "d": place("d"),
    }
    ids = [*places, STAY]
    m = {(a, b): 2.0 for a in ids for b in ids if a != b}
    w = DayWindow(day=2, day_start="08:00", day_end="22:00", free_minutes=840)
    plan = DayPlan(id="L", day=2, leave_stay="08:00", stops=["b", "v", "q1", "q2", "q3", "q4", "d"])
    assert any(
        "bữa trưa trong một ngày" in i for i in simulate(plan, places, m, w, [], False).issues
    )
    # Bỏ q1, q3, q4; xong thì quán tối "d" dời lên giờ trưa (thành bữa trưa thứ 2) nên bỏ luôn —
    # kiem_lich đặt lại quán tối đúng giờ ngay sau bước này (place_meals).
    assert drop_extra_meals(plan, places, m, w, []) == 4
    lunch = [s for s in plan.stops if s.startswith("q")]
    assert lunch == ["q2"]  # giữ quán đúng món user nhắc
    assert not any("bữa trưa" in i for i in simulate(plan, places, m, w, [], False).issues)


def test_off_hour_and_late_opening_meals_are_dropped():
    from ailusion.itinerary.coordinator.simulate import drop_bad_meals
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str = "meal", stay: int = 45, **kw) -> Place:
        cat = "Nhà hàng" if kind == "meal" else "Bảo tàng"
        return Place(
            place_id=pid,
            name=pid,
            category=cat,
            kind=kind,
            lat=16.0,
            lng=108.2,
            stay_low=stay,
            stay_high=stay,
            **kw,
        )

    # Sushi 9:30 mới mở làm bữa sáng lúc 8:08 (chờ 82 phút); quán ăn lúc 15:00.
    places = {
        "sushi": place("sushi", hours={2: (570, 1320)}),
        "v": place("v", "visit", 120),
        "l": place("l"),
        "v2": place("v2", "visit", 60),
        "ktv": place("ktv"),
        "v3": place("v3", "visit", 120),
        "bb": place("bb", wanted="bún bò"),
    }
    ids = [*places, STAY]
    m = {(a, b): 8.0 for a in ids for b in ids if a != b}
    w = DayWindow(day=2, day_start="08:00", day_end="22:00", free_minutes=840)
    plan = DayPlan(
        id="L", day=2, leave_stay="08:00", stops=["sushi", "v", "l", "v2", "ktv", "v3", "bb"]
    )
    assert drop_bad_meals(plan, places, m, w, []) == 2
    assert "sushi" not in plan.stops and "ktv" not in plan.stops
    assert "bb" in plan.stops  # món user nhắc: giữ


def test_one_meal_per_day_for_the_same_wanted_dish():
    from ailusion.itinerary.coordinator.simulate import drop_extra_meals
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str = "meal", stay: int = 45, **kw) -> Place:
        cat = "Nhà hàng" if kind == "meal" else "Bảo tàng"
        return Place(
            place_id=pid,
            name=pid,
            category=cat,
            kind=kind,
            lat=16.0,
            lng=108.2,
            stay_low=stay,
            stay_high=stay,
            **kw,
        )

    # Bún bò cả sáng lẫn trưa (lịch Huế thật) -> giữ bữa sáng, bỏ bữa trưa (bước sau đặt quán khác).
    places = {
        "bb1": place("bb1", wanted="bún bò"),
        "v": place("v", "visit", 150),
        "bb2": place("bb2", wanted="bún bò"),
    }
    ids = [*places, STAY]
    m = {(a, b): 5.0 for a in ids for b in ids if a != b}
    w = DayWindow(day=2, day_start="08:00", day_end="22:00", free_minutes=840)
    plan = DayPlan(id="L", day=2, leave_stay="08:00", stops=["bb1", "v", "bb2"])
    assert drop_extra_meals(plan, places, m, w, []) == 1
    assert plan.stops == ["bb1", "v"]


def test_long_day_with_only_restaurants_gets_places_to_visit():
    from ailusion.itinerary.coordinator.simulate import fill_visits
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str = "visit", stay: int = 90) -> Place:
        cat = "Nhà hàng" if kind == "meal" else {"a": "Bảo tàng", "b": "Chùa", "c": "Chợ"}[pid]
        return Place(
            place_id=pid,
            name=pid,
            category=cat,
            kind=kind,
            lat=16.0,
            lng=108.2,
            stay_low=stay,
            stay_high=stay,
        )

    places = {"bb": place("bb", "meal", 45), "a": place("a"), "b": place("b"), "c": place("c")}
    ids = [*places, STAY]
    m = {(x, y): 10.0 for x in ids for y in ids if x != y}
    w = DayWindow(day=2, day_start="08:00", day_end="20:45", free_minutes=675)
    plan = DayPlan(id="L", day=2, leave_stay="08:00", stops=["bb"])  # chỉ còn 1 quán bún bò
    assert fill_visits(plan, places, m, w, ["a", "b", "c"], [], []) >= 2
    assert sum(places[s].kind == "visit" for s in plan.stops) >= 2 and "bb" in plan.stops
    short = DayWindow(day=3, day_start="08:00", day_end="11:00", free_minutes=180)
    assert (
        fill_visits(
            DayPlan(id="S", day=3, leave_stay="08:00", stops=["bb"]),
            places,
            m,
            short,
            ["a"],
            [],
            [],
        )
        == 1
    )  # ngày ngắn (về trưa): 1 chỗ chơi là đủ


def test_two_meals_must_be_at_least_two_hours_apart():
    from ailusion.itinerary.coordinator.simulate import drop_bad_meals, simulate
    from ailusion.itinerary.shared.form import skipped_meals
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str = "meal", stay: int = 45) -> Place:
        cat = "Nhà hàng" if kind == "meal" else "Bảo tàng"
        return Place(
            place_id=pid,
            name=pid,
            category=cat,
            kind=kind,
            lat=16.0,
            lng=108.2,
            stay_low=stay,
            stay_high=stay,
        )

    # Ăn sáng 8:15 rồi 9:20 lại vào quán cà phê (lịch Huế thật): quá sát -> lỗi, bước sửa bỏ quán 2.
    places = {"b": place("b"), "c": place("c"), "v": place("v", "visit", 120), "l": place("l")}
    ids = [*places, STAY]
    m = {(a, b): 15.0 for a in ids for b in ids if a != b}
    w = DayWindow(day=2, day_start="08:00", day_end="14:00", free_minutes=360)
    plan = DayPlan(id="L", day=2, leave_stay="08:00", stops=["b", "c", "v", "l"])
    assert any("chỉ cách bữa trước" in i for i in simulate(plan, places, m, w, [], False).issues)
    assert drop_bad_meals(plan, places, m, w, []) == 1 and "c" not in plan.stops
    # Ghi rõ bữa không xếp / tuỳ chọn để không ai tự thêm lại.
    assert skipped_meals("10:00", "22:00") == [
        "Bữa sáng tuỳ chọn: ngày bắt đầu 10:00, có thể ăn sáng muộn"
    ]


def test_breakfast_comes_first_and_spa_waits_for_the_afternoon():
    from ailusion.itinerary.coordinator.simulate import reorder_visits, simulate
    from ailusion.itinerary.shared.models import STAY, Place

    def place(pid: str, kind: str, cat: str, stay: int) -> Place:
        return Place(
            place_id=pid,
            name=pid,
            category=cat,
            kind=kind,
            lat=10.2,
            lng=103.9,
            stay_low=stay,
            stay_high=stay,
        )

    # Lịch Phú Quốc thật: 08:10 spa -> 10:00 ăn sáng -> 12:00 ăn trưa -> bảo tàng -> ăn tối.
    places = {
        "spa": place("spa", "visit", "Spa mát xa", 90),
        "b": place("b", "meal", "Quán ăn", 45),
        "bien": place("bien", "visit", "Bãi biển", 90),
        "l": place("l", "meal", "Nhà hàng", 45),
        "m": place("m", "visit", "Bảo tàng", 90),
        "cho": place("cho", "visit", "Chợ", 90),
        "d": place("d", "meal", "Nhà hàng", 45),
    }
    ids = [*places, STAY]
    mx = {(a, c): 10.0 for a in ids for c in ids if a != c}
    w = DayWindow(day=2, day_start="08:00", day_end="22:45", free_minutes=800)
    plan = DayPlan(
        id="L", day=2, leave_stay="08:00", stops=["spa", "b", "bien", "l", "m", "cho", "d"]
    )
    issues = simulate(plan, places, mx, w, [], False).issues
    assert any("bữa sáng phải là việc đầu tiên" in i for i in issues)
    assert any("tới spa lúc" in i and "phải chờ" in i for i in issues)  # spa coi như mở từ 13:00
    reorder_visits(plan, places, mx, w, [], [])
    run = simulate(plan, places, mx, w, [], False)
    assert places[plan.stops[0]].kind == "meal"  # ăn sáng trước
    spa = next(s for s in run.stops if s.place_id == "spa")
    assert spa.start >= 13 * 60  # spa dời sang chiều


def test_a_cafe_named_spa_is_still_a_breakfast_place():
    from ailusion.itinerary.coordinator.simulate import simulate
    from ailusion.itinerary.shared.models import STAY, Place

    cafe = Place(
        place_id="c",
        name="The Garden Coffee, Food & Spa",
        category="Quán cà phê",
        kind="meal",
        lat=10.2,
        lng=103.9,
        stay_low=45,
        stay_high=45,
    )
    m = {("STAY", "c"): 10.0, ("c", STAY): 10.0, (STAY, "c"): 10.0}
    w = DayWindow(day=4, day_start="08:00", day_end="12:30", free_minutes=270)
    run = simulate(
        DayPlan(id="L", day=4, leave_stay="08:00", stops=["c"]), {"c": cafe}, m, w, [], False
    )
    assert run.stops[0].start < 9 * 60  # không bị bắt chờ tới 13:00 như spa

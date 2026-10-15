"""Pipeline lập lộ trình — các luật code, kiểm bằng đúng ví dụ tính tay trên trang thiết kế."""

from __future__ import annotations

from typing import Any

import pytest

from ailusion.itinerary.agents.budget import PriceFind, accept_price
from ailusion.itinerary.agents.decision import (
    DecisionOutput,
    decision_errors,
    route_links,
    template_decision,
)
from ailusion.itinerary.agents.intake import (
    Extracted,
    ExtractedBusy,
    Extraction,
    apply_extracted,
    parse_clock,
)
from ailusion.itinerary.agents.planner import PlanDraft, PlannerOutput, errors_of, plan_errors
from ailusion.itinerary.coordinator.profile import profile_trip, questions_for
from ailusion.itinerary.coordinator.rank import assemble, mark_beaten, rank
from ailusion.itinerary.coordinator.score import MEALS_PER_DAY, capacity, pick_pool
from ailusion.itinerary.coordinator.simulate import check_plan, simulate
from ailusion.itinerary.shared.form import TripForm, daily_budget
from ailusion.itinerary.shared.math import pareto_tiers, rating_floor
from ailusion.itinerary.shared.models import STAY, DayPlan, Place, place_to_dict, weather_text


def family_form(**over: Any) -> TripForm:
    """Ví dụ của bước 3: đoàn 5 người có bé 4 tuổi và ông bà ngoài 70, đi 3 ngày, ngày 2 dự tiệc
    từ 17:00 chưa biết giờ xong, ngày 3 bay về 15:00."""
    data = {
        "destination": "Đà Nẵng",
        "start_date": "2026-10-25",
        "end_date": "2026-10-27",
        "group_size": 5,
        "budget": 22_500_000,
        "budget_unit": "group_trip",
        "group_type": "family",
        "kids_under_6": 1,
        "seniors_65": 2,
        "departure_time": "15:00",
        "night_return_time": "23:30",
        "busy_slots": [{"day": 2, "busy_start": "17:00", "busy_end": None}],
        "purposes": ["relax", "food"],
        "message": "cả nhà muốn nghỉ ngơi, ăn hải sản; ngày 2 đi tiệc cưới",
    }
    data.update(over)
    return TripForm.model_validate(data)


# ---------------------------------------------------------------- bước 2


def test_daily_budget_by_unit() -> None:
    form = family_form()
    assert (form.num_days, form.num_nights) == (3, 2)
    assert daily_budget(form) == 1_500_000  # 22.500.000 ÷ 5 ÷ 3
    assert daily_budget(family_form(budget=4_500_000, budget_unit="person_trip")) == 1_500_000
    assert daily_budget(family_form(budget=1_500_000, budget_unit="person_day")) == 1_500_000


def test_extracted_ideas_need_a_real_quote() -> None:
    form = family_form(
        group_type=None,
        purposes=[],
        busy_slots=[],
        message="cả nhà 3 thế hệ, muốn nghỉ ngơi; ngày 2 dự tiệc từ 5 giờ chiều",
    )
    extraction = Extraction(
        items=[
            Extracted(field="group_type", value="family", quote="cả nhà 3 thế hệ"),
            Extracted(field="purposes", value="relax", quote="muốn nghỉ ngơi"),
            Extracted(field="purposes", value="fun", quote="thích công viên"),  # không có câu này
            Extracted(field="priority", value="cheap", quote="muốn nghỉ ngơi"),  # giá trị lạ
        ],
        busy_slots=[
            ExtractedBusy(day=2, busy_start="5 giờ chiều", quote="ngày 2 dự tiệc từ 5 giờ chiều")
        ],
    )
    new, kept = apply_extracted(form, extraction, sent=set())
    assert new.group_type == "family"
    assert new.purposes == ["relax"]
    assert new.priority is None
    assert new.busy_slots[0].busy_start == "17:00"
    assert {k["field"] for k in kept} == {"group_type", "purposes", "busy_slots"}


def test_form_fields_the_user_filled_are_kept() -> None:
    form = family_form(message="đi một mình thôi")
    extraction = Extraction(
        items=[Extracted(field="group_type", value="solo", quote="đi một mình")]
    )
    new, _ = apply_extracted(form, extraction, sent={"group_type"})
    assert new.group_type == "family"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("5 giờ chiều", "17:00"), ("17h30", "17:30"), ("9:15", "09:15"), ("buổi sáng", None)],
)
def test_parse_clock(text: str, expected: str | None) -> None:
    assert parse_clock(text) == expected


# ---------------------------------------------------------------- bước 3–4


def test_family_example_is_p2_with_free_minutes_840_435_270() -> None:
    profile = profile_trip(family_form(), stay_prices=[800_000], meal_prices=[100_000])
    assert profile.needs_care is True
    assert profile.nice_to_have_needs[:3] == ["kids", "high_chair", "baby_changing"]
    # Giờ về tối theo user (23:30); không có bé thì cũng không tự đặt luật về sớm.
    assert profile.night_return_time == "23:30"
    # Không nhập giờ đến: ngày 1 bắt đầu 08:00 như các ngày khác. Ngày 1, 2 kéo tới tối: có nghỉ
    # trưa 90 phút (930 − 90, 525 − 90).
    assert [d.nap for d in profile.days] == [True, True, False]
    assert [d.free_minutes for d in profile.days] == [840, 435, 270]
    assert profile.days[1].meal_only_slots == ["16:45–23:30"]
    assert profile.days[2].day_end == "12:30"  # 15:00 − 90 − 30 − 30
    assert (profile.group, profile.rule) == ("P2", "B2")
    assert profile.sort_order[0] == ("ride_min", "min")
    assert "Ngày 1 bắt đầu 08:00" in profile.guess


def test_group_rules_in_order() -> None:
    # B0: user chọn ưu tiên thì theo user.
    assert profile_trip(family_form(priority="saving"), [], []).group == "P1"
    couple = {"group_type": "couple", "kids_under_6": 0, "seniors_65": 0, "busy_slots": []}
    # Không còn B3: ngân sách thấp không tự thành "tiết kiệm" (bước ghép bám ±5% ngân sách).
    low = profile_trip(family_form(**couple, budget=3_000_000), [800_000], [100_000])
    assert (low.group, low.rule) == ("P3", "B4")
    # B4: còn lại.
    rich = profile_trip(family_form(**couple, budget=60_000_000), [800_000], [100_000])
    assert (rich.group, rich.rule) == ("P3", "B4")
    # B1: chuyến 1 ngày mà mục đích chính không phải nghỉ dưỡng.
    short = family_form(**couple, end_date="2026-10-25", purposes=["food"], departure_time="20:00")
    assert profile_trip(short, [], []).group == "P4"


def test_questions_at_most_three_in_order() -> None:
    form = family_form(group_type=None, budget=None, purposes=[], interests="")
    profile = profile_trip(form, [], [])
    assert questions_for(form, profile) == ["H1", "H3", "H2"]


# ---------------------------------------------------------------- bước 7–8


def test_rating_floor_matches_the_worked_examples() -> None:
    assert rating_floor(4.6, 20_000) == pytest.approx(4.565, abs=0.001)
    assert rating_floor(4.8, 40) == pytest.approx(4.026, abs=0.001)
    assert rating_floor(4.5, 0) is None


def test_capacity_example_is_10_5_3() -> None:
    days = profile_trip(family_form(), [], []).days
    assert capacity(days, typical_stay=60, typical_hop=20) == [10, 5, 3]  # ngày 1, 2 nghỉ trưa


def test_pareto_tiers() -> None:
    items = [("a", [3, 3]), ("b", [2, 2]), ("c", [3, 1]), ("d", [1, 1])]
    tiers = pareto_tiers(items, lambda x: x[1])
    assert [[x[0] for x in t] for t in tiers] == [["a"], ["b", "c"], ["d"]]


# ---------------------------------------------------------------- bước 11–14


def _place(pid: str, kind: str = "visit", **kw: Any) -> Place:
    base = {
        "name": pid,
        "category": "Bãi biển",
        "lat": 16.0,
        "lng": 108.2,
        "rating": 4.5,
        "reviews": 5000,
    }
    base.update(kw)
    return Place(place_id=pid, kind=kind, **base)


def test_simulate_long_stay_hits_meal_only_slot() -> None:
    """Ví dụ bước 11: P2, rời chỗ ở 10:00; ở ít thì khớp, ở lâu thì chạm khung tiệc 16:45."""
    places = {
        "vui": _place("vui", stay_low=240, stay_high=360, hours={2: (8 * 60, 20 * 60)}),
        "cho": _place("cho", stay_low=60, stay_high=90, hours={2: (8 * 60, 20 * 60)}),
    }
    matrix = {(STAY, "vui"): 25, ("vui", "cho"): 20, ("cho", STAY): 20}
    profile = profile_trip(family_form(), [], [])
    # Ngày bắt đầu 10:00: không bắt bữa sáng; khu vui chơi trùm giờ trưa; tối dự tiệc từ 16:45.
    window = profile.days[1].model_copy(update={"day_start": "10:00"})
    plan = DayPlan(id="L3", day=2, leave_stay="10:00", stops=["vui", "cho"])
    short = simulate(plan, places, matrix, window, [], long_stay=False)
    assert not short.issues
    reason = check_plan(plan, places, matrix, window, profile, family_form())
    assert plan.status == "risky"
    assert reason and "bớt 1 chỗ" in reason  # nhóm P2 trả Planner bớt 1 chỗ


def test_beaten_and_rank_follow_the_examples() -> None:
    """L6 thua L1 ở ngồi xe, bằng ở mặt khác → kém hơn. L1 với L5 mỗi cái hơn một mặt → giữ cả hai.
    P2 xếp ngồi xe trước; P1 xếp tiền trước rồi số chỗ nhiều."""

    def plan(pid: str, **m: float) -> DayPlan:
        base = {
            "cost": 250_000,
            "ride_min": 50,
            "wait_min": 0,
            "num_stops": 2,
            "num_matches": 2,
            "quality": 4.3,
            "popularity": 11_500,
            "needs_met": 1,
        }
        base.update(m)
        return DayPlan(id=pid, day=2, leave_stay="08:00", stops=[], metrics=base)

    l1, l2, l5, l6 = (
        plan("L1"),
        plan("L2", ride_min=55, quality=4.4),
        plan("L5", ride_min=80, num_stops=3, quality=4.5),
        plan("L6", ride_min=80),
    )
    mark_beaten([l1, l2, l5, l6], "P2")
    assert l6.beaten_by == "L1"
    assert l5.beaten_by is None
    order = rank([l1, l2, l5, l6], [("ride_min", "min"), ("num_stops", "min"), ("cost", "min")])
    assert [p.id for p in order] == ["L1", "L2", "L5", "L6"]
    p1 = rank([l1, l2, l5], [("cost", "min"), ("num_stops", "max"), ("needs_met", "max")])
    assert [p.id for p in p1][:2] == ["L5", "L1"]


# ---------------------------------------------------------------- kiểm đầu ra agent


def test_planner_output_rules() -> None:
    out = PlannerOutput(
        days=[
            {
                "day": 1,
                "plans": [
                    PlanDraft(id="L1", leave_stay="08:00", stops=["D1", "Q1"]),
                    PlanDraft(id="L2", leave_stay="07:00", stops=["D9"]),
                ],
            }
        ]
    )
    errs = plan_errors(out, {"D1", "Q1"}, {1: "08:00", 2: "08:00"})
    text = " ".join(errs[1])
    assert "D9" in text and "sớm hơn" in text and "ít nhất" not in text  # 2 lịch là đủ
    assert errs[2] == ["thiếu ngày 2"]
    one = PlannerOutput(days=[{"day": 1, "plans": [out.days[0].plans[0]]}])
    assert "ít nhất 2" in " ".join(plan_errors(one, {"D1", "Q1"}, {1: "08:00"})[1])


def test_price_must_match_the_page() -> None:
    page = "Bảng giá 2026. Vé người lớn: 120.000đ. Trẻ em miễn phí."
    good = PriceFind(code="V1", price=120_000, url="https://x", quote="Vé người lớn: 120.000đ")
    assert accept_price(good, page)
    assert not accept_price(good.model_copy(update={"price": 100_000}), page)
    assert not accept_price(good.model_copy(update={"quote": "Vé người lớn: 99.000đ"}), page)


def test_decision_rules() -> None:
    facts = "PA1 ngồi xe 50 phút; PA2 ngồi xe 55 phút"
    ok = DecisionOutput(chosen="PA1", why="Phương án 1 ngồi xe 50 phút, ít nhất.")
    assert decision_errors(ok, ["PA1", "PA2"], "", facts) == []
    switch = DecisionOutput(
        chosen="PA2", why="Phương án 2 có biển.", override_quote="thích ngắm hoàng hôn"
    )
    assert (
        decision_errors(switch, ["PA1", "PA2"], "mình thích ngắm hoàng hôn trên biển", facts) == []
    )
    assert decision_errors(switch, ["PA1", "PA2"], "không nói gì", facts)
    made_up = DecisionOutput(chosen="PA1", why="Tiết kiệm 300 nghìn.")
    assert any("300" in e for e in decision_errors(made_up, ["PA1"], "", facts))


# ---------------------------------------------------------------- đa dạng, không lặp


def test_spa_massage_count_as_one_type() -> None:
    assert _place("a", category="Spa").group == "spa"
    assert _place("b", category="Dịch vụ mát xa").group == "spa"
    assert _place("c", category="Chùa").group == "chùa"


def test_pool_takes_at_most_one_place_of_a_type_per_day() -> None:
    beaches = [_place(f"b{i}", category="Bãi biển", rating=4.9) for i in range(6)]
    others = [_place(f"o{i}", category=f"Loại {i}", rating=4.0) for i in range(6)]
    pool, _ = pick_pool(beaches + others, [], need_visits=8, need_meals=0, center=None, per_group=2)
    assert sum(p.group == "biển" for p in pool) == 2


def test_spa_is_capped_at_one_per_two_days() -> None:
    spas = [_place(f"s{i}", category="Spa", rating=4.9) for i in range(6)]
    others = [_place(f"o{i}", category=f"Loại {i}", rating=4.0) for i in range(6)]
    pool, _ = pick_pool(spas + others, [], need_visits=8, need_meals=0, center=None, per_group=3)
    assert sum(p.group == "spa" for p in pool) == 2  # 3 ngày -> tối đa 2 spa


def _draft(day: int, *plans: list[str]) -> Any:
    return {
        "day": day,
        "plans": [
            PlanDraft(id=f"L{i}", leave_stay="09:00", stops=s) for i, s in enumerate(plans, 1)
        ],
    }


def test_planner_may_not_repeat_a_type_or_use_another_days_place() -> None:
    out = PlannerOutput(
        days=[
            _draft(1, ["D1", "Q1", "D2"], ["D3"], ["D4"]),
            _draft(2, ["D3", "Q2"], ["D5"], ["D6", "D6"]),
        ]
    )
    groups = {"D1": "spa", "D2": "spa", "D3": "chùa", "D4": "biển", "D5": "chợ", "D6": "bảo tàng"}
    allowed = {1: ["D1", "D2", "D3", "D4", "Q1"], 2: ["D5", "D6", "Q2"]}
    codes = {"D1", "D2", "D3", "D4", "D5", "D6", "Q1", "Q2"}
    errors = plan_errors(out, codes, {1: "08:00", 2: "08:00"}, groups, allowed)
    assert any("2 chỗ cùng loại spa" in e for e in errors[1])
    assert any("D3 không thuộc danh sách ngày 2" in e for e in errors[2])
    assert any("xuất hiện 2 lần" in e for e in errors[2])


def test_options_do_not_revisit_a_place_on_a_later_day() -> None:
    def plan(pid: str, day: int, stops: list[str]) -> DayPlan:
        return DayPlan(id=pid, day=day, leave_stay="09:00", stops=stops, metrics={"cost": 0})

    ranked = {
        1: [plan("N1-L1", 1, ["a", "q1"]), plan("N1-L2", 1, ["b", "q2"])],
        2: [plan("N2-L1", 2, ["a", "q3"]), plan("N2-L2", 2, ["c", "q4"])],
    }
    form = family_form()
    trip = assemble(ranked, None, form, profile_trip(form, [], []))
    assert [p.id for p in trip["options"][0]["days"]] == ["N1-L1", "N2-L2"]


def test_template_decision_reads_naturally() -> None:
    out = template_decision([{"id": "PA1", "better_at": ["ít ngồi xe nhất"]}], "thong thả")
    assert out.why == (
        "Đề xuất Phương án 1 vì xếp đầu theo điều nhóm thong thả coi trọng nhất, "
        "và hơn các phương án khác ở: ít ngồi xe nhất."
    )


# ---------------------------------------------------------------- lỗi tìm khi rà code


def test_weather_text_counts_days_from_one() -> None:
    state = {"weather": [{"condition": "Nắng"}, {"condition": "Mưa"}]}
    assert weather_text(state, 1) == "Nắng"
    assert weather_text(state, 2) == "Mưa"
    assert weather_text(state, 3) is None


def test_plan_label_only_carries_its_own_errors() -> None:
    errs = ["L1: 2 chỗ cùng loại spa trong một ngày", "L2: D3 đã dùng ở ngày khác", "chỉ có 2 lịch"]
    assert errors_of("L1", errs) == [errs[0], errs[2]]


def test_check_plan_keeps_earlier_labels() -> None:
    profile = profile_trip(family_form(), [], [])
    places = {"vui": _place("vui", stay_low=60, stay_high=90)}
    plan = DayPlan(id="L1", day=2, leave_stay="10:00", stops=["vui"])
    plan.unchecked.append("Planner chưa sửa hết lỗi: x")
    check_plan(plan, places, {}, profile.days[1], profile, family_form())
    assert plan.unchecked[0] == "Planner chưa sửa hết lỗi: x"


def test_maps_link_follows_the_shown_order() -> None:
    places = {
        "a": _place("a", lat=16.1, lng=108.1),
        "b": _place("b", kind="meal", lat=16.2, lng=108.2),
    }
    plans = {"N1-L1": DayPlan(id="N1-L1", day=1, leave_stay="09:00", stops=["b", "a"])}
    stay = _place("s", kind="stay", lat=16.0, lng=108.0)
    state = {"stay": place_to_dict(stay), "form": family_form().model_dump(mode="json")}
    url = route_links({"days": ["N1-L1"]}, plans, places, state)["N1-L1"]
    assert "origin=16.0,108.0" in url and "destination=16.0,108.0" in url
    assert "waypoints=16.2,108.2|16.1,108.1" in url


def test_decomposed_vietnamese_text_still_matches() -> None:
    import unicodedata

    from ailusion.itinerary.shared.tools import add_places

    pool: dict[str, Place] = {}
    nfd = unicodedata.normalize("NFD", "Nhà hàng hải sản")
    add_places(
        pool,
        {
            "status": "success",
            "places": [{"place_id": "x", "name": "Bé Mặn", "category": nfd, "lat": 16, "lng": 108}],
        },
        "find_places",
    )
    assert pool["x"].kind == "meal"
    assert _place("y", category=unicodedata.normalize("NFD", "Chùa")).group == "chùa"


# ---------------------------------------- giờ nghỉ trưa, lịch bận chưa rõ giờ


def test_lunch_break_is_read_and_respected() -> None:
    from ailusion.itinerary.agents.planner import hours_text
    from ailusion.itinerary.shared.tools import add_hours

    pool = {"bt": _place("bt", category="Bảo tàng", stay_low=60, stay_high=60)}
    add_hours(
        pool,
        2,
        {
            "status": "success",
            "results": [{"place_id": "bt", "hours_that_day": "07:30–11:00, 13:30–17:00"}],
        },
    )
    p = pool["bt"]
    assert p.hours[2] == (450, 1020) and p.breaks[2] == [(660, 810)]
    assert hours_text(p, 2) == "07:30–11:00 + 13:30–17:00"
    profile = profile_trip(family_form(), [], [])
    # Tới 11:30 (đang nghỉ) -> chờ tới 13:30, nhưng chờ 120 phút > 45 là lỗi; tới 13:00 chờ 30
    # phút thì được; tới 10:30 ở 60 phút -> chạm giờ nghỉ.
    late = DayPlan(id="L1", day=2, leave_stay="11:30", stops=["bt"])
    run = simulate(
        late, pool, {(STAY, "bt"): 0, ("bt", STAY): 0}, profile.days[1], [], long_stay=False
    )
    assert run.stops[0].start == 810 and [
        i for i in run.issues if "thiếu bữa" not in i and "bữa sáng phải" not in i
    ] == ["tới bt lúc 11:30, phải chờ 120 phút tới 13:30 mới vào được"]
    ok = DayPlan(id="L1", day=2, leave_stay="13:00", stops=["bt"])
    run = simulate(
        ok, pool, {(STAY, "bt"): 0, ("bt", STAY): 0}, profile.days[1], [], long_stay=False
    )
    assert run.stops[0].start == 810 and not [
        i for i in run.issues if "thiếu bữa" not in i and "bữa sáng phải" not in i
    ]
    early = DayPlan(id="L2", day=2, leave_stay="10:30", stops=["bt"])
    run = simulate(
        early, pool, {(STAY, "bt"): 0, ("bt", STAY): 0}, profile.days[1], [], long_stay=False
    )
    assert any("nghỉ 11:00–13:30" in i for i in run.issues)


def test_busy_slot_without_time_asks_h5() -> None:
    form = family_form(busy_slots=[{"day": 2}])
    profile = profile_trip(form, [], [])
    assert "H5" in profile.questions
    assert profile.days[1].blocked_slots == []


def test_options_prefer_repeating_a_meal_over_a_place_to_visit() -> None:
    def plan(pid: str, day: int, stops: list[str]) -> DayPlan:
        return DayPlan(id=pid, day=day, leave_stay="09:00", stops=stops, metrics={"cost": 0})

    ranked = {
        1: [plan("N1-L1", 1, ["a", "q1"])],
        2: [plan("N2-L1", 2, ["a", "q2"]), plan("N2-L2", 2, ["b", "q1"])],
    }
    form = family_form()
    kinds = {"a": "visit", "b": "visit", "q1": "meal", "q2": "meal"}
    trip = assemble(ranked, None, form, profile_trip(form, [], []), kinds)
    # Ngày 2: lặp quán q1 (N2-L2) tốt hơn đi lại chỗ chơi a (N2-L1).
    assert [p.id for p in trip["options"][0]["days"]] == ["N1-L1", "N2-L2"]


def test_places_are_split_across_days_without_repeats() -> None:
    from ailusion.itinerary.coordinator.score import split_by_day

    pool = [
        _place("bana", category="Công viên giải trí", reviews=70000, lat=16.0, lng=107.9),
        _place(
            "cauvang", category="Điểm thu hút khách du lịch", reviews=15000, lat=16.0, lng=107.91
        ),
        _place("spa1", category="Spa", reviews=4000, lat=15.88, lng=108.33),
        _place("spa2", category="Spa", reviews=3000, lat=15.88, lng=108.33),
        _place("bien", category="Bãi biển", reviews=5000, lat=16.06, lng=108.25),
        _place("q1", kind="meal", category="Nhà hàng", reviews=900),
    ]
    near = {("bana", "cauvang"): 5, ("spa1", "spa2"): 3}
    minutes = {f"{a}|{b}": v for (a, b), v in near.items()} | {
        f"{b}|{a}": v for (a, b), v in near.items()
    }
    by_day = split_by_day(pool, [1, 2, 3], minutes)
    visits = [pid for pids in by_day.values() for pid in pids if pid != "q1"]
    assert sorted(visits) == sorted(["bana", "cauvang", "spa1", "spa2", "bien"])  # không lặp
    assert all("q1" in pids for pids in by_day.values())  # ít quán: ngày nào cũng dùng chung
    assert not any({"spa1", "spa2"} <= set(pids) for pids in by_day.values())  # 1 spa mỗi ngày
    assert by_day[1][0] == "bana"  # mỏ neo ngày 1 là chỗ nổi tiếng nhất


def test_missing_prices_are_estimated_and_labelled() -> None:
    from ailusion.itinerary.coordinator.score import fill_price_estimates

    places = {
        "s1": _place("s1", category="Spa", price=300_000, price_by="tool"),
        "s2": _place("s2", category="Spa", price=500_000, price_by="tool"),
        "s3": _place("s3", category="Spa"),
        "bien": _place("bien", category="Bãi biển"),
        "vui": _place("vui", category="Công viên giải trí"),
        "q": _place("q", kind="meal", category="Nhà hàng"),
    }
    fill_price_estimates(places)
    assert (places["s3"].price, places["s3"].price_by) == (400_000, "ước theo chỗ cùng loại")
    assert places["bien"].price == 0 and places["vui"].price == 700_000
    assert places["q"].price_by == "ước theo loại"
    assert places["s1"].price_by == "tool"


def test_bridge_name_is_not_assumed_free() -> None:
    from ailusion.itinerary.coordinator.score import VISIT_PRICE, fill_price_estimates

    places = {"cv": _place("cv", name="Cầu Vàng", category="Điểm thu hút khách du lịch")}
    fill_price_estimates(places)
    assert places["cv"].price == VISIT_PRICE


def test_explanation_may_not_leak_internal_codes() -> None:
    out = DecisionOutput(
        chosen="PA1",
        why="N1-L3 có số sao cao nhất, PA1 ít ngồi xe.",
        explanation={"PA1": "Đề xuất: ít ngồi xe."},
    )
    errs = decision_errors(out, ["PA1", "PA2"], "", "PA1")
    assert any("mã nội bộ N1-L3, PA1" in e for e in errs)
    ok = DecisionOutput(
        chosen="PA1", why="Phương án 1 ít ngồi xe nhất ở ngày 1.", explanation={"PA1": "x"}
    )
    assert not any("mã nội bộ" in e for e in decision_errors(ok, ["PA1"], "", "1"))


def test_pilgrimage_site_is_assumed_free() -> None:
    from ailusion.itinerary.coordinator.score import fill_price_estimates

    places = {"dm": _place("dm", name="Đức Maria Mẹ Sao Biển", category="Địa điểm hành hương")}
    fill_price_estimates(places)
    assert places["dm"].price == 0


def test_meals_must_fall_in_lunch_or_dinner() -> None:
    profile = profile_trip(family_form(), [], [])
    places = {
        "bt": _place("bt", category="Bảo tàng", stay_low=60, stay_high=60),
        "q": _place("q", kind="meal", category="Nhà hàng", stay_low=45, stay_high=45),
    }
    m = {
        (STAY, "q"): 5,
        ("q", "bt"): 5,
        ("bt", STAY): 5,
        (STAY, "bt"): 5,
        ("bt", "q"): 5,
        ("q", STAY): 5,
    }
    breakfast = DayPlan(id="L1", day=2, leave_stay="08:00", stops=["q", "bt"])
    run = simulate(breakfast, places, m, profile.days[1], [], long_stay=False)
    assert not any("ngoài giờ ăn" in i for i in run.issues)  # 08:05 là bữa sáng
    afternoon = DayPlan(id="L0", day=2, leave_stay="14:30", stops=["q", "bt"])
    run = simulate(afternoon, places, m, profile.days[1], [], long_stay=False)
    assert any("ngoài giờ ăn" in i for i in run.issues)
    lunch = DayPlan(id="L2", day=2, leave_stay="10:00", stops=["bt", "q"])
    run = simulate(lunch, places, m, profile.days[1], [], long_stay=False)
    assert not any("giờ ăn" in i for i in run.issues)
    hungry = DayPlan(id="L3", day=2, leave_stay="10:30", stops=["bt", "bt2"])
    places["bt2"] = _place("bt2", category="Chùa", stay_low=150, stay_high=150)
    m |= {("bt", "bt2"): 5, ("bt2", STAY): 5}
    run = simulate(hungry, places, m, profile.days[1], [], long_stay=False)
    assert any("thiếu bữa trưa" in i for i in run.issues)


def test_code_moves_the_meal_into_lunch_time() -> None:
    from ailusion.itinerary.coordinator.simulate import place_meals

    profile = profile_trip(family_form(), [], [])
    places = {
        "bt": _place("bt", category="Bảo tàng", stay_low=90, stay_high=90),
        "chua": _place("chua", category="Chùa", stay_low=60, stay_high=60),
        "q": _place("q", kind="meal", category="Nhà hàng", stay_low=45, stay_high=45),
    }
    ids = ["bt", "chua", "q", STAY]
    m = {(a, b): 10 for a in ids for b in ids if a != b}
    # Ngày bắt đầu 10:00; Planner xếp quán đầu tiên lúc 10:10 (đã qua giờ ăn sáng, chưa tới
    # trưa) -> code chuyển quán vào giờ trưa.
    window = profile.days[1].model_copy(update={"day_start": "10:00"})
    plan = DayPlan(id="L1", day=2, leave_stay="10:00", stops=["q", "bt", "chua"])
    assert place_meals(plan, places, m, window, [])
    assert plan.stops[0] != "q" and "q" in plan.stops  # không bỏ quán, chỉ dời
    run = simulate(plan, places, m, window, [], long_stay=False)
    meal = next(st for st in run.stops if st.place_id == "q")
    assert 11 * 60 <= meal.start <= 13 * 60 + 30 and not run.issues


def test_code_adds_a_spare_meal_when_lunch_is_missing() -> None:
    from ailusion.itinerary.coordinator.simulate import place_meals

    profile = profile_trip(family_form(), [], [])
    places = {
        "bt": _place("bt", category="Bảo tàng", stay_low=120, stay_high=120),
        "chua": _place("chua", category="Chùa", stay_low=120, stay_high=120),
        "q": _place("q", kind="meal", category="Nhà hàng", stay_low=45, stay_high=45),
    }
    ids = ["bt", "chua", "q", STAY]
    m = {(a, b): 10 for a in ids for b in ids if a != b}
    plan = DayPlan(id="L1", day=2, leave_stay="09:30", stops=["bt", "chua"])
    place_meals(plan, places, m, profile.days[1], ["q"])
    assert "q" in plan.stops


def test_same_beach_under_two_names_is_merged() -> None:
    from ailusion.itinerary.coordinator.filter import merge_duplicates

    places = {
        "a": _place(
            "a", name="Bãi biển Mỹ Khê", category="Bãi biển", reviews=5000, lat=16.06, lng=108.247
        ),
        "b": _place(
            "b",
            name="Bãi tắm hàng dừa Mỹ Khê",
            category="Bãi biển",
            reviews=900,
            lat=16.062,
            lng=108.248,
        ),
        "c": _place("c", name="Cầu Rồng", category="Cầu", reviews=9000, lat=16.061, lng=108.227),
        "d": _place(
            "d", name="Cầu Tình Yêu", category="Cầu", reviews=3000, lat=16.064, lng=108.229
        ),
    }
    removed = merge_duplicates(places)
    assert set(places) == {"a", "c", "d"}  # Cầu Rồng và Cầu Tình Yêu vẫn là 2 chỗ
    assert removed == ["Bãi tắm hàng dừa Mỹ Khê (trùng Bãi biển Mỹ Khê)"]


# ------------------------------------- ảnh, lý do chọn, vùng đi, chia ngày theo cụm, thứ tự


def test_thumbnail_is_read_from_search_tools() -> None:
    from ailusion.itinerary.shared.tools import add_photos, add_places

    pool: dict[str, Place] = {}
    raw = {"place_id": "x", "name": "Hồ Xuân Hương", "category": "Hồ", "lat": 11.9, "lng": 108.4}
    add_places(pool, {"status": "success", "places": [raw]}, "find_places")
    assert pool["x"].thumbnail is None
    add_places(
        pool,
        {"status": "success", "places": [{**raw, "thumbnail": "https://img/x.jpg"}]},
        "find_places",
    )
    assert pool["x"].thumbnail == "https://img/x.jpg"  # lần sau có ảnh thì điền
    assert add_photos(pool, {"status": "error"}) == 0
    assert add_photos(pool, {"photos": {"x": "https://img/khac.jpg", "y": None}}) == 0
    assert pool["x"].thumbnail == "https://img/x.jpg"  # không ghi đè ảnh đã có


class _PhotoCtx:
    def __init__(self, result: Any = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result, self.fail = result, fail

    async def tool(self, name: str, **args: Any) -> tuple[Any, dict[str, Any]]:
        self.calls.append((name, args))
        if self.fail:
            return {"status": "error", "message": "hỏng"}, {"tool": name, "ok": False}
        photos = {pid: f"https://img/{pid}.jpg" for pid in args["places"] if pid != "q"}
        return self.result or {"status": "success", "photos": photos}, {"tool": name, "ok": True}

    async def log(self, *_: Any, **__: Any) -> None:
        return None


def _photo_state() -> dict[str, Any]:
    from ailusion.itinerary.shared.models import dump_places, plan_to_dict

    places = {
        "a": _place("a", thumbnail="https://img/co-san.jpg"),
        "b": _place("b", category="Chùa"),
        "q": _place("q", kind="meal", category="Nhà hàng"),
        "khac": _place("khac"),
    }
    plan = DayPlan(id="N1-L1", day=1, leave_stay="09:00", stops=["a", "q", "b"])
    return {
        "form": family_form().model_dump(mode="json"),
        "places": dump_places(places),
        "plans": {plan.id: plan_to_dict(plan)},
        "trip": {"options": [{"id": "PA1", "days": ["N1-L1"]}]},
    }


async def test_photos_are_fetched_once_for_places_missing_them() -> None:
    from ailusion.itinerary.response import fill_photos
    from ailusion.itinerary.shared.models import places_of

    state = _photo_state()
    ctx = _PhotoCtx()
    out = await fill_photos(state, ctx)  # type: ignore[arg-type]
    assert ctx.calls == [
        ("get_place_photos", {"places": ["q", "b"], "destination": "Đà Nẵng"})
    ]  # chỉ chỗ trong phương án, chưa có ảnh; 1 lượt
    places = places_of(out)
    assert places["b"].thumbnail == "https://img/b.jpg"
    assert places["a"].thumbnail == "https://img/co-san.jpg"
    assert places["q"].thumbnail is None  # tool trả null
    assert out["tool_log"] == [{"tool": "get_place_photos", "ok": True}]


async def test_photo_tool_failure_keeps_the_plan_and_full_set_skips_the_call() -> None:
    from ailusion.itinerary.response import fill_photos
    from ailusion.itinerary.shared.models import dump_places, places_of

    state = _photo_state()
    out = await fill_photos(state, _PhotoCtx(fail=True))  # type: ignore[arg-type]
    assert "places" not in out and out["tool_log"][0]["ok"] is False
    places = places_of(state)
    for p in places.values():
        p.thumbnail = p.thumbnail or "https://img/x.jpg"
    state["places"] = dump_places(places)
    ctx = _PhotoCtx()
    assert await fill_photos(state, ctx) == {}  # type: ignore[arg-type]
    assert not ctx.calls


def test_summary_is_cleaned_and_trimmed() -> None:
    from ailusion.itinerary.response import SUMMARY_CHARS, short_summary

    assert short_summary("") is None
    assert short_summary("  <b>Hồ</b>   nước  giữa\nthành phố ") == "Hồ nước giữa thành phố"
    long = short_summary("rừng thông " * 60)
    assert long and len(long) <= SUMMARY_CHARS and long.endswith("…") and "  " not in long


def test_item_gets_image_summary_and_short_reasons() -> None:
    from ailusion.itinerary.response import _day
    from ailusion.itinerary.shared.models import Run, Stop

    form = family_form()
    places = {
        "cho": _place(
            "cho",
            name="Chợ đêm Đà Lạt",
            category="Chợ",
            rating=4.6,
            reviews=2300,
            price=0,
            price_by="tool",
            matches_purpose=True,
            match_by="từ khoá 'chợ'",
            hours={2: (17 * 60, 23 * 60)},
            thumbnail="https://img/cho.jpg",
            summary="Chợ   đêm bán đồ len, đồ nướng",
        ),
    }
    plan = DayPlan(id="N2-L1", day=2, leave_stay="17:00", stops=["cho"])
    run = Run(stops=[Stop("cho", 1026, 1026, 1086, 6, 0)], back=1092, issues=[], unchecked=[])
    day = _day(plan, run, places, form, {"stay": None}, True, 1_500_000, {})
    item = day.items[1]
    assert item.image == "https://img/cho.jpg"
    assert item.summary == "Chợ đêm bán đồ len, đồ nướng"
    assert item.why == [
        "Hợp mục đích chuyến (từ khoá 'chợ')",
        "4.6★ tin được, 2.300 đánh giá",
        "Cách chỗ ở 6 phút",
    ]
    assert day.items[0].why == [] and day.items[0].image is None


def test_reasons_mention_budget_and_hours_when_nothing_else_fits() -> None:
    from ailusion.itinerary.response import why_of
    from ailusion.itinerary.shared.models import Stop

    p = _place(
        "bt", category="Bảo tàng", rating=None, price=50000, price_by="tool", hours={2: (480, 1020)}
    )
    why = why_of(p, 2, Stop("bt", 600, 600, 690, 25, 0), first=False, daily_budget=1_500_000)
    assert why == [
        "Giá 50.000 đ/người, nhẹ so với ngân sách 1.500.000 đ/người/ngày",
        "Mở cửa 08:00–17:00, ghé lúc 10:00 vừa giờ",
    ]
    near = why_of(p, 2, Stop("bt", 600, 600, 690, 6, 0), first=False)
    assert "Cách điểm trước 6 phút" in near


def test_area_note_names_the_farthest_place_and_explains_an_outlier() -> None:
    from ailusion.itinerary.response import area_note

    stay = {"name": "KS", "lat": 11.94, "lng": 108.44}
    a = _place("a", name="Hồ Xuân Hương", lat=11.942, lng=108.442)
    b = _place("b", name="Chợ Đà Lạt", lat=11.946, lng=108.437)
    far = _place(
        "far",
        name="Chùa Linh Phước",
        lat=11.944,
        lng=108.58,
        rating=4.7,
        reviews=20000,
        matches_purpose=True,
    )
    note = area_note([a, b], {"stay": stay}, {(STAY, "b"): 6.0})
    assert note == "Các điểm trong bán kính 1 km quanh chỗ ở; xa nhất: Chợ Đà Lạt (6 phút từ chỗ ở)"
    note = area_note([a, b, far], {"stay": stay}, {(STAY, "far"): 25.0})
    assert note and "riêng Chùa Linh Phước xa hơn (25 phút từ chỗ ở)" in note
    assert "hợp mục đích chuyến" in note and "4.7★" in note
    assert area_note([], {"stay": stay}, {}) is None


def test_split_keeps_a_far_cluster_off_the_central_day() -> None:
    from ailusion.itinerary.coordinator.score import split_by_day

    # Trung tâm Đà Lạt và một cụm cách ~15 km về phía đông (Trại Mát).
    pool = [
        _place("c1", category="Hồ", reviews=50000, lat=11.94, lng=108.44),
        _place("c2", category="Chợ", reviews=9000, lat=11.943, lng=108.437),
        _place("c3", category="Nhà thờ", reviews=4000, lat=11.936, lng=108.44),
        _place("e1", category="Chùa", reviews=8000, lat=11.94, lng=108.58),
        _place("e2", category="Ga", reviews=3000, lat=11.945, lng=108.575),
        _place("e3", category="Đồi chè", reviews=2000, lat=11.95, lng=108.585),
    ]
    by_day = split_by_day(pool, [1, 2], {})
    assert set(by_day[1]) == {"c1", "c2", "c3"}
    assert set(by_day[2]) == {"e1", "e2", "e3"}


def test_reorder_cuts_zigzag_and_respects_opening_hours() -> None:
    from ailusion.itinerary.coordinator.simulate import reorder_visits, ride_of

    profile = profile_trip(family_form(), [], [])
    window = profile.days[1]
    places = {k: _place(k, category=k, stay_low=30, stay_high=30) for k in ("a", "b", "c")}
    pos = {STAY: 0, "a": 1, "b": 2, "c": 3}  # trên một đường thẳng, mỗi khoảng 10 phút
    m = {(x, y): abs(pos[x] - pos[y]) * 10.0 for x in pos for y in pos if x != y}
    plan = DayPlan(id="L1", day=2, leave_stay="09:00", stops=["c", "a", "b"])
    before = ride_of(plan.stops, m)
    assert reorder_visits(plan, places, m, window, [], [])
    assert ride_of(plan.stops, m) < before and plan.stops in (["a", "b", "c"], ["c", "b", "a"])

    # Thứ tự ngắn hơn làm tới x sau giờ đóng cửa -> giữ thứ tự cũ.
    places = {
        "x": _place("x", category="Bảo tàng", stay_low=30, stay_high=30, hours={2: (480, 630)}),
        "y": _place("y", category="Chùa", stay_low=60, stay_high=60),
    }
    m = {
        (STAY, "x"): 30.0,
        ("x", "y"): 30.0,
        ("y", STAY): 30.0,
        (STAY, "y"): 5.0,
        ("y", "x"): 5.0,
        ("x", STAY): 5.0,
    }
    plan = DayPlan(id="L2", day=2, leave_stay="09:00", stops=["x", "y"])
    assert not reorder_visits(plan, places, m, window, [], [])
    assert plan.stops == ["x", "y"]


def test_planner_sees_every_pair_of_its_day() -> None:
    from ailusion.itinerary.agents.planner import day_pairs

    codes = {f"D{i}": f"p{i}" for i in range(1, 31)}
    matrix = {
        (f"p{i}", f"p{j}"): float(i + j) for i in range(1, 31) for j in range(1, 31) if i != j
    }
    day = [f"D{i}" for i in range(1, 13)]
    pairs = day_pairs(day, codes, matrix)
    # Chỉ cặp trong ngày; mỗi chỗ có ít nhất PAIRS_PER_PLACE cặp gần nhất, không phải đủ 66 cặp.
    assert pairs[0] == "D1–D2 3" and len(pairs) < 12 * 11 // 2
    for c in day:
        assert sum(c in p.split()[0].split("–") for p in pairs) >= 4
    assert not any("D13" in p for p in pairs)


# ------------------------------------- chuỗi quán lặp, chợ đêm chờ mở cửa (lịch Đà Lạt thật)

TAO_NGO = (
    "Lẩu Gà Lá É TAO NGỘ - 70A 3 tháng 2",
    "Lẩu Gà Lá É Tao Ngộ 39 Đường 3/2",
    "Lẩu Gà Lá É TAO NGỘ - 02 Phạm hồng thái (Quán Nhi)",
)


def _dalat_meals() -> dict[str, Place]:
    meals = {
        f"tn{i}": _place(
            f"tn{i}",
            kind="meal",
            name=name,
            category="Nhà hàng lẩu",
            rating=4.6 - i * 0.1,
            reviews=3000,
            lat=11.94 + i * 0.002,
            lng=108.44,
            stay_low=60,
            stay_high=75,
        )
        for i, name in enumerate(TAO_NGO)
    }
    for pid, name, cat in (
        ("bc", "Bánh căn Nhà Chung", "Quán bánh căn"),
        ("bt", "Lẩu bò Ba Toa", "Nhà hàng lẩu"),
        ("nb", "Nem nướng Bà Hùng", "Quán nem nướng"),
        ("pc", "Phở Cường", "Quán phở"),
    ):
        meals[pid] = _place(
            pid,
            kind="meal",
            name=name,
            category=cat,
            rating=4.4,
            reviews=2000,
            lat=11.941,
            lng=108.441,
            stay_low=45,
            stay_high=60,
        )
    return meals


def test_branches_of_one_restaurant_chain_count_as_one_brand() -> None:
    from ailusion.itinerary.coordinator.score import one_per_brand
    from ailusion.itinerary.shared.models import brand_key, same_brand

    meals = _dalat_meals()
    keys = {brand_key(n) for n in TAO_NGO}
    assert keys == {("lau", "ga", "la", "e", "tao", "ngo")}
    assert same_brand(meals["tn0"], meals["tn2"]) and same_brand(meals["tn1"], meals["tn2"])
    assert not same_brand(meals["tn0"], meals["bt"])  # cùng món lẩu, khác quán
    assert brand_key("Phở 24") == ("pho", "24")  # số là một phần tên ngắn thì giữ
    kept = one_per_brand(list(meals.values()), (11.94, 108.44))
    assert [p.place_id for p in kept if p.place_id.startswith("tn")] == ["tn0"]  # sao cao nhất
    assert {"bc", "bt", "nb", "pc"} <= {p.place_id for p in kept}


class _MatrixCtx:
    async def tool(self, name: str, **_: Any) -> tuple[Any, dict[str, Any]]:
        return {"status": "error", "message": "hỏng"}, {"tool": name, "ok": False}

    async def log(self, *_: Any, **__: Any) -> None:
        return None


async def test_shortlist_keeps_one_branch_per_chain_for_the_whole_trip() -> None:
    from ailusion.itinerary.coordinator.score import chot_danh_sach
    from ailusion.itinerary.shared.models import dump_places, same_brand

    form = family_form(destination="Đà Lạt", end_date="2026-10-26", busy_slots=[])
    profile = profile_trip(form, [], [])
    visits = {
        k: _place(k, category=c, lat=11.94, lng=108.44 + i * 0.01, stay_low=60, stay_high=90)
        for i, (k, c) in enumerate((("h", "Hồ"), ("d", "Đồi chè"), ("th", "Thác"), ("v", "Vườn")))
    }
    places = visits | _dalat_meals()
    state = {
        "form": form.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
        "places": dump_places(places),
        "center": [11.94, 108.44],
        "stay": None,
    }
    out = await chot_danh_sach(state, _MatrixCtx())  # type: ignore[arg-type]
    meals = [places[pid] for pid in out["pool"] if places[pid].kind == "meal"]
    chain = [m.place_id for m in meals if m.place_id.startswith("tn")]
    assert chain == ["tn0"]  # 3 chi nhánh còn 1
    assert not any(
        same_brand(a, b) for i, a in enumerate(meals) for b in meals[i + 1 :]
    )  # cả chuyến không 2 quán cùng chuỗi, nên không ngày nào lặp chuỗi của ngày khác
    by_day = {d: [out["codes"][c] for c in cs] for d, cs in out["day_codes"].items()}
    chain_days = [d for d, pids in by_day.items() if "tn0" in pids]
    assert len(chain_days) <= 1 or len(meals) < MEALS_PER_DAY * len(profile.days)


def test_place_meals_never_eats_one_chain_twice_a_day() -> None:
    from ailusion.itinerary.coordinator.simulate import place_meals

    profile = profile_trip(family_form(destination="Đà Lạt", busy_slots=[]), [], [])
    places = _dalat_meals() | {
        "a": _place("a", category="Hồ", stay_low=120, stay_high=120),
        "b": _place("b", category="Khu du lịch", stay_low=330, stay_high=330),
        "c": _place("c", category="Chùa", stay_low=100, stay_high=100),
    }
    ids = list(places) + [STAY]
    m = {(x, y): 10.0 for x in ids for y in ids if x != y}
    window = profile.days[1]  # 08:00–21:00
    # Planner chọn 2 chi nhánh Tao Ngộ cho trưa và tối -> bỏ 1, bù quán khác chuỗi, khác món.
    plan = DayPlan(id="L1", day=2, leave_stay="08:00", stops=["a", "tn0", "b", "c", "tn1"])
    place_meals(plan, places, m, window, ["tn2", "bt", "bc"])
    meals = [s for s in plan.stops if places[s].kind == "meal"]
    # Ngày 08:00–21:00: đủ 3 bữa (sáng, trưa, tối), không 2 quán cùng chuỗi.
    assert sum(s.startswith("tn") for s in meals) == 1 and len(meals) == 3
    assert "bc" in meals  # "Lẩu bò Ba Toa" cũng là lẩu -> chọn bánh căn cho khác món
    assert not simulate(plan, places, m, window, [], long_stay=False).issues
    # Bù bữa còn thiếu: chi nhánh khác của chuỗi đã ăn không được chọn.
    plan = DayPlan(id="L2", day=2, leave_stay="09:00", stops=["a", "tn0", "b", "c"])
    place_meals(plan, places, m, window, ["tn2", "bt", "bc"])
    assert "tn2" not in plan.stops and "bc" in plan.stops


def test_night_market_goes_last_instead_of_waiting_hours() -> None:
    from ailusion.itinerary.coordinator.simulate import place_meals, reorder_visits
    from ailusion.itinerary.shared.models import opens_late

    profile = profile_trip(family_form(destination="Đà Lạt", busy_slots=[]), [], [])
    window = profile.days[1]  # 08:00–21:00
    places = {
        "cd": _place(
            "cd",
            name="Chợ Đêm Đà Lạt",
            category="Chợ đêm",
            stay_low=60,
            stay_high=90,
            hours={2: (17 * 60, 23 * 60)},
        ),
        "a": _place("a", name="Hồ Xuân Hương", category="Hồ", stay_low=120, stay_high=120),
        "c": _place("c", name="Thiền viện Trúc Lâm", category="Chùa", stay_low=90, stay_high=90),
        "b": _place(
            "b", name="Thung lũng Tình Yêu", category="Khu du lịch", stay_low=240, stay_high=240
        ),
        "q1": _place("q1", kind="meal", category="Quán phở", stay_low=45, stay_high=45),
        "q2": _place("q2", kind="meal", category="Nhà hàng lẩu", stay_low=45, stay_high=45),
    }
    assert opens_late(places["cd"], 2) and not opens_late(places["a"], 2)
    assert opens_late(_place("pub", name="Maze Bar", category="Quán bar"), 2)  # chưa rõ giờ
    ids = list(places) + [STAY]
    m = {(x, y): 10.0 for x in ids for y in ids if x != y}
    # Planner xếp chợ đêm giữa buổi sáng (như lịch thật: tới 13:48, chờ mở cửa 270 phút).
    plan = DayPlan(id="L1", day=2, leave_stay="08:00", stops=["a", "cd", "q1", "c", "b", "q2"])
    before = simulate(plan, places, m, window, [], long_stay=False)
    assert any("Chợ Đêm Đà Lạt" in i and "phải chờ" in i for i in before.issues)
    assert reorder_visits(plan, places, m, window, [], [])
    place_meals(plan, places, m, window, [])
    visits = [s for s in plan.stops if places[s].kind != "meal"]
    assert visits[-1] == "cd"
    run = simulate(plan, places, m, window, [], long_stay=False)
    market = next(s for s in run.stops if s.place_id == "cd")
    assert market.start >= 17 * 60 and market.wait <= 45
    # Chỉ có 2 quán, không quán dự phòng: thiếu đúng 1 bữa, ngoài ra không lỗi gì.
    assert len(run.issues) == 1 and run.issues[0].startswith("thiếu bữa")

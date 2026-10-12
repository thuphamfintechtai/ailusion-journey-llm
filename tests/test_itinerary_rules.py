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
from ailusion.itinerary.coordinator.score import capacity, pick_pool
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


def test_family_example_is_p2_with_free_minutes_600_525_270() -> None:
    profile = profile_trip(family_form(), stay_prices=[800_000], meal_prices=[100_000])
    assert profile.needs_care is True
    assert profile.nice_to_have_needs[:3] == ["kids", "high_chair", "baby_changing"]
    assert profile.night_return_time == "21:00"
    assert [d.free_minutes for d in profile.days] == [600, 525, 270]
    assert profile.days[1].meal_only_slots == ["16:45–21:00"]
    assert profile.days[2].day_end == "12:30"  # 15:00 − 90 − 30 − 30
    assert (profile.group, profile.rule) == ("P2", "B2")
    assert profile.sort_order[0] == ("ride_min", "min")
    assert "Ngày 1 bắt đầu 11:00" in profile.guess


def test_group_rules_in_order() -> None:
    # B0: user chọn ưu tiên thì theo user.
    assert profile_trip(family_form(priority="saving"), [], []).group == "P1"
    couple = {"group_type": "couple", "kids_under_6": 0, "seniors_65": 0, "busy_slots": []}
    # B3: ngân sách thấp hơn 1,5 × chi phí tối thiểu.
    low = profile_trip(family_form(**couple, budget=3_000_000), [800_000], [100_000])
    assert (low.group, low.rule) == ("P1", "B3")
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


def test_capacity_example_is_7_6_3() -> None:
    days = profile_trip(family_form(), [], []).days
    assert capacity(days, typical_stay=60, typical_hop=20) == [7, 6, 3]


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
    plan = DayPlan(id="L3", day=2, leave_stay="10:00", stops=["vui", "cho"])
    short = simulate(plan, places, matrix, profile.days[1], [], long_stay=False)
    assert not short.issues
    reason = check_plan(plan, places, matrix, profile.days[1], profile, family_form())
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
    assert "D9" in text and "sớm hơn" in text and "ít nhất 3" in text
    assert errs[2] == ["thiếu ngày 2"]


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
        "browse_places",
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
    # Tới 11:30 (đang nghỉ) -> chờ tới 13:30; tới 10:30 ở 60 phút -> chạm giờ nghỉ.
    late = DayPlan(id="L1", day=2, leave_stay="11:30", stops=["bt"])
    run = simulate(
        late, pool, {(STAY, "bt"): 0, ("bt", STAY): 0}, profile.days[1], [], long_stay=False
    )
    assert run.stops[0].start == 810 and not run.issues
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
    assert any("ngoài giờ ăn" in i for i in run.issues)
    lunch = DayPlan(id="L2", day=2, leave_stay="10:00", stops=["bt", "q"])
    run = simulate(lunch, places, m, profile.days[1], [], long_stay=False)
    assert not any("giờ ăn" in i for i in run.issues)
    hungry = DayPlan(id="L3", day=2, leave_stay="10:30", stops=["bt", "bt2"])
    places["bt2"] = _place("bt2", category="Chùa", stay_low=150, stay_high=150)
    m |= {("bt", "bt2"): 5, ("bt2", STAY): 5}
    run = simulate(hungry, places, m, profile.days[1], [], long_stay=False)
    assert any("không có quán ăn" in i for i in run.issues)


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
    # Planner xếp quán đầu tiên lúc 09:00 (ngoài giờ ăn) -> code chuyển quán vào giờ trưa.
    plan = DayPlan(id="L1", day=2, leave_stay="09:00", stops=["q", "bt", "chua"])
    assert place_meals(plan, places, m, profile.days[1], [])
    assert plan.stops[0] != "q" and "q" in plan.stops  # không bỏ quán, chỉ dời
    run = simulate(plan, places, m, profile.days[1], [], long_stay=False)
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

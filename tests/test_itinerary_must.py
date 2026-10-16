"""Chỗ user chỉ định (form `must_visits`): tìm đúng chỗ, giữ qua các bước, đúng ngày / giờ."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ailusion.itinerary.agents.intake import ExtractedMust, Extraction, apply_extracted
from ailusion.itinerary.agents.planner import PlanDraft, PlannerOutput, plan_errors
from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.coordinator.score import moved_closed_days, split_by_day
from ailusion.itinerary.coordinator.search import best_match, find_musts, name_match
from ailusion.itinerary.coordinator.simulate import check_plan, simulate
from ailusion.itinerary.shared.form import TripForm
from ailusion.itinerary.shared.models import STAY, DayPlan, Place


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Đà Nẵng",
        "start_date": "2026-10-25",
        "end_date": "2026-10-27",
        "group_size": 2,
        "group_type": "couple",
    }
    data.update(over)
    return TripForm.model_validate(data)


def _place(pid: str, kind: str = "visit", **kw: Any) -> Place:
    base = {"name": pid, "category": "Điểm du lịch", "lat": 16.0, "lng": 108.2, "reviews": 1000}
    base.update(kw)
    return Place(place_id=pid, kind=kind, **base)


def test_must_day_cannot_be_past_the_trip() -> None:
    with pytest.raises(ValueError, match="không có ngày 4"):
        _form(must_visits=[{"name": "Bà Nà Hills", "day": 4}])
    form = _form(must_visits=[{"name": "Bếp Hên", "day": 2, "time": "19:00"}])
    assert form.must_visits[0].time == "19:00"


def test_name_match_ignores_accents_and_extra_words() -> None:
    assert name_match("ba na hills", "Sun World Bà Nà Hills") == 1.0
    found = [
        _place("a", name="Chợ Hàn", reviews=20000),
        _place("b", name="Chợ đêm Sơn Trà", reviews=9000),
    ]
    assert best_match("chợ Hàn", found).place_id == "a"  # type: ignore[union-attr]
    assert best_match("Cầu Vàng", found) is None


class FakeCtx:
    """`many` trả kết quả theo tên tool; ghi lại các lượt gọi."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def many(self, calls: list[tuple[str, dict[str, Any]]]) -> tuple[list[Any], list[Any]]:
        self.calls += calls
        out = []
        for name, args in calls:
            rows = [r for r in self.answers.get(name, []) if r["_for"] in args["query"]]
            out.append({"status": "success", "places": rows})
        return out, [{} for _ in calls]


def _row(pid: str, name: str, category: str, for_: str) -> dict[str, Any]:
    return {
        "place_id": pid,
        "name": name,
        "category": category,
        "lat": 16,
        "lng": 108,
        "_for": for_,
    }


def test_musts_are_found_in_db_then_serpapi_and_reported_when_missing() -> None:
    form = _form(
        must_visits=[
            {"name": "Bà Nà Hills", "day": 2},
            {"name": "Bếp Hên", "day": 1, "time": "evening"},
            {"name": "Quán Không Có Thật"},
        ]
    )
    ctx = FakeCtx(
        {
            "find_places": [
                _row("bana", "Sun World Bà Nà Hills", "Công viên giải trí", "Bà Nà Hills")
            ],
            "search_places": [_row("bephen", "Bếp Hên", "Nhà hàng", "Bếp Hên")],
        }
    )
    places: dict[str, Place] = {}
    _logs, notes = asyncio.run(find_musts(form, places, ctx))  # type: ignore[arg-type]
    assert places["bana"].must and places["bana"].must_day == 2
    assert places["bephen"].kind == "meal" and places["bephen"].must_time == "evening"
    # SerpApi chỉ hỏi cho chỗ DB không có.
    assert [a["query"] for n, a in ctx.calls if n == "search_places"] == [
        "Bếp Hên Đà Nẵng",
        "Quán Không Có Thật Đà Nẵng",
    ]
    assert notes == ["Không tìm thấy “Quán Không Có Thật” ở Đà Nẵng, lộ trình chưa có chỗ này."]


def test_pinned_place_goes_to_its_day_and_unpinned_must_is_kept() -> None:
    pool = [
        _place("bana", reviews=70000, lat=16.0, lng=107.9),
        _place("bien", category="Bãi biển", reviews=50000, lat=16.06, lng=108.25),
        _place("chua", category="Chùa", reviews=40000, lat=16.1, lng=108.27),
        _place("nho", category="Bảo tàng", reviews=10, lat=15.9, lng=108.3, must=True),
        _place("pin", category="Chợ", reviews=20, lat=16.07, lng=108.22, must=True, must_day=3),
        _place("q1", kind="meal", category="Nhà hàng", must=True, must_day=1),
        _place("q2", kind="meal", category="Nhà hàng"),
    ]
    by_day = split_by_day(pool, [1, 2, 3], {})
    assert by_day[3][0] == "pin"  # ghim ngày 3 và làm mỏ neo của ngày đó
    assert sum("nho" in pids for pids in by_day.values()) == 1  # ít review vẫn được xếp
    assert "q1" in by_day[1] and all("q1" not in by_day[d] for d in (2, 3))


def test_pinned_day_that_is_closed_moves_to_the_nearest_open_day() -> None:
    p = _place(
        "bt",
        must=True,
        must_day=2,
        must_name="bảo tàng Chăm",
        hours={1: (480, 1020), 2: None, 3: (480, 1020)},
    )
    notes = moved_closed_days([p], [1, 2, 3], _form())
    assert p.must_day == 1
    assert "đóng cửa ngày 2 (26/10), đã chuyển sang ngày 1" in notes[0]


def test_every_plan_of_the_day_must_include_the_chosen_place() -> None:
    out = PlannerOutput(
        days=[
            {
                "day": 1,
                "plans": [
                    PlanDraft(id="L1", leave_stay="09:00", stops=["D1", "Q1"]),
                    PlanDraft(id="L2", leave_stay="09:00", stops=["D2", "Q1"]),
                    PlanDraft(id="L3", leave_stay="09:00", stops=["D1", "D2"]),
                ],
            }
        ]
    )
    errors = plan_errors(out, {"D1", "D2", "Q1"}, {1: "08:00"}, required={1: ["D1"]})
    assert errors[1] == ["L2: thiếu chỗ BẮT BUỘC D1"]


def test_chosen_time_waits_when_early_and_flags_when_late() -> None:
    form = _form(must_visits=[{"name": "x"}])
    window = profile_trip(form, [], []).days[1]
    places = {
        "a": _place("a", stay_low=60, stay_high=60, hours={2: (480, 1320)}),
        "cho": _place(
            "cho", stay_low=60, stay_high=90, hours={2: (480, 1320)}, must=True, must_time="11:00"
        ),
    }
    matrix = {(STAY, "a"): 10, ("a", "cho"): 10, ("cho", STAY): 10, (STAY, "cho"): 10}
    early = DayPlan(id="L1", day=2, leave_stay="09:30", stops=["a", "cho"])
    run = simulate(early, places, matrix, window, [], long_stay=False)
    assert run.stops[1].start == 11 * 60 and run.stops[1].wait == 10  # tới 10:50, chờ tới giờ hẹn
    assert not [i for i in run.issues if "cho" in i]
    late = DayPlan(id="L2", day=2, leave_stay="10:30", stops=["a", "cho"])
    run = simulate(late, places, matrix, window, [], long_stay=False)
    assert any("trễ so với giờ bạn muốn (lúc 11:00)" in i for i in run.issues)


def test_plan_without_the_chosen_place_fails() -> None:
    form = _form()
    profile = profile_trip(form, [], [])
    places = {"a": _place("a", stay_low=60, stay_high=60), "cho": _place("cho", must=True)}
    plan = DayPlan(id="L1", day=1, leave_stay="12:00", stops=["a"])
    reason = check_plan(plan, places, {}, profile.days[0], profile, form, ["cho"])
    assert plan.status == "fail" and reason and "thiếu chỗ bạn chọn: cho" in reason


def test_intake_reads_named_places_with_day_and_time() -> None:
    message = "nhất định phải lên Bà Nà Hills, tối ngày 2 ăn ở Bếp Hên lúc 7 giờ tối"
    form = _form(message=message)
    extraction = Extraction(
        must_visits=[
            ExtractedMust(name="Bà Nà Hills", quote="nhất định phải lên Bà Nà Hills"),
            ExtractedMust(
                name="Bếp Hên",
                day=2,
                time="7 giờ tối",
                quote="tối ngày 2 ăn ở Bếp Hên lúc 7 giờ tối",
            ),
            ExtractedMust(name="Cầu Rồng", quote="đi Cầu Rồng"),  # không có trong lời nhắn
        ]
    )
    new, kept = apply_extracted(form, extraction, sent=set())
    assert [(m.name, m.day, m.time) for m in new.must_visits] == [
        ("Bà Nà Hills", None, None),
        ("Bếp Hên", 2, "19:00"),
    ]
    assert [k["field"] for k in kept] == ["must_visits", "must_visits"]
    # User đã điền ô này trên form thì không đụng.
    same, _ = apply_extracted(form, extraction, sent={"must_visits"})
    assert same.must_visits == []


def test_same_name_prefers_the_reachable_place_near_the_city() -> None:
    peak = _place("peak", name="Ba Na Hills", reviews=99108, lat=15.995, lng=107.996)
    base = _place("base", name="Sun World Bà Nà Hills", reviews=71779, lat=16.0266, lng=108.0328)
    center = (16.0544, 108.2022)
    assert best_match("Bà Nà Hills", [peak, base], center).place_id == "base"  # type: ignore[union-attr]
    tiny = _place("tiny", name="Bà Nà Hills tour", reviews=50, lat=16.05, lng=108.2)
    assert best_match("Bà Nà Hills", [peak, tiny], center).place_id == "peak"  # type: ignore[union-attr]


def test_evening_return_is_asked_and_defaults_to_20h() -> None:
    from ailusion.itinerary.coordinator.profile import QUESTIONS

    form = _form(must_visits=[{"name": "Cầu Rồng", "day": 1, "time": "21:00"}])
    profile = profile_trip(form, [], [])
    # Không nhập giờ về tối: bước 4 hỏi (H6); chưa trả lời thì tạm về lúc 20:00.
    assert "H6" in profile.questions
    assert profile.days[1].day_end == "20:45"  # đi chơi tới 20:00 + 45 phút đường về
    # Ngày 1 hẹn Cầu Rồng lúc 21:00: kéo tới sau giờ hẹn (21:00 + 90 phút), không thành "về trễ".
    assert profile.days[0].day_end == "22:30"
    unsure = dict(QUESTIONS["H6"]["choices"])["Chưa biết (để 20h)"]
    assert unsure == {"night_return_time": "20:45"}
    late = profile_trip(_form(night_out=True), [], [])
    assert "H6" not in late.questions and late.days[0].day_end == "23:30"
    own = _form(night_return_time="21:00", must_visits=[{"name": "x", "day": 1, "time": "21:00"}])
    assert profile_trip(own, [], []).days[0].day_end == "21:00"  # user tự đặt giờ về: giữ


def test_free_time_before_a_fixed_time_is_not_an_error_and_dinner_waits() -> None:
    form = _form(must_visits=[{"name": "Cầu Rồng", "day": 1, "time": "21:00"}])
    # Ngày bắt đầu 15:00: chỉ cần bữa tối (đã có quán q).
    window = profile_trip(form, [], []).days[0].model_copy(update={"day_start": "15:00"})
    places = {
        "a": _place("a", stay_low=60, stay_high=60, hours={1: (480, 1320)}),
        "q": _place("q", kind="meal", category="Nhà hàng", stay_low=60, stay_high=60),
        "cau": _place("cau", stay_low=30, stay_high=30, must=True, must_time="21:00"),
    }
    matrix = {(STAY, "a"): 10, ("a", "q"): 10, ("q", "cau"): 10, ("cau", STAY): 10}
    plan = DayPlan(id="L1", day=1, leave_stay="15:00", stops=["a", "q", "cau"])
    run = simulate(plan, places, matrix, window, [], long_stay=False)
    assert run.stops[1].start == 18 * 60  # tới quán 16:20, chờ tới giờ ăn tối
    assert run.stops[2].start == 21 * 60 and run.stops[2].wait == 110  # tự do trước giờ hẹn
    assert run.issues == []


def test_vietmap_is_tried_before_serpapi_for_named_places() -> None:
    form = _form(must_visits=[{"name": "Bếp Hên", "day": 1}])
    vietmap_row = {
        "ref_id": "vm:1",
        "name": "Bếp Hên",
        "categories": ["restaurant"],
        "lat": 16.07,
        "lng": 108.22,
        "_for": "Bếp Hên",
    }
    ctx = FakeCtx(
        {
            "find_nearby": [vietmap_row],
            "search_places": [_row("x", "Bếp Hên", "Nhà hàng", "Bếp Hên")],
        }
    )
    places: dict[str, Place] = {}
    asyncio.run(find_musts(form, places, ctx, (16.05, 108.2)))  # type: ignore[arg-type]
    assert places["vm:1"].must and places["vm:1"].kind == "meal"
    assert [n for n, _ in ctx.calls] == ["find_places", "find_nearby"]  # không gọi SerpApi
    near = ctx.calls[1][1]
    assert near["lat"] == 16.05 and near["radius_m"] == 50_000


def test_every_group_choice_is_accepted_without_asking_again() -> None:
    from ailusion.itinerary.coordinator.profile import QUESTIONS

    base = {"night_return_time": "21:45"}
    # Ô "Đi cùng ai" trên form (5 lựa chọn): chọn rồi thì không hỏi lại H1, kể cả "Gia đình" để
    # trống số trẻ em / người lớn tuổi.
    for group in ("solo", "couple", "friends", "colleagues", "family"):
        p = profile_trip(_form(group_type=group, **base), [], [])
        assert "H1" not in p.questions, group
    # Câu hỏi lại H1: mọi lựa chọn đều trả lời xong câu hỏi, có lựa chọn gia đình toàn người lớn.
    choices = dict(QUESTIONS["H1"]["choices"])
    assert choices["Gia đình (người lớn)"] == {"group_type": "family"}
    for label, answer in choices.items():
        p = profile_trip(_form(**{"group_type": None, **base, **answer}), [], [])
        assert "H1" not in p.questions, label
    assert "H1" in profile_trip(_form(group_type=None, **base), [], []).questions


def test_fixed_time_later_does_not_push_breakfast_to_dinner() -> None:
    """Chỗ hẹn 16:30 cuối ngày: quán sáng 08:05 ăn luôn (từng bị chờ tới 18:00, 595 phút), quán
    trưa chờ tới mốc trưa, quán tối sau chỗ hẹn chờ tới 18:00."""
    form = _form()
    window = profile_trip(form, [], []).days[1]  # ngày 2: 08:00–20:45
    places = {
        "b": _place("b", kind="meal", category="Nhà hàng", stay_low=45, stay_high=45),
        "a": _place("a", stay_low=60, stay_high=60),
        "l": _place("l", kind="meal", category="Nhà hàng", stay_low=45, stay_high=45),
        "cho": _place("cho", stay_low=45, stay_high=45, must=True, must_time="16:30"),
        "d": _place("d", kind="meal", category="Nhà hàng", stay_low=45, stay_high=45),
    }
    ids = [*places, STAY]
    matrix = {(p, q): 5.0 for p in ids for q in ids if p != q}
    plan = DayPlan(id="L1", day=2, leave_stay="08:00", stops=["b", "a", "l", "cho", "d"])
    run = simulate(plan, places, matrix, window, [], long_stay=False)
    start = {s.place_id: s.start for s in run.stops}
    assert start["b"] == 8 * 60 + 5  # ăn sáng luôn
    assert start["l"] == 11 * 60 + 30  # chờ tới mốc trưa, không nhảy sang mốc tối
    assert start["cho"] == 16 * 60 + 30 and start["d"] == 18 * 60
    assert not [i for i in run.issues if "bữa" in i or "giờ ăn" in i]


def test_code_adds_the_chosen_place_the_planner_left_out() -> None:
    from ailusion.itinerary.coordinator.simulate import add_missing_musts

    form = _form()
    window = profile_trip(form, [], []).days[1]
    places = {
        "a": _place("a", stay_low=60, stay_high=60),
        "cho": _place("cho", stay_low=60, stay_high=60, must=True, must_day=2),
        "q": _place("q", kind="meal", category="Nhà hàng", stay_low=45, stay_high=45),
    }
    ids = [*places, STAY]
    matrix = {(p, q): 10.0 for p in ids for q in ids if p != q}
    plan = DayPlan(id="L1", day=2, leave_stay="08:00", stops=["a", "q"])
    assert add_missing_musts(plan, places, matrix, window, ["cho"], [], []) == 1
    assert "cho" in plan.stops and {"a", "q"} <= set(plan.stops)
    assert add_missing_musts(plan, places, matrix, window, ["cho"], [], []) == 0  # đã có: thôi


def test_city_name_in_the_chosen_place_does_not_count_toward_a_match() -> None:
    """ "Đại Nội Huế" (chuyến Huế) không khớp quán "NGƯ PHỐ - Chả Cá Hà Nội tại Huế" chỉ vì có chữ
    "Nội" và "Huế"; vẫn khớp "Đại Nội Kinh Thành Huế"."""
    shop = _place("shop", name="NGƯ PHỐ - Chả Cá Hà Nội tại Huế", category="Nhà hàng")
    citadel = _place("citadel", name="Đại Nội Kinh Thành Huế", category="4004, 4004-2")
    assert best_match("Đại Nội Huế", [shop], place="Huế") is None
    assert best_match("Đại Nội Huế", [shop, citadel], place="Huế").place_id == "citadel"  # type: ignore[union-attr]
    assert name_match("Huế", "Kinh Thành Huế", place="Huế") == 1.0  # chỉ có tên điểm đến: giữ

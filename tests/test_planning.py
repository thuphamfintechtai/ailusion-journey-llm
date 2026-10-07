"""Pipeline POST /plan: form, planner, đọc kết quả tool, xếp lịch — dữ liệu giả, không gọi mạng."""

from __future__ import annotations

import pytest
from langchain_core.tools import tool
from pydantic import ValidationError

from ailusion.planning import PlanRequest, build_plan_graph
from ailusion.planning import candidates as cand
from ailusion.planning.assemble import pool_from_dict, pool_to_dict
from ailusion.planning.planner import collect_steps, hours_steps
from ailusion.planning.request import assumptions
from ailusion.planning.schedule import (
    PROFILES,
    build_option,
    cost_of,
    pick_hotel,
    simulate,
    validate,
)

CENTER = (11.94, 108.44)


def form(**overrides) -> PlanRequest:
    data = {
        "basic": {"destination": "Đà Lạt", "start_date": "2026-10-17", "days": 2, "adults": 2},
        "budget": {"total_vnd": 10_000_000},
    }
    data.update(overrides)
    return PlanRequest.model_validate(data)


def place(pid: str, name: str, category: str, dlat: float, dlng: float, **kw) -> dict:
    return {
        "place_id": pid,
        "name": name,
        "category": category,
        "rating": kw.get("rating", 4.5),
        "reviews": kw.get("reviews", 2000),
        "lat": CENTER[0] + dlat,
        "lng": CENTER[1] + dlng,
    }


@pytest.fixture
def pool() -> cand.Pool:
    p = cand.Pool(center=CENTER)
    cand.add_places(
        p,
        "places:popular",
        {
            "status": "success",
            "places": [
                place("a", "Hồ Xuân Hương", "Hồ", 0.00, 0.01),
                place("b", "Bảo tàng Lâm Đồng", "Bảo tàng", 0.01, 0.00),
                place("c", "Thác Datanla", "Điểm thu hút khách du lịch", -0.04, 0.01),
                place("d", "Chùa Linh Phước", "Chùa phật giáo", 0.02, 0.03),
                place("e", "Vườn hoa thành phố", "Vườn", 0.01, 0.01),
                place("f", "Dinh I Bảo Đại", "Lâu đài", -0.01, -0.01),
                place("g", "Ga Đà Lạt", "Ga", 0.00, 0.02),
                place("h", "Quảng trường Lâm Viên", "Quảng trường", -0.005, 0.005),
            ],
        },
    )
    cand.add_places(
        p,
        "food",
        {
            "status": "success",
            "places": [
                place("f1", "Lẩu gà lá é Tao Ngộ chính gốc", "Nhà hàng", 0.0, 0.0),
                place("f2", "Lẩu gà lá é Tao Ngộ chi nhánh 2", "Nhà hàng", 0.001, 0.0),
                place("f3", "Nem nướng Bà Hùng", "Nhà hàng", 0.005, 0.005),
                place("f4", "Quán Ốc Đào", "Nhà hàng", 0.002, 0.002),
                place("f5", "Bánh căn Nhà Chung", "Quán ăn nhỏ", -0.003, 0.004),
            ],
        },
    )
    cand.add_hotels(
        p,
        {
            "status": "success",
            "hotels": [
                {
                    "name": "Căn hộ Vũng Tàu",
                    "price_per_night_vnd": 300_000,
                    "rating": 4.5,
                    "gps": {"latitude": 10.35, "longitude": 107.08},
                },
                {
                    "name": "Khách sạn Trung Tâm",
                    "price_per_night_vnd": 700_000,
                    "rating": 4.4,
                    "gps": {"latitude": 11.941, "longitude": 108.441},
                },
                {
                    "name": "Resort Đồi Thông",
                    "price_per_night_vnd": 2_000_000,
                    "rating": 4.8,
                    "gps": {"latitude": 11.93, "longitude": 108.45},
                },
            ],
        },
    )
    p.weather = [
        {
            "date": "2026-10-17",
            "hours": [
                {"time": "06:00", "chance_of_rain_pct": 10},
                {"time": "15:00", "chance_of_rain_pct": 80},
            ],
        }
    ]
    return p


# ---------------------------------------------------------------- form
def test_form_defaults_and_assumptions():
    req = PlanRequest()
    assert req.basic.days == 3 and req.transport == "taxi"
    notes = assumptions(form())
    assert not any(n.startswith("Cơ bản") for n in notes)
    assert any(n.startswith("Nhịp độ") for n in notes)


@pytest.mark.parametrize(
    "bad",
    [
        {"basic": {"days": 6}},
        {"time": {"day_start": "8h"}},
        {"time": {"day_start": "10:00", "day_end": "12:00"}},
        {"basic": {"days": 2}, "time": {"fixed": [{"day": 3, "start": "18:00", "end": "20:00"}]}},
        {"budget": {"intercity": True}},
    ],
)
def test_form_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        PlanRequest.model_validate(bad)


# ---------------------------------------------------------------- planner
def test_planner_rules_follow_the_form():
    req = form(
        access={"veg": True, "wheelchair": True},
        interests={"tags": {"vanhoa": 2, "cafe": 1}},
        budget={"total_vnd": 10_000_000, "intercity": True, "origin": "Hà Nội"},
    )
    steps = collect_steps(req, "Tỉnh Lâm Đồng")
    keys = [s["key"] for s in steps]
    assert {"weather", "festivals", "hotels", "food:veg", "intercity:flight"} <= set(keys)
    assert all(s["args"].get("wheelchair") for s in steps if s["key"].startswith("places"))
    flight = next(s for s in steps if s["key"] == "intercity:flight")
    assert (flight["args"]["departure_id"], flight["args"]["arrival_id"]) == ("HAN", "DLI")
    assert len(steps) <= 15


def test_planner_skips_hotel_when_already_have_stay():
    req = form(budget={"total_vnd": 5_000_000, "need_stay": False})
    assert "hotels" not in [s["key"] for s in collect_steps(req, None)]


def test_hours_steps_one_call_per_day():
    steps = hours_steps(form(), ["a", "b"])
    assert [s["args"]["at"] for s in steps] == ["2026-10-17 10:00", "2026-10-18 10:00"]


# ---------------------------------------------------------------- đọc kết quả tool
def test_classify_and_seafood():
    assert cand.classify("Bảo tàng", "Bảo tàng Lâm Đồng")[1] is True  # trong nhà
    assert cand.classify("Nhà hàng", "Lẩu bò")[4] == "food"
    assert not cand.is_seafood("Lẩu gà lá é QUÁN GỐC")
    assert cand.is_seafood("Quán Ốc Đào")


def test_hours_parsing_and_closed_day(pool):
    cand.add_hours(
        pool,
        0,
        {
            "status": "success",
            "results": [
                {"place_id": "a", "status": "open", "hours_that_day": "07:30–16:30"},
                {"place_id": "b", "status": "closed"},
            ],
        },
    )
    assert pool.places["a"].hours[0] == (450, 990)
    assert pool.places["b"].hours[0] is None


def test_hard_filters_remove_steep_and_seafood(pool):
    req = form(access={"no_stairs": True, "no_seafood": True})
    cand.apply_hard_filters(pool, req)
    names = {n for n, _ in pool.excluded}
    assert {"Thác Datanla", "Quán Ốc Đào"} <= names
    assert "f1" in pool.places  # "chính gốc" không phải ốc


def test_pool_roundtrip(pool):
    pool.places["a"].hours[1] = (480, 1020)
    again = pool_from_dict(pool_to_dict(pool))
    assert again.places["a"].hours[1] == (480, 1020)
    assert again.places["a"].tags == pool.places["a"].tags


# ---------------------------------------------------------------- xếp lịch
def test_hotel_far_away_is_ignored(pool):
    hotel = pick_hotel(pool.hotels, 0.0, form(), CENTER)
    assert hotel.name == "Khách sạn Trung Tâm"


def test_schedule_respects_window_lunch_and_order(pool):
    req = form()
    for key in PROFILES:
        plan = build_option(key, pool, req)
        for day in plan.days:
            start, end = day.window
            slots = day.slots
            assert all(start <= s.start < s.end <= end for s in slots)
            assert all(a.end <= b.start for a, b in zip(slots, slots[1:], strict=False))
            assert any(s.type == "meal" and s.name.startswith("Ăn trưa") for s in slots)
            assert any(s.type == "poi" for s in slots)


def test_no_repeated_food_chain_in_a_day(pool):
    plan = build_option("canbang", pool, form())
    for day in plan.days:
        meals = [s.place.name for s in day.slots if s.type == "meal" and s.place]
        assert sum("Tao Ngộ" in m for m in meals) <= 1


def test_siesta_and_fixed_event(pool):
    req = form(
        access={"siesta": True},
        time={"fixed": [{"day": 1, "start": "15:00", "end": "16:00", "label": "Họp"}]},
    )
    day = build_option("canbang", pool, req).days[0]
    assert any(s.type == "siesta" and s.end - s.start == 90 for s in day.slots)
    fixed = next(s for s in day.slots if s.type == "fixed")
    assert (fixed.start, fixed.end) == (900, 960)


def test_hard_budget_is_validated(pool):
    req = form(budget={"total_vnd": 1_000_000})
    plan = build_option("trainghiem", pool, req)
    plan.cost = cost_of(plan, pool, req)
    validate(plan, req)
    assert not plan.hard_ok
    assert any("ngân sách cứng" in i for i in plan.issues)


def test_risk_simulation_is_deterministic(pool):
    req = form()
    plan = build_option("canbang", pool, req)
    assert simulate(plan, req, seed=7, n=200) == simulate(plan, req, seed=7, n=200)


def test_graph_nodes_match_pipeline_diagram():
    names = {"geocode_vn", "vn_calendar", "check_opening_hours", "score_options", "plan_day_route"}

    def make(name):
        @tool(name)
        def _t(x: str = "") -> str:
            """fake"""
            return x

        return _t

    graph = build_plan_graph([make(n) for n in names]).compile()
    assert set(graph.get_graph().nodes) - {"__start__", "__end__"} == {
        "chuan_hoa",
        "chien_luoc",
        "planner",
        "dieu_phoi",
        "goi_tool",
        "gom_ket_qua",
        "dong_bang",
        "xep_lich",
        "kiem_tra",
        "rui_ro_chon",
        "giai_thich",
    }

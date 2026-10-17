"""Mức chi của điểm đến (coordinator/levels.py): mỗi mức chi user chọn so ngân sách với một trung vị
riêng của mặt bằng giá cả điểm đến, mỗi mức một lời báo riêng.

Kèm các kịch bản ngân sách: chỗ đắt / rẻ, ngân sách thấp / cao, đơn vị ngân sách, đông người, khoảng
ngân sách, vé đã mua, đi trong ngày."""

from __future__ import annotations

from typing import Any

import pytest

from ailusion.itinerary.coordinator.levels import (
    budget_level,
    budget_text,
    level_costs,
    price_levels,
    up,
)
from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.shared.form import EXTRA_MID, TripForm, money, required_meals

# Mặt bằng giá trong DB (10/2026): giá phòng / đêm, giá quán / người — p25 / trung vị / p75.
PQ = {  # Phú Quốc: chỗ đắt
    "stay": {"p25": 801_451, "median": 1_401_065, "p75": 3_248_604, "count": 150},
    "meal": {"p25": 150_000, "median": 250_000, "p75": 350_000, "count": 56},
}
HUE = {  # Huế: chỗ rẻ
    "stay": {"p25": 408_689, "median": 532_767, "p75": 1_000_589, "count": 134},
    "meal": {"p25": 150_000, "median": 150_000, "p75": 250_000, "count": 38},
}
DN = {  # Đà Nẵng
    "stay": {"p25": 513_369, "median": 811_094, "p75": 1_577_504, "count": 283},
    "meal": {"p25": 150_000, "median": 250_000, "p75": 350_000, "count": 44},
}


def _form(**over: Any) -> TripForm:
    data = {
        "destination": "Phú Quốc",
        "start_date": "2026-10-24",
        "end_date": "2026-10-27",
        "group_size": 2,
        "group_type": "couple",
        "budget": 5_000_000,
        "budget_unit": "group_trip",
        "night_return_time": "20:45",
    }
    data.update(over)
    return TripForm.model_validate(data)


def _level(levels: dict[str, Any] = PQ, **over: Any) -> dict[str, Any] | None:
    form = _form(**over)
    return budget_level(form, profile_trip(form, levels))


def _severity(levels: dict[str, Any] = PQ, **over: Any) -> str | None:
    level = _level(levels, **over)
    return level["severity"] if level else None


def test_levels_take_quartiles_per_night_and_per_room_plus_extras() -> None:
    form = _form()
    profile = profile_trip(form, PQ)
    meals = sum(len(required_meals(d.day_start, d.day_end)) for d in profile.days)
    rows = {r["key"]: r for r in level_costs(form, profile)}
    assert [r["label"] for r in rows.values()] == ["Tiết kiệm", "Thường gặp", "Sang"]
    assert rows["saving"]["basis"] == "trung vị nửa rẻ"
    assert rows["luxury"]["basis"] == "trung vị nửa đắt"
    # 4 ngày 3 đêm, 2 người 1 phòng: phòng tính theo số ĐÊM, không ÷ 2 người; cộng phát sinh như
    # tổng ước của lịch.
    core = 1_401_065 * 3 + 250_000 * meals * 2
    assert rows["normal"]["total"] == round(core * (1 + EXTRA_MID))
    # Đã đặt chỗ ở: chỉ còn tiền ăn.
    booked = _form(stay_booked="Nhà bạn")
    assert level_costs(booked, profile_trip(booked, PQ))[0]["total"] == round(
        150_000 * meals * 2 * (1 + EXTRA_MID)
    )


def test_each_spend_style_compares_with_its_own_median() -> None:
    # Bình thường: 5 triệu dưới cả mức tiết kiệm (trung vị nửa rẻ) -> cảnh báo.
    low = _level()
    assert low and low["severity"] == "warn" and low["target"] == "normal"
    assert low["title"] == "Ngân sách thấp so với Phú Quốc" and "mức thường gặp" in low["text"]
    # Đủ tiết kiệm, chưa tới trung vị: báo nhẹ.
    mid = _level(budget=8_000_000)
    assert mid and mid["severity"] == "info" and "dưới mức thường gặp" in mid["title"]
    assert _level(budget=12_000_000) is None  # đủ mức thường gặp: không báo
    # Sang, không vượt ngân sách: so với trung vị nửa đắt.
    lux = _level(budget=12_000_000, spend_style="luxury_within_budget")
    assert lux and lux["severity"] == "info" and lux["target"] == "luxury"
    assert "chưa tới mức sang" in lux["title"] and "không vượt ngân sách" in lux["text"]
    assert _level(budget=20_000_000, spend_style="luxury_within_budget") is None
    assert "mức sang" in _level(spend_style="luxury_within_budget")["text"]  # type: ignore[index]


def test_unlimited_reports_the_estimate_not_the_budget() -> None:
    form = _form(spend_style="unlimited")
    profile = profile_trip(form, PQ)
    assert budget_level(form, profile) is None  # chưa có tổng ước (bước 3): không báo
    level = budget_level(form, profile, 9_000_000)
    assert level and level["severity"] == "info" and level["target"] is None
    assert "9.000.000 đ" in level["text"] and "Cao hơn ngân sách 5.000.000 đ" in level["text"]
    assert len(level["levels"]) == 3


def test_no_price_stats_no_level() -> None:
    # MCP cũ chưa trả mặt bằng giá quán: không đoán từ vài quán phổ biến nhất.
    assert price_levels({"price_stats_vnd": PQ["stay"]}, {}) is None
    assert price_levels({"price_stats_vnd": PQ["stay"]}, {"price_stats_vnd": PQ["meal"]}) == PQ
    form = _form()
    assert budget_level(form, profile_trip(form)) is None


# ---------------------------------------------------------------- kịch bản ngân sách
TRIP_3D = {"end_date": "2026-10-26"}  # 3 ngày 2 đêm


@pytest.mark.parametrize(
    ("levels", "over", "expected"),
    [
        # Chỗ đắt: ngân sách thấp / vừa / cao.
        (PQ, {"budget": 4_000_000}, "warn"),  # phòng + ăn tiết kiệm 3,9tr + phát sinh > 4tr
        (PQ, {"budget": 6_000_000}, "info"),
        (PQ, {"budget": 20_000_000}, None),
        # Chỗ rẻ: ngân sách thấp / vừa / rất cao (mức chi nào cũng không báo thiếu).
        (HUE, {"budget": 2_000_000}, "warn"),
        (HUE, {"budget": 5_000_000}, None),
        (HUE, {"budget": 30_000_000}, None),
        (HUE, {"budget": 30_000_000, "spend_style": "luxury_within_budget"}, None),
        # Đông người, ngân sách không cao: 8 người = 4 phòng, 8 suất ăn mỗi bữa.
        (DN, {"budget": 10_000_000, "group_size": 8, "group_type": "friends"}, "warn"),
        (DN, {"budget": 20_000_000, "group_size": 8, "group_type": "friends"}, "info"),
    ],
)
def test_budget_scenarios(levels: dict[str, Any], over: dict[str, Any], expected: str) -> None:
    assert _severity(levels, **TRIP_3D, **over) == expected


def test_budget_units_mean_the_same_trip_budget() -> None:
    # 2 người 3 ngày: 6tr cả đoàn = 3tr mỗi người cả chuyến = 1tr mỗi người mỗi ngày.
    same = [
        {"budget": 6_000_000},
        {"budget": 3_000_000, "budget_unit": "person_trip"},
        {"budget": 1_000_000, "budget_unit": "person_day"},
    ]
    forms = [_form(destination="Đà Nẵng", **TRIP_3D, **o) for o in same]
    assert {profile_trip(f, DN).trip_budget for f in forms} == {6_000_000}
    # Cùng lời báo, chỉ khác dòng ghi phép quy đổi (`budget_text`).
    same_level = [budget_level(f, profile_trip(f, DN)) or {} for f in forms]
    assert len({str({k: v for k, v in x.items() if k != "budget_text"}) for x in same_level}) == 1
    # Đông người thì "mỗi người" nhân lên: 8 người × 1tr × 3 ngày = 24tr.
    many = _form(group_size=8, budget=1_000_000, budget_unit="person_day", **TRIP_3D)
    assert profile_trip(many, DN).trip_budget == 24_000_000


@pytest.mark.parametrize(
    ("over", "trip_budget"),
    [
        ({"budget": 3_000_000, "budget_min": 0}, 3_000_000),  # "Dưới 3 triệu"
        ({"budget": 6_000_000, "budget_min": 3_000_000}, 4_500_000),  # "3–6 triệu": giữa khoảng
        ({"budget": None, "budget_min": 10_000_000}, 10_000_000),  # "Trên 10 triệu": mức sàn
        ({"budget": 10_000_000, "prepaid_tickets": 1_500_000}, 7_000_000),  # trừ vé đã mua
    ],
)
def test_budget_ranges_and_prepaid_tickets(over: dict[str, Any], trip_budget: int) -> None:
    form = _form(**TRIP_3D, **over)
    assert profile_trip(form, PQ).trip_budget == trip_budget


def test_day_trip_has_no_room_cost() -> None:
    form = _form(destination="Đà Nẵng", end_date="2026-10-24", budget=600_000)
    profile = profile_trip(form, DN)
    meals = sum(len(required_meals(d.day_start, d.day_end)) for d in profile.days)
    # 0 đêm: chỉ tiền ăn trong ngày.
    assert [r["total"] for r in level_costs(form, profile)] == [
        round(DN["meal"][q] * meals * 2 * (1 + EXTRA_MID)) for q in ("p25", "median", "p75")
    ]


def test_shown_amount_is_above_the_budget_when_warning() -> None:
    # Ngân sách sát dưới mức tiết kiệm: số hiện ra (làm tròn lên) phải lớn hơn ngân sách, không
    # thành "khoảng 8.000.000 đ, hơn ngân sách 8.000.000 đ".
    form = _form(**TRIP_3D)
    saving = level_costs(form, profile_trip(form, PQ))[0]["total"]
    budget = saving - 1_000
    level = _level(**TRIP_3D, budget=budget)
    assert level and level["severity"] == "warn"
    assert up(saving) > budget and f"khoảng {money(up(saving))}, hơn" in level["text"]
    assert level["levels"][0]["total"] == up(saving)


# ---------------------------------------------------------------- ngân sách dư, phép quy đổi
def test_surplus_when_the_plan_cannot_use_a_high_budget() -> None:
    # Chỗ rẻ, ngân sách rất cao: Huế 30 triệu, lịch chỉ ước 12 triệu.
    form = _form(destination="Huế", budget=30_000_000, **TRIP_3D)
    profile = profile_trip(form, HUE)
    level = budget_level(form, profile, 12_000_000)
    assert level and level["kind"] == "surplus" and level["severity"] == "info"
    assert level["title"] == "Ngân sách dư so với Huế" and level["target"] == "luxury"
    assert "thấp hơn ngân sách 30.000.000 đ khoảng 60%" in level["text"]
    assert level["log"] is None  # chỉ biết sau khi lập lịch
    assert budget_level(form, profile) is None  # bước 3: chưa có tổng ước
    assert budget_level(form, profile, 29_000_000) is None  # lịch dùng gần hết ngân sách
    # Ngân sách chưa tới mức sang: lịch tiêu ít là do lịch, không phải điểm đến rẻ -> không báo dư.
    top = level_costs(form, profile)[2]["total"]
    near = _form(destination="Huế", budget=top - 100_000, **TRIP_3D)
    assert budget_level(near, profile_trip(near, HUE), 1_000_000) is None
    # "Sang, không vượt ngân sách" cũng báo; "không giới hạn" thì không (không so ngân sách).
    lux = _form(destination="Huế", budget=30_000_000, spend_style="luxury_within_budget", **TRIP_3D)
    assert budget_level(lux, profile_trip(lux, HUE), 12_000_000)["kind"] == "surplus"  # type: ignore[index]
    free = _form(destination="Huế", budget=30_000_000, spend_style="unlimited", **TRIP_3D)
    assert budget_level(free, profile_trip(free, HUE), 12_000_000)["kind"] == "unlimited"  # type: ignore[index]


@pytest.mark.parametrize(
    ("over", "text"),
    [
        ({"budget": 6_000_000}, "6 triệu cả đoàn cả chuyến"),
        (
            {"budget": 3_000_000, "budget_unit": "person_trip"},
            "3 triệu mỗi người × 2 người = 6 triệu cả đoàn cả chuyến",
        ),
        (
            {"budget": 1_000_000, "budget_unit": "person_day"},
            "1 triệu mỗi người mỗi ngày × 2 người × 3 ngày = 6 triệu cả đoàn cả chuyến",
        ),
        (  # bấm nhầm đơn vị: thấy ngay 36 triệu
            {"budget": 6_000_000, "budget_unit": "person_day"},
            "6 triệu mỗi người mỗi ngày × 2 người × 3 ngày = 36 triệu cả đoàn cả chuyến",
        ),
        (
            {"budget": 10_000_000, "prepaid_tickets": 1_500_000},
            "10 triệu − vé đã mua 1,5 triệu × 2 người = 7 triệu cả đoàn cả chuyến",
        ),
        (
            {"budget": 6_000_000, "budget_min": 3_000_000},
            "3–6 triệu cả đoàn cả chuyến, so với giữa khoảng 4,5 triệu",
        ),
        ({"budget": None, "budget_min": 10_000_000}, "từ 10 triệu cả đoàn cả chuyến"),
        ({"budget": 3_000_000, "budget_min": 0}, "tối đa 3 triệu cả đoàn cả chuyến"),
        ({"budget": None}, None),
    ],
)
def test_budget_text_shows_the_conversion(over: dict[str, Any], text: str | None) -> None:
    assert budget_text(_form(**TRIP_3D, **over)) == text


def test_request_text_states_ranges_and_per_person_totals() -> None:
    from ailusion.itinerary.response import request_text

    day = request_text(_form(budget=6_000_000, budget_unit="person_day", **TRIP_3D))
    assert "Ngân sách 6.000.000 đ mỗi người mỗi ngày (= 36.000.000 đ cả đoàn cả chuyến)." in day
    span = request_text(_form(budget=6_000_000, budget_min=3_000_000, **TRIP_3D))
    assert "Ngân sách 3.000.000–6.000.000 đ cả đoàn cả chuyến." in span
    above = request_text(_form(budget=None, budget_min=10_000_000, **TRIP_3D))
    assert "Ngân sách từ 10.000.000 đ cả đoàn cả chuyến." in above


def test_unknown_budget_plans_at_the_usual_level_not_the_dearest() -> None:
    # Ô ngân sách "Không giới hạn" = chưa biết bao nhiêu (không phải chi thoải mái): không hỏi lại
    # H3, chỗ ở nhắm giá phòng thường gặp (trung vị), kết quả báo tổng ước.
    from ailusion.itinerary.coordinator.profile import questions_for
    from ailusion.itinerary.coordinator.search import stay_band_calls

    form = _form(budget=None, budget_unknown=True)
    profile = profile_trip(form, PQ)
    assert "H3" not in questions_for(form, profile)
    assert "H3" in questions_for(_form(budget=None), profile_trip(_form(budget=None), PQ))
    assert budget_text(form) == "chưa biết — lên theo mức chi thường gặp"
    (_, args), *_ = stay_band_calls(form, profile.trip_budget, [], profile.price_levels)
    assert args["min_price_vnd"] < PQ["stay"]["median"] < args["max_price_vnd"]
    assert budget_level(form, profile) is None  # chưa có tổng ước (bước phân nhóm)
    level = budget_level(form, profile, 9_000_000)
    assert level and (level["kind"], level["severity"]) == ("unknown", "info")
    assert level["text"].startswith("Lịch theo mức chi thường gặp ở Phú Quốc (phòng khoảng 1,4")
    assert "9.000.000 đ" in level["text"]

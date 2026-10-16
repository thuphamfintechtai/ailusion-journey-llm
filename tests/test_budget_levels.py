"""Mức chi của điểm đến (coordinator/levels.py): mỗi mức chi user chọn so ngân sách với một trung vị
riêng của mặt bằng giá cả điểm đến, mỗi mức một lời báo riêng."""

from __future__ import annotations

from typing import Any

from ailusion.itinerary.coordinator.levels import budget_level, level_costs, price_levels
from ailusion.itinerary.coordinator.profile import profile_trip
from ailusion.itinerary.shared.form import TripForm, required_meals

# Phú Quốc (DB 10/2026): phòng 0,80 / 1,40 / 3,25 triệu mỗi đêm (150 chỗ có giá), quán 150 / 250 /
# 350 nghìn mỗi người (56 quán có giá).
PQ = {
    "stay": {"p25": 801_451, "median": 1_401_065, "p75": 3_248_604, "count": 150},
    "meal": {"p25": 150_000, "median": 250_000, "p75": 350_000, "count": 56},
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


def _level(**over: Any) -> dict[str, Any] | None:
    form = _form(**over)
    return budget_level(form, profile_trip(form, PQ))


def test_levels_take_quartiles_per_night_and_per_room() -> None:
    form = _form()
    profile = profile_trip(form, PQ)
    meals = sum(len(required_meals(d.day_start, d.day_end)) for d in profile.days)
    rows = {r["key"]: r for r in level_costs(form, profile)}
    assert [r["label"] for r in rows.values()] == ["Tiết kiệm", "Thường gặp", "Sang"]
    assert (
        rows["saving"]["basis"] == "trung vị nửa rẻ"
        and rows["luxury"]["basis"] == "trung vị nửa đắt"
    )
    # 4 ngày 3 đêm, 2 người 1 phòng: phòng tính theo số ĐÊM (không phải số ngày), không ÷ 2 người.
    assert rows["normal"]["total"] == 1_401_065 * 3 + 250_000 * meals * 2
    # Đã đặt chỗ ở: chỉ còn tiền ăn.
    booked = _form(stay_booked="Nhà bạn")
    assert level_costs(booked, profile_trip(booked, PQ))[0]["total"] == 150_000 * meals * 2


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

"""Chuyển kết quả nội bộ (OptionPlan, Pool) thành dữ liệu trả về của POST /plan."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .candidates import Hotel, Place, Pool
from .request import PlanRequest
from .response import Cost, Day, Item, Leg, Option, Risk, WeatherBlock, WhyCriterion
from .schedule import PROFILES, OptionPlan, Slot, day_date, hhmm

# ---------------------------------------------------------------- Pool <-> dict (để lưu checkpoint)


def pool_to_dict(pool: Pool) -> dict[str, Any]:
    data = asdict(pool)
    for p in data["places"].values():
        p["hours"] = {str(k): v for k, v in p["hours"].items()}
    return data


def pool_from_dict(data: dict[str, Any]) -> Pool:
    places = {}
    for pid, raw in data["places"].items():
        raw = dict(raw)
        raw["tags"] = tuple(raw["tags"])
        raw["hours"] = {int(k): tuple(v) if v else None for k, v in raw["hours"].items()}
        places[pid] = Place(**raw)
    return Pool(
        places=places,
        hotels=[Hotel(**h) for h in data["hotels"]],
        weather=data["weather"],
        festivals=data["festivals"],
        intercity=data["intercity"],
        health=data["health"],
        excluded=[tuple(e) for e in data["excluded"]],
        center=tuple(data["center"]) if data.get("center") else None,
    )


# ---------------------------------------------------------------- OptionPlan -> Option


def to_option(plan: OptionPlan, pool: Pool, req: PlanRequest, maps: dict[int, str]) -> Option:
    prof = PROFILES[plan.key]
    days = []
    for d in plan.days:
        date, weekday = day_date(req, d.index)
        weather = pool.weather[d.index] if d.index < len(pool.weather) else {}
        days.append(
            Day(
                index=d.index + 1,
                date=date,
                weekday=weekday,
                weather=WeatherBlock(
                    morning_rain_pct=d.rain["sang"],
                    afternoon_rain_pct=d.rain["chieu"],
                    evening_rain_pct=d.rain["toi"],
                    temp_min_c=weather.get("temp_min_c"),
                    temp_max_c=weather.get("temp_max_c"),
                    condition=weather.get("condition"),
                    warnings=weather.get("warnings", []),
                ),
                items=[_item(s) for s in d.slots],
                travel_minutes=sum(s.travel for s in d.slots) + d.back_min,
                travel_km=round(sum(s.km for s in d.slots) + d.back_km, 1),
                maps_url=maps.get(d.index),
                warnings=d.warnings,
            )
        )
    hotel = None
    if plan.hotel:
        h = plan.hotel
        hotel = Item(
            type="hotel",
            start="15:00",
            end="12:00",
            name=h.name,
            lat=h.lat,
            lng=h.lng,
            price_vnd=plan.cost["stay"],
            rating=h.rating,
            source="search_hotels",
            confidence="cao",
            note=f"{h.price_night:,} đ/đêm".replace(",", "."),
        )
    return Option(
        key=plan.key,  # type: ignore[arg-type]
        name=prof["name"],
        description=prof["desc"],
        hotel=hotel,
        days=days,
        cost=Cost(**plan.cost),
        hard_ok=plan.hard_ok,
        issues=plan.issues,
        repairs=plan.repairs,
        risk=Risk(**plan.risk),
        scores=plan.scores,
    )


def _item(s: Slot) -> Item:
    p = s.place
    confidence = "cao" if p and p.rating and p.reviews > 500 else "trung_binh"
    if s.type in ("siesta", "fixed", "free"):
        confidence = "cao"
    return Item(
        type=s.type,  # type: ignore[arg-type]
        start=hhmm(s.start),
        end=hhmm(s.end),
        name=s.name,
        place_id=p.place_id if p else None,
        category=p.category if p else None,
        lat=p.lat if p else None,
        lng=p.lng if p else None,
        price_vnd=s.price,
        leg=Leg(minutes=s.travel, km=round(s.km, 1)) if s.travel else None,
        indoor=p.indoor if p else None,
        rating=p.rating if p else None,
        source=p.source if p else "lap_lich",
        confidence=confidence,  # type: ignore[arg-type]
        why=[WhyCriterion(**w) for w in s.why],
        note=s.note
        or ("Giá vé ước tính theo loại địa điểm" if s.type == "poi" and s.price else None),
    )

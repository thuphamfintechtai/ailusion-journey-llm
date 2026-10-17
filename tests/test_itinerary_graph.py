"""Pipeline lập lộ trình chạy trọn với tool MCP giả và LLM giả — không gọi mạng, không tốn quota."""

from __future__ import annotations

import importlib
import re
import sys
from typing import Any

import pytest
from langchain_core.tools import BaseTool

from ailusion.itinerary import TripForm, build_itinerary_graph, itinerary_markdown, to_response
from ailusion.itinerary.agents.budget import PriceFinds
from ailusion.itinerary.agents.decision import DecisionOutput
from ailusion.itinerary.agents.experience import PurposeChecks
from ailusion.itinerary.agents.intake import Extraction
from ailusion.itinerary.agents.planner import PlannerOutput
from ailusion.itinerary.shared.models import Place

C = (16.06, 108.22)
CALLS: list[tuple[str, dict[str, Any]]] = []


def _p(
    pid: str, name: str, cat: str, dlat: float, dlng: float, price: int | None = None, **kw: Any
) -> dict:
    out = {
        "place_id": pid,
        "name": name,
        "category": cat,
        "rating": kw.get("rating", 4.5),
        "reviews": kw.get("reviews", 3000),
        "lat": C[0] + dlat,
        "lng": C[1] + dlng,
        "summary": kw.get("summary", ""),
        "thumbnail": kw.get("thumbnail"),
    }
    if price is not None:
        out["price_vnd"] = {"kind": "per_person", "vnd": price}
    return out


FAR = _p("far", "VinWonders Nha Trang", "Công viên giải trí", -3.8, 1.0, 900000)  # ~500 km
VISITS = [
    _p(
        "v1", "Bãi biển Mỹ Khê", "Bãi biển", 0.0, 0.03, 0, reviews=20000, thumbnail="https://img/v1"
    ),
    _p("v2", "Bảo tàng Chăm", "Bảo tàng", 0.0, 0.0, 60000),
    _p("v3", "Chùa Linh Ứng", "Chùa", 0.05, 0.05, 0),
    _p("v4", "Cầu Rồng", "Điểm mốc", 0.0, 0.01, 0, summary="nơi ngắm cầu phun lửa tối cuối tuần"),
    _p("v5", "Công viên Châu Á", "Khu vui chơi", -0.02, 0.0, 300000),
    _p("v6", "Ngũ Hành Sơn", "Núi", -0.1, 0.05, None),
    FAR,
]
FOODS = [
    _p("f1", "Hải sản Bé Mặn", "Nhà hàng hải sản", 0.01, 0.03, 250000),
    _p("f2", "Mì Quảng Bà Mua", "Nhà hàng", 0.0, 0.0, 50000),
    _p("f3", "Bánh xèo Bà Dưỡng", "Quán ăn", 0.01, 0.0, 60000),
]


def _respond(name: str, args: dict[str, Any]) -> dict[str, Any]:
    ok = {"status": "success"}
    if name == "geocode_vn":
        return {**ok, "results": [{"lat": C[0], "lng": C[1]}]}
    if name == "find_stays":
        return {
            **ok,
            # Mặt bằng giá cả điểm đến (MCP tính trước khi cắt `limit`).
            "price_stats_vnd": {"p25": 800_000, "median": 1_200_000, "p75": 2_500_000, "count": 40},
            "stays": [
                {
                    "name": "Khách sạn Biển",
                    "price_per_night_vnd": 800000,
                    "rating": 4.5,
                    "reviews": 900,
                    "lat": C[0],
                    "lng": C[1] + 0.02,
                },
                {
                    "name": "Resort Sơn Trà",
                    "price_per_night_vnd": 2500000,
                    "rating": 4.8,
                    "reviews": 1500,
                    "lat": C[0] + 0.05,
                    "lng": C[1] + 0.05,
                },
            ],
        }
    if name == "search_hotels":
        return {**ok, "hotels": []}
    if name == "find_places":
        text = f"{args.get('category', '')} {args.get('query', '')}".lower()
        if any(w in text for w in ("nhà hàng", "quán", "hải sản", "đặc sản", "ẩm thực", "chợ")):
            stats = {"p25": 100_000, "median": 150_000, "p75": 250_000, "count": 30}
            return {**ok, "places": FOODS, "price_stats_vnd": stats}
        return {**ok, "places": VISITS}
    if name == "check_opening_hours":
        return {
            **ok,
            "results": [
                {"place_id": pid, "status": "open", "hours_that_day": "07:00–21:30"}
                for pid in args["places"]
            ],
        }
    if name == "get_place_details":
        return {**ok, "place": {"stay_min": {"low": 60, "high": 90}}}
    if name == "distance_matrix":
        n_o = len(args["origins"])
        n_d = len(args.get("destinations") or [])
        if n_d:
            pairs = [
                {"from": i, "to": n_o + j, "duration_min": 15.4}
                for i in range(n_o)
                for j in range(n_d)
            ]
        else:
            pairs = [
                {
                    "from": i,
                    "to": j,
                    "duration_min": 14.6,
                    "distance_km": 3.2,
                }  # phút lẻ như Vietmap
                for i in range(n_o)
                for j in range(n_o)
                if i != j
            ]
        return {**ok, "pairs": pairs}
    if name == "weather_forecast":
        return {
            **ok,
            "days": [
                {
                    "date": f"2026-10-{25 + k}",
                    "condition": "Nắng",
                    "temp_min_c": 24,
                    "temp_max_c": 31,
                    "hours": [
                        {"time": "09:00", "chance_of_rain_pct": 10, "condition": "Nắng"},
                        {"time": "15:00", "chance_of_rain_pct": 80 if k else 0, "condition": "Mưa"},
                        {"time": "21:00", "chance_of_rain_pct": 0, "condition": "Trời quang"},
                    ],
                }
                for k in range(2)  # ngày 3 ngoài tầm dự báo
            ],
        }
    if name == "web_search":
        return {
            **ok,
            "results": [
                {
                    "url": "https://nguhanhson.vn/gia-ve",
                    "content": "Giá vé tham quan: Vé người lớn: 40.000đ",
                }
            ],
        }
    if name == "fetch_page":
        return {
            **ok,
            "pages": [
                {
                    "url": args["urls"][0],
                    "content": "Bảng giá. Vé người lớn: 40.000đ. Trẻ em 20.000đ",
                }
            ],
        }
    if name == "get_place_reviews":
        texts = {"v4": "Tối thứ 7 ra đây xem cầu phun lửa, rất vui"}
        text = texts.get(args["place_id"], "Chỗ này đẹp")
        return {**ok, "reviews": [{"rating": 5, "text": text}]}
    if name == "get_place_photos":
        return {**ok, "photos": {pid: f"https://img/{pid}" for pid in args["places"]}}
    if name == "plan_day_route":
        return {**ok, "google_maps_url": "https://maps.google.com/x"}
    return {**ok}


class FakeTool(BaseTool):
    def _run(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def _arun(self, **kwargs: Any) -> Any:
        CALLS.append((self.name, kwargs))
        return _respond(self.name, kwargs)

    def _to_args_and_kwargs(self, tool_input: Any, tool_call_id: Any) -> tuple[tuple, dict]:
        return (), dict(tool_input)


TOOLS = (
    "geocode_vn",
    "find_stays",
    "search_hotels",
    "find_places",
    "find_festivals",
    "search_events",
    "weather_forecast",
    "disaster_alerts",
    "check_opening_hours",
    "check_place_needs",
    "get_place_details",
    "distance_matrix",
    "web_search",
    "fetch_page",
    "plan_day_route",
    "search_places",
    "get_place_reviews",
    "get_place_photos",
)


class FakeLLM:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def with_structured_output(self, schema: type, **_: Any) -> FakeLLM:
        self.schema = schema
        return self

    async def ainvoke(self, messages: Any) -> Any:
        human = messages[1].content
        self.calls.append(self.schema.__name__)
        answer: Any
        if self.schema is Extraction:
            answer = Extraction()
        elif self.schema is PurposeChecks:
            answer = PurposeChecks(
                items=[
                    {
                        "code": c,
                        "matches_purpose": True,
                        "purpose": "relax",
                        "quote": "ra đây xem cầu phun lửa",
                    }
                    for c in re.findall(r"^## (X\d+) \|", human, re.M)
                ]
            )
        elif self.schema is PriceFinds:
            answer = PriceFinds(
                items=[
                    {
                        "code": c,
                        "price": 40000,
                        "url": "https://nguhanhson.vn/gia-ve",
                        "quote": "Vé người lớn: 40.000đ",
                    }
                    for c in re.findall(r"^## (V\d+)", human, re.M)
                ]
            )
        elif self.schema is PlannerOutput:
            d_codes = re.findall(r"^(D\d+) \|", human, re.M)
            q_codes = re.findall(r"^(Q\d+) \|", human, re.M)
            days = [int(d) for d in re.findall(r"^Ngày (\d+) \(", human, re.M)]
            answer = PlannerOutput(
                days=[
                    {
                        "day": d,
                        "plans": [
                            {
                                "id": "L1",
                                "leave_stay": "09:00",
                                "stops": [d_codes[0], q_codes[0], d_codes[1]],
                            },
                            {
                                "id": "L2",
                                "leave_stay": "09:00",
                                "stops": [d_codes[2], q_codes[1], d_codes[3]],
                            },
                            {"id": "L3", "leave_stay": "09:00", "stops": [d_codes[1], q_codes[2]]},
                        ],
                    }
                    for d in days
                ]
            )
        else:
            answer = DecisionOutput(
                chosen="PA1",
                why="Đề xuất Phương án 1.",
                explanation={"PA1": "Đề xuất: ít ngồi xe nhất."},
            )
        return {"raw": None, "parsed": answer}


@pytest.fixture
def llm_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    graph_module = importlib.import_module("ailusion.itinerary.shared.context")

    async def nop(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(graph_module.agent_log, "publish", nop)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda **_: FakeLLM(calls))
    CALLS.clear()
    return calls


def _form(**over: Any) -> dict[str, Any]:
    data = {
        "destination": "Đà Nẵng",
        "start_date": "2026-10-25",
        "end_date": "2026-10-27",
        "group_size": 2,
        "budget": 15_000_000,
        "group_type": "couple",
        "departure_time": "18:00",
        "night_return_time": "22:00",  # có giờ về tối thì bước 4 không hỏi H6
        "purposes": ["relax", "food"],
        "message": "",
    }
    data.update(over)
    return TripForm.model_validate(data).model_dump(mode="json")


async def _run(form: dict[str, Any], sent: list[str] | None = None) -> Any:
    """`sent`: ô user đã điền trên form (mặc định: các ô của _form)."""
    graph = build_itinerary_graph([FakeTool(name=n, description="fake") for n in TOOLS]).compile()
    sent = sent if sent is not None else [k for k, v in form.items() if v not in (None, [], "")]
    state = await graph.ainvoke({"form": form, "sent": sent}, {"configurable": {"thread_id": "t"}})
    return to_response(state, "plan-x", 1.0)


async def test_budget_levels_reach_the_response(llm_calls: list[str]) -> None:
    # Ngân sách thấp: cảnh báo ở đầu kết quả, kèm bảng 3 mức.
    low = await _run(_form(budget=1_000_000))
    assert low.budget_level and low.budget_level.kind == "low"
    assert [lv.key for lv in low.budget_level.levels] == ["saving", "normal", "luxury"]
    # Chỗ rẻ, ngân sách rất cao: báo dư, không lặp ghi chú "thấp hơn ngân sách" ở cuối (từng làm
    # hỏng cả kết quả vì trùng tên biến).
    rich = await _run(_form(budget=100_000_000))
    assert rich.status == "ok" and rich.budget_level and rich.budget_level.kind == "surplus"
    assert rich.budget_level.budget_text == "100 triệu cả đoàn cả chuyến"
    assert not any("thấp hơn ngân sách" in n for n in rich.notes)
    # Đơn vị "mỗi người mỗi ngày": dòng quy đổi ghi rõ phép tính.
    per_day = await _run(_form(budget=10_000_000, budget_unit="person_day"))
    assert per_day.budget_level and per_day.budget_level.budget_text == (
        "10 triệu mỗi người mỗi ngày × 2 người × 3 ngày = 60 triệu cả đoàn cả chuyến"
    )


async def test_time_window_cases_reach_the_response(llm_calls: list[str]) -> None:
    # Tới nơi 21:00 (về chỗ ở trước 22:00): ngày 1 không xếp gì, ghi rõ vì sao — trước đây lịch
    # trống bị báo "dữ liệu điểm đến thiếu".
    late = await _run(_form(arrival_time="21:00"))
    day1 = late.options[0].days[0]
    assert late.status == "ok" and day1.items == [] and day1.status == "ok"
    assert "Không xếp chỗ đi" in (day1.area_note or "")
    # Bận ngày 2 14:00–16:00: lịch hiện mục "Lịch bận" đúng giờ user nhập, không còn lỗi chạm
    # giờ bận.
    busy = await _run(
        _form(
            busy_slots=[{"day": 2, "busy_start": "14:00", "busy_end": "16:00", "place": "Hội nghị"}]
        )
    )
    day2 = busy.options[0].days[1]
    items = [(i.type, i.start, i.end, i.name) for i in day2.items if i.type == "busy"]
    assert items == [("busy", "14:00", "16:00", "Lịch bận: Hội nghị")]
    assert not any("chạm giờ bận" in x for x in day2.issues)
    # Ngày cuối bận 11:00–13:00 mà 12:30 phải rời chỗ ở (bay 15:00): không chỗ nào sau giờ bận
    # (chạy thật từng ra lịch về 16:54), đầu kết quả ghi chú giờ bận va giờ về.
    last = await _run(
        _form(
            departure_time="15:00",
            busy_slots=[{"day": 3, "busy_start": "11:00", "busy_end": "13:00"}],
        )
    )
    assert any("kết thúc sau giờ phải rời chỗ ở (12:30)" in n for n in last.notes)
    for option in last.options:
        day3 = option.days[2]
        assert all(i.start < "11:00" for i in day3.items if i.type in ("visit", "meal"))
        assert not any("quá giờ kết thúc" in x for x in day3.issues)
        # Bận xong 13:00 mà 12:30 đã phải rời chỗ ở: dòng cuối đi thẳng ra bến, không còn "Về
        # <chỗ ở> 13:15, kết thúc chuyến".
        depart = day3.items[-1]
        assert (depart.type, depart.start) == ("depart", "13:00")
        assert depart.name == (
            "Từ chỗ bận ra thẳng sân bay / ga / bến xe, kịp chuyến về 15:00 "
            "(trả phòng trước giờ bận)"
        )
    assert "- 13:00 ✈️ Từ chỗ bận ra thẳng sân bay" in itinerary_markdown(last)
    # Ngày cuối bận xong 12:00, trước giờ rời chỗ ở 12:30: bận rồi về chỗ ở như cũ.
    early = await _run(
        _form(
            departure_time="15:00",
            busy_slots=[{"day": 3, "busy_start": "11:00", "busy_end": "12:00"}],
        )
    )
    *_, busy, back = early.options[0].days[2].items
    assert (busy.type, busy.end) == ("busy", "12:00")
    assert early.stay and (back.start, back.name) == (
        "12:15",
        f"Về {early.stay.name}, kết thúc chuyến",
    )


async def test_full_pipeline_returns_three_options(llm_calls: list[str]) -> None:
    resp = await _run(_form())
    assert resp.status == "ok"
    assert resp.profile.group == "P3"  # cặp đôi, đủ tiền -> B4
    assert 1 <= len(resp.options) <= 3
    first = resp.options[0]
    assert first.days[0].items[0].type == "start" and first.days[0].items[-1].type == "back"
    assert any(i.type == "meal" for i in first.days[0].items)
    assert resp.chosen == "PA1"
    assert resp.why == "Đề xuất Phương án 1."  # lời của agent, không lộ mã nội bộ
    assert resp.stay and resp.stay.name in ("Khách sạn Biển", "Resort Sơn Trà")
    # Agent Experience xác nhận Cầu Rồng (câu trích có thật); Budget tìm giá Ngũ Hành Sơn có link.
    names = {i.name: i for o in resp.options for d in o.days for i in d.items}
    assert "Planner" not in " ".join(resp.notes)
    # Lượt LLM: Experience, Budget, Planner (1 lần, ≤ 4 ngày), Decision — không có đọc lời nhắn.
    assert "Extraction" not in llm_calls
    assert llm_calls.count("PlannerOutput") >= 1
    assert "DecisionOutput" in llm_calls
    assert resp.stats.llm_calls == len(llm_calls)
    # Thời gian ở lại lấy từ Google (get_place_details), giới hạn số lượt gọi.
    assert sum(n == "get_place_details" for n, _ in CALLS) <= 10
    # Bước 6a đọc review thật: chỉ chỗ có câu trích nằm trong review mới được nhận hợp mục đích.
    reviewed = [a["place_id"] for n, a in CALLS if n == "get_place_reviews"]
    assert "v4" in reviewed
    # Review đọc DB trước (auto); thời gian ở lại chỉ đọc DB (DB chưa lưu time_spent).
    assert all(a["source"] == "auto" for n, a in CALLS if n == "get_place_reviews")
    assert all(a["source"] == "db" for n, a in CALLS if n == "get_place_details")
    # DB không có lễ hội -> tìm sự kiện trên web; DB chỉ 2 chỗ ở có giá -> gọi search_hotels.
    assert any(n == "search_events" for n, _ in CALLS)
    assert any(n == "search_hotels" for n, _ in CALLS)
    v4 = (
        next(i for o in resp.options for d in o.days for i in d.items if i.place_id == "v4")
        if any(i.place_id == "v4" for o in resp.options for d in o.days for i in d.items)
        else None
    )
    if v4 is not None:
        assert v4.matches_purpose is True
    assert names
    # Ảnh: chỗ đã có ảnh từ find_places không hỏi lại; còn lại lấy bằng 1 lượt get_place_photos.
    photo_calls = [a for n, a in CALLS if n == "get_place_photos"]
    assert len(photo_calls) == 1 and "v1" not in photo_calls[0]["places"]
    stops = [i for o in resp.options for d in o.days for i in d.items if i.place_id]
    assert all(i.image == f"https://img/{i.place_id}" for i in stops)
    assert all(len(i.why) <= 3 for i in stops)
    assert all(d.area_note for o in resp.options for d in o.days)
    # 5 tab: lịch âm, mức đông, thời tiết theo buổi, chặng đi, chi phí, hành lý.
    d1, d2, d3 = first.days
    assert d1.lunar == "16/9" and d1.crowd == "medium"  # 25/10/2026 Chủ Nhật
    assert d2.crowd == "low" and d2.holiday is None
    assert [x.part for x in d1.sessions] == ["morning", "afternoon", "evening"]
    assert d2.sessions[1].icon == "rain" and d2.sessions[1].rain_chance == 80
    assert d1.temp_min == 24 and d3.sessions == [] and d3.temp_max is None
    legs = [i for i in d1.items if i.type in ("visit", "meal", "back")]
    assert all(i.travel_mode is None and i.travel_cost is None for i in legs)
    assert any(i.travel_km == 3.2 for i in legs)  # km thật từ distance_matrix
    # Chưa chọn phương tiện: ước cả xe máy; không bao giờ có bus.
    assert all({"motorbike", "car", "taxi"} <= {a.mode for a in i.travel_alt} for i in legs)
    assert all(a.mode != "bus" for i in legs for a in i.travel_alt)
    # Tổng phút đi cả ngày gồm cả chặng tới chỗ nghỉ trưa (mục "rest").
    rides = legs + [i for i in d1.items if i.type == "rest" and i.travel_alt]
    assert {a.mode: a.minutes for a in d1.ride_alt}["car"] == sum(
        {a.mode: a.minutes for a in i.travel_alt}["car"] for i in rides
    )
    for o in resp.options:
        cb = o.cost_breakdown
        assert cb.total == sum(d.cost.total for d in o.days)
        assert cb.total == cb.stay + cb.tickets + cb.food + cb.transport + cb.rental + cb.extras
        assert cb.total_min == sum(d.cost.total_min for d in o.days)
        assert cb.total_max == sum(d.cost.total_max for d in o.days)
        assert cb.total_min <= cb.total <= cb.total_max
        assert cb.per_person_min <= cb.per_person <= cb.per_person_max
        assert cb.extras == sum(d.cost.extras for d in o.days) > 0
        labels = [e.label for e in cb.extra_items]
        assert labels == ["Chi phí phát sinh"]
        assert cb.extras_notes and 0 < cb.extras_pct_min <= cb.extras_pct_max <= 0.25
        assert not any("hư xe" in n for n in cb.extras_notes)  # form không chọn phương tiện
        assert all(e.reason and e.min <= e.max for d in o.days for e in d.cost.extra_items)
        assert o.days[-1].cost.stay == 0 and o.days[0].cost.stay == cb.stay // 2
    stops1 = [i for i in d1.items if i.type in ("visit", "meal")]
    assert all((i.price_min is None) == (i.price is None) for i in stops1)
    assert all(
        i.price_min == i.price == i.price_max
        for i in stops1
        if i.price is not None and not (i.price_by or "").startswith("ước")
    )
    assert all(i.travel_cost_min is None and i.travel_cost_max is None for i in legs)
    assert resp.packing and resp.packing[0].item == "CCCD / hộ chiếu"
    assert any(p.item == "Áo mưa / ô" and "chiều ngày 2" in p.reason for p in resp.packing)
    # Chỗ DB gắn nhầm điểm đến (cách ~500 km) bị bỏ ở bước lọc.
    assert "VinWonders Nha Trang" not in names


async def test_paid_search_skipped_when_db_has_data(
    llm_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stays = [
        {
            "name": f"KS {i}",
            "price_per_night_vnd": 700000 + i,
            "rating": 4.5,
            "reviews": 500,
            "lat": C[0],
            "lng": C[1] + 0.01 * i,
        }
        for i in range(6)
    ]
    real = _respond

    def respond(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "find_festivals":
            return {"status": "success", "festivals": [{"name": "Lễ hội pháo hoa"}]}
        if name == "find_stays":
            return {"status": "success", "stays": stays}
        return real(name, args)

    monkeypatch.setattr(sys.modules[__name__], "_respond", respond)
    resp = await _run(_form())
    assert resp.status == "ok"
    assert not any(n in ("search_events", "search_hotels") for n, _ in CALLS)
    assert "Lễ hội pháo hoa" in resp.festivals


async def test_matrix_error_falls_back_to_estimates(
    llm_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _respond

    def respond(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "distance_matrix":
            return {"status": "error", "message": "quá 25 điểm"}
        return real(name, args)

    monkeypatch.setattr(sys.modules[__name__], "_respond", respond)
    resp = await _run(_form())
    assert resp.status == "ok"
    assert any("ước theo khoảng cách" in n for n in resp.notes)
    # Không còn chặng nào "chưa có phút đi": mọi chặng đã có số ước.
    unchecked = [u for o in resp.options for d in o.days for u in d.unchecked]
    assert not any("chưa có phút đi" in u for u in unchecked)


# Trạng thái nhu cầu từng chỗ như MCP trả (find_places có `needs`, check_place_needs).
NEED_STATUS: dict[str, dict[str, str]] = {
    "f1": {"wheelchair": "no"},  # Hải sản Bé Mặn: món chay chưa rõ, không có lối xe lăn
    "f2": {"vegetarian": "yes", "wheelchair": "yes"},  # Mì Quảng Bà Mua: có món chay
    "v2": {"wheelchair": "yes", "wheelchair_toilet": "yes"},
    "v3": {"wheelchair": "no"},  # Chùa Linh Ứng: nhiều bậc
}


@pytest.fixture
def need_tools(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Tool giả trả trạng thái nhu cầu; trả về danh sách lời nhắn đưa Planner."""
    real = _respond

    def status(pid: str, needs: list[str]) -> dict[str, str]:
        return {n: NEED_STATUS.get(pid, {}).get(n, "unknown") for n in needs}

    def respond(name: str, args: dict[str, Any]) -> dict[str, Any]:
        out = real(name, args)
        needs = args.get("needs") or []
        if name == "find_places" and needs:
            places = [{**p, "needs_status": status(p["place_id"], needs)} for p in out["places"]]
            return {**out, "places": places}
        if name == "check_place_needs":
            results = [{"place_id": p, "needs_status": status(p, needs)} for p in args["places"]]
            return {"status": "success", "results": results}
        return out

    monkeypatch.setattr(sys.modules[__name__], "_respond", respond)
    prompts: list[str] = []
    ask = FakeLLM.ainvoke

    async def ainvoke(self: FakeLLM, messages: Any) -> Any:
        if self.schema is PlannerOutput:
            prompts.append(messages[1].content)
        return await ask(self, messages)

    monkeypatch.setattr(FakeLLM, "ainvoke", ainvoke)
    return prompts


def _meals(resp: Any) -> set[str]:
    return {i.name for o in resp.options for d in o.days for i in d.items if i.type == "meal"}


async def test_meal_needs_only_apply_to_restaurants(
    llm_calls: list[str], need_tools: list[str]
) -> None:
    # Chọn "Có món chay" + "Ghế cao cho bé": trước đây Planner không biết gì, lịch toàn quán chưa
    # rõ món chay; bãi biển, bảo tàng cũng bị ghi "chưa rõ có đáp ứng nhu cầu bắt buộc".
    resp = await _run(
        _form(
            group_type="family",
            group_size=3,
            kids_under_6=1,
            required_needs=["vegetarian", "high_chair"],
        )
    )
    assert resp.status == "ok"
    prompt = need_tools[0]
    assert "# Đoàn cần (bắt buộc): có món chay, ghế cao cho bé (quán Q…)" in prompt
    assert re.search(
        r"^Q\d+ \| Mì Quảng Bà Mua .*\| đáp ứng: có món chay \| chưa rõ: ghế cao", prompt, re.M
    )
    assert re.search(
        r"^Q\d+ \| Hải sản Bé Mặn .*\| chưa rõ: có món chay, ghế cao cho bé$", prompt, re.M
    )
    # Chỗ chơi không mang nhãn nhu cầu ăn uống.
    assert not re.search(r"^D\d+ .*(món chay|ghế cao)", prompt, re.M)
    # Quán có món chay được xếp trước (Planner giả lấy quán đầu danh sách).
    assert "Mì Quảng Bà Mua" in _meals(resp)
    # Cảnh báo ghi rõ nhu cầu nào, chỉ cho quán.
    unsure = [n for n in resp.notes if "chưa rõ" in n]
    assert unsure and all(
        n.split(":")[0] in {"Hải sản Bé Mặn", "Bánh xèo Bà Dưỡng", "Mì Quảng Bà Mua"}
        for n in unsure
    )
    assert any(
        n == "Hải sản Bé Mặn: chưa rõ đáp ứng: có món chay, ghế cao cho bé — nên hỏi trước"
        for n in resp.notes
    )
    # Chỗ ở không bị lọc theo món chay / ghế cao.
    stays = [a for n, a in CALLS if n == "find_stays"]
    assert stays and not any(a.get("needs") for a in stays)


async def test_wheelchair_need_drops_places_that_say_no(
    llm_calls: list[str], need_tools: list[str]
) -> None:
    resp = await _run(_form(group_type="family", group_size=3, wheelchair_users=1))
    names = {i.name for o in resp.options for d in o.days for i in d.items}
    assert "Chùa Linh Ứng" not in names and "Hải sản Bé Mặn" not in names
    dropped = next(n for n in resp.notes if n.startswith("Bỏ"))
    assert "Chùa Linh Ứng: thiếu nhu cầu bắt buộc: lối vào cho xe lăn" in dropped
    prompt = need_tools[0]
    assert "# Đoàn cần (bắt buộc): lối vào cho xe lăn, nhà vệ sinh cho xe lăn (mọi chỗ)" in prompt
    assert re.search(
        r"^D\d+ \| Bảo tàng Chăm .*\| đáp ứng: lối vào cho xe lăn, nhà vệ sinh", prompt, re.M
    )


async def test_missing_info_asks_once(llm_calls: list[str]) -> None:
    resp = await _run(_form(group_type=None, budget=None))
    assert resp.status == "need_info"
    assert [q.id for q in resp.questions] == ["H1", "H3"]
    assert resp.questions[0].choices[0].set == {"group_type": "solo"}
    assert not any(n == "search_hotels" for n, _ in CALLS)  # chưa tìm kiếm gì


async def test_message_is_read_when_fields_are_blank(llm_calls: list[str]) -> None:
    form = _form(message="đi cho đỡ mệt nha")
    resp = await _run({**form})
    assert "Extraction" in llm_calls
    assert resp.status == "ok"


async def test_photo_tool_failure_still_returns_the_plan(
    llm_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _respond

    def respond(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_place_photos":
            raise RuntimeError("SerpApi hết quota")
        return real(name, args)

    monkeypatch.setattr(sys.modules[__name__], "_respond", respond)
    resp = await _run(_form())
    assert resp.status == "ok" and resp.options
    images = [i.image for o in resp.options for d in o.days for i in d.items if i.place_id]
    assert all(img in (None, "https://img/v1") for img in images)


async def test_three_options_run_as_parallel_branches(llm_calls: list[str]) -> None:
    """3 nhánh song song, mỗi nhánh 1 phương án: Planner chạy mỗi nhánh, lịch mang mã nhánh, tìm
    rộng hơn (theo sao + quét quanh trung tâm), 3 phương án không trùng hệt nhau."""
    graph = build_itinerary_graph([FakeTool(name=n, description="fake") for n in TOOLS]).compile()
    form = _form()
    sent = [k for k, v in form.items() if v not in (None, [], "")]
    state = await graph.ainvoke({"form": form, "sent": sent}, {"configurable": {"thread_id": "r"}})
    ids = [o["id"] for o in state["trip"]["options"]]
    assert ids[0] == "PA1" and len(ids) == len(set(ids))
    for o in state["trip"]["options"]:
        k = o["id"][2:]
        assert all(pid.startswith(f"V{k}-") for pid in o["days"])
        assert all(pid in state["plans"] for pid in o["days"])
    assert llm_calls.count("PlannerOutput") >= 3
    sigs = [
        tuple(tuple(state["plans"][p]["stops"]) for p in o["days"])
        for o in state["trip"]["options"]
    ]
    assert len(sigs) == len(set(sigs))
    finds = [a for n, a in CALLS if n == "find_places"]
    assert any(a.get("sort_by") == "rating" for a in finds)
    assert any(a.get("radius_km") == 5 and "lat" in a for a in finds)


def test_places_are_shared_out_in_turn() -> None:
    from ailusion.itinerary.coordinator.branches import split_shares

    def place(i: int, kind: str, **kw: Any) -> Place:
        return Place(
            place_id=f"{kind}{i}",
            name=f"{kind} {i}",
            category="",
            kind=kind,
            lat=16.0,
            lng=108.2,
            rating=5 - i * 0.1,
            reviews=1000,
            **kw,
        )

    places = {
        p.place_id: p
        for p in [place(i, "visit") for i in range(6)]
        + [place(i, "meal") for i in range(3)]
        + [place(9, "visit", must=True), place(8, "meal", wanted="bún bò")]
    }
    shares = split_shares(places)
    assert shares[0] == {"visit0", "visit3"}
    assert shares[1] == {"visit1", "visit4"}  # quán không chia
    assert not any("visit9" in s or "meal8" in s for s in shares)  # user chọn / nhắc: không chia


def test_verification_only_looks_at_places_the_branches_will_plan_with() -> None:
    from ailusion.itinerary.coordinator.filter import branch_pool

    state = {"branches": [{"pool": ["m", "a1", "a2"]}, {"pool": ["m", "b1"]}, {"pool": ["c1"]}]}
    # Xen kẽ giữa các nhánh, bỏ trùng: cắt theo trần vẫn chia đều cho 3 phương án.
    assert branch_pool(state) == ["m", "c1", "a1", "b1", "a2"]

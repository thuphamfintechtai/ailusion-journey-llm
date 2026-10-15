"""Pipeline lập lộ trình chạy trọn với tool MCP giả và LLM giả — không gọi mạng, không tốn quota."""

from __future__ import annotations

import importlib
import re
import sys
from typing import Any

import pytest
from langchain_core.tools import BaseTool

from ailusion.itinerary import TripForm, build_itinerary_graph, to_response
from ailusion.itinerary.agents.budget import PriceFinds
from ailusion.itinerary.agents.decision import DecisionOutput
from ailusion.itinerary.agents.experience import PurposeChecks
from ailusion.itinerary.agents.intake import Extraction
from ailusion.itinerary.agents.planner import PlannerOutput

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
            return {**ok, "places": FOODS}
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
    assert {a.mode: a.minutes for a in d1.ride_alt}["car"] == sum(
        {a.mode: a.minutes for a in i.travel_alt}["car"] for i in legs
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

"""Graph A: phần chuẩn hoá, gộp kết quả nhánh, và hình dạng graph — không gọi LLM / MCP."""

from __future__ import annotations

from langchain_core.tools import tool

from ailusion.graphs.research import BRANCHES, build_research_graph, normalize
from ailusion.graphs.research.graph import _valid_steps
from ailusion.graphs.research.schemas import ToolStep, add_results
from ailusion.tools import NODE_TOOLS


def test_budget_sums_vnd_and_converted_foreign_amounts():
    result = {
        "status": "success",
        "amounts": [
            {"text": "15tr", "value_min": 15_000_000.0, "currency": "VND"},
            {"text": "100 đô", "value_min": 100.0, "currency": "USD", "vnd_min": 2_579_000},
        ],
    }
    assert normalize.budget_vnd(result) == 17_579_000


def test_budget_tolerates_tool_error():
    assert normalize.budget_vnd("Error: quota") is None
    assert normalize.budget_vnd({"status": "success", "amounts": []}) is None


def test_end_date_counts_first_day():
    assert normalize.end_date("2026-10-10", 3) == "2026-10-12"
    assert normalize.end_date(None, 3) is None
    assert normalize.end_date("10/10", 3) is None


def test_location_reads_new_province():
    result = {
        "status": "success",
        "results": [
            {
                "name": "Phường Lâm Viên-Đà Lạt",
                "address_new": "Phường Lâm Viên-Đà Lạt, Tỉnh Lâm Đồng",
                "admin_new": {"province": "Tỉnh Lâm Đồng"},
                "lat": 11.96,
                "lng": 108.44,
            }
        ],
    }
    assert normalize.location(result)["province"] == "Tỉnh Lâm Đồng"


def test_missing_destination_means_ask_back():
    assert normalize.missing_essentials({"destination": None}) == ["điểm đến"]
    assert normalize.missing_essentials({"destination": "Huế"}) == []


def test_collect_urls_reads_urls_inside_tool_data():
    results = [
        {"data": '{"url": "https://a.vn/1", "x": "https://a.vn/1"}'},
        {"data": "Xem https://b.vn/tin."},
        {"data": "không có link"},
    ]
    assert normalize.collect_urls(results) == ["https://a.vn/1", "https://b.vn/tin"]


def test_add_results_appends_and_resets_on_none():
    first = add_results([], [{"tool": "a"}])
    second = add_results(first, [{"tool": "b"}])
    assert [r["tool"] for r in second] == ["a", "b"]
    assert add_results(second, None) == []


def test_valid_steps_drops_wrong_branch_bad_json_and_duplicates():
    allowed = {"prices": {"search_hotels"}, "places": {"search_places"}}
    done = {normalize.step_key("search_places", {"query": "cà phê"})}
    steps = [
        ToolStep(
            branch="prices", tool="search_hotels", args_json='{"destination": "Huế"}', why="a"
        ),
        ToolStep(
            branch="prices", tool="search_hotels", args_json='{"destination": "Huế"}', why="b"
        ),
        ToolStep(branch="prices", tool="search_places", args_json="{}", why="sai nhánh"),
        ToolStep(branch="places", tool="search_places", args_json="{query", why="JSON hỏng"),
        ToolStep(
            branch="places", tool="search_places", args_json='{"query": "cà phê"}', why="đã chạy"
        ),
    ]
    valid = _valid_steps(steps, allowed, done)
    assert valid == [
        {"branch": "prices", "tool": "search_hotels", "args": {"destination": "Huế"}, "why": "a"}
    ]


def test_branches_match_tool_groups():
    assert set(BRANCHES) <= set(NODE_TOOLS)


def test_graph_has_every_node_of_the_diagram():
    names = {n for group in NODE_TOOLS.values() for n in group}

    def make(name):
        @tool(name)
        def _t(x: str = "") -> str:
            """fake"""
            return x

        return _t

    graph = build_research_graph([make(n) for n in sorted(names)]).compile()
    nodes = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == {
        "trich_slot",
        "chuan_hoa",
        "hoi_lai",
        "lap_ke_hoach",
        "dieu_phoi",
        "goi_tool",
        "phan_bien",
        "dong_bang",
    }

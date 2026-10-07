"""Quy ước key Redis — khớp bảng "Redis: bố trí key" trong README."""

from __future__ import annotations

import re

from ailusion.redis import keys


def test_agent_log_key():
    assert keys.agent_log("trip-1:A") == "agentlog:trip-1:A"


def test_tool_cache_key_shape():
    key = keys.tool_cache("weather", {"city": "Đà Lạt"})
    assert re.fullmatch(r"toolcache:weather:[0-9a-f]{32}", key)


def test_tool_cache_key_is_stable_and_order_independent():
    a = keys.tool_cache("weather", {"city": "Đà Lạt", "days": 3})
    b = keys.tool_cache("weather", {"days": 3, "city": "Đà Lạt"})
    assert a == b


def test_tool_cache_key_differs_by_args_and_tool():
    assert keys.tool_cache("weather", {"city": "Huế"}) != keys.tool_cache(
        "weather", {"city": "Đà Lạt"}
    )
    assert keys.tool_cache("weather", {"city": "Huế"}) != keys.tool_cache("events", {"city": "Huế"})


def test_tool_cache_pattern_matches_its_keys():
    pattern = keys.tool_cache_pattern("weather")
    assert pattern == "toolcache:weather:*"
    assert keys.tool_cache("weather", {"city": "Huế"}).startswith(pattern[:-1])


def test_tool_cache_handles_non_json_args():
    """default=str: tham số lạ (datetime, object...) không làm vỡ việc tạo key."""
    assert keys.tool_cache("weather", {"when": object()}).startswith("toolcache:weather:")

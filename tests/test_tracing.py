"""Chạy test không gửi trace ra ngoài (tắt ở conftest.py), dù .env của máy bật tracing."""

from __future__ import annotations

from langsmith.utils import tracing_is_enabled

from ailusion.core.settings import get_settings
from ailusion.core.tracing import setup_observability


def test_tests_send_no_traces() -> None:
    assert get_settings().langsmith.enabled is False
    assert setup_observability().langsmith is False
    assert tracing_is_enabled() is False

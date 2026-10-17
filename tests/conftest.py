"""Cấu hình chung cho test."""

from __future__ import annotations

import os

import pytest

# Test không gửi trace lên LangSmith / Langfuse dù .env của máy bật tracing: trước đây mỗi lần
# chạy test là một loạt trace, hết hạn mức trace của tháng thì tràn log lỗi 429. Phải đặt trước
# khi import ailusion — `get_settings()` nạp .env vào os.environ nhưng không ghi đè biến đã có.
# - LANGSMITH_TRACING: settings coi LangSmith là tắt (`setup_observability`).
# - LANGSMITH_TRACING_V2: LangChain xét biến này trước mọi tên khác (kể cả LANGCHAIN_TRACING_V2).
# - LANGFUSE_TRACING_ENABLED: Langfuse SDK không gửi gì dù có khoá.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_TRACING_V2"] = "false"
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"

from ailusion.core.settings import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _no_service_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test gọi API không kèm X-Service-Key: tắt kiểm khoá dù .env có SERVICE_API_KEY.
    Test riêng của phần kiểm khoá (test_service_key.py) tự đặt lại khoá."""
    monkeypatch.setattr(get_settings(), "service_api_key", None)

"""Cấu hình chung cho test."""

from __future__ import annotations

import pytest

from ailusion.core.settings import get_settings


@pytest.fixture(autouse=True)
def _no_service_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test gọi API không kèm X-Service-Key: tắt kiểm khoá dù .env có SERVICE_API_KEY.
    Test riêng của phần kiểm khoá (test_service_key.py) tự đặt lại khoá."""
    monkeypatch.setattr(get_settings(), "service_api_key", None)

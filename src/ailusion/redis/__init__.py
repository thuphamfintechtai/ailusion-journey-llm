"""Kết nối Redis dùng chung (`client.py`) và quy ước đặt key (`keys.py`)."""

from . import keys
from .client import close_redis, get_redis

__all__ = ["close_redis", "get_redis", "keys"]

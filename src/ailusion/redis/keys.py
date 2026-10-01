"""Toàn bộ quy ước key Redis ở một chỗ — khớp bảng "Redis: bố trí key" trong README.

Mọi thứ nằm chung DB 0 (index của Redis Query Engine chỉ chạy trên DB 0), tách nhau
bằng tiền tố:

| Tiền tố                             | Nội dung             | Hết hạn                            |
|-------------------------------------|----------------------|------------------------------------|
| `checkpoint*`, `checkpoint_write*`  | LangGraph checkpoint | TTL checkpoint, gia hạn khi đọc    |
| `agentlog:<thread_id>`              | Stream nhật ký agent | như checkpoint, cắt theo maxlen    |
| `toolcache:<tool>:<hash>`           | Kết quả tool         | TTL riêng từng tool                |

Tiền tố `checkpoint*` do `AsyncRedisSaver` tự quản lý, không khai báo ở đây.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

AGENT_LOG_PREFIX = "agentlog"
TOOL_CACHE_PREFIX = "toolcache"

# Độ dài phần hash trong key cache: 32 ký tự hex = 128 bit, đủ để không đụng nhau.
_HASH_LENGTH = 32


def agent_log(thread_id: str) -> str:
    """Stream nhật ký agent của một thread."""
    return f"{AGENT_LOG_PREFIX}:{thread_id}"


def tool_cache(tool: str, args: Any) -> str:
    """Kết quả tool, định danh bằng tên tool + vân tay của toàn bộ tham số."""
    return f"{TOOL_CACHE_PREFIX}:{tool}:{_fingerprint(args)}"


def tool_cache_pattern(tool: str) -> str:
    """Mẫu SCAN khớp mọi key cache của một tool (dùng khi xoá cache tool đó)."""
    return f"{TOOL_CACHE_PREFIX}:{tool}:*"


def _fingerprint(args: Any) -> str:
    """Vân tay ổn định của tham số: JSON đã sắp khoá -> SHA-256 rút gọn."""
    raw = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:_HASH_LENGTH]

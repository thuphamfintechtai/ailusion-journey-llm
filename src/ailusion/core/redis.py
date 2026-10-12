"""Redis dùng chung: một async client (connection pool) và quy ước key.

Mọi thứ nằm chung DB 0, tách nhau bằng tiền tố:

| Tiền tố                             | Nội dung             | Hết hạn                          |
|-------------------------------------|----------------------|----------------------------------|
| `checkpoint*`, `checkpoint_write*`  | LangGraph checkpoint | TTL checkpoint, gia hạn khi đọc  |
| `agentlog:<thread_id>`              | Stream nhật ký agent | như checkpoint, cắt theo maxlen  |

Tiền tố `checkpoint*` do `AsyncRedisSaver` tự quản lý (xem `memory.py`, mở kết nối riêng).
`close_redis()` khi tiến trình kết thúc.
"""

from __future__ import annotations

# `ailusion.core.redis` không che khuất thư viện `redis`: Python 3 luôn import tuyệt đối.
from redis.asyncio import Redis

from .settings import get_settings

AGENT_LOG_PREFIX = "agentlog"

_client: Redis | None = None

# Chờ phản hồi một lệnh tối đa bao lâu (giây). redis-py 8 mặc định 5s; đặt rõ ở đây vì lệnh
# chặn (XREAD BLOCK của nhật ký agent) phải luôn chờ ngắn hơn mốc này, không thì client tự
# ngắt với TimeoutError giữa lúc stream /events đang mở.
SOCKET_TIMEOUT_SECONDS = 10


def get_redis() -> Redis:
    """Client dùng chung, tạo ở lần gọi đầu tiên."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            get_settings().redis.url,
            decode_responses=True,
            socket_timeout=SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=5,
        )
    return _client


async def close_redis() -> None:
    """Đóng connection pool; lần `get_redis()` sau sẽ tạo client mới."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def agent_log_key(thread_id: str) -> str:
    """Stream nhật ký agent của một thread."""
    return f"{AGENT_LOG_PREFIX}:{thread_id}"

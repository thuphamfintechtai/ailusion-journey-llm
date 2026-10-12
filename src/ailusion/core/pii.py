"""Che dữ liệu nhạy cảm (PII) trước khi gửi trace sang LangSmith / Langfuse.

Đây là lớp chặn ở biên observability, tương ứng ô "Che dữ liệu nhạy cảm · Che PII trước
trace & ghi" trong sơ đồ. Tool che PII đầy đủ (dùng trong graph) có thể dùng lại `mask_text`.

Bao phủ: email, số thẻ (kiểm Luhn), số điện thoại VN (di động + cố định, có/không +84),
CCCD 12 số / CMND 9 số, hộ chiếu VN (1 chữ + 7 số).

Hai hàm `langsmith_mask` / `langfuse_mask` ở cuối file chỉ là lớp vỏ cho đúng chữ ký mà
mỗi SDK yêu cầu — `tracing.py` cắm chúng vào lúc khởi tạo.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_PHONE = re.compile(r"(?<![\d+])(?:\+84|84|0)(?:[\s.-]?\d){9,10}(?!\d)")
_NATIONAL_ID = re.compile(r"(?<!\d)(?:\d{12}|\d{9})(?!\d)")
_PASSPORT = re.compile(r"\b[A-Z]\d{7}\b")

# Chặn đệ quy vô hạn khi payload tự tham chiếu hoặc lồng quá sâu.
_MAX_DEPTH = 50


def mask_text(text: str) -> str:
    """Thay PII trong một chuỗi bằng nhãn <EMAIL>, <CARD>, <PHONE>, <NATIONAL_ID>, <PASSPORT>."""
    if not text:
        return text
    text = _EMAIL.sub("<EMAIL>", text)
    text = _CARD.sub(_mask_card, text)
    text = _PHONE.sub("<PHONE>", text)
    text = _NATIONAL_ID.sub("<NATIONAL_ID>", text)
    text = _PASSPORT.sub("<PASSPORT>", text)
    return text


def mask_payload(data: Any, _depth: int = 0) -> Any:
    """Che PII đệ quy trong dict / list / str / pydantic model (vd. LangChain message)."""
    if _depth > _MAX_DEPTH:
        return data
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {k: mask_payload(v, _depth + 1) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return type(data)(mask_payload(v, _depth + 1) for v in data)
    dump: Callable[[], Any] | None = getattr(data, "model_dump", None)
    if callable(dump):
        try:
            return mask_payload(dump(), _depth + 1)
        except Exception:  # noqa: BLE001 - không để lỗi che PII làm hỏng tracing
            return "<UNSERIALIZABLE>"
    return data


def _mask_card(match: re.Match[str]) -> str:
    """Chỉ che khi dãy số thật sự giống số thẻ, tránh che nhầm timestamp / mã đơn hàng."""
    digits = re.sub(r"\D", "", match.group(0))
    # Đầu số của tổ chức phát hành thật: Amex/JCB 3, Visa 4, Mastercard 2 & 5,
    # UnionPay 6, Napas 9704.
    issuer_ok = digits[0] in "23456" or digits.startswith("9704")
    looks_like_card = issuer_ok and 13 <= len(digits) <= 19 and _luhn_ok(digits)
    return "<CARD>" if looks_like_card else match.group(0)


def _luhn_ok(digits: str) -> bool:
    """Kiểm tra số thẻ bằng thuật toán Luhn (tổng chia hết cho 10)."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def langfuse_mask(*, data: Any, **_: Any) -> Any:
    """Chữ ký hàm `mask` mà Langfuse SDK yêu cầu."""
    return mask_payload(data)


def langsmith_mask(data: dict) -> dict:
    """Chữ ký `hide_inputs` / `hide_outputs` của LangSmith Client."""
    return mask_payload(data)

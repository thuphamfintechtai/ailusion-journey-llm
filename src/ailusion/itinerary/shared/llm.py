"""Gọi LLM có kiểm soát và luật kiểm đầu ra của LLM.

- Chặn chi phí: trần token đầu ra theo từng việc (`OUTPUT_TOKENS`), thử lại tối đa 1 lần, không
  bật thinking; `cost_usd` quy token ra tiền theo bảng giá OpenAI.
- LLM không tự đặt số, mọi thứ nó đưa phải tìm lại được: `quote_found` (câu trích nằm nguyên văn
  trong văn bản gốc), `money_in` (số tiền trong một đoạn chữ), `numbers_in` (con số trong lời
  giải thích). Trang web dạng markdown thì so sau `plain_text` (bỏ **đậm**, link, ảnh).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ailusion.core.settings import get_settings

OUTPUT_TOKENS = {"intake": 800, "check": 900, "planner": 2500, "decision": 900}
PRICES_USD = {"gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1": (2.0, 8.0)}


def cost_usd(usage: list[dict[str, Any]]) -> float | None:
    model = (get_settings().llm.chat_model or "openai:gpt-4.1-mini").split(":")[-1]
    price = PRICES_USD.get(model)
    if price is None:
        return None
    return round(
        (sum(u["in"] for u in usage) * price[0] + sum(u["out"] for u in usage) * price[1]) / 1e6, 4
    )


def norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return re.sub(r"\s+", " ", text).strip(" .,!?\"'")


def quote_found(quote: str, message: str) -> bool:
    """Câu trích phải nằm nguyên văn trong lời nhắn (không phân biệt hoa thường, khoảng trắng)."""
    q = norm(quote)
    return bool(q) and q in norm(message)


MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
DATA_URI = re.compile(r"data:[\w/+.-]+;base64,[\w+/=]+")
MD_MARKS = re.compile(r"[*_`~|#>]+")


def plain_text(text: str) -> str:
    """Bỏ định dạng markdown của trang web (ảnh, ảnh base64, link -> chữ của link, **đậm**, _,
    `, #, >, bảng |) để so câu trích: trang ghi "**Vé tham quan:** 80.000 VNĐ" thì câu
    "Vé tham quan: 80.000 VNĐ" vẫn khớp."""
    text = MD_IMAGE.sub(" ", text)
    text = DATA_URI.sub(" ", text)
    text = MD_LINK.sub(r"\1", text)
    return MD_MARKS.sub(" ", text)


# "1,2 triệu", "1.5tr", "2 triệu" (không khớp "tr" đầu chữ "trẻ", "trên").
MILLIONS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|tr)(?!\w)")


def money_in(text: str) -> list[int]:
    """Mọi số tiền trong đoạn chữ: "120.000đ", "120k", "50,000 VND", "80.000 VNĐ", "1,2 triệu" ->
    120000, 120000, 50000, 80000, 1200000."""
    low = text.lower()
    out = [int(round(float(n.replace(",", ".")) * 1_000_000)) for n in MILLIONS.findall(low)]
    low = MILLIONS.sub(" ", low)
    for num, unit in re.findall(r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(k|nghìn|ngàn|vnđ|vnd|đ|₫)?", low):
        value = int(num.replace(".", "").replace(",", ""))
        if unit in ("k", "nghìn", "ngàn"):
            value *= 1000
        if value >= 1000:
            out.append(value)
    return out


def numbers_in(text: str) -> set[str]:
    """Các con số trong câu (bỏ dấu chấm nghìn), để soát số LLM có thêm không."""
    return {n.replace(".", "").replace(",", "") for n in re.findall(r"\d[\d.,]*", text)}

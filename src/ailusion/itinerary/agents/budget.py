"""Agent Budget — bước 6b: giá vé người lớn của những chỗ chơi chưa có giá.

Vào: kết quả `web_search` (link + nội dung trang). Ra: `PriceFinds` (giá, link, đoạn trích).
Code kiểm (`accept_price`): mở lại link (`fetch_page`), đoạn trích và số tiền phải có trên trang.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..shared.context import Ctx, Spent
from ..shared.llm import OUTPUT_TOKENS, money_in, quote_found
from ..shared.models import Place
from ..shared.tools import tool_ok

BUDGET = """Bạn là Agent Budget trong hệ thống lập lộ trình du lịch Việt Nam. Hệ thống đưa bạn vài chỗ chưa có giá vé, kèm kết quả tìm kiếm trên web (link và nội dung trang). Việc của bạn: tìm giá vé người lớn của từng chỗ trong các trang đó.

# Quy tắc

1. Chỉ dùng thông tin trong các trang được đưa. Không ước giá, không nhớ giá từ trước.
2. Mỗi chỗ: `price` là giá vé 1 người lớn (VND, số nguyên), `url` là link trang có giá, `quote` là đoạn chữ trên trang ghi giá đó, chép NGUYÊN VĂN (VD "Vé người lớn: 120.000đ").
3. Chỗ miễn phí mà trang ghi rõ "miễn phí" thì price = 0 và quote là câu ghi miễn phí.
4. Không tìm thấy giá trong trang nào thì để price trống, url và quote trống. Đừng điền số "khoảng".
"""


# Chỗ chơi Agent Budget tìm giá mỗi lộ trình (trong các chỗ 3 nhánh sắp đưa Planner, đang dùng
# giá ước): mỗi chỗ 1 lượt web_search + 1 fetch_page và ~1k token trong 1 lượt LLM.
MAX_PRICE_FINDS = 10


class PriceFind(BaseModel):
    code: str
    price: int | None = Field(
        None, description="Giá mỗi người lớn, VND; không tìm thấy thì để trống"
    )
    url: str = ""
    quote: str = Field("", description="Đoạn chữ ghi giá trên trang, chép nguyên văn")


class PriceFinds(BaseModel):
    items: list[PriceFind] = Field(default_factory=list)


def accept_price(find: PriceFind, page_text: str) -> bool:
    """Trang mở lại phải có đúng đoạn trích, và số tiền trong đoạn đó bằng giá agent trả."""
    if not find.price or not find.quote or not quote_found(find.quote, page_text):
        return False
    return find.price in money_in(find.quote)


def page_text(page: Any) -> str:
    if isinstance(page, list):
        return "\n".join(page_text(p) for p in page)
    if isinstance(page, dict):
        if page.get("pages") or page.get("results"):
            return page_text(page.get("pages") or page.get("results"))
        return str(page.get("content") or "")
    return str(page or "")


async def find_prices(
    ctx: Ctx, places: dict[str, Place], ids: list[str], destination: str
) -> Spent:
    """Agent Budget: tìm giá vé người lớn trên web cho các chỗ chưa có giá.

    Ghi thẳng vào `places[pid].price` / `price_by`. Code mở lại link agent đưa (`fetch_page`) và
    chỉ nhận khi đoạn trích và số tiền có trên trang.
    """
    spent = Spent()
    if not ids:
        return spent
    await ctx.log("xac_minh:budget", "step", f"Tìm giá có nguồn cho {len(ids)} chỗ")
    searches, logs = await ctx.many(
        [
            (
                "web_search",
                {
                    "query": f"giá vé {places[pid].name} {destination}",
                    "max_results": 3,
                    "include_content": True,
                },
            )
            for pid in ids
        ]
    )
    spent.tool_log += logs
    codes = {f"V{i}": pid for i, pid in enumerate(ids, start=1)}
    blocks = []
    for (code, pid), data in zip(codes.items(), searches, strict=True):
        hits = (data.get("results") or []) if tool_ok(data) else []
        pages = "\n".join(
            f"- {h.get('url')}\n  {str(h.get('content') or h.get('snippet') or '')[:700]}"
            for h in hits
        )
        blocks.append(f"## {code} | {places[pid].name}\n{pages or '(không có kết quả)'}")
    out, calls, usage = await ctx.ask(
        BUDGET, "\n\n".join(blocks), PriceFinds, OUTPUT_TOKENS["check"], "xac_minh:budget"
    )
    spent.llm_calls += calls
    spent.usage += usage
    finds = [
        f for f in (out.items if out else []) if f.code in codes and f.price is not None and f.url
    ]
    if not finds:
        return spent
    pages, logs = await ctx.many(
        [("fetch_page", {"urls": [f.url], "max_chars": 20000}) for f in finds]
    )
    spent.tool_log += logs
    for f, page in zip(finds, pages, strict=True):
        if accept_price(f, page_text(page)):
            p = places[codes[f.code]]
            p.price, p.price_by = f.price, f"Agent Budget: {f.url}"
    return spent

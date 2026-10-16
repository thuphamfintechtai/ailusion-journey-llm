"""Agent Budget — bước 6b: giá vé người lớn của những chỗ chơi chưa có giá.

Agent KHÔNG gọi tool. Code làm hết phần tool, agent chỉ đọc và trích:
1. Code `web_search` mỗi chỗ 1 lượt ("giá vé <tên> <điểm đến>", 3 kết quả).
2. Agent (1 lượt LLM cho mọi chỗ) đọc đoạn tóm tắt Tavily (`snippet`, đoạn hợp câu tìm nhất) +
   vài đoạn quanh số tiền trong trang (`price_windows`), trả `PriceFinds` (giá, link, đoạn trích).
3. Code lọc (`trusted_finds`): link phải là kết quả tìm của chính chỗ đó; một câu trích dùng cho
   2 chỗ thì bỏ cả hai (không rõ của chỗ nào).
4. Code mở lại link (`fetch_page`) và chỉ nhận khi đoạn trích có trên trang (bỏ định dạng markdown
   rồi so), số tiền có trong đoạn trích (`accept_price`; miễn phí thì đoạn trích phải ghi miễn
   phí), và trang có nhắc tên chỗ (`name_on_page`). Nhận thì ghi `price_by = "Agent Budget: <link>"` — giá thật; không nhận thì chỗ đó giữ
   giá ước ("ước …", coordinator/score.fill_price_estimates).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..shared.context import Ctx, Spent
from ..shared.llm import OUTPUT_TOKENS, money_in, norm, plain_text, quote_found
from ..shared.models import BRANCH_CUT, Place, _plain
from ..shared.tools import tool_ok

BUDGET = """Bạn là Agent Budget trong hệ thống lập lộ trình du lịch Việt Nam. Hệ thống đưa bạn vài chỗ chơi chưa có giá vé, mỗi chỗ kèm tối đa 3 trang tìm được trên web (link + các đoạn chữ trên trang, ưu tiên đoạn có số tiền). Bạn không gọi được công cụ nào; hệ thống tự mở lại link bạn đưa để kiểm đoạn trích.

# Quy tắc

1. Chỉ dùng các đoạn được đưa. Không ước giá, không nhớ giá từ trước.
2. Giá phải nói về CHÍNH chỗ đó (đúng tên). Không lấy giá của chỗ khác trên cùng trang, giá combo / tour / vé xe / gửi xe / thuê đồ / giá phòng. Vé dùng chung nhiều điểm mà trang nói rõ dùng để vào chỗ đó (VD vé tham quan phố cổ Hội An gồm Chùa Cầu) thì là giá vào cửa của chỗ đó.
3. `price`: giá vé 1 người lớn khách Việt Nam, VND, số nguyên — đúng một con số có trong `quote`. Trang ghi khoảng hay nhiều mức ("80.000 – 150.000 đ") thì chọn đúng mức người lớn khách Việt, KHÔNG tính số giữa.
4. `quote`: một đoạn liền trên trang, chép NGUYÊN VĂN (đúng chữ, đúng dấu), có con số đó (VD "Vé tham quan: 80.000 VNĐ / người").
5. Trang ghi rõ vào cửa không mất tiền ("miễn phí", "không mất phí", "không thu phí"): price = 0, quote là câu ghi điều đó.
6. Không chắc, các trang ghi mâu thuẫn mà không rõ mức nào đúng, hoặc không thấy giá: price = null, url và quote để chuỗi rỗng. Không điền số "khoảng".
7. `url` phải là một trong các link của chính mã đó.
8. Trả ĐỦ mọi mã được đưa (V1, V2, …), mỗi mã 1 dòng — mã không có giá thì price = null.
"""


# Chỗ chơi Agent Budget tìm giá mỗi lộ trình (trong các chỗ 3 nhánh sắp đưa Planner, đang dùng
# giá ước): mỗi chỗ 1 lượt web_search, 1 lượt LLM cho mọi chỗ, fetch_page cho link agent đưa.
MAX_PRICE_FINDS = 10
# Đoạn chữ đưa agent mỗi kết quả tìm (số tạm, "tu_dat"): đoạn tóm tắt Tavily tối đa
# SNIPPET_CHARS + tối đa PRICE_WINDOWS đoạn quanh số tiền, mỗi bên WINDOW_CHARS ký tự. Trước đây đưa
# 700 ký tự ĐẦU trang — toàn menu, logo, ảnh base64, chỗ ghi giá nằm sâu bên dưới nên agent không
# thấy giá (2 lộ trình Hội An / Huế 10/2026: 20 lượt tìm, 0 giá được nhận).
SNIPPET_CHARS = 600
PRICE_WINDOWS = 2
WINDOW_CHARS = 150
FETCH_CHARS = 20_000  # trần của fetch_page
MONEY_OR_FREE = re.compile(
    r"\d{1,3}(?:[.,]\d{3})+\s*(?:đ|₫|vnđ|vnd)?|\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|triệu|tr|đ|₫|vnđ|vnd)"
    r"(?!\w)|miễn phí|không (?:mất|thu|tốn)[^.;]{0,25}(?:phí|tiền)",
    re.IGNORECASE,
)
GAP = re.compile(r"\s*(?:\[\s*\.\.\.\s*\]|…|\.\.\.)\s*")
# "Miễn phí", "không mất bất cứ khoản phí nào", "không thu phí", "free".
FREE = re.compile(r"miễn phí|không (?:mất|thu|tốn)[^.;]{0,25}(?:phí|tiền)|(?<!\w)free(?!\w)")


class PriceFind(BaseModel):
    code: str
    price: int | None = Field(
        None, description="Giá mỗi người lớn, VND; miễn phí = 0; không tìm thấy thì để trống"
    )
    url: str = ""
    quote: str = Field("", description="Đoạn chữ ghi giá trên trang, chép nguyên văn")

    @field_validator("price", mode="before")
    @classmethod
    def blank_is_none(cls, v: Any) -> Any:
        """Model hay trả `price: ""` cho mã không có giá — không đổi về None thì cả câu trả lời
        hỏng (Pydantic từ chối), mọi giá khác cũng mất."""
        if isinstance(v, str):
            digits = v.replace(".", "").replace(",", "").strip()
            return int(digits) if digits.isdigit() else None
        return v


class PriceFinds(BaseModel):
    items: list[PriceFind] = Field(default_factory=list)


def flat(text: str) -> str:
    """Chữ trang web đưa agent: bỏ markdown, gộp khoảng trắng (agent chép nguyên văn đoạn này)."""
    return re.sub(r"\s+", " ", plain_text(text)).strip()


def price_windows(content: str, skip: str = "") -> list[str]:
    """Tối đa PRICE_WINDOWS đoạn quanh số tiền / "miễn phí" trong nội dung trang (đoạn chồng nhau
    thì gộp), bỏ đoạn đã nằm trong `skip` (đoạn tóm tắt)."""
    text = flat(content)
    spans: list[list[int]] = []
    for m in MONEY_OR_FREE.finditer(text):
        a, b = max(0, m.start() - WINDOW_CHARS), min(len(text), m.end() + WINDOW_CHARS)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    seen = norm(skip)
    out = [text[a:b] for a, b in spans if norm(text[a:b]) not in seen]
    return out[:PRICE_WINDOWS]


def hit_text(hit: dict[str, Any]) -> str:
    """Một kết quả tìm đưa agent: link, đoạn tóm tắt, các đoạn quanh số tiền."""
    snippet = flat(str(hit.get("snippet") or ""))[:SNIPPET_CHARS]
    parts = [snippet] if snippet else []
    parts += [f"… {w} …" for w in price_windows(str(hit.get("content") or ""), skip=snippet)]
    return f"- {hit.get('url')}\n" + "\n".join(f"  {p}" for p in parts or ["(không có nội dung)"])


def canon_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def trusted_finds(
    finds: list[PriceFind], codes: dict[str, str], hit_urls: dict[str, set[str]]
) -> list[PriceFind]:
    """Giá agent đưa đáng đem đi kiểm: đúng mã, có giá (0 = miễn phí), có link và link là một kết
    quả tìm của CHÍNH chỗ đó; một câu trích dùng cho 2 chỗ trở lên thì bỏ hết (agent gắn cùng
    "Vé tham quan: 80.000 VNĐ" của một bảo tàng cho 2 bảo tàng khác nhau)."""
    ok = [
        f
        for f in finds
        if f.code in codes
        and f.price is not None
        and f.url
        and canon_url(f.url) in hit_urls.get(f.code, set())
    ]
    used = Counter(norm(plain_text(f.quote)) for f in ok if f.quote)
    return [f for f in ok if f.quote and used[norm(plain_text(f.quote))] == 1]


def core_name(name: str, destination: str) -> str:
    """Phần lõi tên chỗ để tìm trên trang (bỏ dấu): bỏ phần sau " - ", "(", "|"… và tên điểm đến
    ("CSO Gallery Hội An" -> "cso gallery", "Chùa Huyền Không (Theravāda)" -> "chua huyen khong")."""
    core = " ".join(re.findall(r"\w+", _plain(BRANCH_CUT.split(name)[0])))
    dest = " ".join(re.findall(r"\w+", _plain(destination)))
    trimmed = " ".join(core.replace(dest, " ").split()) if dest else core
    return trimmed or core


def name_on_page(name: str, destination: str, page: str) -> bool:
    """Trang ghi giá phải nhắc tên chỗ (lõi tên liền một cụm, bỏ dấu): agent từng gắn vé phố cổ Hội
    An 80.000đ cho Chợ Bà Lê từ trang không hề nhắc Chợ Bà Lê. So cả cụm chứ không từng chữ: tên
    Việt toàn âm tiết ngắn ("chợ", "bà", "lê") trùng chữ thường ("dành cho", "lễ")."""
    core = core_name(name, destination)
    words = " ".join(re.findall(r"\w+", _plain(plain_text(page))))
    return bool(core) and f" {core} " in f" {words} "


def accept_price(find: PriceFind, page_text: str) -> bool:
    """Trang mở lại phải có đoạn trích (so sau khi bỏ markdown), và số tiền trong đoạn đó bằng giá
    agent trả; giá 0 thì đoạn trích phải ghi miễn phí."""
    if find.price is None or not find.quote:
        return False
    # Đoạn tóm tắt Tavily nối các đoạn bằng "[...]" và agent hay chép cả dấu đó: mỗi đoạn phải có
    # nguyên văn trên trang.
    page = plain_text(page_text)
    parts = [x for x in GAP.split(find.quote) if x.strip()]
    if not parts or not all(quote_found(plain_text(x), page) for x in parts):
        return False
    if find.price == 0:
        return FREE.search(norm(find.quote)) is not None
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

    Ghi thẳng vào `places[pid].price` / `price_by` những giá code kiểm lại được trên trang."""
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
    blocks, hit_urls = [], {}
    for (code, pid), data in zip(codes.items(), searches, strict=True):
        hits = (data.get("results") or []) if tool_ok(data) else []
        hit_urls[code] = {canon_url(str(h.get("url") or "")) for h in hits if h.get("url")}
        pages = "\n".join(hit_text(h) for h in hits)
        blocks.append(f"## {code} | {places[pid].name}\n{pages or '(không có kết quả)'}")
    out, calls, usage = await ctx.ask(
        BUDGET, "\n\n".join(blocks), PriceFinds, OUTPUT_TOKENS["check"], "xac_minh:budget"
    )
    spent.llm_calls += calls
    spent.usage += usage
    finds = trusted_finds(list(out.items if out else []), codes, hit_urls)
    if not finds:
        await ctx.log("xac_minh:budget", "finding", f"Không có giá kiểm được ({len(ids)} chỗ)")
        return spent
    urls = list(dict.fromkeys(f.url for f in finds))  # mỗi link mở 1 lần
    pages, logs = await ctx.many(
        [("fetch_page", {"urls": [u], "max_chars": FETCH_CHARS}) for u in urls]
    )
    spent.tool_log += logs
    text_of = {u: page_text(p) for u, p in zip(urls, pages, strict=True)}
    accepted = 0
    for f in finds:
        p = places[codes[f.code]]
        text = text_of[f.url]
        if accept_price(f, text) and name_on_page(p.name, destination, text):
            p.price, p.price_by = f.price, f"Agent Budget: {f.url}"
            accepted += 1
    await ctx.log(
        "xac_minh:budget",
        "finding",
        f"Nhận {accepted}/{len(ids)} giá có nguồn (agent đưa {len(finds)}, code kiểm lại trên trang)",
    )
    return spent

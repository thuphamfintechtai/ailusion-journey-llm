"""Agent Experience — bước 6a: chỗ này có hợp mục đích chuyến đi không?

Vào: tên, loại, mô tả và review (`get_place_reviews`, DB trước) của những chỗ không chứa từ khoá
mục đích. Ra: `PurposeChecks`. Code kiểm (`accept_purpose`): câu trích phải nằm nguyên văn trong
tên / mô tả / review của chính chỗ đó; khen chung chung không đủ làm bằng.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..shared.context import Ctx, Spent
from ..shared.form import PURPOSE_NAMES
from ..shared.llm import OUTPUT_TOKENS, quote_found
from ..shared.models import Place
from ..shared.tools import tool_ok

EXPERIENCE = """Bạn là Agent Experience trong hệ thống lập lộ trình du lịch Việt Nam. Hệ thống đưa bạn vài chỗ mà loại trên Google không cho biết rõ chỗ đó dùng để làm gì. Việc của bạn: đọc mô tả và review của từng chỗ, trả lời chỗ đó có hợp với mục đích chuyến đi không.

# Quy tắc

1. Trả lời đúng mọi mã được đưa, không thêm mã khác.
2. `matches_purpose` = true chỉ khi mô tả / review cho thấy rõ chỗ đó phục vụ một mục đích của chuyến. Ghi `purpose` là mã mục đích đó.
3. `quote`: câu làm bằng, chép NGUYÊN VĂN (một đoạn liền, đúng chữ, đúng dấu) từ mô tả hoặc review của chính chỗ đó. Ưu tiên câu trong review nói khách đến đó để làm gì (VD "ra đây ngắm hoàng hôn rất đẹp", "món mì Quảng ở đây ngon"). Không có câu nào làm bằng thì matches_purpose = false và để quote trống.
4. Review khen chung chung ("đẹp", "đáng đi") không đủ làm bằng cho một mục đích cụ thể.
5. Không cho điểm, không so chỗ này với chỗ khác.

# Ví dụ

Quán Y, loại "Cửa hàng", review có câu "nổi tiếng với mì Quảng gia truyền", mục đích chuyến có food -> matches_purpose = true, purpose = food, quote = "nổi tiếng với mì Quảng gia truyền".
"""


MAX_PURPOSE_CHECKS = 8
REVIEWS_PER_PLACE = 8  # review đưa Agent Experience mỗi chỗ (1 trang get_place_reviews)
REVIEW_CHARS = 300
# Đọc review đã lưu trong DB trước; DB dưới 3 review mới gọi SerpApi.
REVIEW_ARGS = {"sort_by": "relevant", "source": "auto"}


class PurposeCheck(BaseModel):
    code: str = Field(description="Mã chỗ, VD 'X3'")
    matches_purpose: bool
    purpose: str | None = Field(None, description="Mã mục đích hợp, VD 'food'")
    quote: str = Field("", description="Câu trong mô tả / review làm bằng, chép nguyên văn")


class PurposeChecks(BaseModel):
    items: list[PurposeCheck] = Field(default_factory=list)


def accept_purpose(check: PurposeCheck, evidence: str) -> bool:
    """Chỉ nhận "hợp mục đích" khi câu trích có trong mô tả / review của chính chỗ đó."""
    return check.matches_purpose and bool(check.quote) and quote_found(check.quote, evidence)


async def check_purposes(
    ctx: Ctx, places: dict[str, Place], ids: list[str], purposes: list[str]
) -> Spent:
    """Agent Experience: đọc review từng chỗ, trả lời chỗ có hợp mục đích chuyến đi không.

    Ghi thẳng vào `places[pid].matches_purpose` / `match_by`. Chỉ nhận "hợp" khi câu trích nằm
    nguyên văn trong tên / mô tả / review của chính chỗ đó.
    """
    spent = Spent()
    if not ids:
        return spent
    await ctx.log("xac_minh:experience", "step", f"Xem {len(ids)} chỗ có hợp mục đích không")
    codes = {f"X{i}": pid for i, pid in enumerate(ids, start=1)}
    # Mô tả thường quá ngắn để biết chỗ dùng làm gì, nên đọc review. source="auto": review đã
    # lưu trong DB trước, DB dưới 3 review mới gọi SerpApi (1 lượt quota).
    fetched, logs = await ctx.many(
        [("get_place_reviews", {"place_id": pid, **REVIEW_ARGS}) for pid in ids]
    )
    spent.tool_log += logs
    evidence: dict[str, str] = {}
    blocks = []
    for (code, pid), data in zip(codes.items(), fetched, strict=True):
        p = places[pid]
        reviews = [
            str(r.get("text") or "").strip()
            for r in ((data.get("reviews") or []) if tool_ok(data) else [])
            if r.get("text")
        ][:REVIEWS_PER_PLACE]
        evidence[pid] = "\n".join([p.name, p.summary, *reviews])
        body = "\n".join(f"  - {t[:REVIEW_CHARS]}" for t in reviews) or "  (không có review)"
        blocks.append(
            f"## {code} | {p.name} | {p.category}\n"
            f"Mô tả: {p.summary[:300] or '(không có)'}\nReview:\n{body}"
        )
    human = (
        "# Mục đích chuyến đi\n"
        + ", ".join(f"{k} ({PURPOSE_NAMES[k]})" for k in purposes)
        + "\n\n# Các chỗ\n"
        + "\n\n".join(blocks)
    )
    out, calls, usage = await ctx.ask(
        EXPERIENCE, human, PurposeChecks, OUTPUT_TOKENS["check"], "xac_minh:experience"
    )
    spent.llm_calls += calls
    spent.usage += usage
    answered = {c.code: c for c in (out.items if out else [])}
    for code, pid in codes.items():
        p = places[pid]
        check = answered.get(code)
        matched = bool(check) and accept_purpose(check, evidence[pid])  # type: ignore[arg-type]
        p.matches_purpose = matched
        p.match_by = f"Agent Experience: “{check.quote}”" if matched and check else None
    return spent

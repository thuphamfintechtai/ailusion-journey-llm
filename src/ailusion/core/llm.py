"""Chọn và tạo chat model. Dự án dùng Gemini và OpenAI, không dùng Anthropic.

Tên model theo dạng "provider:model" của `init_chat_model`:

    openai:gpt-4.1-mini                (mặc định — rẻ, đủ cho phần giải thích)
    google_genai:gemini-flash-latest
    google_genai:gemini-2.5-pro

Đặt LLM_MODEL trong `.env` để đổi, hoặc truyền thẳng tên vào `get_chat_model("...")`.
"""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from .settings import get_settings

DEFAULT_CHAT_MODEL = "openai:gpt-4.1-mini"

# Provider -> (biến môi trường chứa khoá, cách lấy khoá từ Settings)
_REQUIRED_KEY = {
    "google_genai": ("GEMINI_API_KEY", lambda s: s.llm.gemini_api_key),
    "openai": ("OPENAI_API_KEY", lambda s: s.llm.openai_api_key),
}


class MissingApiKeyError(RuntimeError):
    """Thiếu khoá API cho provider đang chọn — báo rõ cần điền biến nào."""


def message_text(content: object) -> str:
    """Lấy phần chữ của nội dung message.

    Gemini trả `content` là danh sách content block, không phải chuỗi:

        [{"type": "text", "text": "Xin chào!", "index": 0},
         {"type": "text", "text": "", "extras": {"signature": "..."}}]

    OpenAI thì trả thẳng chuỗi. Hàm này nhận cả hai, bỏ qua block không phải chữ
    (chữ ký suy luận, ảnh, ...) để không lẫn dữ liệu rác vào câu trả lời.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return str(content)


@lru_cache
def get_chat_model(
    model: str | None = None, *, max_tokens: int | None = None, max_retries: int = 2
) -> BaseChatModel:
    """Chat model dùng chung (nhớ lại theo tham số, không tạo lại mỗi lượt gọi).

    - `max_tokens`: trần độ dài câu trả lời — chặn model viết lan man, tốn tiền đầu ra.
    - `max_retries`: gặp giới hạn tần suất thì thử lại; mặc định 6 lần của SDK làm request treo
      gần 1 phút và có thể nhân đôi chi phí.
    - Luôn tắt chế độ suy luận (thinking): token suy luận tính tiền như token đầu ra mà người
      dùng không thấy. gpt-4.1-mini vốn không suy luận; đổi sang model có suy luận (Gemini 2.5,
      o-series, gpt-5) thì ép về mức thấp nhất.
    """
    name = model or get_settings().llm.chat_model or DEFAULT_CHAT_MODEL
    _require_api_key(name)
    kwargs: dict[str, object] = {"max_retries": max_retries, **no_thinking(name)}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    return init_chat_model(name, **kwargs)


def no_thinking(model: str) -> dict[str, object]:
    """Tham số tắt / hạ chế độ suy luận theo từng nhà cung cấp."""
    provider, _, name = model.partition(":")
    if provider == "google_genai":
        # Gemini 2.5 Flash tắt được hẳn; 2.5 Pro không tắt được, đặt mức thấp nhất cho phép.
        return {"thinking_budget": 128 if "pro" in name else 0, "include_thoughts": False}
    if provider == "openai" and name.startswith(("o1", "o3", "o4", "gpt-5")):
        return {"reasoning_effort": "minimal" if name.startswith("gpt-5") else "low"}
    return {}


def _require_api_key(model: str) -> None:
    """Dừng sớm với thông báo dễ hiểu, thay vì để SDK ném lỗi khó đoán lúc gọi."""
    provider = model.split(":", 1)[0]
    required = _REQUIRED_KEY.get(provider)
    if required is None:
        return  # provider lạ -> để SDK tự xử

    env_var, read_key = required
    if read_key(get_settings()) is None:
        raise MissingApiKeyError(f"Model '{model}' cần {env_var} trong .env (hiện đang trống).")

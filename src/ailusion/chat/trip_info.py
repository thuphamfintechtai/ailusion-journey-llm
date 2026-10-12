"""Đọc cuộc trò chuyện, lấy ra thông tin chuyến đi để điền sẵn form ở trang chat.

Người dùng kể dần qua nhiều lượt chat ("đi Đà Lạt", "nhà mình 2 vợ chồng với 1 bé",
"ngân sách tầm 15tr"). Khi họ mở form "Điền thông tin chuyến đi", form phải hiện sẵn những
gì đã nói, thay vì bắt điền lại từ đầu.

Giá trị của các trường lựa chọn khớp đúng với form bên frontend
(`ailusion-journey-fe/src/components/chat/trip-form-sheet.tsx`): đổi một bên thì đổi cả hai.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ailusion.core.llm import get_chat_model
from ailusion.core.settings import get_settings

from .prompts import TRIP_INFO, TRIP_UPDATE

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Câu trả lời trước của trợ lý chỉ cần đoạn đủ để hiểu "ok", "được" là đồng ý với cái gì.
LAST_AI_CHARS = 1500
# Form chỉ vài chục trường ngắn: trần token đầu ra thấp.
FORM_TOKENS = 500

Access = Literal[
    "Hạn chế leo dốc, bậc thang",
    "Có người dùng xe lăn",
    "Cần nghỉ trưa",
    "Ăn chay",
    "Dị ứng hải sản",
]
Interest = Literal[
    "Thiên nhiên",
    "Ẩm thực",
    "Văn hoá, lịch sử",
    "Chụp ảnh, check-in",
    "Cà phê",
    "Nghỉ dưỡng",
    "Phiêu lưu",
    "Biển",
    "Mua sắm",
    "Về đêm",
]


class TripInfo(BaseModel):
    """Thông tin chuyến đi đã nói trong cuộc trò chuyện. Trống = chưa nhắc tới."""

    persona: Literal["p1", "p2", "p3", "p4"] | None = Field(
        None, description="Nhóm người dùng, chỉ khi mô tả khớp rõ một nhóm"
    )
    destination: str | None = Field(None, description="Điểm đến chính, tiếng Việt có dấu")
    start_date: str | None = Field(None, description="Ngày đi, dạng YYYY-MM-DD")
    days: int | None = Field(None, description="Số ngày của chuyến đi")
    adults: int | None = Field(None, description="Số người lớn dưới 60 tuổi")
    children: int | None = Field(None, description="Số trẻ dưới 12 tuổi")
    seniors: int | None = Field(None, description="Số người lớn tuổi")
    # Đi cùng ai — chuyển sang ô group_type của form lập lộ trình, để pipeline không hỏi lại.
    group_type: Literal["solo", "couple", "friends", "colleagues", "family"] | None = Field(
        None, description="Đi cùng ai: một mình, cặp đôi, bạn bè, đồng nghiệp, gia đình"
    )
    budget_million: float | None = Field(
        None, description="Tổng ngân sách cả đoàn, đơn vị triệu đồng"
    )
    budget_hard: bool | None = Field(None, description="true = không được vượt ngân sách")
    access: list[Access] = Field(default_factory=list, description="Yêu cầu bắt buộc")
    interests: list[Interest] = Field(default_factory=list, description="Sở thích")
    transport: Literal["motorbike", "grab", "private", "public"] | None = Field(
        None,
        description="motorbike = xe máy tự lái, grab = Grab/taxi, "
        "private = xe riêng có tài xế, public = đi bộ và xe buýt",
    )
    pace: Literal["slow", "normal", "packed"] | None = Field(
        None, description="slow = thong thả, normal = vừa phải, packed = đi nhiều nơi"
    )

    def cleaned(self) -> TripInfo:
        """Bỏ số vô lý model đôi khi trả về (0 ngày, -1 người), để form không nhận rác."""
        return self.model_copy(
            update={
                "days": _within(self.days, 1, 30),
                "adults": _within(self.adults, 0, 20),
                "children": _within(self.children, 0, 20),
                "seniors": _within(self.seniors, 0, 20),
                "budget_million": self.budget_million
                if self.budget_million and self.budget_million > 0
                else None,
                "destination": (self.destination or "").strip() or None,
                "start_date": self.start_date if _is_date(self.start_date) else None,
                "access": list(dict.fromkeys(self.access)),
                "interests": list(dict.fromkeys(self.interests)),
            }
        )


class TripUpdate(TripInfo):
    """Form sau khi đọc thêm một tin nhắn, kèm việc người dùng có muốn lập lộ trình không."""

    wants_plan: bool = Field(
        False,
        description="true khi người dùng muốn lập lộ trình / lịch trình chi tiết ngay, "
        "hoặc đồng ý khi trợ lý vừa mời lập lộ trình",
    )


# Các mục bắt buộc để lập lộ trình (POST /plan) — thiếu thì trợ lý hỏi cho đủ rồi mới mở form.
# "party" = số người: chỉ cần nhắc tới một trong người lớn / trẻ em / người lớn tuổi.
REQUIRED: dict[str, str] = {
    "destination": "điểm đến",
    "start_date": "ngày đi",
    "days": "số ngày",
    "party": "số người đi",
    "budget_million": "ngân sách",
}


def missing_fields(info: TripInfo) -> list[str]:
    """Các mục bắt buộc còn trống, theo thứ tự hỏi trong REQUIRED."""
    missing = []
    for key in REQUIRED:
        if key == "party":
            if info.adults is None and info.children is None and info.seniors is None:
                missing.append(key)
        elif getattr(info, key) is None:
            missing.append(key)
    return missing


def merge(old: TripInfo, new: TripInfo) -> TripInfo:
    """Gộp form cũ với form mới đọc được: mục nào bản mới có thì lấy bản mới, trống thì giữ cũ.

    Danh sách (yêu cầu, sở thích) mới khác rỗng thì thay hẳn — LLM nhận form cũ và trả về
    form đầy đủ, nên danh sách mới đã gồm phần cũ còn giữ.
    """
    data = old.model_dump()
    for key, value in new.model_dump().items():
        if key not in data:
            continue
        if value is None or value == []:
            continue
        data[key] = value
    return TripInfo.model_validate(data).cleaned()


async def update_trip_info(
    current: TripInfo, message: str, last_ai: str | None = None
) -> TripUpdate:
    """Đọc thêm một tin nhắn của người dùng, trả về form đã cập nhật (một lượt LLM).

    Chỉ đưa form hiện tại + câu trả lời ngay trước của trợ lý + tin mới, không đọc lại cả
    cuộc trò chuyện — form đã lưu chính là phần tóm tắt các lượt trước.
    """
    llm = get_chat_model(
        get_settings().llm.form_model, max_tokens=FORM_TOKENS, max_retries=1
    ).with_structured_output(TripUpdate)
    today = datetime.now(VN_TZ).date().isoformat()
    parts = [f"Form hiện tại (JSON):\n{current.model_dump_json(exclude_none=True)}"]
    if last_ai:
        parts.append(f"Trợ lý vừa nói:\n{last_ai.strip()[:LAST_AI_CHARS]}")
    parts.append(f"Người dùng nói:\n{message.strip()}")
    result = await llm.ainvoke(
        [SystemMessage(TRIP_UPDATE.format(today=today)), HumanMessage("\n\n".join(parts))]
    )
    update = result if isinstance(result, TripUpdate) else TripUpdate.model_validate(result)
    merged = merge(current, update)
    return TripUpdate(**merged.model_dump(), wants_plan=update.wants_plan)


async def extract_trip_info(messages: Sequence[tuple[str, str]]) -> TripInfo:
    """`messages`: các cặp (role, content), role là "human" hoặc "ai", cũ nhất trước."""
    transcript = transcript_text(messages)
    if not transcript:
        return TripInfo()
    llm = get_chat_model(max_tokens=FORM_TOKENS, max_retries=1).with_structured_output(TripInfo)
    today = datetime.now(VN_TZ).date().isoformat()
    result = await llm.ainvoke(
        [SystemMessage(TRIP_INFO.format(today=today)), HumanMessage(transcript)]
    )
    info = result if isinstance(result, TripInfo) else TripInfo.model_validate(result)
    return info.cleaned()


def transcript_text(messages: Sequence[tuple[str, str]]) -> str:
    speaker = {"human": "Người dùng", "ai": "Trợ lý"}
    lines = [
        f"{speaker[role]}: {content.strip()}"
        for role, content in messages
        if role in speaker and content.strip()
    ]
    return "\n\n".join(lines)


def _within(value: int | None, low: int, high: int) -> int | None:
    return value if value is not None and low <= value <= high else None


def _is_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True

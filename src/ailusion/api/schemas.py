"""Kiểu dữ liệu vào/ra của API — đây là hợp đồng giữa backend và service này."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ailusion.chat.trip_info import TripInfo
from ailusion.itinerary import ItineraryResponse


class Message(BaseModel):
    role: str = Field(description="human | ai | system")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="Câu hỏi của người dùng")
    thread_id: str | None = Field(
        default=None,
        description=(
            "Mã hội thoại. Bỏ trống -> tạo mới. Gửi lại mã cũ để nói tiếp cùng một hội thoại "
            "(lịch sử đã nằm trong checkpointer, backend không cần gửi lại)."
        ),
    )
    user_id: str | None = Field(default=None, description="Mã người dùng, để gắn vào trace")
    session_id: str | None = Field(
        default=None, description="Gom nhiều hội thoại vào một session trên Langfuse"
    )
    history: list[Message] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Các tin nhắn trước đó do backend lưu (cũ nhất trước). Chỉ dùng khi bộ nhớ của "
            "thread_id đã hết hạn, để trợ lý nói tiếp được một hội thoại cũ; còn bộ nhớ thì bỏ qua."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"message": "Đi Đà Lạt 3 ngày 2 người lớn 1 bé, ngân sách 15tr thì nên đi đâu?"}
            ]
        }
    }


class MissingField(BaseModel):
    field: str = Field(description="destination | start_date | days | party | budget_million")
    label: str = Field(description='Tên tiếng Việt, ví dụ "ngày đi"')


class TripState(BaseModel):
    """Form chuyến đi của hội thoại, lọc từ tin nhắn và lưu ở Postgres."""

    info: TripInfo
    missing: list[MissingField] = Field(description="Mục bắt buộc còn thiếu để lập lộ trình")
    wants_plan: bool = Field(description="Người dùng muốn lập lộ trình, trợ lý đang hỏi cho đủ")
    open_form: bool = Field(
        False, description="True ở đúng lượt form vừa đủ và người dùng muốn lập lộ trình"
    )


class ChatReply(BaseModel):
    thread_id: str = Field(description="Gửi lại mã này ở lượt sau để nối tiếp hội thoại")
    reply: str
    trip: TripState | None = None


class ChatPlanReply(BaseModel):
    """Lộ trình lập từ form của trang chat, kèm hai tin nhắn đã nối vào hội thoại."""

    plan: ItineraryResponse
    request_text: str = Field(description="Form đã xác nhận, viết thành tin nhắn của người dùng")
    reply: str = Field(description="Lộ trình dạng markdown, tin nhắn của trợ lý")


class TripConfirm(BaseModel):
    """Người dùng xác nhận form (bấm Lập lộ trình): lưu đúng bản họ đã xem và sửa."""

    info: TripInfo
    user_id: str | None = Field(None, description="Có thì cập nhật yêu cầu bắt buộc vào hồ sơ")


class TripInfoRequest(BaseModel):
    messages: list[Message] = Field(
        min_length=1,
        max_length=60,
        description="Cuộc trò chuyện (cũ nhất trước), để lấy ra thông tin chuyến đi",
    )


class History(BaseModel):
    thread_id: str
    messages: list[Message]

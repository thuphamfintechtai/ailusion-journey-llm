"""Kiểu dữ liệu vào/ra của API — đây là hợp đồng giữa backend và service này."""

from __future__ import annotations

from pydantic import BaseModel, Field


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

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"message": "Đi Đà Lạt 3 ngày 2 người lớn 1 bé, ngân sách 15tr thì nên đi đâu?"}
            ]
        }
    }


class ChatReply(BaseModel):
    thread_id: str = Field(description="Gửi lại mã này ở lượt sau để nối tiếp hội thoại")
    reply: str


class Message(BaseModel):
    role: str = Field(description="human | ai | system")
    content: str


class History(BaseModel):
    thread_id: str
    messages: list[Message]

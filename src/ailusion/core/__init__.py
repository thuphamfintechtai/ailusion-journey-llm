"""Hạ tầng dùng chung, mỗi việc một file:

settings.py   biến môi trường -> Settings
llm.py        tạo chat model (OpenAI / Gemini), lỗi thiếu khoá
mcp.py        nạp tool từ MCP server, gọi tool trả JSON
redis.py      Redis client dùng chung + quy ước key
memory.py     checkpointer Redis (ngắn hạn) + store Postgres (dài hạn)
agent_log.py  stream nhật ký agent cho trang web xem theo thời gian thực
tracing.py    LangSmith + Langfuse, config mỗi lượt chạy graph
pii.py        che dữ liệu cá nhân trước khi gửi trace
"""

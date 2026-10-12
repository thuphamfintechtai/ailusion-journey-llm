"""Ailusion Journey — dịch vụ LLM (LangGraph) cho trợ lý du lịch.

api/        FastAPI: nhận request từ backend, gọi graph, trả kết quả
chat/       luồng chat: lọc tin nhắn vào form chuyến đi, trả lời
itinerary/  pipeline lập lộ trình 17 bước (form -> 3 phương án)
core/       hạ tầng dùng chung: settings, LLM, MCP tool, Redis, bộ nhớ, tracing
"""

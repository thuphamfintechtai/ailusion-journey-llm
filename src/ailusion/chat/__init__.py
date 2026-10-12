"""Luồng chat: lọc tin nhắn vào form chuyến đi, rồi trả lời.

graph.py       graph chat: loc_tin (cập nhật form) -> tra_loi (trả lời, stream được)
filter.py      bộ lọc luật: tin nào đáng gọi LLM cập nhật form
trip_info.py   form chuyến đi trong chat (TripInfo) + LLM đọc tin nhắn để điền
trip_store.py  lưu / đọc form đang bàn (Postgres store)
prompts.py     prompt của chat
"""

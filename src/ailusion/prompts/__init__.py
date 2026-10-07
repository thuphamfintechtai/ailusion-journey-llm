"""Mọi prompt của dự án — mỗi file .py ở đây chứa prompt của một chỗ dùng.

    from ailusion.prompts.plan_explain import PLAN_EXPLAIN
    from ailusion.prompts.research import SLOT
    SLOT.format(today="2026-10-17", profile="null")

Prompt có biến thì viết `{ten_bien}` và gọi `.format(...)` ở chỗ dùng. Đầu mỗi file ghi prompt
dùng ở đâu, nhận gì, trả gì — sửa prompt chỉ cần sửa file ở đây, không đụng code nghiệp vụ.
"""

"""Chuẩn hoá yêu cầu: đọc kết quả tool MCP thành số liệu dùng được. Hàm thuần, không gọi mạng.

Tool trả về dict có `status`; lỗi (sai tham số, hết quota) có thể là chuỗi. Hàm nào ở đây
cũng nhận được cả hai, đọc không ra thì trả None thay vì ném lỗi.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

_URL = re.compile(r"https?://[^\s\"'<>]+")


def ok(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "success"


def budget_vnd(result: Any) -> int | None:
    """Tổng ngân sách VND từ `parse_money(convert_to_vnd=True)`. Ngoại tệ đã quy đổi sẵn."""
    if not ok(result):
        return None
    total = 0.0
    for amount in result.get("amounts", []):
        if amount.get("currency") == "VND":
            total += amount.get("value_min") or 0
        else:
            total += amount.get("vnd_min") or 0
    return round(total) or None


def end_date(start: str | None, days: int | None) -> str | None:
    """Ngày về: đi 3 ngày từ 10/10 thì về 12/10."""
    if not start or not days:
        return None
    try:
        return (date.fromisoformat(start) + timedelta(days=days - 1)).isoformat()
    except ValueError:
        return None


def location(result: Any) -> dict[str, Any] | None:
    """Toạ độ và tỉnh (theo đơn vị hành chính mới) từ `geocode_vn`."""
    if not ok(result) or not result.get("results"):
        return None
    top = result["results"][0]
    return {
        "name": top.get("name"),
        "province": (top.get("admin_new") or {}).get("province"),
        "address": top.get("address_new"),
        "lat": top.get("lat"),
        "lng": top.get("lng"),
    }


def calendar(result: Any) -> dict[str, Any] | None:
    """Ngày nghỉ, lễ và mức cao điểm (0-3) trong khoảng ngày đi, từ `vn_calendar`."""
    if not ok(result):
        return None
    summary = result.get("summary") or {}
    return {
        "days_off": summary.get("days_off"),
        "holidays": summary.get("holidays", []),
        "max_peak_level": summary.get("max_peak_level"),
        "day_off_blocks": result.get("day_off_blocks", []),
    }


def missing_essentials(slots: dict[str, Any]) -> list[str]:
    """Thiếu thì không nghiên cứu được, phải hỏi lại người dùng."""
    return ["điểm đến"] if not slots.get("destination") else []


def assumptions(slots: dict[str, Any]) -> list[str]:
    """Thứ người dùng không nói nhưng vẫn chạy tiếp được, ghi lại để giải thích sau."""
    notes = []
    if not slots.get("start_date"):
        notes.append("Chưa có ngày đi — tạm xem các ngày tới.")
    if not slots.get("days"):
        notes.append("Chưa có số ngày — tạm tính 3 ngày.")
    if slots.get("adults") is None:
        notes.append("Chưa rõ số người — tạm tính 2 người lớn.")
    if not slots.get("budget_text"):
        notes.append("Chưa có ngân sách — dò giá ở mức trung bình.")
    return notes


def trip_days(slots: dict[str, Any]) -> int:
    return slots.get("days") or 3


def collect_urls(results: list[dict[str, Any]], limit: int = 10) -> list[str]:
    """URL xuất hiện trong kết quả tool, bỏ trùng, giữ thứ tự — để chấm tin cậy nguồn."""
    seen: dict[str, None] = {}
    for result in results:
        for url in _URL.findall(result.get("data") or ""):
            seen.setdefault(url.rstrip(".,)"), None)
    return list(seen)[:limit]


def step_key(tool: str, args: dict[str, Any]) -> str:
    """Định danh một lượt gọi để planner không gọi lại đúng bước đã chạy."""
    return f"{tool}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"


def compact(data: Any, limit: int) -> str:
    """Kết quả tool thành chuỗi JSON gọn, cắt bớt nếu dài — để đưa vào prompt Phản biện."""
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"

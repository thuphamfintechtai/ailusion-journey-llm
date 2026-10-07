"""Bốn nhóm người dùng (P1–P4) và chiến lược lập lộ trình theo nhóm.

Người dùng chọn nhóm trong form. Node `chien_luoc` gọi LLM với prompt riêng của nhóm
(prompts/strategy/) để ra `Strategy` cho đúng chuyến này, rồi `clamp()` ép mọi giá trị vào
khoảng cho phép của nhóm — LLM chỉ tinh chỉnh trong khuôn, không đổi tính cách của nhóm.
LLM lỗi thì dùng `PERSONAS[...]["default"]`.

P1 Khám phá tiết kiệm     — nhiều trải nghiệm trong ngân sách hạn; hy sinh thoải mái, sang trọng
P2 Thoải mái, dễ tiếp cận — nhẹ nhàng, an toàn, không mệt; hy sinh số điểm, chi phí
P3 Thư giãn, trải nghiệm  — ít điểm nhưng chất lượng cao; hy sinh chi phí, số điểm
P4 Quỹ thời gian hạn chế  — lấp giờ trống, không trễ lịch chính; hy sinh một phần chi phí
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from .request import (
    INTEREST_NAMES,
    MOBILITY_NAMES,
    MOBILITY_STOPS,
    RAIN_NAMES,
    TRANSPORT_NAMES,
    Interest,
    PlanRequest,
    busy_minutes,
    to_minutes,
)


class SearchHint(BaseModel):
    query: str = Field(description="Từ khoá tìm địa điểm, VD 'chợ đêm', 'spa', 'quán chay'")
    why: str = Field(description="Vì sao nhóm này cần tìm thêm")


class Strategy(BaseModel):
    """Chiến lược cho một chuyến đi cụ thể. Mọi số đều bị ép vào khoảng của nhóm."""

    summary: str = Field(description="1–2 câu: chuyến này sẽ được lập theo hướng nào")
    stops_per_day: int = Field(ge=1, le=6, description="Số điểm tham quan mỗi ngày (ngày đủ giờ)")
    buffer_min: int = Field(ge=0, le=60, description="Phút dự phòng cộng vào mỗi chặng di chuyển")
    hotel_tier: float = Field(ge=0, le=1, description="0 = chỗ ở rẻ nhất đạt chuẩn, 1 = tốt nhất")
    meal_vnd: int = Field(ge=40_000, le=600_000, description="Mỗi người mỗi bữa, VND")
    ticket_sensitivity: float = Field(ge=0, le=1, description="1 = rất ngại điểm có vé đắt")
    quality_over_quantity: float = Field(
        ge=0, le=1, description="1 = chỉ chọn nơi đánh giá rất cao, chấp nhận ít điểm"
    )
    max_travel_min: int = Field(ge=10, le=90, description="Một chặng di chuyển dài nhất, phút")
    rest_break_min: int = Field(ge=0, le=120, description="Nghỉ giữa ngày ngoài bữa trưa, phút")
    avoid_crowd: bool = Field(description="Tránh nơi thường rất đông")
    focus_tags: list[Interest] = Field(
        default_factory=list, max_length=3, description="Nhãn sở thích được ưu tiên thêm"
    )
    extra_searches: list[SearchHint] = Field(
        default_factory=list, max_length=3, description="Lượt tìm địa điểm bổ sung"
    )
    sacrifices: list[str] = Field(
        default_factory=list, max_length=3, description="Điều chấp nhận hy sinh trong chuyến này"
    )


# Khoảng cho phép của từng trường số theo nhóm: (thấp nhất, cao nhất).
Bounds = dict[str, tuple[float, float]]

PERSONAS: dict[str, dict[str, Any]] = {
    "P1": {
        "name": "Khám phá tiết kiệm",
        "goal": "Trải nghiệm nhiều nhất trong ngân sách có hạn",
        "priorities": "hiệu quả chi phí, đúng ngân sách, khám phá, ẩm thực, lịch dày",
        "sacrifices": "sự thoải mái, sang trọng",
        "example": "Nhóm bạn trẻ, ngân sách cứng",
        "bounds": {
            "stops_per_day": (4, 6),
            "buffer_min": (0, 15),
            "hotel_tier": (0.0, 0.3),
            "meal_vnd": (40_000, 150_000),
            "ticket_sensitivity": (0.5, 1.0),
            "quality_over_quantity": (0.0, 0.5),
            "max_travel_min": (20, 60),
            "rest_break_min": (0, 30),
        },
        "default": {
            "stops_per_day": 5,
            "buffer_min": 5,
            "hotel_tier": 0.1,
            "meal_vnd": 80_000,
            "ticket_sensitivity": 0.8,
            "quality_over_quantity": 0.2,
            "max_travel_min": 40,
            "rest_break_min": 0,
            "avoid_crowd": False,
        },
        # Bộ trọng số chọn phương án của nhóm (cùng tiêu chí với schedule.WEIGHT_SETS).
        "weights": {
            "interest": 25,
            "experience": 10,
            "budget": 30,
            "pace": 20,
            "weather": 5,
            "travel": 10,
            "cost_vnd": 25,
            "p_break": 5,
        },
    },
    "P2": {
        "name": "Thoải mái và dễ tiếp cận",
        "goal": "Đi nhẹ nhàng, an toàn, không mệt",
        "priorities": "thoải mái, dễ tiếp cận, nghỉ ngơi, độ tin cậy",
        "sacrifices": "số lượng điểm, chi phí",
        "example": "Gia đình có trẻ nhỏ, ông bà",
        "bounds": {
            "stops_per_day": (2, 3),
            "buffer_min": (15, 40),
            "hotel_tier": (0.4, 0.9),
            "meal_vnd": (100_000, 300_000),
            "ticket_sensitivity": (0.2, 0.6),
            "quality_over_quantity": (0.5, 1.0),
            "max_travel_min": (10, 30),
            "rest_break_min": (30, 120),
        },
        "default": {
            "stops_per_day": 3,
            "buffer_min": 25,
            "hotel_tier": 0.6,
            "meal_vnd": 180_000,
            "ticket_sensitivity": 0.4,
            "quality_over_quantity": 0.7,
            "max_travel_min": 25,
            "rest_break_min": 60,
            "avoid_crowd": True,
        },
        "weights": {
            "interest": 10,
            "experience": 10,
            "budget": 10,
            "pace": 20,
            "weather": 15,
            "travel": 25,
            "cost_vnd": 10,
            "p_break": 25,
        },
    },
    "P3": {
        "name": "Thư giãn và trải nghiệm",
        "goal": "Ít điểm nhưng chất lượng cao",
        "priorities": "trải nghiệm, thoải mái, cảnh đẹp, yên tĩnh",
        "sacrifices": "chi phí, số lượng điểm",
        "example": "Cặp đôi nghỉ dưỡng",
        "bounds": {
            "stops_per_day": (2, 3),
            "buffer_min": (10, 30),
            "hotel_tier": (0.7, 1.0),
            "meal_vnd": (200_000, 600_000),
            "ticket_sensitivity": (0.0, 0.3),
            "quality_over_quantity": (0.7, 1.0),
            "max_travel_min": (15, 45),
            "rest_break_min": (30, 90),
        },
        "default": {
            "stops_per_day": 3,
            "buffer_min": 15,
            "hotel_tier": 0.9,
            "meal_vnd": 300_000,
            "ticket_sensitivity": 0.1,
            "quality_over_quantity": 0.9,
            "max_travel_min": 35,
            "rest_break_min": 60,
            "avoid_crowd": True,
        },
        "weights": {
            "interest": 20,
            "experience": 35,
            "budget": 5,
            "pace": 10,
            "weather": 15,
            "travel": 10,
            "cost_vnd": 5,
            "p_break": 10,
        },
    },
    "P4": {
        "name": "Quỹ thời gian hạn chế",
        "goal": "Tận dụng tối đa khung giờ trống mà không trễ lịch chính",
        "priorities": "vừa khung giờ, tin cậy, thời gian dự phòng",
        "sacrifices": "một phần chi phí và trải nghiệm",
        "example": "Công tác kết hợp du lịch",
        "bounds": {
            "stops_per_day": (1, 4),
            "buffer_min": (20, 45),
            "hotel_tier": (0.3, 0.7),
            "meal_vnd": (100_000, 300_000),
            "ticket_sensitivity": (0.2, 0.6),
            "quality_over_quantity": (0.4, 0.8),
            "max_travel_min": (10, 25),
            "rest_break_min": (0, 15),
        },
        "default": {
            "stops_per_day": 2,
            "buffer_min": 30,
            "hotel_tier": 0.5,
            "meal_vnd": 150_000,
            "ticket_sensitivity": 0.4,
            "quality_over_quantity": 0.6,
            "max_travel_min": 20,
            "rest_break_min": 0,
            "avoid_crowd": False,
        },
        "weights": {
            "interest": 10,
            "experience": 10,
            "budget": 10,
            "pace": 10,
            "weather": 5,
            "travel": 25,
            "cost_vnd": 10,
            "p_break": 40,
        },
    },
}

PERSONA_WEIGHT_SET = "nhom"  # tên bộ trọng số của nhóm khi gửi sang score_options


def clamp(strategy: Strategy, persona: str) -> Strategy:
    """Ép mọi trường số vào khoảng của nhóm. Trả bản mới, không sửa bản gốc."""
    data = strategy.model_dump()
    for field, (lo, hi) in PERSONAS[persona]["bounds"].items():
        value = min(max(data[field], lo), hi)
        data[field] = int(round(value)) if isinstance(data[field], int) else round(value, 2)
    if persona in ("P2", "P3"):
        data["avoid_crowd"] = True  # yên tĩnh / an toàn là ưu tiên cố định của hai nhóm này
    return Strategy.model_validate(data)


def default_strategy(persona: str) -> Strategy:
    p = PERSONAS[persona]
    return Strategy(summary=f"Lộ trình theo nhóm {p['name']}: {p['goal'].lower()}.", **p["default"])


WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
PEAK_LEVELS = {
    0: "0 - ngày thường",
    1: "1 - cuối tuần",
    2: "2 - đông (dịp lễ, nghỉ dài)",
    3: "3 - rất đông, vé và phòng khan hiếm",
}


def budget_band(per_person_day: float) -> str:
    """Mốc ngân sách bình quân (đã gồm chỗ ở) — để prompt chiến lược hiệu chỉnh theo."""
    if per_person_day < 600_000:
        return "thấp (dưới 600.000 đ/người/ngày, đã gồm chỗ ở)"
    if per_person_day <= 1_500_000:
        return "trung bình (600.000–1.500.000 đ/người/ngày)"
    return "cao (trên 1.500.000 đ/người/ngày)"


def free_hours(req: PlanRequest) -> list[str]:
    """Giờ trống thật từng ngày = khung giờ trong form trừ lịch cố định."""
    t, out = req.time, []
    for d in range(req.basic.days):
        start, end = to_minutes(t.day_start), to_minutes(t.day_end)
        if d == 0:
            start = max(start, to_minutes(t.first_start))
        if d == req.basic.days - 1:
            end = min(end, to_minutes(t.last_end))
        busy = [f for f in t.fixed if f.day == d + 1]
        spans = [(to_minutes(f.start), to_minutes(f.end)) for f in busy]
        free = (end - start - busy_minutes(start, end, spans)) / 60
        day = req.basic.start_date + timedelta(days=d)
        text = (
            f"Ngày {d + 1} ({WEEKDAYS[day.weekday()]} {day:%d/%m}): "
            f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"
        )
        if busy:
            text += ", bận " + ", ".join(f"{f.start}–{f.end} ({f.label})" for f in busy)
        out.append(f"{text} → trống khoảng {max(0, free):.1f} giờ")
    return out


def trip_brief(req: PlanRequest, normalized: dict[str, Any]) -> dict[str, Any]:
    """Tóm tắt chuyến đi đưa cho LLM ở node chien_luoc.

    Những gì tính được bằng code thì tính sẵn (bình quân ngân sách, mốc ngân sách, giờ trống
    từng ngày, trần số điểm theo nhịp độ) để LLM chỉ việc cân nhắc, không phải làm toán.
    """
    b, bud, t, acc = req.basic, req.budget, req.time, req.access
    per_day = bud.total_vnd / max(1, b.people) / b.days
    access = [
        label
        for flag, label in (
            (acc.no_stairs, "hạn chế leo dốc, bậc thang"),
            (acc.wheelchair, "có người dùng xe lăn"),
            (acc.siesta, "cần nghỉ trưa 90 phút (hệ thống tự chèn sau bữa trưa)"),
            (acc.veg, "ăn chay"),
            (acc.no_seafood, "dị ứng hải sản"),
        )
        if flag
    ]
    care = []
    if b.children:
        care.append(f"{b.children} trẻ dưới 12 tuổi")
    if b.seniors:
        care.append(f"{b.seniors} người lớn tuổi")
    if acc.wheelchair:
        care.append("người dùng xe lăn")
    peak = normalized.get("max_peak_level")
    return {
        "nhom_nguoi_dung": f"{req.persona} {PERSONAS[req.persona]['name']}",
        "diem_den": b.destination,
        "tinh_thanh": normalized.get("province") or "chưa xác định",
        "ngay_di": f"{b.start_date:%d/%m/%Y} ({WEEKDAYS[b.start_date.weekday()]})",
        "so_ngay": b.days,
        "doan": f"{b.adults} người lớn, {b.children} trẻ dưới 12 tuổi, {b.seniors} người lớn tuổi",
        "nguoi_can_luu_y": care or ["không có"],
        "ngan_sach": f"{bud.total_vnd:,} đ".replace(",", ".")
        + (" (không được vượt)" if bud.hard else " (linh hoạt)"),
        "binh_quan_moi_nguoi_moi_ngay": f"{round(per_day, -3):,.0f} đ".replace(",", "."),
        "muc_ngan_sach": budget_band(per_day),
        "can_cho_o": "có" if bud.need_stay else "không, đoàn đã có chỗ ở",
        "gom_ve_di_ve": f"có, từ {bud.origin}" if bud.intercity else "không",
        "gio_trong_tung_ngay": free_hours(req),
        "lich_co_dinh": [f"ngày {f.day} {f.start}–{f.end}: {f.label}" for f in t.fixed]
        or ["không có"],
        "so_thich": [
            f"{INTEREST_NAMES[k]} [{k}] ({'rất thích' if v == 2 else 'thích'})"
            for k, v in req.interests.tags.items()
        ]
        or ["chưa chọn"],
        "tranh_dong": "tuyệt đối không đến nơi rất đông"
        if req.interests.crowd_hard
        else ("ưu tiên nơi vắng" if req.interests.avoid_crowd else "không yêu cầu"),
        "khi_mua": RAIN_NAMES[req.weather.rain]
        + (" (không xếp ngoài trời khi mưa từ 60%)" if req.weather.rain_hard else ""),
        "phuong_tien": TRANSPORT_NAMES[req.transport],
        "nhip_do_nguoi_dung_chon": f"{MOBILITY_NAMES[req.mobility]} "
        f"(trần {MOBILITY_STOPS[req.mobility]} điểm/ngày)",
        "tiep_can": access or ["không có yêu cầu riêng"],
        "ngay_le_trong_chuyen": normalized.get("holidays") or ["không có"],
        "muc_cao_diem": PEAK_LEVELS.get(peak, "chưa rõ") if peak is not None else "chưa rõ",
    }

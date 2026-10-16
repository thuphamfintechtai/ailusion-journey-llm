"""Mức chi của điểm đến: so ngân sách với mặt bằng giá, mỗi mức chi user chọn so một trung vị riêng.

Mặt bằng giá = tứ phân vị giá của CẢ điểm đến: `price_stats_vnd` của find_stays (phòng / đêm, mọi
chỗ ở có giá) và find_places loại "Nhà hàng" (mỗi người, mọi quán có giá), tính trước khi cắt
`limit`. Không lấy vài chỗ "phổ biến" nhất: toàn resort, nhà hàng đắt (Phú Quốc 2 người 4 ngày
từng ra "tối thiểu 25 triệu").

- Mỗi mức = phòng × số đêm × số phòng (2 người 1 phòng) + 1 bữa × số bữa bắt buộc × số người. Chưa
  gồm vé, đi lại, phát sinh: ngân sách dưới mức này là chắc thiếu, không báo nhầm.
- Tiết kiệm = trung vị nửa rẻ (p25), Thường gặp = trung vị, Sang = trung vị nửa đắt (p75).
- Mức chi "Bình thường" so ngân sách với Thường gặp, "Sang, không vượt ngân sách" với Sang; dưới cả
  Tiết kiệm thì cảnh báo. "Không giới hạn" không so ngân sách: ghi tổng ước của lịch, kèm các mức
  của điểm đến để tham khảo.

Chỉ để báo user (đầu kết quả, nhật ký bước 3), không dùng để lọc hay chọn chỗ.
"""

from __future__ import annotations

from typing import Any

from ..shared.form import TripForm, money, required_meals
from ..shared.models import TripProfile

# (mã, tên hiện cho user, phân vị trong `price_stats_vnd`, cách tính)
LEVELS = (
    ("saving", "Tiết kiệm", "p25", "trung vị nửa rẻ"),
    ("normal", "Thường gặp", "median", "trung vị"),
    ("luxury", "Sang", "p75", "trung vị nửa đắt"),
)
# Mức chi user chọn -> mức của điểm đến đem so ngân sách; "unlimited" không so.
STYLE_LEVEL = {"normal": "normal", "luxury_within_budget": "luxury"}


def price_levels(stays: dict[str, Any], food: dict[str, Any]) -> dict[str, dict[str, int]] | None:
    """Mặt bằng giá từ kết quả find_stays / find_places; thiếu phòng hay quán thì None (MCP cũ
    chưa trả `price_stats_vnd` cho quán: không đoán từ 30 quán phổ biến nhất)."""
    stay, meal = stays.get("price_stats_vnd"), food.get("price_stats_vnd")
    if not isinstance(stay, dict) or not isinstance(meal, dict):
        return None
    return {"stay": stay, "meal": meal}


def level_costs(form: TripForm, profile: TripProfile) -> list[dict[str, Any]]:
    """Phòng + ăn cả đoàn cả chuyến ở từng mức của điểm đến; chưa có mặt bằng giá thì []."""
    levels = profile.price_levels
    if not levels:
        return []
    rooms = -(-form.group_size // 2)
    nights = 0 if form.stay_booked else form.num_nights
    meals = sum(len(required_meals(d.day_start, d.day_end)) for d in profile.days)
    return [
        {
            "key": key,
            "label": label,
            "basis": basis,
            "stay_night": levels["stay"][q],
            "meal": levels["meal"][q],
            "total": levels["stay"][q] * nights * rooms
            + levels["meal"][q] * meals * form.group_size,
        }
        for key, label, q, basis in LEVELS
    ]


def about(v: float) -> str:
    """Tổng của một mức, làm tròn tới 100 nghìn: số ước, không giả chính xác tới từng đồng."""
    return money(round(v / 100_000) * 100_000)


def budget_level(
    form: TripForm, profile: TripProfile, estimate: int | None = None
) -> dict[str, Any] | None:
    """Thông báo mức chi cho đầu kết quả: `severity` "warn" (ngân sách dưới cả mức Tiết kiệm) hoặc
    "info" (dưới mức của mức chi đã chọn; "không giới hạn" thì ghi tổng ước `estimate`), kèm cả 3
    mức để hiện thành bảng. Ngân sách đủ mức đã chọn / chưa có mặt bằng giá thì None."""
    rows = level_costs(form, profile)
    if not rows:
        return None
    by = {r["key"]: r for r in rows}
    levels = profile.price_levels or {}
    dest, budget = form.destination, profile.trip_budget
    base = {
        "style": form.spend_style,
        "budget": budget,
        "levels": rows,
        "scope": (
            f"Phòng + ăn cho {form.group_size} người, {form.num_days} ngày {form.num_nights} đêm "
            f"(chưa gồm vé, đi lại, phát sinh), theo giá {levels['stay']['count']} chỗ ở và "
            f"{levels['meal']['count']} quán đã lưu ở {dest}."
        ),
    }
    if form.spend_style == "unlimited":
        if estimate is None:
            return None
        text = (
            "Lịch chọn theo chất lượng, không theo ngân sách: tổng ước "
            f"{money(estimate)} cho cả đoàn (gồm vé, đi lại, phát sinh)."
        )
        if budget and estimate - form.prepaid_tickets * form.group_size > budget:
            text += f" Cao hơn ngân sách {money(budget)}."
        return {
            **base,
            "severity": "info",
            "target": None,
            "title": "Mức chi không giới hạn",
            "text": text,
            "log": None,
        }
    target_key = STYLE_LEVEL.get(form.spend_style)
    if not budget or target_key is None:
        return None
    saving, target = by["saving"], by[target_key]
    luxury = target_key == "luxury"
    if budget < saving["total"]:
        severity, title = "warn", f"Ngân sách thấp so với {dest}"
        text = (
            f"Chỉ phòng và ăn ở mức tiết kiệm đã khoảng {about(saving['total'])}, hơn ngân sách "
            f"{money(budget)}"
            + (
                f", chưa nói tới mức sang (khoảng {about(target['total'])}). Lịch giữ gần ngân "
                "sách nhất có thể."
                if luxury
                else f". Lịch dưới đây ưu tiên chỗ ở, quán rẻ nhưng tổng vẫn có thể vượt; mức "
                f"thường gặp ở {dest} khoảng {about(target['total'])}."
            )
        )
        shown = saving
    elif budget < target["total"]:
        severity = "info"
        title = (
            f"Ngân sách chưa tới mức sang ở {dest}"
            if luxury
            else f"Ngân sách dưới mức thường gặp ở {dest}"
        )
        where = f"phòng {money(target['stay_night'])}/đêm, quán {money(target['meal'])}/người"
        text = (
            f"Mức sang ({where}) khoảng {about(target['total'])}; với ngân sách {money(budget)} "
            "lịch chọn chỗ ở, quán tốt nhất mà không vượt ngân sách."
            if luxury
            else f"Mức thường gặp ({where}) khoảng {about(target['total'])}; ngân sách "
            f"{money(budget)} đủ mức tiết kiệm nên lịch ưu tiên chỗ ở, quán rẻ hơn mức giữa."
        )
        shown = target
    else:
        return None
    return {
        **base,
        "severity": severity,
        "target": target_key,
        "title": title,
        "text": text,
        "log": (
            f"{title}: mức {shown['label'].lower()} khoảng {about(shown['total'])} (phòng + ăn), "
            f"ngân sách {money(budget)}"
        ),
    }


def below_level(form: TripForm, profile: TripProfile) -> bool:
    """Ngân sách thấp hơn mức của mức chi đã chọn (bước chốt danh sách ghi rõ đã ưu tiên chỗ rẻ)."""
    return budget_level(form, profile) is not None and form.spend_style != "unlimited"

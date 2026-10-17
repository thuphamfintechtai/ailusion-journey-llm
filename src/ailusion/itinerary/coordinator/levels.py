"""Mức chi của điểm đến: so ngân sách với mặt bằng giá, mỗi mức chi user chọn so một trung vị riêng.

Mặt bằng giá = tứ phân vị giá của CẢ điểm đến: `price_stats_vnd` của find_stays (phòng / đêm, mọi
chỗ ở có giá) và find_places loại "Nhà hàng" (mỗi người, mọi quán có giá), tính trước khi cắt
`limit`. Không lấy vài chỗ "phổ biến" nhất: toàn resort, nhà hàng đắt (Phú Quốc 2 người 4 ngày
từng ra "tối thiểu 25 triệu").

- Mỗi mức = (phòng × số đêm × số phòng (2 người 1 phòng) + 1 bữa × số bữa bắt buộc × số người) ×
  (1 + phát sinh `EXTRA_MID`, như tổng ước của lịch). Chưa gồm vé, đi lại: ngân sách dưới mức này là
  chắc thiếu, không báo nhầm.
- Tiết kiệm = trung vị nửa rẻ (p25), Thường gặp = trung vị, Sang = trung vị nửa đắt (p75).
- Mức chi "Bình thường" so ngân sách với Thường gặp, "Sang, không vượt ngân sách" với Sang; dưới cả
  Tiết kiệm thì cảnh báo. Ngân sách trên cả mức Sang mà tổng ước của lịch vẫn dưới khoảng ngân sách
  thì báo ngân sách dư (chỗ rẻ, ngân sách cao: Huế 30 triệu, phòng đắt nhất trong 30 km 3,4 triệu).
  "Không giới hạn" không so ngân sách: ghi tổng ước của lịch, kèm các mức của điểm đến để tham khảo.
- Lời báo ghi ngân sách quy về cả đoàn cả chuyến kèm phép tính (`budget_text`): bấm nhầm "mỗi người
  mỗi ngày" thì 6 triệu thành 36 triệu, user thấy ngay.

Chỉ để báo user (đầu kết quả, nhật ký bước 3), không dùng để lọc hay chọn chỗ.
"""

from __future__ import annotations

from typing import Any

from ..shared.form import (
    EXTRA_MID,
    TripForm,
    budget_band,
    money,
    required_meals,
    rooms_for,
    trip_budget,
)
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
    """Phòng + ăn + phát sinh cả đoàn cả chuyến ở từng mức của điểm đến (`total`, chưa làm tròn:
    dùng để so ngân sách); chưa có mặt bằng giá thì []."""
    levels = profile.price_levels
    if not levels:
        return []
    rooms = rooms_for(form)
    nights = 0 if form.stay_booked else form.num_nights
    meals = sum(len(required_meals(d.day_start, d.day_end)) for d in profile.days)
    return [
        {
            "key": key,
            "label": label,
            "basis": basis,
            "stay_night": levels["stay"][q],
            "meal": levels["meal"][q],
            "total": round(
                (levels["stay"][q] * nights * rooms + levels["meal"][q] * meals * form.group_size)
                * (1 + EXTRA_MID)
            ),
        }
        for key, label, q, basis in LEVELS
    ]


def up(v: float) -> int:
    """Tổng của một mức để hiện: làm tròn LÊN tới 100 nghìn (số ước, không giả chính xác tới từng
    đồng; làm tròn thường thì 8,04 triệu thành "8.000.000 đ, hơn ngân sách 8.000.000 đ")."""
    return int(-(-v // 100_000) * 100_000)


def about(v: float) -> str:
    return money(up(v))


def short(v: float) -> str:
    """Số tiền gọn trong lời báo: 1.401.065 -> "1,4 triệu", 6.000.000 -> "6 triệu", 162.500 ->
    "163 nghìn"."""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " triệu"
    return f"{int(v / 1000 + 0.5)} nghìn"


def budget_text(form: TripForm) -> str | None:
    """Ngân sách user nhập quy về cả đoàn cả chuyến, kèm phép tính theo đơn vị đã chọn (đúng
    `shared/form._to_trip`): "1 triệu mỗi người mỗi ngày × 2 người × 3 ngày = 6 triệu cả đoàn cả
    chuyến". Không có ngân sách thì None."""
    if form.budget_unknown and not form.budget and form.budget_min is None:
        return "chưa biết — lên theo mức chi thường gặp"
    band = budget_band(form)
    total = trip_budget(form)
    if band is None or not total:
        return None
    whole = "cả đoàn cả chuyến"
    if form.budget_min is not None:  # khoảng user chọn ở câu hỏi lại
        low, high = band
        if low and high:
            # "3–6 triệu", không phải "3 triệu–6 triệu".
            first = short(low).removesuffix(" triệu") if low >= 1_000_000 else short(low)
            return f"{first}–{short(high)} {whole}, so với giữa khoảng {short(total)}"
        return f"từ {short(low)} {whole}" if low else f"tối đa {short(total)} {whole}"
    n, d, b, paid = form.group_size, form.num_days, form.budget or 0, form.prepaid_tickets
    if form.budget_unit == "person_day":
        how = f"{short(b)} mỗi người mỗi ngày × {n} người × {d} ngày"
    elif form.budget_unit == "person_trip":
        how = (
            f"({short(b)} − vé đã mua {short(paid)}) × {n} người"
            if paid
            else f"{short(b)} mỗi người × {n} người"
        )
    else:
        how = f"{short(b)} − vé đã mua {short(paid)} × {n} người" if paid else ""
    return f"{how} = {short(total)} {whole}" if how else f"{short(total)} {whole}"


def under_budget_pct(form: TripForm, budget: int, estimate: int | None) -> int | None:
    """Tổng ước (bỏ vé đã mua) dưới mức dưới của khoảng ngân sách: thấp hơn ngân sách mốc bao
    nhiêu %; không thì None."""
    band = budget_band(form)
    if estimate is None or band is None or not band[0]:
        return None
    net = estimate - form.prepaid_tickets * form.group_size
    return round((budget - net) / budget * 100) if net < band[0] else None


def budget_level(
    form: TripForm, profile: TripProfile, estimate: int | None = None
) -> dict[str, Any] | None:
    """Thông báo mức chi cho đầu kết quả, `kind`:
    - "low" (`severity` "warn"): ngân sách dưới cả mức Tiết kiệm;
    - "below" ("info"): dưới mức của mức chi đã chọn;
    - "surplus" ("info"): ngân sách trên cả mức Sang mà tổng ước `estimate` của lịch vẫn dưới
      khoảng ngân sách (chỉ biết sau khi lập lịch);
    - "unlimited" ("info"): mức chi không giới hạn, ghi tổng ước `estimate`;
    - "unknown" ("info"): user chưa biết ngân sách (ô ngân sách "Không giới hạn"), lịch theo mức
      chi thường gặp, ghi tổng ước `estimate`.
    Kèm cả 3 mức để hiện thành bảng. Ngân sách vừa / chưa có mặt bằng giá thì None."""
    rows = level_costs(form, profile)
    if not rows:
        return None
    by = {r["key"]: r for r in rows}
    levels = profile.price_levels or {}
    dest, budget = form.destination, profile.trip_budget
    base = {
        "style": form.spend_style,
        "budget": budget,
        "budget_text": budget_text(form),
        "levels": [{**r, "total": up(r["total"])} for r in rows],
        "scope": (
            f"Phòng + ăn + phát sinh {f'{EXTRA_MID * 100:g}'.replace('.', ',')}% cho "
            f"{form.group_size} người, {form.num_days} ngày {form.num_nights} đêm (chưa gồm vé, đi "
            f"lại), theo giá {levels['stay']['count']} chỗ ở và {levels['meal']['count']} quán đã "
            f"lưu ở {dest}."
        ),
    }
    if form.budget_unknown and not budget:
        if estimate is None:
            return None
        return {
            **base,
            "kind": "unknown",
            "severity": "info",
            "target": "normal",
            "title": "Chưa có ngân sách",
            "text": (
                f"Lịch theo mức chi thường gặp ở {dest} (phòng khoảng "
                f"{short(levels['stay']['median'])}/đêm), không chọn chỗ đắt nhất: tổng ước "
                f"{money(estimate)} cho cả đoàn (gồm vé, đi lại, phát sinh). Có ngân sách thì "
                "nhập để lịch bám theo."
            ),
            "log": None,
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
            "kind": "unlimited",
            "severity": "info",
            "target": None,
            "title": "Mức chi không giới hạn",
            "text": text,
            "log": None,
        }
    target_key = STYLE_LEVEL.get(form.spend_style)
    if not budget or target_key is None:
        return None
    saving, target, top = by["saving"], by[target_key], by["luxury"]
    luxury = target_key == "luxury"
    if budget < saving["total"]:
        kind, severity, title = "low", "warn", f"Ngân sách thấp so với {dest}"
        text = (
            f"Chỉ phòng, ăn và phát sinh ở mức tiết kiệm đã khoảng {about(saving['total'])}, hơn "
            f"ngân sách {money(budget)}"
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
        kind, severity = "below", "info"
        title = (
            f"Ngân sách chưa tới mức sang ở {dest}"
            if luxury
            else f"Ngân sách dưới mức thường gặp ở {dest}"
        )
        where = f"phòng {short(target['stay_night'])}/đêm, quán {short(target['meal'])}/người"
        text = (
            f"Mức sang ({where}) khoảng {about(target['total'])}; với ngân sách {money(budget)} "
            "lịch chọn chỗ ở, quán tốt nhất mà không vượt ngân sách."
            if luxury
            else f"Mức thường gặp ({where}) khoảng {about(target['total'])}; ngân sách "
            f"{money(budget)} đủ mức tiết kiệm nên lịch ưu tiên chỗ ở, quán rẻ hơn mức giữa."
        )
        shown = target
    elif budget > top["total"] and (pct := under_budget_pct(form, budget, estimate)):
        kind, severity, title = "surplus", "info", f"Ngân sách dư so với {dest}"
        text = (
            f"Lịch ước khoảng {money(estimate)} cho cả đoàn, thấp hơn ngân sách {money(budget)} "
            f"khoảng {pct}%: cả mức sang ở {dest} (phòng {short(top['stay_night'])}/đêm, quán "
            f"{short(top['meal'])}/người) cũng chỉ khoảng {about(top['total'])}, nên khó tiêu hết "
            "ngân sách. Có thể giảm ngân sách hoặc đi thêm ngày."
        )
        target_key, shown = "luxury", top
    else:
        return None
    return {
        **base,
        "kind": kind,
        "severity": severity,
        "target": target_key,
        "title": title,
        "text": text,
        "log": (
            f"{title}: mức {shown['label'].lower()} khoảng {about(shown['total'])} (phòng + ăn + "
            f"phát sinh), ngân sách {money(budget)}"
            if kind != "surplus"
            else None
        ),
    }


def below_level(form: TripForm, profile: TripProfile) -> bool:
    """Ngân sách thấp hơn mức của mức chi đã chọn (bước chốt danh sách ghi rõ đã ưu tiên chỗ rẻ)."""
    level = budget_level(form, profile)
    return level is not None and level["kind"] in ("low", "below")

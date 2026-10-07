"""Bản dữ kiện đưa cho LLM ở bước giải thích (prompts/plan_explain.md). Hàm thuần.

Nguyên tắc: LLM chỉ diễn đạt, không suy luận số. Vì vậy mọi con số được viết sẵn kèm đơn vị
và tên gọi rõ nghĩa ("trễ khi gặp ngày xấu" thay vì `late_p90_min`), mọi phép so sánh (rẻ
nhất, ít rủi ro nhất, điểm cao nhất) đều tính bằng code ở mục `so_sanh`. Số liệu dễ đọc nhầm
và không cần cho lời giải thích (như xác suất dính mưa cả chuyến) thì không đưa vào.
"""

from __future__ import annotations

from typing import Any

from .persona import PERSONA_WEIGHT_SET, PERSONAS
from .request import PlanRequest, assumptions

WEIGHT_SET_NAMES = {
    "nhom": "ưu tiên của nhóm",
    "can_bang": "cân bằng",
    "tiet_kiem": "tiết kiệm",
    "trai_nghiem": "trải nghiệm",
}
BLOCKS = (
    ("morning_rain_pct", "sáng"),
    ("afternoon_rain_pct", "chiều"),
    ("evening_rain_pct", "tối"),
)


def vnd(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " đ"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def decision_facts(
    req: PlanRequest,
    options: dict[str, Any],
    decision: dict[str, Any],
    festivals: list[str],
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`options` là các Option đã model_dump(); `decision` là dict của bước rui_ro_chon."""
    persona = PERSONAS[req.persona]
    b = req.basic
    winner = decision["winner"]
    name = {k: o["name"] for k, o in options.items()}
    party = [f"{b.adults} người lớn"]
    if b.children:
        party.append(f"{b.children} trẻ em")
    if b.seniors:
        party.append(f"{b.seniors} người lớn tuổi")
    budget_kind = "không được vượt" if req.budget.hard else "có thể linh hoạt"

    return {
        "chuyen_di": (
            f"{b.destination}, {b.days} ngày từ {b.start_date:%d/%m/%Y}, {b.people} người "
            f"({', '.join(party)}), ngân sách {vnd(req.budget.total_vnd)} ({budget_kind})"
        ),
        "nhom_nguoi_dung": (
            f"{req.persona} {persona['name']}: {persona['goal'].lower()}; ưu tiên "
            f"{persona['priorities']}; sẵn sàng hy sinh {persona['sacrifices']}"
        ),
        "chien_luoc": _strategy(strategy),
        "phuong_an_duoc_chon": name[winner],
        "ly_do_chon": _reasons(decision, name),
        "so_sanh": _comparisons(options, decision, name),
        "phuong_an": [_option(o) for o in options.values()],
        "thoi_tiet": _weather(options[winner]),
        "rang_buoc_dang_cham": decision.get("binding", []),
        "le_hoi": festivals or ["Không có lễ hội nào trong dữ liệu trùng ngày đi"],
        "gia_dinh": assumptions(req),
        "bi_loai": [f"{e['name']}: {e['reason']}" for e in decision.get("excluded", [])[:5]],
    }


def _strategy(strategy: dict[str, Any] | None) -> list[str]:
    if not strategy:
        return []
    s = strategy
    out = [
        s["summary"],
        f"Khoảng {s['stops_per_day']} điểm mỗi ngày, dự phòng {s['buffer_min']} phút mỗi chặng, "
        f"chặng dài nhất {s['max_travel_min']} phút",
    ]
    if s["rest_break_min"]:
        out.append(f"Nghỉ giữa ngày {s['rest_break_min']} phút")
    if s.get("sacrifices"):
        out.append("Chấp nhận hy sinh: " + "; ".join(s["sacrifices"]))
    return out


def _reasons(decision: dict[str, Any], name: dict[str, str]) -> list[str]:
    winner = decision["winner"]
    by_set = dict(decision.get("winner_by_weight_set", {}))
    out = []
    if by_set.pop(PERSONA_WEIGHT_SET, None) == winner:
        out.append("Điểm cao nhất theo bộ trọng số ưu tiên của nhóm người dùng")
    won = [WEIGHT_SET_NAMES.get(ws, ws) for ws, k in by_set.items() if k == winner]
    lost = [
        f"bộ {WEIGHT_SET_NAMES.get(ws, ws)} nghiêng về {name.get(k, k)}"
        for ws, k in by_set.items()
        if k != winner
    ]
    if by_set:
        out.append(
            f"Theo {len(by_set)} bộ trọng số chung: thắng {len(won)}"
            + (f" ({', '.join(won)})" if won else "")
            + (f"; {'; '.join(lost)}" if lost else "")
        )
    if decision.get("tie_break"):
        out.append(f"Phân định: {decision['tie_break']}")
    return out


def _comparisons(
    options: dict[str, Any], decision: dict[str, Any], name: dict[str, str]
) -> list[str]:
    costs = {k: o["cost"]["total"] for k, o in options.items()}
    risks = {k: o["risk"]["p_break"] for k, o in options.items()}
    cheap, dear = min(costs, key=costs.get), max(costs, key=costs.get)
    safe = min(risks, key=risks.get)
    out = [
        f"Rẻ nhất: {name[cheap]} ({vnd(costs[cheap])})",
        f"Đắt nhất: {name[dear]} ({vnd(costs[dear])})",
        f"Ít rủi ro vỡ lịch nhất: {name[safe]} ({pct(risks[safe])})",
    ]
    for ws, row in decision.get("matrix", {}).items():
        if not row:
            continue
        best = max(row, key=row.get)
        ranked = ", ".join(
            f"{name.get(k, k)} {v:.2f}" for k, v in sorted(row.items(), key=lambda kv: -kv[1])
        )
        out.append(
            f"Điểm cao nhất theo bộ {WEIGHT_SET_NAMES.get(ws, ws)}: "
            f"{name.get(best, best)} ({ranked})"
        )
    failing = [name[k] for k, o in options.items() if not o["hard_ok"]]
    if failing:
        out.append(f"Không đạt ràng buộc bắt buộc: {', '.join(failing)}")
    return out


def _option(o: dict[str, Any]) -> dict[str, Any]:
    c, r = o["cost"], o["risk"]
    breakdown = [
        f"lưu trú {vnd(c['stay'])}",
        f"ăn uống {vnd(c['food'])}",
        f"vé tham quan {vnd(c['tickets'])} (ước tính)",
        f"di chuyển {vnd(c['move'])}",
    ]
    if c["intercity"]:
        breakdown.append(f"đi – về {vnd(c['intercity'])}")
    breakdown.append(f"dự phòng {vnd(c['reserve'])}")
    places = [i["name"] for d in o["days"] for i in d["items"] if i["type"] == "poi"]
    return {
        "ten": o["name"],
        "mo_ta": o["description"],
        "tong_chi_phi": vnd(c["total"]),
        "chi_tiet_chi_phi": ", ".join(breakdown),
        "khach_san": (
            f"{o['hotel']['name']} ({o['hotel']['note']})" if o.get("hotel") else "Không đặt chỗ ở"
        ),
        "dat_rang_buoc_bat_buoc": "có" if o["hard_ok"] else "không",
        "van_de": o["issues"] or ["không có"],
        "xac_suat_vo_lich": pct(r["p_break"]),
        "tre_thuong_gap": f"{r['late_p50_min']} phút",
        "tre_khi_gap_ngay_xau": f"{r['late_p90_min']} phút",
        "so_diem_tham_quan": len(places),
        "diem_tham_quan": places,
    }


def _weather(option: dict[str, Any]) -> list[str]:
    out = []
    for d in option["days"]:
        w = d["weather"]
        blocks = ", ".join(f"{label} {w[key]}%" for key, label in BLOCKS if w.get(key) is not None)
        day = f"{d['weekday']} {d['date'][8:10]}/{d['date'][5:7]}"
        line = f"Ngày {d['index']} ({day}): khả năng mưa {blocks}"
        if w.get("temp_min_c") is not None:
            line += f"; nhiệt độ {round(w['temp_min_c'])}–{round(w['temp_max_c'])}°C"
        if w.get("warnings"):
            line += "; cảnh báo: " + "; ".join(w["warnings"])
        out.append(line)
    return out

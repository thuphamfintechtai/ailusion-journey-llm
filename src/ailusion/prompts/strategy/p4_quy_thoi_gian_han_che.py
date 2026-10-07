"""Chiến lược cho nhóm P4 — Quỹ thời gian hạn chế.

Dùng ở : ailusion/planning/graph.py · node chien_luoc, khi form chọn persona = "P4"
Biến   : không có
Khoảng : phải khớp PERSONAS["P4"]["bounds"] trong planning/persona.py (có test đối chiếu)
"""

from .common import FIELD_GUIDE, SYSTEM_CONTEXT

PROMPT = (
    SYSTEM_CONTEXT
    + """
# Nhóm của chuyến này: P4 — Quỹ thời gian hạn chế

- Mục tiêu chính: tận dụng tối đa khung giờ trống mà không làm trễ lịch chính.
- Ưu tiên cao: vừa khung giờ, độ tin cậy, có thời gian dự phòng.
- Sẵn sàng hy sinh: một phần chi phí và trải nghiệm.
- Ví dụ điển hình: đi công tác kết hợp du lịch, dự hội thảo, đám cưới, có chuyến bay giờ cố định.

Họ hài lòng khi: tranh thủ được vài nơi đáng đi trong giờ trống, luôn về kịp lịch chính, không phải lo tính giờ. Họ khó chịu khi: trễ họp vì kẹt xe, mất nửa giờ trống cho một chặng xe, đến nơi cần xếp hàng hoặc đặt trước, ăn trưa quá lâu.

# Khoảng cho phép của nhóm

stops_per_day 1–4 · buffer_min 20–45 · hotel_tier 0.3–0.7 · meal_vnd 100.000–300.000 · ticket_sensitivity 0.2–0.6 · quality_over_quantity 0.4–0.8 · max_travel_min 10–25 · rest_break_min 0–15

# Cấu hình điển hình

2 điểm/ngày · dự phòng 30 phút · hotel_tier 0,5 · ăn 150.000 đ/bữa · ticket_sensitivity 0,4 · quality_over_quantity 0,6 · chặng tối đa 20 phút · không nghỉ giữa ngày · không né đông (trừ khi form yêu cầu).

# Cách tính số điểm cho nhóm này

Đọc `gio_trong_tung_ngay`. Hệ thống sẽ tự giảm số điểm ở ngày ít giờ trống (theo tỷ lệ giờ trống trên 10 giờ), nên `stops_per_day` là số điểm cho ngày **nhiều giờ trống nhất**:

| Giờ trống nhiều nhất trong các ngày | stops_per_day |
|---|---|
| dưới 3 giờ | 1 |
| 3–5 giờ | 2 |
| 5–8 giờ | 3 |
| trên 8 giờ | 4 |

Trừ thêm 1 nếu buffer_min từ 40 trở lên (dự phòng ăn bớt giờ).

# Chỉnh theo tình huống

| Tình huống trong bản tóm tắt | Chỉnh |
|---|---|
| Có lịch cố định trong ngày | buffer_min 35–45; max_travel_min 10–15 để luôn ở gần nơi có lịch |
| Không có lịch cố định (chỉ là ít ngày) | buffer_min 20–25; max_travel_min 20–25; dùng bảng số điểm như trên |
| Cao điểm 2–3 hoặc thành phố lớn giờ tan tầm | buffer_min 40–45 |
| Mức ngân sách cao | hotel_tier 0,6–0,7 (gần trung tâm, đỡ mất giờ di chuyển); ticket_sensitivity 0,2 |
| Mức ngân sách thấp | hotel_tier 0,3; meal_vnd 100.000 |
| Đi một mình | meal_vnd 100.000–150.000 (bữa nhanh gọn) |
| Muốn mua quà, đặc sản | extra_searches "đặc sản" hoặc "chợ" |
| Cần chỗ làm việc giữa giờ | extra_searches "quán cà phê" |

# Khi nhóm và form mâu thuẫn

- Form chọn nhịp "Năng động" nhưng giờ trống ít: làm theo giờ trống, không nhồi điểm; nói rõ trong summary.
- Không có lịch cố định: vẫn ưu tiên tin cậy và chặng ngắn, coi "ít ngày" là ràng buộc chính.

# Ví dụ

Ví dụ chỉ để minh hoạ cách suy nghĩ; chuyến của bạn khác thì chiến lược phải khác theo.

Bản tóm tắt (rút gọn): Đà Nẵng, 2 ngày từ Thứ Ba, 1 người lớn; ngân sách 5.000.000 đ không được vượt (2.500.000 đ/người/ngày, mức cao); Grab hoặc taxi; nhịp Vừa phải (trần 4); giờ trống: ngày 1 khoảng 4,0 giờ (hội thảo 08:00–17:00), ngày 2 khoảng 5,0 giờ (họp 08:00–12:00); sở thích chưa chọn; cao điểm 0.

Chiến lược:
```json
{
  "summary": "Chuyến công tác Đà Nẵng: chỉ tranh thủ tối ngày 1 và chiều ngày 2, mỗi khoảng 2 điểm gần khách sạn, chặng xe dưới 15 phút và dự phòng 35 phút để không trễ hội thảo, buổi họp.",
  "stops_per_day": 2,
  "buffer_min": 35,
  "hotel_tier": 0.6,
  "meal_vnd": 150000,
  "ticket_sensitivity": 0.3,
  "quality_over_quantity": 0.6,
  "max_travel_min": 15,
  "rest_break_min": 0,
  "avoid_crowd": false,
  "focus_tags": ["amthuc", "checkin"],
  "extra_searches": [
    {"query": "đặc sản", "why": "mua quà mang về sau chuyến công tác"}
  ],
  "sacrifices": ["ít điểm tham quan", "bỏ các nơi xa trung tâm", "chi phí taxi cao hơn đi xe máy"]
}
```
"""
    + FIELD_GUIDE
)

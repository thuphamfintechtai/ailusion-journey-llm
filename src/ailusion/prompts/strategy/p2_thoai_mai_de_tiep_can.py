"""Chiến lược cho nhóm P2 — Thoải mái và dễ tiếp cận.

Dùng ở : ailusion/planning/graph.py · node chien_luoc, khi form chọn persona = "P2"
Biến   : không có
Khoảng : phải khớp PERSONAS["P2"]["bounds"] trong planning/persona.py (có test đối chiếu)
"""

from .common import FIELD_GUIDE, SYSTEM_CONTEXT

PROMPT = (
    SYSTEM_CONTEXT
    + """
# Nhóm của chuyến này: P2 — Thoải mái và dễ tiếp cận

- Mục tiêu chính: đi nhẹ nhàng, an toàn, không mệt.
- Ưu tiên cao: thoải mái, dễ tiếp cận, có thời gian nghỉ ngơi, độ tin cậy của lịch.
- Sẵn sàng hy sinh: số lượng điểm, chi phí.
- Ví dụ điển hình: gia đình có trẻ nhỏ, có ông bà; người có vấn đề sức khoẻ, đi lại khó khăn.

Họ hài lòng khi: cả đoàn đi hết ngày mà không ai kiệt sức, không phải chạy theo giờ, có chỗ ngồi nghỉ, ăn đúng bữa, về khách sạn sớm. Họ khó chịu khi: lịch dày, chặng xe dài làm trẻ quấy hoặc người già say xe, nơi đông chen lấn, dốc và bậc thang, đến nơi thì đóng cửa.

# Khoảng cho phép của nhóm

stops_per_day 2–3 · buffer_min 15–40 · hotel_tier 0.4–0.9 · meal_vnd 100.000–300.000 · ticket_sensitivity 0.2–0.6 · quality_over_quantity 0.5–1 · max_travel_min 10–30 · rest_break_min 30–120

# Cấu hình điển hình

3 điểm/ngày · dự phòng 25 phút · hotel_tier 0,6 · ăn 180.000 đ/bữa · ticket_sensitivity 0,4 · quality_over_quantity 0,7 · chặng tối đa 25 phút · nghỉ giữa ngày 60 phút · né đông.

# Chỉnh theo tình huống

| Tình huống trong bản tóm tắt | Chỉnh |
|---|---|
| Có người lớn tuổi | stops_per_day 2; buffer_min 30–40; max_travel_min tối đa 20; rest_break_min 90–120 |
| Có trẻ dưới 12 tuổi | buffer_min từ 25; rest_break_min từ 60; extra_searches "khu vui chơi" |
| Có cả trẻ nhỏ và người lớn tuổi | stops_per_day 2; buffer_min 35–40; rest_break_min 120; max_travel_min 15–20 |
| Có xe lăn hoặc hạn chế leo dốc | quality_over_quantity 0,8 trở lên; max_travel_min 15–20; ưu tiên nơi bằng phẳng (hệ thống tự loại nơi dốc) |
| Form đã bật nghỉ trưa 90 phút | rest_break_min chỉ có tác dụng phần vượt 90; đặt 90–120 nếu muốn nghỉ thêm |
| Mức ngân sách thấp | hotel_tier 0,4–0,5; meal_vnd 100.000–130.000; ticket_sensitivity 0,6; vẫn giữ ít điểm và nhiều nghỉ |
| Mức ngân sách cao | hotel_tier 0,8–0,9; meal_vnd 250.000–300.000; ticket_sensitivity 0,2 |
| Vùng núi, đèo (Đà Lạt, Sa Pa) | max_travel_min 25–30 (đường đèo chậm) nhưng buffer_min cao |
| Mùa mưa hoặc khi mưa "ưu tiên trong nhà" | focus_tags có vanhoa hoặc cafe (nơi có mái che); buffer_min từ 30 |
| Cao điểm 2–3 | buffer_min 35–40; avoid_crowd luôn bật |

# Khi nhóm và form mâu thuẫn

- Form chọn nhịp "Năng động": vẫn giữ 2–3 điểm của nhóm (nhóm này hy sinh số điểm), nói rõ trong summary.
- Form không có trẻ em hay người lớn tuổi nhưng chọn P2: hiểu là người dùng muốn đi chậm, dùng cấu hình điển hình.

# Ví dụ

Ví dụ chỉ để minh hoạ cách suy nghĩ; chuyến của bạn khác thì chiến lược phải khác theo.

Bản tóm tắt (rút gọn): Đà Lạt, 3 ngày từ Thứ Bảy; 2 người lớn, 1 trẻ dưới 12 tuổi, 2 người lớn tuổi; ngân sách 20.000.000 đ linh hoạt (1.333.000 đ/người/ngày, mức trung bình); Grab hoặc taxi; nhịp Nhẹ nhàng (trần 3); sở thích Thiên nhiên [thiennhien]; khi mưa ưu tiên trong nhà; cao điểm 1.

Chiến lược:
```json
{
  "summary": "Gia đình ba thế hệ đi Đà Lạt: mỗi ngày 2 điểm gần khách sạn, chặng xe không quá 20 phút, nghỉ dài sau bữa trưa cho ông bà và bé; chọn nơi có mái che vì đang mùa mưa chiều.",
  "stops_per_day": 2,
  "buffer_min": 35,
  "hotel_tier": 0.7,
  "meal_vnd": 180000,
  "ticket_sensitivity": 0.4,
  "quality_over_quantity": 0.8,
  "max_travel_min": 20,
  "rest_break_min": 120,
  "avoid_crowd": true,
  "focus_tags": ["thiennhien", "cafe"],
  "extra_searches": [
    {"query": "khu vui chơi", "why": "có bé dưới 12 tuổi"},
    {"query": "nhà thuốc", "why": "có ông bà đi cùng"}
  ],
  "sacrifices": ["ít điểm tham quan mỗi ngày", "chi phí phòng và đi lại cao hơn"]
}
```
"""
    + FIELD_GUIDE
)

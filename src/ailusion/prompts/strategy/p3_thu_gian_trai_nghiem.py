"""Chiến lược cho nhóm P3 — Thư giãn và trải nghiệm.

Dùng ở : ailusion/planning/graph.py · node chien_luoc, khi form chọn persona = "P3"
Biến   : không có
Khoảng : phải khớp PERSONAS["P3"]["bounds"] trong planning/persona.py (có test đối chiếu)
"""

from .common import FIELD_GUIDE, SYSTEM_CONTEXT

PROMPT = (
    SYSTEM_CONTEXT
    + """
# Nhóm của chuyến này: P3 — Thư giãn và trải nghiệm

- Mục tiêu chính: ít điểm nhưng chất lượng cao.
- Ưu tiên cao: trải nghiệm đáng nhớ, thoải mái, cảnh đẹp, yên tĩnh.
- Sẵn sàng hy sinh: chi phí, số lượng điểm.
- Ví dụ điển hình: cặp đôi đi nghỉ dưỡng, kỷ niệm, trăng mật; người đi một mình để nghỉ ngơi.

Họ hài lòng khi: mỗi nơi đến đều đẹp và đáng thời gian, có khoảng lặng giữa ngày, chỗ ở là một phần trải nghiệm, bữa tối ngon và có không khí. Họ khó chịu khi: chen chúc ở điểm nổi tiếng nhưng ồn, chạy lịch, ăn quán bình dân đông đúc, phòng nghỉ tầm thường.

# Khoảng cho phép của nhóm

stops_per_day 2–3 · buffer_min 10–30 · hotel_tier 0.7–1 · meal_vnd 200.000–600.000 · ticket_sensitivity 0–0.3 · quality_over_quantity 0.7–1 · max_travel_min 15–45 · rest_break_min 30–90

# Cấu hình điển hình

3 điểm/ngày · dự phòng 15 phút · hotel_tier 0,9 · ăn 300.000 đ/bữa · ticket_sensitivity 0,1 · quality_over_quantity 0,9 · chặng tối đa 35 phút · nghỉ giữa ngày 60 phút · né đông.

# Chỉnh theo tình huống

| Tình huống trong bản tóm tắt | Chỉnh |
|---|---|
| Mức ngân sách cao | hotel_tier 0,95–1; meal_vnd 400.000–600.000; ticket_sensitivity 0–0,1 |
| Mức ngân sách trung bình | hotel_tier 0,8; meal_vnd 250.000–300.000; ticket_sensitivity 0,2 |
| Mức ngân sách thấp (mâu thuẫn với nhóm) | hotel_tier 0,7; meal_vnd 200.000; ticket_sensitivity 0,3; giữ ít điểm, chọn nơi đẹp miễn phí (hồ, đồi, chùa) |
| Sở thích nghỉ dưỡng hoặc cà phê | focus_tags có nghiduong, cafe; extra_searches "spa" |
| Sở thích thiên nhiên | focus_tags có thiennhien; max_travel_min 40–45 cho một nơi đẹp xa trung tâm |
| Thích về đêm hoặc ẩm thực | extra_searches "nhà hàng" (không dùng từ quá dài) hoặc "rooftop" |
| Cao điểm 2–3 | avoid_crowd giữ bật; quality_over_quantity 1; stops_per_day 2 |
| Mùa mưa | focus_tags có cafe hoặc nghiduong (trong nhà nhưng vẫn thư thái) |
| Có trẻ em hoặc người lớn tuổi (ít gặp ở P3) | buffer_min 20–30; max_travel_min tối đa 30 |

# Khi nhóm và form mâu thuẫn

- Ngân sách thấp: làm theo ngân sách, giữ tinh thần ít mà chất, nói rõ trong summary rằng chỗ ở sẽ ở mức vừa.
- Nhịp "Năng động": giữ 3 điểm (mức cao của nhóm), không tăng thêm; nói rõ trong summary.

# Ví dụ

Ví dụ chỉ để minh hoạ cách suy nghĩ; chuyến của bạn khác thì chiến lược phải khác theo.

Bản tóm tắt (rút gọn): Đà Lạt, 3 ngày từ Thứ Sáu, 2 người lớn; ngân sách 25.000.000 đ linh hoạt (4.167.000 đ/người/ngày, mức cao); Grab hoặc taxi; nhịp Vừa phải (trần 4); sở thích Nghỉ dưỡng [nghiduong] rất thích, Cà phê [cafe], Thiên nhiên [thiennhien]; cao điểm 1.

Chiến lược:
```json
{
  "summary": "Cặp đôi nghỉ dưỡng ở Đà Lạt với ngân sách rộng: chỉ 2 điểm mỗi ngày nhưng đều là nơi đánh giá rất cao và yên tĩnh, ở chỗ tốt nhất tìm được, dành buổi trưa để nghỉ và cà phê.",
  "stops_per_day": 2,
  "buffer_min": 15,
  "hotel_tier": 1.0,
  "meal_vnd": 450000,
  "ticket_sensitivity": 0.05,
  "quality_over_quantity": 0.95,
  "max_travel_min": 40,
  "rest_break_min": 90,
  "avoid_crowd": true,
  "focus_tags": ["nghiduong", "cafe", "thiennhien"],
  "extra_searches": [
    {"query": "spa", "why": "nghỉ dưỡng là ưu tiên số một"},
    {"query": "nhà hàng", "why": "bữa tối ngon, có không khí"}
  ],
  "sacrifices": ["ít điểm tham quan", "chi phí cao", "bỏ qua các điểm nổi tiếng nhưng đông"]
}
```
"""
    + FIELD_GUIDE
)

"""Chiến lược cho nhóm P1 — Khám phá tiết kiệm.

Dùng ở : ailusion/planning/graph.py · node chien_luoc, khi form chọn persona = "P1"
Biến   : không có
Khoảng : phải khớp PERSONAS["P1"]["bounds"] trong planning/persona.py (có test đối chiếu)
"""

from .common import FIELD_GUIDE, SYSTEM_CONTEXT

PROMPT = (
    SYSTEM_CONTEXT
    + """
# Nhóm của chuyến này: P1 — Khám phá tiết kiệm

- Mục tiêu chính: trải nghiệm được nhiều nhất trong một ngân sách có hạn.
- Ưu tiên cao: hiệu quả chi phí, đúng ngân sách, khám phá, ẩm thực địa phương, lịch dày.
- Sẵn sàng hy sinh: sự thoải mái, sang trọng.
- Ví dụ điển hình: nhóm bạn trẻ, sinh viên, người mới đi làm; thường ngân sách cứng, đi xe máy hoặc Grab.

Họ hài lòng khi: về nhà kể được nhiều nơi, ăn được nhiều món địa phương, tiêu đúng hoặc dưới ngân sách. Họ khó chịu khi: tốn tiền cho phòng đẹp mà ít dùng, mất nửa ngày ở một chỗ, phải trả vé đắt cho nơi "chỉ để chụp ảnh".

# Khoảng cho phép của nhóm

stops_per_day 4–6 · buffer_min 0–15 · hotel_tier 0–0.3 · meal_vnd 40.000–150.000 · ticket_sensitivity 0.5–1 · quality_over_quantity 0–0.5 · max_travel_min 20–60 · rest_break_min 0–30

# Cấu hình điển hình

5 điểm/ngày · dự phòng 5 phút · hotel_tier 0,1 · ăn 80.000 đ/bữa · ticket_sensitivity 0,8 · quality_over_quantity 0,2 · chặng tối đa 40 phút · không nghỉ giữa ngày · không né đông.

# Chỉnh theo tình huống

| Tình huống trong bản tóm tắt | Chỉnh |
|---|---|
| Mức ngân sách thấp | ticket_sensitivity 0,9–1; meal_vnd 40.000–70.000; hotel_tier 0 |
| Mức ngân sách trung bình | ticket_sensitivity 0,6–0,7; meal_vnd 100.000–150.000; được đi vài điểm có vé |
| Mức ngân sách cao (hiếm với P1) | giữ tinh thần lịch dày nhưng meal_vnd 150.000, ticket_sensitivity 0,5, hotel_tier 0,3 |
| Gánh cả vé đi – về | coi như ngân sách thấp hơn một mức |
| Có trẻ dưới 12 tuổi hoặc người lớn tuổi | stops_per_day 4; buffer_min 10–15; max_travel_min tối đa 30; rest_break_min 30 |
| Người dùng chọn nhịp "Nhẹ nhàng" | stops_per_day không quá 3 (trần), dồn ưu tiên vào ăn uống và điểm miễn phí |
| Xe máy tự lái | max_travel_min 45–60 được, chi phí di chuyển rẻ |
| Đi bộ và xe buýt | max_travel_min 20–30; cụm điểm gần nhau |
| Cao điểm 2–3 | buffer_min 10–15; vẫn không né đông (nơi đông thường là nơi đáng đi), trừ khi form yêu cầu |
| Thích ẩm thực hoặc chưa chọn sở thích | focus_tags có amthuc; extra_searches "chợ đêm" hoặc "quán ăn vặt" |
| Thích về đêm | extra_searches "chợ đêm" hoặc "phố đi bộ" |

# Khi nhóm và form mâu thuẫn

- Form chọn nhịp "Nhẹ nhàng": làm theo trần của form, bù bằng điểm miễn phí gần nhau và nhiều trải nghiệm ăn uống.
- Ngân sách "linh hoạt" và mức cao: vẫn giữ tinh thần tiết kiệm của nhóm, nói rõ trong summary là lộ trình sẽ dư ngân sách.

# Ví dụ

Ví dụ chỉ để minh hoạ cách suy nghĩ; chuyến của bạn khác thì chiến lược phải khác theo.

Bản tóm tắt (rút gọn): Đà Lạt, 3 ngày từ Thứ Bảy, 4 người lớn; ngân sách 6.000.000 đ không được vượt (500.000 đ/người/ngày, mức thấp); xe máy tự lái; nhịp Năng động (trần 5); sở thích Ẩm thực [amthuc] rất thích, Chụp ảnh check-in [checkin]; cao điểm 1 - cuối tuần; tháng 10 (mùa mưa Tây Nguyên).

Chiến lược:
```json
{
  "summary": "Bốn bạn đi Đà Lạt bằng xe máy với 500.000 đ/người/ngày: dồn tiền cho ăn uống và điểm miễn phí, ngủ phòng rẻ, lịch dày 5 điểm mỗi ngày; chừa ít dự phòng vì đang mùa mưa chiều.",
  "stops_per_day": 5,
  "buffer_min": 10,
  "hotel_tier": 0.0,
  "meal_vnd": 60000,
  "ticket_sensitivity": 0.9,
  "quality_over_quantity": 0.2,
  "max_travel_min": 50,
  "rest_break_min": 0,
  "avoid_crowd": false,
  "focus_tags": ["amthuc", "checkin"],
  "extra_searches": [
    {"query": "chợ đêm", "why": "ăn tối rẻ, nhiều món địa phương"},
    {"query": "quán ăn vặt", "why": "ẩm thực là ưu tiên số một của đoàn"}
  ],
  "sacrifices": ["phòng nghỉ đơn giản", "ít điểm có vé đắt", "ngày đi sớm về muộn"]
}
```
"""
    + FIELD_GUIDE
)

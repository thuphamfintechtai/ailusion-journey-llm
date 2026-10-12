"""Pipeline lập lộ trình: form -> tối đa 3 phương án. Đọc `pipeline.py` trước để thấy luồng.

Chia theo 5 làn của trang thiết kế "Pipeline lập lộ trình":

    pipeline.py      nối các bước theo sơ đồ 17 bước
    coordinator/     làn Coordinator — code, không gọi LLM
      profile.py       B1, 3–4  nhận form, phân nhóm P1–P4, hỏi lại
      search.py        B5       gọi tool tìm kiếm (DB trước, tốn quota sau)
      filter.py        B6       lọc, gửi 2 agent xác minh
      score.py         B7–8     chấm điểm, chọn chỗ ở, chốt danh sách
      simulate.py      B10–11   sắp thứ tự cho ít ngồi xe, chạy thử lịch theo đồng hồ
      rank.py          B12–15   xếp hạng, ghép phương án, kiểm ngân sách
    agents/          5 lượt LLM — mỗi file: prompt + đầu ra + luật code kiểm đầu ra
      intake.py        B2       đọc lời nhắn
      experience.py    B6a      chỗ có hợp mục đích không (đọc review)
      budget.py        B6b      giá vé có nguồn
      planner.py       B9       xếp lịch từng ngày
      decision.py      B16      chốt phương án, viết giải thích
    shared/          dùng chung
      form.py          B1 form + bảng luật
      models.py        Place, DayPlan, TripProfile, state của graph
      context.py       Ctx: gọi tool, gọi LLM, ghi nhật ký
      tools.py         đọc kết quả tool MCP thành Place
      llm.py           trần token, tính tiền, kiểm câu trích / số tiền / con số
      math.py          sao tin được (Hoeffding), không bị trội, khoảng cách, trung vị
    response.py      B17 ảnh, lý do chọn, kết quả trả về (JSON + dạng chữ cho chat)
"""

from .coordinator.score import NoPlacesError
from .pipeline import build_itinerary_graph
from .response import (
    ItineraryResponse,
    TraceEvent,
    itinerary_markdown,
    request_text,
    to_response,
)
from .shared.form import TripForm

__all__ = [
    "ItineraryResponse",
    "NoPlacesError",
    "TraceEvent",
    "TripForm",
    "build_itinerary_graph",
    "itinerary_markdown",
    "request_text",
    "to_response",
]

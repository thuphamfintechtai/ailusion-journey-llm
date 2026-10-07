"""POST /plan — người dùng điền form, hệ thống trả về một lộ trình (3 phương án).

request.py   form đầu vào (8 nhóm, 29 trường) + giả định khi bỏ trống
planner.py   planner theo luật: form -> danh sách bước gọi tool
candidates.py kết quả tool -> địa điểm, khách sạn, thời tiết có cấu trúc
schedule.py  Graph B: xếp lịch, chi phí, kiểm tra, mô phỏng rủi ro, điểm thành phần
assemble.py  kết quả nội bộ -> PlanResponse
graph.py     pipeline LangGraph nối các bước 02-09
"""

from .graph import build_plan_graph, to_response
from .request import PlanRequest
from .response import PlanResponse

__all__ = ["PlanRequest", "PlanResponse", "build_plan_graph", "to_response"]

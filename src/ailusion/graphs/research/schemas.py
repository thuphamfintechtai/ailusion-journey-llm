"""Kiểu dữ liệu của Graph A: đầu ra có cấu trúc của LLM ở từng node, và state của graph."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

BranchName = Literal["places", "transport", "local", "prices", "reviews"]

# Năm nhánh nghiên cứu của ô "Điều phối" trong sơ đồ. Tên nhánh trùng tên nhóm tool.
BRANCHES: dict[str, str] = {
    "places": "Tìm kiếm địa điểm — điểm tham quan, chỗ chơi, chỗ ăn hợp với nhóm đi",
    "transport": "Di chuyển & phương tiện — cách đi từ nơi xuất phát tới nơi đến và đi lại tại chỗ",
    "local": "Tin địa phương — lễ hội, sự kiện, thời tiết xấu, thiên tai, cấm đường",
    "prices": "Dò giá & chỗ trống — giá phòng, giá vé, còn chỗ không",
    "reviews": "Thu thập đánh giá — điều người Việt quan tâm: sạch sẽ, đỗ xe, phục vụ, hợp trẻ nhỏ",
}


class TripSlots(BaseModel):
    """Trích slot: yêu cầu tự do -> JSON. Chỉ ghi điều người dùng nói, không đoán."""

    destination: str | None = Field(None, description="Điểm đến chính, tên tiếng Việt có dấu")
    origin: str | None = Field(None, description="Nơi xuất phát, nếu người dùng nói")
    start_date: str | None = Field(None, description="Ngày đi, dạng YYYY-MM-DD")
    days: int | None = Field(None, ge=1, le=30, description="Số ngày của chuyến đi")
    adults: int | None = Field(None, ge=0, description="Số người lớn")
    children: int | None = Field(None, ge=0, description="Số trẻ em")
    children_ages: list[int] = Field(default_factory=list, description="Tuổi từng bé, nếu có")
    budget_text: str | None = Field(
        None, description="Nguyên văn phần nói về ngân sách, VD 'tầm 15tr cả nhà'"
    )
    interests: list[str] = Field(default_factory=list, description="Sở thích, kiểu chuyến đi")
    transport: str | None = Field(None, description="Phương tiện mong muốn, nếu có")
    hard_constraints: list[str] = Field(
        default_factory=list, description="Điều bắt buộc, VD 'bé dị ứng hải sản', 'không leo dốc'"
    )
    soft_constraints: list[str] = Field(
        default_factory=list, description="Điều mong muốn nhưng nới được, VD 'gần chợ đêm'"
    )


class ToolStep(BaseModel):
    """Một bước trong kế hoạch: gọi đúng một tool với tham số cụ thể."""

    branch: BranchName = Field(description="Nhánh mà bước này phục vụ")
    tool: str = Field(description="Tên tool, phải nằm trong danh mục của nhánh")
    args_json: str = Field(description='Tham số dạng JSON object, VD {"destination": "Đà Lạt"}')
    why: str = Field(description="Bước này trả lời câu hỏi gì, một câu ngắn")


class ResearchPlan(BaseModel):
    """Planner: cần tìm gì, bằng tool nào, tham số gì. Điều phối chạy đúng các bước này."""

    reasoning: str = Field(description="Một hai câu: vì sao chọn các bước này")
    steps: list[ToolStep] = Field(min_length=1, max_length=12)


class Finding(BaseModel):
    title: str
    detail: str = Field(description="Số liệu cụ thể: giá VND, giờ mở cửa, khoảng cách, rating...")
    source: str | None = Field(None, description="Tool hoặc trang web cho ra thông tin này")
    url: str | None = None
    place_id: str | None = Field(None, description="place_id Google Maps nếu có")


class BranchSummary(BaseModel):
    branch: BranchName
    summary: str = Field(description="Tóm tắt kết quả của nhánh, 2-4 câu, có số liệu")
    items: list[Finding] = Field(default_factory=list, description="Tối đa 6 kết quả đáng chú ý")
    gaps: list[str] = Field(default_factory=list, description="Điều chưa tìm được")


class Digest(BaseModel):
    """Phản biện: tóm tắt từng nhánh, rồi kết luận đủ dữ kiện để lập phương án chưa."""

    branches: list[BranchSummary]
    verdict: Literal["du", "thieu"] = Field(
        description="du = đủ để lập phương án; thieu = cần tìm thêm"
    )
    missing: list[str] = Field(
        default_factory=list, description="Nếu thiếu: từng việc cụ thể cần tìm thêm"
    )
    conflicts: list[str] = Field(default_factory=list, description="Chỗ các nguồn nói khác nhau")
    trust_notes: list[str] = Field(default_factory=list, description="Nhận xét độ tin cậy nguồn")


def add_results(old: list[Any] | None, new: list[Any] | None) -> list[Any]:
    """Gộp kết quả các bước chạy song song. `new=None` xoá sạch khi bắt đầu yêu cầu mới."""
    if new is None:
        return []
    return [*(old or []), *new]


class ResearchState(TypedDict, total=False):
    request: str
    profile: dict[str, Any] | None
    slots: dict[str, Any]
    normalized: dict[str, Any]
    question: str | None
    snapshot: dict[str, Any] | None
    plan: dict[str, Any]
    results: Annotated[list[dict[str, Any]], add_results]
    digest: dict[str, Any]
    replans: int
    context: dict[str, Any]

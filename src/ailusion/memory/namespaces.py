"""Quy ước namespace cho bộ nhớ dài hạn (khớp các ô "bộ nhớ" trong sơ đồ).

Namespace là tuple tiền tố của khoá trong store. Gom hết ở đây để không nơi nào tự chế
chuỗi namespace riêng:

    await store.aput(Namespaces.profile(user_id), "main", {...})
    items = await store.asearch(Namespaces.knowledge("da-lat"), query="mùa mưa")
"""

from __future__ import annotations


class Namespaces:
    """Chỗ duy nhất định nghĩa namespace của bộ nhớ dài hạn."""

    @staticmethod
    def profile(user_id: str) -> tuple[str, ...]:
        """Hồ sơ người dùng: thành viên đi cùng, ngân sách quen, dị ứng, khả năng di chuyển."""
        return ("users", user_id, "profile")

    @staticmethod
    def habits(user_id: str) -> tuple[str, ...]:
        """Thói quen / ưu tiên cá nhân rút ra sau các phiên (qua cổng lọc)."""
        return ("users", user_id, "habits")

    @staticmethod
    def knowledge(region: str) -> tuple[str, ...]:
        """Kho tri thức theo vùng: mẹo địa phương, nguồn tin cậy, mùa cao điểm."""
        return ("knowledge", region)

    @staticmethod
    def calibration(tool: str) -> tuple[str, ...]:
        """Hiệu chỉnh sai số: lệch giữa dự báo (giá, ETA, ...) và thực tế, theo từng tool."""
        return ("calibration", tool)

"""Prompt chiến lược theo nhóm người dùng — mỗi nhóm một file, phần chung ở common.py."""

from . import (
    p1_kham_pha_tiet_kiem,
    p2_thoai_mai_de_tiep_can,
    p3_thu_gian_trai_nghiem,
    p4_quy_thoi_gian_han_che,
)

BY_PERSONA = {
    "P1": p1_kham_pha_tiet_kiem.PROMPT,
    "P2": p2_thoai_mai_de_tiep_can.PROMPT,
    "P3": p3_thu_gian_trai_nghiem.PROMPT,
    "P4": p4_quy_thoi_gian_han_che.PROMPT,
}

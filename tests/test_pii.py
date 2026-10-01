from langchain_core.messages import HumanMessage

from ailusion.observability.pii import mask_payload, mask_text


def test_phone_vn():
    assert mask_text("gọi 0901234567 nhé") == "gọi <PHONE> nhé"
    assert mask_text("sđt +84 90 123 4567") == "sđt <PHONE>"
    assert mask_text("tel: 090.123.4567") == "tel: <PHONE>"
    assert mask_text("bàn 024 3826 1234") == "bàn <PHONE>"


def test_email():
    assert mask_text("mail thu.pham@example.vn") == "mail <EMAIL>"


def test_national_id_and_passport():
    assert mask_text("CCCD 001203004567") == "CCCD <NATIONAL_ID>"
    assert mask_text("CMND 123456789") == "CMND <NATIONAL_ID>"
    assert mask_text("hộ chiếu C1234567") == "hộ chiếu <PASSPORT>"


def test_card_luhn():
    assert mask_text("thẻ 4111 1111 1111 1111") == "thẻ <CARD>"
    # Không qua Luhn -> giữ nguyên
    assert mask_text("mã 4111 1111 1111 1112") == "mã 4111 1111 1111 1112"


def test_keeps_non_pii():
    s = "Ngân sách 15.000.000 VND, 3 ngày, 2 người lớn, ngày 2026-10-01, ts 1759312345678"
    assert mask_text(s) == s


def test_payload_recursive_and_messages():
    data = {"messages": [HumanMessage("liên hệ 0912345678")], "n": 3, "tags": ("a", "0987654321")}
    out = mask_payload(data)
    assert out["messages"][0]["content"] == "liên hệ <PHONE>"
    assert out["n"] == 3
    assert out["tags"] == ("a", "<PHONE>")

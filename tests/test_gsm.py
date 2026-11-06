from app.services import gsm
from app.services.phone import normalize_phone


def test_basic_table_size():
    assert len(gsm.GSM_BASIC) == 128


def test_pack7_hello():
    sept = [gsm._BASIC_IDX[c] for c in "hello"]
    assert gsm.pack7(sept).hex().upper() == "E8329BFD06"
    sept = [gsm._BASIC_IDX[c] for c in "hellohello"]
    assert gsm.pack7(sept).hex().upper() == "E8329BFD4697D9EC37"


def test_pack_unpack_roundtrip():
    text = "Tasdiqlash kodi: 482910. Hech kimga bermang!"
    sept = [gsm._BASIC_IDX[c] for c in text]
    assert gsm.decode_gsm7(gsm.unpack7(gsm.pack7(sept), len(sept))) == text


def test_split_gsm7_single_and_multi():
    s = gsm.split_message("a" * 160)
    assert s.encoding == "gsm7" and len(s.parts) == 1
    s = gsm.split_message("a" * 161)
    assert len(s.parts) == 2 and len(s.parts[0]) == 153
    # kengaytma belgisi 2 septet
    s = gsm.split_message("[" * 81)
    assert s.encoding == "gsm7" and len(s.parts) == 2


def test_split_ucs2():
    s = gsm.split_message("Салом " * 11)  # 66 belgi
    assert s.encoding == "ucs2" and len(s.parts) == 1
    s = gsm.split_message("Салом " * 13)  # 78 belgi
    assert s.encoding == "ucs2" and len(s.parts) == 2


def test_apostrophes():
    assert gsm.normalize_apostrophes("o‘zbek, oʻqish, g’ayrat") == "o'zbek, o'qish, g'ayrat"
    assert gsm.is_gsm7(gsm.normalize_apostrophes("Oʻzbekiston"))
    assert not gsm.is_gsm7("Oʻzbekiston")


def test_pdu_single_gsm7():
    pdu, ln = gsm.build_pdu("+998901234567", "hello", "gsm7")
    assert pdu.startswith("0011000C91")  # SMSC=00, SUBMIT, MR, 12 raqam, international
    assert "998901234567"[:2] == "99"
    assert pdu.endswith("05E8329BFD06")
    assert ln == len(pdu) // 2 - 1


def test_pdu_multipart_udh():
    pdu, ln = gsm.build_pdu("+998901234567", "x" * 10, "gsm7", ref=7, seq=2, total=3)
    assert pdu[2:4] == "51"  # UDHI bit
    assert "050003070302" in pdu
    # UDL = 7 (UDH) + 10 septet
    assert "11" in pdu  # 0x11 == 17
    assert ln == len(pdu) // 2 - 1


def test_pdu_ucs2():
    pdu, _ = gsm.build_pdu("+998901234567", "Салом", "ucs2")
    assert "0008AA0A" in pdu  # PID 00, DCS 08, VP AA, UDL 10 bayt
    assert pdu.endswith("04210430043B043E043C")


def test_phone_normalize():
    for raw in ["+998901234567", "998901234567", "901234567", "90 123-45-67", "0901234567", "+998 (90) 123 45 67"]:
        assert normalize_phone(raw) == "+998901234567", raw
    assert normalize_phone("12345") is None
    assert normalize_phone("+79991234567") is None
    assert normalize_phone("+79991234567", allow_foreign=True) == "+79991234567"

"""SMS kodlash: GSM 7-bit / UCS-2, qismlarga bo'lish va PDU yasash."""
from dataclasses import dataclass

GSM_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXT = {"\f": 0x0A, "^": 0x14, "{": 0x28, "}": 0x29, "\\": 0x2F, "[": 0x3C, "~": 0x3D, "]": 0x3E, "|": 0x40, "€": 0x65}
_BASIC_IDX = {c: i for i, c in enumerate(GSM_BASIC) if c != "\x1b"}
_EXT_REV = {v: k for k, v in GSM_EXT.items()}

APOSTROPHE_VARIANTS = "ʻʼ’‘`´ʹ′"


def normalize_apostrophes(text: str) -> str:
    for ch in APOSTROPHE_VARIANTS:
        text = text.replace(ch, "'")
    return text


def is_gsm7(text: str) -> bool:
    return all(c in _BASIC_IDX or c in GSM_EXT for c in text)


def _septets(ch: str) -> list[int]:
    if ch in _BASIC_IDX:
        return [_BASIC_IDX[ch]]
    return [0x1B, GSM_EXT[ch]]


@dataclass
class Split:
    encoding: str  # gsm7 | ucs2
    parts: list[str]


def split_message(text: str) -> Split:
    """Matnni kodlashni aniqlab, SMS qismlariga bo'ladi."""
    if is_gsm7(text):
        units = sum(len(_septets(c)) for c in text)
        if units <= 160:
            return Split("gsm7", [text])
        limit = 153
        parts, cur, cur_units = [], "", 0
        for c in text:
            n = len(_septets(c))
            if cur_units + n > limit:
                parts.append(cur)
                cur, cur_units = "", 0
            cur += c
            cur_units += n
        if cur:
            parts.append(cur)
        return Split("gsm7", parts)

    units = len(text.encode("utf-16-le")) // 2
    if units <= 70:
        return Split("ucs2", [text])
    limit = 67
    parts, cur, cur_units = [], "", 0
    for c in text:
        n = len(c.encode("utf-16-le")) // 2
        if cur_units + n > limit:
            parts.append(cur)
            cur, cur_units = "", 0
        cur += c
        cur_units += n
    if cur:
        parts.append(cur)
    return Split("ucs2", parts)


def pack7(septets: list[int], pad_bits: int = 0) -> bytes:
    out = bytearray()
    acc = 0
    accbits = pad_bits
    for s in septets:
        acc |= (s & 0x7F) << accbits
        accbits += 7
        while accbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            accbits -= 8
    if accbits > 0:
        out.append(acc & 0xFF)
    return bytes(out)


def unpack7(data: bytes, count: int | None = None) -> list[int]:
    acc = 0
    accbits = 0
    res: list[int] = []
    for b in data:
        acc |= b << accbits
        accbits += 8
        while accbits >= 7:
            res.append(acc & 0x7F)
            acc >>= 7
            accbits -= 7
    return res[:count] if count is not None else res


def decode_gsm7(septets: list[int]) -> str:
    out, esc = [], False
    for s in septets:
        if esc:
            out.append(_EXT_REV.get(s, " "))
            esc = False
        elif s == 0x1B:
            esc = True
        else:
            out.append(GSM_BASIC[s] if s < len(GSM_BASIC) else "?")
    return "".join(out)


def _phone_to_semi_octets(number: str) -> str:
    digits = number.lstrip("+")
    if len(digits) % 2:
        digits += "F"
    return "".join(digits[i + 1] + digits[i] for i in range(0, len(digits), 2))


def build_pdu(number: str, part_text: str, encoding: str, ref: int = 0, seq: int = 1, total: int = 1) -> tuple[str, int]:
    """SMS-SUBMIT PDU (hex) va AT+CMGS uchun uzunlik (SMSC qismisiz) qaytaradi."""
    digits = number.lstrip("+")
    multipart = total > 1
    first = 0x11 | (0x40 if multipart else 0)  # SUBMIT, VP relative, UDHI
    udh = bytes([0x05, 0x00, 0x03, ref & 0xFF, total, seq]) if multipart else b""

    if encoding == "gsm7":
        septets: list[int] = []
        for c in part_text:
            septets += _septets(c)
        if multipart:
            ud = pack7(septets, pad_bits=1)  # UDH 48 bit -> 7 septet chegarasigacha 1 bit to'ldirish
            udl = 7 + len(septets)
            ud_bytes = udh + ud
        else:
            ud_bytes = pack7(septets)
            udl = len(septets)
        dcs = 0x00
    else:
        body = part_text.encode("utf-16-be")
        ud_bytes = udh + body
        udl = len(ud_bytes)
        dcs = 0x08

    tpdu = (
        f"{first:02X}"
        "00"  # message reference (modem to'ldiradi)
        f"{len(digits):02X}91"
        f"{_phone_to_semi_octets(number)}"
        "00"  # PID
        f"{dcs:02X}"
        "AA"  # VP: 4 kun
        f"{udl:02X}"
        f"{ud_bytes.hex().upper()}"
    )
    return "00" + tpdu, len(tpdu) // 2

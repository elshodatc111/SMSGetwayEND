import re


def normalize_phone(raw: str, allow_foreign: bool = False) -> str | None:
    """+998XXXXXXXXX ko'rinishiga keltiradi. Noto'g'ri bo'lsa None."""
    if not raw:
        return None
    raw = raw.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") or raw.startswith("00"):
        if raw.startswith("00"):
            digits = digits[2:]
        if digits.startswith("998"):
            return "+" + digits if len(digits) == 12 else None
        if allow_foreign and 8 <= len(digits) <= 15:
            return "+" + digits
        return None
    if len(digits) == 9:
        return "+998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    if len(digits) == 10 and digits.startswith("0"):
        return "+998" + digits[1:]
    return None

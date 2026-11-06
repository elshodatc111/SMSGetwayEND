"""Admin paneldan o'zgartiriladigan sozlamalar (DB da saqlanadi)."""
from sqlalchemy.orm import Session

from ..models import AppSetting

# (kalit, tur, standart, sarlavha, izoh)
SETTING_DEFS: list[tuple[str, str, object, str, str]] = [
    ("rate_per_minute", "int", 1, "Bir raqamga 1 daqiqada maks. SMS", "0 = cheklovsiz (OTP himoyasi)"),
    ("rate_per_hour", "int", 5, "Bir raqamga 1 soatda maks. SMS", "0 = cheklovsiz"),
    ("message_ttl_seconds", "int", 900, "Navbatda kutish muddati (soniya)", "Modem yo'q yoki limit tugagan bo'lsa, shu vaqtdan keyin xabar 'expired' bo'ladi"),
    ("max_attempts", "int", 3, "Yuborishga urinishlar soni", "Xato bo'lsa qayta urinish"),
    ("retry_delay_seconds", "int", 15, "Qayta urinish oralig'i (soniya)", "Urinish raqamiga ko'paytiriladi"),
    ("send_interval_seconds", "int", 5, "SMSlar orasidagi pauza (soniya)", "Navbat bilan: bir vaqtda bitta SMS, har biridan keyin shu qadar kutiladi"),
    ("log_retention_days", "int", 90, "Jurnalni saqlash muddati (kun)", "Undan eski xabarlar o'chiriladi, statistika qoladi"),
    ("normalize_apostrophes", "bool", True, "Apostrofni avtomatik tuzatish", "ʻ ’ ‘ ` belgilarini oddiy ' ga almashtiradi (SMS arzonroq: 160 belgi)"),
    ("mask_text", "bool", True, "Jurnalda matnni yashirish", "Yuborilgach 4+ xonali raqamlar (OTP) *** bilan almashtiriladi"),
    ("allow_foreign", "bool", False, "Chet el raqamlariga ruxsat", "O'chiq bo'lsa faqat +998 raqamlar"),
    ("ussd_refresh_minutes", "int", 360, "SMS paket qoldig'ini USSD bilan yangilash (daqiqa)", "USSD kodi kiritilgan modemlar uchun"),
    ("max_parts", "int", 6, "Bitta xabarning maks. qismlari", "Uzun matn himoyasi"),
]
_DEFS = {k: (t, d) for k, t, d, _, _ in SETTING_DEFS}


def _parse(kind: str, raw: str, default):
    try:
        if kind == "int":
            return int(raw)
        if kind == "bool":
            return raw.lower() in ("1", "true", "yes", "on")
        return raw
    except Exception:
        return default


def get_setting(db: Session, key: str):
    kind, default = _DEFS[key]
    row = db.get(AppSetting, key)
    if row is None:
        return default
    return _parse(kind, row.value, default)


def all_settings(db: Session) -> dict:
    return {k: get_setting(db, k) for k in _DEFS}


def set_setting(db: Session, key: str, value) -> None:
    kind, _ = _DEFS[key]
    raw = ("1" if value else "0") if kind == "bool" else str(value)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=raw))
    else:
        row.value = raw

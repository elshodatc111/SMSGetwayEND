"""Xabar yaratish, tekshiruvlar va yakunlash (statistika, maskalash, webhook navbati)."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    CANCELLED,
    EXPIRED,
    FAILED,
    FINAL_STATUSES,
    QUEUED,
    SENDING,
    SENT,
    Message,
    Modem,
    Project,
    UsageDaily,
)
from ..security import mask_digits
from ..timeutil import iso_z, local_day, local_month, now_utc
from . import gsm
from .phone import normalize_phone
from .settings_store import all_settings
from .stats import modem_remaining, project_period_usage


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def serialize(m: Message) -> dict:
    return {
        "id": m.id,
        "external_id": m.external_id,
        "to": m.to_number,
        "status": m.status,
        "parts": m.parts,
        "parts_sent": m.parts_sent,
        "encoding": m.encoding,
        "error": m.error,
        "created_at": iso_z(m.created_at),
        "sent_at": iso_z(m.sent_at),
        "finished_at": iso_z(m.finished_at),
    }


def webhook_payload(m: Message) -> dict:
    data = serialize(m)
    data["project"] = m.project.name if m.project else None
    return {"event": "sms.status", "data": data}


def prepare_text(text: str, cfg: dict) -> tuple[str, gsm.Split]:
    text = (text or "").replace("\x00", "")
    if cfg["normalize_apostrophes"]:
        text = gsm.normalize_apostrophes(text)
    if not text.strip():
        raise ApiError(422, "empty_text", "Matn bo'sh bo'lishi mumkin emas")
    split = gsm.split_message(text)
    if len(split.parts) > cfg["max_parts"]:
        raise ApiError(
            422,
            "text_too_long",
            f"Matn juda uzun: {len(split.parts)} qism (maksimum {cfg['max_parts']})",
        )
    return text, split


def create_message(db: Session, project: Project, to: str, text: str, external_id: str | None = None) -> tuple[Message, bool]:
    """Xabarni tekshirib navbatga qo'yadi. (xabar, dublikatmi) qaytaradi."""
    cfg = all_settings(db)
    if not project.is_active:
        raise ApiError(403, "project_disabled", "Loyiha o'chirilgan")

    number = normalize_phone(to, cfg["allow_foreign"])
    if not number:
        raise ApiError(422, "invalid_phone", "Telefon raqam noto'g'ri. Format: +998901234567")
    text, split = prepare_text(text, cfg)
    parts = len(split.parts)

    if external_id:
        external_id = external_id[:100]
        dup = db.scalar(select(Message).where(Message.project_id == project.id, Message.external_id == external_id))
        if dup:
            return dup, True

    now = now_utc()
    # raqam bo'yicha rate-limit
    for window, key, label in ((60, "rate_per_minute", "daqiqada"), (3600, "rate_per_hour", "soatda")):
        limit = cfg[key]
        if limit and limit > 0:
            cnt = db.scalar(
                select(func.count()).select_from(Message).where(
                    Message.project_id == project.id,
                    Message.to_number == number,
                    Message.created_at >= now - timedelta(seconds=window),
                    Message.status != CANCELLED,
                )
            )
            if (cnt or 0) >= limit:
                raise ApiError(429, "rate_limited", f"Bu raqamga {label} {limit} tadan ortiq SMS yuborib bo'lmaydi")

    # loyiha limitlari
    if project.daily_limit or project.monthly_limit:
        used = project_period_usage(db, project.id)
        if project.daily_limit and used["day_parts"] + parts > project.daily_limit:
            raise ApiError(429, "project_daily_limit", "Loyihaning kunlik SMS limiti tugadi")
        if project.monthly_limit and used["month_parts"] + parts > project.monthly_limit:
            raise ApiError(429, "project_monthly_limit", "Loyihaning oylik SMS limiti tugadi")

    # modem sig'imi
    modems = list(db.scalars(select(Modem).where(Modem.is_enabled == True)))  # noqa: E712
    if not modems:
        raise ApiError(503, "no_modem", "Faol modem topilmadi")
    if not any((r := modem_remaining(db, m)) is None or r >= parts for m in modems):
        raise ApiError(429, "quota_exceeded", "SMS paketi tugagan: yuborish uchun limit yetarli emas")

    msg = Message(
        project_id=project.id,
        to_number=number,
        text=text,
        encoding=split.encoding,
        parts=parts,
        external_id=external_id,
        status=QUEUED,
        day=local_day(),
        month=local_month(),
        created_at=now,
        next_attempt_at=now,
    )
    db.add(msg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        dup = db.scalar(select(Message).where(Message.project_id == project.id, Message.external_id == external_id))
        if dup:
            return dup, True
        raise
    db.refresh(msg)
    return msg, False


def _bump_usage(db: Session, day: str, project_id: int, modem_id: int, sent: int, failed: int, parts: int) -> None:
    row = db.get(UsageDaily, (day, project_id, modem_id))
    if row is None:
        row = UsageDaily(day=day, project_id=project_id, modem_id=modem_id, sent=0, failed=0, parts=0)
        db.add(row)
    row.sent = (row.sent or 0) + sent
    row.failed = (row.failed or 0) + failed
    row.parts = (row.parts or 0) + parts


def finish_message(db: Session, msg: Message, status: str, error: str | None = None, parts_sent: int = 0) -> None:
    """Xabarni yakuniy holatga o'tkazadi: statistika, modem limiti, matnni yashirish, webhook."""
    assert status in FINAL_STATUSES
    if msg.status in FINAL_STATUSES:
        return
    cfg = all_settings(db)
    now = now_utc()
    msg.status = status
    msg.error = error
    msg.parts_sent = parts_sent
    msg.finished_at = now
    if status == SENT:
        msg.sent_at = now
    day = local_day(now)
    _bump_usage(
        db,
        day,
        msg.project_id,
        msg.modem_id or 0,
        1 if status == SENT else 0,
        0 if status in (SENT, CANCELLED) else 1,
        parts_sent,
    )
    if msg.modem_id and parts_sent:
        modem = db.get(Modem, msg.modem_id)
        if modem is not None and modem.quota_remaining is not None:
            modem.quota_remaining = max(0, modem.quota_remaining - parts_sent)
    if cfg["mask_text"]:
        msg.text = mask_digits(msg.text)
    if msg.project and msg.project.webhook_url and status != CANCELLED:
        msg.webhook_state = "pending"
        msg.webhook_next_at = now
    db.commit()


def expire_old(db: Session) -> int:
    cfg = all_settings(db)
    cutoff = now_utc() - timedelta(seconds=cfg["message_ttl_seconds"])
    rows = list(db.scalars(select(Message).where(Message.status == QUEUED, Message.created_at < cutoff).limit(200)))
    for m in rows:
        finish_message(db, m, EXPIRED, "Navbatda kutish muddati tugadi (faol modem yoki limit yo'q)")
    return len(rows)


def recover_stuck(db: Session) -> int:
    """Server qayta ishga tushganda 'sending' holatida qolganlarni xato deb belgilaydi."""
    rows = list(db.scalars(select(Message).where(Message.status == SENDING)))
    for m in rows:
        finish_message(db, m, FAILED, "Yuborish paytida server to'xtagan (natija noma'lum)")
    return len(rows)


def cleanup_old(db: Session) -> int:
    cfg = all_settings(db)
    days = cfg["log_retention_days"]
    if not days or days <= 0:
        return 0
    cutoff = now_utc() - timedelta(days=days)
    rows = list(
        db.scalars(
            select(Message).where(Message.created_at < cutoff, Message.status.in_(FINAL_STATUSES)).limit(1000)
        )
    )
    for m in rows:
        db.delete(m)
    db.commit()
    return len(rows)

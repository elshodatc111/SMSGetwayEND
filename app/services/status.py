"""Tizim holati (admin panel va /health uchun)."""
import os
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import VERSION, get_settings
from ..models import Message, Modem
from ..runtime import get_runtime
from .settings_store import get_setting
from .stats import modem_remaining, queue_info


def modem_rows(db: Session) -> list[dict]:
    rt = get_runtime()
    out = []
    for m in db.scalars(select(Modem).order_by(Modem.id)):
        out.append(
            {
                "row": m,
                "remaining": modem_remaining(db, m),
                "busy": m.imei in rt.pool._busy,  # noqa: SLF001
                "percent": round((m.signal or 0) / 31 * 100) if m.signal is not None else None,
            }
        )
    return out


def health(db: Session) -> dict:
    rt = get_runtime()
    rows = modem_rows(db)
    usable = [r for r in rows if r["row"].is_enabled and r["row"].is_online and (r["remaining"] is None or r["remaining"] > 0)]
    workers = rt.workers()
    q = queue_info(db)
    if not all(workers.values()):
        status = "down"
    elif usable:
        status = "ok"
    else:
        status = "degraded"
    return {
        "status": status,
        "modems_online": sum(1 for r in rows if r["row"].is_online),
        "modems_usable": len(usable),
        "queue": q["size"],
        "version": VERSION,
    }


def system_status(db: Session) -> dict:
    rt = get_runtime()
    cfg = get_settings()
    h = health(db)
    db_path = cfg.db_url.replace("sqlite:///", "") if cfg.db_url.startswith("sqlite") else None
    db_size = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else None
    q = queue_info(db)
    webhook_pending = int(db.scalar(select(func.count()).select_from(Message).where(Message.webhook_state == "pending")) or 0)
    webhook_failed = int(db.scalar(select(func.count()).select_from(Message).where(Message.webhook_state == "failed")) or 0)
    total_msgs = int(db.scalar(select(func.count()).select_from(Message)) or 0)
    return {
        **h,
        "uptime_seconds": int(time.time() - rt.started_at),
        "workers": rt.workers(),
        "queue_size": q["size"],
        "queue_oldest_age": q["oldest_age"],
        "db_size": db_size,
        "total_messages": total_msgs,
        "webhook_pending": webhook_pending,
        "webhook_failed": webhook_failed,
        "last_scan": rt.pool.last_scan,
        "last_scan_note": rt.pool.last_scan_note,
        "busy_modems": rt.pool.busy_count(),
        "send_interval": get_setting(db, "send_interval_seconds"),
        "timezone": cfg.timezone,
        "modems": modem_rows(db),
    }


def human_duration(seconds: int) -> str:
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d} kun")
    if h:
        parts.append(f"{h} soat")
    if m and not d:
        parts.append(f"{m} daqiqa")
    if not parts:
        parts.append(f"{s} soniya")
    return " ".join(parts)


def human_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

"""Vaqt yordamchilari. DB da hamma vaqt UTC (tz-siz) saqlanadi, ko'rsatishda Asia/Tashkent."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import get_settings


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz())


def local_now() -> datetime:
    return datetime.now(tz())


def local_day(dt: datetime | None = None) -> str:
    d = to_local(dt) if dt else local_now()
    return d.strftime("%Y-%m-%d")


def local_month(dt: datetime | None = None) -> str:
    d = to_local(dt) if dt else local_now()
    return d.strftime("%Y-%m")


def fmt(dt: datetime | None, with_seconds: bool = True) -> str:
    if not dt:
        return "—"
    return to_local(dt).strftime("%d.%m.%Y %H:%M:%S" if with_seconds else "%d.%m.%Y %H:%M")


def iso_z(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None

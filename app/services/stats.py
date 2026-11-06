"""Statistika va limitlar hisob-kitobi."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import QUEUED, SENDING, SENT, Message, Modem, UsageDaily
from ..timeutil import local_day, local_month, local_now, to_local, now_utc


def modem_used_month(db: Session, modem_id: int, month: str | None = None) -> int:
    month = month or local_month()
    q = select(func.coalesce(func.sum(UsageDaily.parts), 0)).where(
        UsageDaily.modem_id == modem_id, UsageDaily.day.like(month + "-%")
    )
    return int(db.scalar(q) or 0)


def modem_remaining(db: Session, row: Modem) -> int | None:
    """Modemda qolgan SMS soni. None = cheklanmagan/noma'lum."""
    if row.quota_remaining is not None and row.quota_checked_at is not None:
        if local_month(row.quota_checked_at) == local_month():
            return max(0, row.quota_remaining)
    if row.monthly_limit:
        return max(0, row.monthly_limit - modem_used_month(db, row.id))
    return None


def project_period_usage(db: Session, project_id: int) -> dict:
    """Loyiha: bugun/oy bo'yicha qabul qilingan SMS (qism) soni (navbatdagilar ham hisobda)."""
    day, month = local_day(), local_month()
    active = (QUEUED, SENDING, SENT)

    def parts(col, val):
        return int(
            db.scalar(
                select(func.coalesce(func.sum(Message.parts), 0)).where(
                    Message.project_id == project_id, col == val, Message.status.in_(active)
                )
            )
            or 0
        )

    return {"day_parts": parts(Message.day, day), "month_parts": parts(Message.month, month)}


def project_totals(db: Session, project_id: int) -> dict:
    day, month = local_day(), local_month()

    def agg(cond):
        r = db.execute(
            select(
                func.coalesce(func.sum(UsageDaily.sent), 0),
                func.coalesce(func.sum(UsageDaily.failed), 0),
                func.coalesce(func.sum(UsageDaily.parts), 0),
            ).where(UsageDaily.project_id == project_id, cond)
        ).one()
        return {"sent": int(r[0]), "failed": int(r[1]), "parts": int(r[2])}

    return {
        "today": agg(UsageDaily.day == day),
        "month": agg(UsageDaily.day.like(month + "-%")),
        "total": agg(UsageDaily.project_id == project_id),
    }


def daily_series(db: Session, project_id: int | None, days: int = 30) -> list[dict]:
    today = local_now().date()
    start = today - timedelta(days=days - 1)
    q = select(
        UsageDaily.day,
        func.sum(UsageDaily.sent),
        func.sum(UsageDaily.failed),
        func.sum(UsageDaily.parts),
    ).where(UsageDaily.day >= start.isoformat())
    if project_id is not None:
        q = q.where(UsageDaily.project_id == project_id)
    q = q.group_by(UsageDaily.day)
    data = {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in db.execute(q)}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        s, f, p = data.get(d, (0, 0, 0))
        out.append({"day": d, "sent": s, "failed": f, "parts": p})
    return out


def monthly_series(db: Session, project_id: int | None, months: int = 12) -> list[dict]:
    q = select(
        func.substr(UsageDaily.day, 1, 7),
        func.sum(UsageDaily.sent),
        func.sum(UsageDaily.failed),
        func.sum(UsageDaily.parts),
    )
    if project_id is not None:
        q = q.where(UsageDaily.project_id == project_id)
    q = q.group_by(func.substr(UsageDaily.day, 1, 7)).order_by(func.substr(UsageDaily.day, 1, 7).desc()).limit(months)
    return [
        {"month": r[0], "sent": int(r[1] or 0), "failed": int(r[2] or 0), "parts": int(r[3] or 0)}
        for r in db.execute(q)
    ]


def queue_info(db: Session) -> dict:
    n = int(db.scalar(select(func.count()).select_from(Message).where(Message.status.in_((QUEUED, SENDING)))) or 0)
    oldest = db.scalar(select(func.min(Message.created_at)).where(Message.status == QUEUED))
    age = int((now_utc() - oldest).total_seconds()) if oldest else 0
    return {"size": n, "oldest_age": age}

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .timeutil import now_utc

# Xabar holatlari
QUEUED = "queued"
SENDING = "sending"
SENT = "sent"
FAILED = "failed"
CANCELLED = "cancelled"
EXPIRED = "expired"
FINAL_STATUSES = (SENT, FAILED, CANCELLED, EXPIRED)
ALL_STATUSES = (QUEUED, SENDING, SENT, FAILED, CANCELLED, EXPIRED)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(20), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    webhook_url: Mapped[str] = mapped_column(String(500), default="")
    webhook_secret: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Modem(Base):
    __tablename__ = "modems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    imei: Mapped[str] = mapped_column(String(40), unique=True)
    port: Mapped[str] = mapped_column(String(100), default="")
    label: Mapped[str] = mapped_column(String(100), default="")
    operator: Mapped[str] = mapped_column(String(60), default="")
    sim_number: Mapped[str] = mapped_column(String(30), default="")
    signal: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # CSQ 0..31
    registered: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # SMS paketi
    quota_remaining: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quota_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ussd_code: Mapped[str] = mapped_column(String(100), default="")
    ussd_regex: Mapped[str] = mapped_column(String(200), default="")
    ussd_reply: Mapped[str] = mapped_column(Text, default="")
    ussd_error: Mapped[str] = mapped_column(Text, default="")

    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_project_external"),
        Index("ix_msg_queue", "status", "next_attempt_at"),
        Index("ix_msg_project_day", "project_id", "day"),
        Index("ix_msg_project_month", "project_id", "month"),
        Index("ix_msg_webhook", "webhook_state", "webhook_next_at"),
        Index("ix_msg_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    modem_id: Mapped[Optional[int]] = mapped_column(ForeignKey("modems.id"), nullable=True)
    to_number: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    encoding: Mapped[str] = mapped_column(String(8), default="gsm7")
    parts: Mapped[int] = mapped_column(Integer, default=1)
    parts_sent: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default=QUEUED, index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    day: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (Toshkent)
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    webhook_state: Mapped[str] = mapped_column(String(10), default="none")  # none/pending/delivered/failed
    webhook_attempts: Mapped[int] = mapped_column(Integer, default=0)
    webhook_next_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    webhook_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(lazy="joined")
    modem: Mapped[Optional[Modem]] = relationship(lazy="joined")


class UsageDaily(Base):
    """Yakunlangan xabarlar bo'yicha kunlik statistika (jurnal tozalansa ham qoladi)."""

    __tablename__ = "usage_daily"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    modem_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)  # muvaffaqiyatli xabarlar
    failed: Mapped[int] = mapped_column(Integer, default=0)  # xato/muddati o'tgan
    parts: Mapped[int] = mapped_column(Integer, default=0)  # haqiqiy yuborilgan SMS (qism) soni


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))

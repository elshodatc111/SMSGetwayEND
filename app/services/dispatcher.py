"""Navbatdan xabarlarni olib, bo'sh modemlarga topshiruvchi fon oqimi."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select

from ..database import SessionLocal
from ..models import FAILED, QUEUED, SENDING, SENT, Message, Modem
from ..modem.base import SendError
from ..modem.pool import ModemPool
from ..timeutil import now_utc
from . import gsm
from .messaging import expire_old, finish_message
from .settings_store import all_settings
from .stats import modem_remaining

log = logging.getLogger("dispatcher")


class Dispatcher(threading.Thread):
    def __init__(self, pool: ModemPool):
        super().__init__(name="dispatcher", daemon=True)
        self.pool = pool
        self._stop_evt = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="send")
        self._last_expire = 0.0
        self.heartbeat = 0.0
        # Navbat bilan yuborish: bir vaqtda bitta SMS, har biridan keyin pauza
        self._in_flight = threading.Event()
        self._next_at = 0.0  # time.monotonic(): keyingi SMS shundan oldin boshlanmaydi

    def stop(self) -> None:
        self._stop_evt.set()
        self._executor.shutdown(wait=False, cancel_futures=False)

    def run(self) -> None:
        while not self._stop_evt.is_set():
            self.heartbeat = time.time()
            did = False
            try:
                did = self._tick()
            except Exception:
                log.exception("Dispatcher xatosi")
            if not did:
                self._stop_evt.wait(0.25)

    def _tick(self) -> bool:
        with SessionLocal() as db:
            if time.monotonic() - self._last_expire > 5:
                self._last_expire = time.monotonic()
                expire_old(db)

            if self._in_flight.is_set() or time.monotonic() < self._next_at:
                return False
            idle = self.pool.idle()
            if not idle:
                return False
            rows = {
                r.imei: r
                for r in db.scalars(
                    select(Modem).where(Modem.imei.in_([m.imei for m in idle]), Modem.is_enabled == True)  # noqa: E712
                )
            }
            now = now_utc()
            did = False
            for modem in idle:
                row = rows.get(modem.imei)
                if row is None:
                    continue
                remaining = modem_remaining(db, row)
                candidates = db.scalars(
                    select(Message)
                    .where(Message.status == QUEUED, Message.next_attempt_at <= now)
                    .order_by(Message.id)
                    .limit(30)
                )
                msg = next((m for m in candidates if remaining is None or m.parts <= remaining), None)
                if msg is None:
                    continue
                if not self.pool.acquire(modem.imei):
                    continue
                msg.status = SENDING
                msg.modem_id = row.id
                msg.attempts += 1
                msg.started_at = now
                db.commit()
                self._in_flight.set()
                self._executor.submit(self._deliver, msg.id, modem.imei)
                return True  # faqat bittasi: qolganlari navbatda kutadi
            return False

    def _deliver(self, message_id: int, imei: str) -> None:
        interval = 5.0
        try:
            with SessionLocal() as db:
                msg = db.get(Message, message_id)
                if msg is None or msg.status != SENDING:
                    return
                cfg = all_settings(db)
                interval = float(cfg["send_interval_seconds"])
                modem = self.pool.get(imei)
                try:
                    if modem is None:
                        raise SendError("Modem uzilgan", 0, fatal=True)
                    split = gsm.split_message(self._original_text(msg))
                    sent = modem.send_sms(msg.to_number, split.parts, split.encoding)
                    finish_message(db, msg, SENT, None, sent)
                    log.info("SMS #%s yuborildi (%s)", msg.id, msg.to_number)
                except SendError as e:
                    self._on_failure(db, msg, str(e), e.parts_sent, cfg)
                    if e.fatal:
                        self.pool.drop(imei, str(e))
                except Exception as e:  # noqa: BLE001
                    log.exception("Yuborishda kutilmagan xato")
                    self._on_failure(db, msg, f"Kutilmagan xato: {e}", 0, cfg)
        finally:
            self.pool.release(imei)
            self._next_at = time.monotonic() + max(0.0, interval)
            self._in_flight.clear()

    @staticmethod
    def _original_text(msg: Message) -> str:
        return msg.text  # yuborilgunga qadar matn maskalanmaydi

    @staticmethod
    def _on_failure(db, msg: Message, error: str, parts_sent: int, cfg: dict) -> None:
        if parts_sent > 0:
            finish_message(db, msg, FAILED, f"Qisman yuborildi ({parts_sent}/{msg.parts}): {error}", parts_sent)
            return
        if msg.attempts < cfg["max_attempts"]:
            msg.status = QUEUED
            msg.error = error
            msg.next_attempt_at = now_utc() + timedelta(seconds=cfg["retry_delay_seconds"] * msg.attempts)
            db.commit()
            log.warning("SMS #%s qayta uriniladi (%s): %s", msg.id, msg.attempts, error)
        else:
            finish_message(db, msg, FAILED, error, 0)
            log.error("SMS #%s yuborilmadi: %s", msg.id, error)

"""Natija xabarlari (webhook): HMAC imzo bilan, qayta urinishlar bilan."""
import json
import logging
import threading
import time
from datetime import timedelta

import httpx
from sqlalchemy import select

from ..database import SessionLocal
from ..models import Message
from ..security import sign_payload
from ..timeutil import now_utc
from .messaging import webhook_payload

log = logging.getLogger("webhook")

RETRY_DELAYS = [30, 120, 600, 3600, 21600]  # soniya


def send_webhook(url: str, secret: str, payload: dict, timeout: float = 10.0) -> tuple[bool, str]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SMS-Gateway/1.0",
        "X-SMS-Timestamp": ts,
        "X-SMS-Signature": sign_payload(secret, ts, body) if secret else "",
    }
    try:
        r = httpx.post(url, content=body, headers=headers, timeout=timeout, follow_redirects=False)
        if 200 <= r.status_code < 300:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:300]


class WebhookWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="webhook", daemon=True)
        self._stop_evt = threading.Event()
        self.heartbeat = 0.0

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            self.heartbeat = time.time()
            try:
                did = self._tick()
            except Exception:
                log.exception("Webhook xatosi")
                did = False
            if not did:
                self._stop_evt.wait(2)

    def _tick(self) -> bool:
        with SessionLocal() as db:
            now = now_utc()
            msgs = list(
                db.scalars(
                    select(Message)
                    .where(Message.webhook_state == "pending", Message.webhook_next_at <= now)
                    .order_by(Message.id)
                    .limit(10)
                )
            )
            for m in msgs:
                url = m.project.webhook_url if m.project else ""
                if not url:
                    m.webhook_state = "none"
                    db.commit()
                    continue
                ok, info = send_webhook(url, m.project.webhook_secret, webhook_payload(m))
                m.webhook_attempts += 1
                if ok:
                    m.webhook_state = "delivered"
                    m.webhook_error = None
                elif m.webhook_attempts > len(RETRY_DELAYS):
                    m.webhook_state = "failed"
                    m.webhook_error = info
                else:
                    m.webhook_next_at = now_utc() + timedelta(seconds=RETRY_DELAYS[m.webhook_attempts - 1])
                    m.webhook_error = info
                db.commit()
            return bool(msgs)

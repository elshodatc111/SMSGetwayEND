"""Fon xizmatlari: modem puli, dispatcher, webhook, jurnal tozalash."""
import logging
import threading
import time

from .config import Settings
from .database import SessionLocal
from .modem.pool import ModemPool
from .services.dispatcher import Dispatcher
from .services.messaging import cleanup_old, recover_stuck
from .services.webhook import WebhookWorker

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.started_at = time.time()
        self.pool = ModemPool(cfg)
        self.dispatcher = Dispatcher(self.pool)
        self.webhook = WebhookWorker()
        self._stop = threading.Event()
        self._janitor = threading.Thread(target=self._janitor_loop, name="janitor", daemon=True)
        self.janitor_heartbeat = 0.0

    def start(self) -> None:
        with SessionLocal() as db:
            n = recover_stuck(db)
            if n:
                log.warning("%s ta 'sending' xabar xato deb belgilandi", n)
        self.pool.start()
        self.dispatcher.start()
        self.webhook.start()
        self._janitor.start()

    def stop(self) -> None:
        self._stop.set()
        self.dispatcher.stop()
        self.webhook.stop()
        self.pool.stop()

    def _janitor_loop(self) -> None:
        while not self._stop.is_set():
            self.janitor_heartbeat = time.time()
            try:
                with SessionLocal() as db:
                    while cleanup_old(db) >= 1000:
                        pass
            except Exception:
                log.exception("Tozalashda xato")
            self._stop.wait(3600)

    def workers(self) -> dict[str, bool]:
        now = time.time()
        return {
            "Dispatcher (navbat)": now - self.dispatcher.heartbeat < 15 and self.dispatcher.is_alive(),
            "Modem skaneri": now - self.pool.heartbeat < self.cfg.scan_interval + 90,
            "Webhook": now - self.webhook.heartbeat < 15 and self.webhook.is_alive(),
        }


runtime: Runtime | None = None


def get_runtime() -> Runtime:
    assert runtime is not None, "Runtime ishga tushmagan"
    return runtime

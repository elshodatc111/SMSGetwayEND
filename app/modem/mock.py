"""Soxta modem: haqiqiy modemsiz ishlab chiqish va sinash uchun."""
import random
import threading
import time

from .base import BaseModem, ModemError, ModemInfo, SendError


class MockModem(BaseModem):
    is_mock = True

    def __init__(self, index: int = 1, delay: float = 1.0, fail_rate: float = 0.0, quota: int = 100):
        super().__init__(f"MOCK{index}")
        self.index = index
        self.delay = delay
        self.fail_rate = fail_rate
        self.quota = quota
        self.sent_log: list[tuple[str, str]] = []
        self.lock = threading.RLock()
        self.info = ModemInfo(
            imei=f"86000000000000{index}",
            port=f"MOCK{index}",
            imsi=f"4780700000000{index:02d}",
            operator="MockTel",
            number=f"+99890000000{index}",
            signal=22,
            registered=True,
        )

    def probe(self) -> ModemInfo:
        return self.info  # type: ignore[return-value]

    def refresh_status(self) -> ModemInfo:
        return self.info  # type: ignore[return-value]

    def send_sms(self, number: str, parts: list[str], encoding: str) -> int:
        with self.lock:
            time.sleep(self.delay)
            if random.random() < self.fail_rate:
                raise SendError("Mock: tasodifiy xato", 0)
            for p in parts:
                self.sent_log.append((number, p))
            self.quota = max(0, self.quota - len(parts))
            return len(parts)

    def ussd(self, sequence: str, timeout: float = 30) -> str:
        if not sequence.strip():
            raise ModemError("USSD kodi bo'sh")
        return f"Sizda {self.quota} ta SMS qoldi. Amal qilish muddati: 30 kun."

    def raw_command(self, cmd: str, timeout: float = 8) -> str:
        c = cmd.strip().upper()
        if not c.startswith("AT"):
            raise ModemError("Faqat AT buyruqlar ruxsat etiladi")
        if c == "AT+CGSN":
            return f"{self.info.imei}\nOK"  # type: ignore[union-attr]
        if c == "AT+CSQ":
            return "+CSQ: 22,99\nOK"
        return "OK"

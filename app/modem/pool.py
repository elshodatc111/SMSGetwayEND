"""Modemlar puli: avtomatik topish, holat kuzatuvi, band/bo'sh boshqaruvi, USSD orqali paket qoldig'i."""
import glob
import logging
import sys
import threading
import time

from sqlalchemy import select

from ..config import Settings
from ..database import SessionLocal
from ..models import Modem
from ..services.settings_store import get_setting
from ..timeutil import now_utc
from .at_modem import ATModem
from .base import BaseModem, ModemError, parse_quota

log = logging.getLogger("pool")


class ModemPool:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self._lock = threading.RLock()
        self.modems: dict[str, BaseModem] = {}
        self._busy: set[str] = set()
        self._bad_ports: dict[str, float] = {}
        self._scan_evt = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_scan = None
        self.last_scan_note = ""
        self.port_report: dict[str, str] = {}  # port -> oxirgi tekshiruv natijasi (diagnostika)
        self.heartbeat = 0.0

    # ---------- band/bo'sh ----------
    def acquire(self, imei: str) -> bool:
        with self._lock:
            if imei in self._busy or imei not in self.modems:
                return False
            self._busy.add(imei)
            return True

    def release(self, imei: str) -> None:
        with self._lock:
            self._busy.discard(imei)

    def get(self, imei: str) -> BaseModem | None:
        with self._lock:
            return self.modems.get(imei)

    def idle(self) -> list[BaseModem]:
        with self._lock:
            return [m for i, m in self.modems.items() if i not in self._busy]

    def online_count(self) -> int:
        with self._lock:
            return len(self.modems)

    def busy_count(self) -> int:
        with self._lock:
            return len(self._busy)

    def drop(self, imei: str, reason: str = "") -> None:
        """Modemni uzilgan deb belgilaydi (keyingi skanerlashda qayta topiladi)."""
        with self._lock:
            m = self.modems.pop(imei, None)
            self._busy.discard(imei)
        if m:
            log.warning("Modem olib tashlandi %s: %s", imei, reason)
            m.close()
            self._db_offline(imei, reason)
            self._scan_evt.set()

    # ---------- hayot sikli ----------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="modem-pool", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._scan_evt.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            for m in self.modems.values():
                m.close()

    def attach(self, modem: BaseModem) -> None:
        """Tayyor modem obyektini qo'shadi (avtomatik testlar uchun)."""
        info = modem.probe()
        with self._lock:
            self.modems[info.imei] = modem
        self._sync_db()

    def request_scan(self) -> None:
        self._scan_evt.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.heartbeat = time.time()
            try:
                self.scan_once()
            except Exception:
                log.exception("Skanerlashda xato")
            self._scan_evt.wait(self.cfg.scan_interval)
            self._scan_evt.clear()

    # ---------- skanerlash ----------
    def _candidate_ports(self) -> list[str]:
        if self.cfg.serial_ports.strip():
            ports = [p.strip() for p in self.cfg.serial_ports.split(",") if p.strip()]
        elif sys.platform.startswith("linux"):
            ports = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
        else:
            from serial.tools import list_ports

            ports = [p.device for p in list_ports.comports()]
        skip = {p.strip() for p in self.cfg.serial_exclude.split(",") if p.strip()}
        return [p for p in ports if p not in skip]

    def scan_once(self) -> None:
        notes = []
        # 1) mavjud modemlar sog'lig'i
        for imei in list(self.modems.keys()):
            if not self.acquire(imei):
                continue
            m = self.modems.get(imei)
            try:
                if m:
                    m.refresh_status()
                    m.fail_count = 0
            except Exception as e:
                if m:
                    m.fail_count += 1
                    if m.fail_count >= 2:
                        self.release(imei)
                        self.drop(imei, f"javob bermadi: {e}")
                        continue
            finally:
                self.release(imei)

        # 2) yangi modemlarni topish
        known_ports = {m.port for m in self.modems.values()}
        now = time.monotonic()
        ports = self._candidate_ports()
        for gone in [p for p in self.port_report if p not in ports]:
            self.port_report.pop(gone, None)
        notes.append(f"portlar: {', '.join(ports) if ports else 'topilmadi'}")
        for port in ports:
            if port in known_ports or self._bad_ports.get(port, 0) > now:
                continue
            modem = ATModem(port, self.cfg.serial_baudrate)
            try:
                info = modem.probe()
            except Exception as e:
                self._bad_ports[port] = now + 30
                msg = f"{type(e).__name__}: {e}"
                if self.port_report.get(port) != msg:
                    log.warning("Port %s modem sifatida ochilmadi: %s", port, msg)
                self.port_report[port] = msg
                continue
            with self._lock:
                dup = info.imei in self.modems
                if not dup:
                    self.modems[info.imei] = modem
            if dup:
                modem.close()
                self._bad_ports[port] = now + 600  # shu modemning ikkinchi porti
                self.port_report[port] = f"Bu modemning ikkinchi porti (IMEI {info.imei}), o'tkazib yuborildi"
            else:
                self.port_report[port] = f"Modem: IMEI {info.imei}, {info.operator or '?'}"
                log.info("Modem topildi: %s (%s) %s", info.imei, port, info.operator)

        self._sync_db()
        self._refresh_quotas()
        self.last_scan = now_utc()
        self.last_scan_note = "; ".join(notes)

    # ---------- DB bilan sinxron ----------
    def _sync_db(self) -> None:
        with SessionLocal() as db:
            active = {}
            with self._lock:
                active = dict(self.modems)
            rows = {r.imei: r for r in db.scalars(select(Modem))}
            for imei, m in active.items():
                info = m.info
                if info is None:
                    continue
                row = rows.get(imei)
                if row is None:
                    row = Modem(
                        imei=imei, is_mock=m.is_mock, port="", label="", operator="", sim_number="",
                        ussd_code="", ussd_regex="", ussd_reply="", ussd_error="", last_error="",
                    )
                    db.add(row)
                    rows[imei] = row
                row.port = m.port
                row.operator = info.operator or row.operator or ""
                row.signal = info.signal
                row.registered = info.registered
                row.is_online = True
                row.is_mock = m.is_mock
                row.last_seen_at = now_utc()
                if info.number and not row.sim_number:
                    row.sim_number = info.number
                if not info.sim_ready:
                    row.last_error = "SIM karta tayyor emas (PIN yoki SIM yo'q)"
                elif (row.last_error or "").startswith("SIM karta"):
                    row.last_error = ""
            for imei, row in rows.items():
                if imei not in active and row.is_online:
                    row.is_online = False
            db.commit()

    def _db_offline(self, imei: str, reason: str) -> None:
        try:
            with SessionLocal() as db:
                row = db.scalar(select(Modem).where(Modem.imei == imei))
                if row:
                    row.is_online = False
                    row.last_error = reason[:300]
                    db.commit()
        except Exception:
            log.exception("DB yangilanmadi")

    # ---------- USSD / paket qoldig'i ----------
    def _refresh_quotas(self) -> None:
        with SessionLocal() as db:
            minutes = get_setting(db, "ussd_refresh_minutes")
            now = now_utc()
            due = []
            for row in db.scalars(select(Modem).where(Modem.ussd_code != "", Modem.is_enabled == True)):  # noqa: E712
                if row.imei not in self.modems:
                    continue
                last = row.quota_checked_at
                if minutes > 0 and (last is None or (now - last).total_seconds() >= minutes * 60):
                    # xato bo'lsa har doim qayta urinavermaslik uchun: 10 daqiqa kutish
                    if row.ussd_error and last is not None and (now - last).total_seconds() < 600:
                        continue
                    due.append(row.imei)
        for imei in due:
            try:
                self.refresh_quota(imei)
            except Exception:
                log.exception("USSD yangilash xatosi")

    def refresh_quota(self, imei: str) -> tuple[bool, str]:
        """USSD yuborib, qolgan SMS sonini yangilaydi. (muvaffaqiyat, xabar) qaytaradi."""
        with SessionLocal() as db:
            row = db.scalar(select(Modem).where(Modem.imei == imei))
            if row is None:
                return False, "Modem topilmadi"
            code, regex = row.ussd_code, row.ussd_regex
        if not code:
            return False, "USSD kodi kiritilmagan"
        if not self.acquire(imei):
            return False, "Modem band yoki ulanmagan, keyinroq urinib ko'ring"
        reply, error = "", ""
        try:
            m = self.get(imei)
            reply = m.ussd(code) if m else ""
            if not m:
                error = "Modem ulanmagan"
        except ModemError as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001
            error = f"Kutilmagan xato: {e}"
        finally:
            self.release(imei)

        value = parse_quota(reply, regex) if reply else None
        with SessionLocal() as db:
            row = db.scalar(select(Modem).where(Modem.imei == imei))
            if row is None:
                return False, "Modem topilmadi"
            row.ussd_reply = reply
            row.quota_checked_at = now_utc()
            if error:
                row.ussd_error = error
            elif value is None:
                row.ussd_error = "Javobdan SMS soni ajratilmadi (regex ni moslang)"
            else:
                row.ussd_error = ""
                row.quota_remaining = value
            db.commit()
            msg = row.ussd_error or f"Qolgan SMS: {value}"
        return (not error and value is not None), msg

    def raw_command(self, imei: str, cmd: str) -> str:
        if not self.acquire(imei):
            raise ModemError("Modem band yoki ulanmagan")
        try:
            m = self.get(imei)
            if not m:
                raise ModemError("Modem ulanmagan")
            return m.raw_command(cmd)
        finally:
            self.release(imei)

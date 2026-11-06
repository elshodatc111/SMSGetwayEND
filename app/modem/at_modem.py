"""Haqiqiy USB modem bilan AT buyruqlar orqali ishlash (pyserial)."""
import logging
import re
import threading
import time

import serial

from ..services import gsm
from .base import BaseModem, ModemError, ModemInfo, SendError

log = logging.getLogger("modem")

_FINAL_RE = re.compile(r"(?:^|\r\n)(OK|ERROR|NO CARRIER|NO DIALTONE|BUSY|\+CME ERROR:[^\r\n]*|\+CMS ERROR:[^\r\n]*)\r\n", re.S)


class ATModem(BaseModem):
    def __init__(self, port: str, baudrate: int = 115200):
        super().__init__(port)
        self.baudrate = baudrate
        self.ser: serial.Serial | None = None
        self.lock = threading.RLock()

    # ---------- past darajali I/O ----------
    def open(self, raw_control: bool = False) -> None:
        """Portni ochadi. raw_control=True bo'lsa DTR/RTS chiziqlariga tegilmaydi
        (ba'zi ZTE/Huawei usb-serial drayverlari ularni rad etib EPIPE beradi)."""
        self.close()
        self.ser = serial.Serial(
            self.port, self.baudrate, timeout=0.2, write_timeout=5,
            rtscts=False, xonxoff=False, dsrdtr=bool(raw_control),
        )

    def close(self) -> None:
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def _read_until(self, matcher, timeout: float) -> str:
        assert self.ser is not None
        end = time.monotonic() + timeout
        buf = ""
        while time.monotonic() < end:
            n = self.ser.in_waiting
            chunk = self.ser.read(n or 1)
            if chunk:
                buf += chunk.decode("latin-1")
                res = matcher(buf)
                if res is not None:
                    return buf
        return buf

    def _final_matcher(self, buf: str):
        m = _FINAL_RE.search(buf)
        return m.group(1) if m else None

    def command(self, cmd: str, timeout: float = 5.0, check: bool = True) -> str:
        """AT buyruq yuboradi, yakuniy javob (OK/ERROR) gacha o'qiydi. Javob matnini qaytaradi."""
        with self.lock:
            if not self.ser or not self.ser.is_open:
                raise ModemError("Port yopiq")
            try:
                self.ser.reset_input_buffer()
                self.ser.write((cmd + "\r").encode("latin-1"))
                raw = self._read_until(self._final_matcher, timeout)
            except (serial.SerialException, OSError) as e:
                raise ModemError(f"Port xatosi: {type(e).__name__}: {e}") from e
            m = _FINAL_RE.search(raw)
            if not m:
                if check:
                    raise ModemError(f"Javob yo'q ({cmd})")
                return raw
            final = m.group(1)
            if check and final != "OK":
                raise ModemError(f"{cmd} -> {final}")
            return raw

    # ---------- ma'lumot ----------
    @staticmethod
    def _line_value(raw: str, prefix: str) -> str:
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""

    def _read_info(self, full: bool) -> ModemInfo:
        info = self.info or ModemInfo(imei="", port=self.port)
        if full:
            raw = self.command("AT+CGSN", 4)
            imei = re.search(r"\b(\d{14,17})\b", raw)
            if not imei:
                raise ModemError("IMEI o'qilmadi")
            info.imei = imei.group(1)
            try:
                raw = self.command("AT+CIMI", 4)
                m = re.search(r"\b(\d{10,16})\b", raw)
                info.imsi = m.group(1) if m else ""
            except ModemError:
                pass
            try:
                raw = self.command("AT+CNUM", 4)
                m = re.search(r'\+CNUM:[^,]*,"(\+?\d+)"', raw)
                info.number = m.group(1) if m else ""
            except ModemError:
                pass
        try:
            raw = self.command("AT+CPIN?", 4)
            info.sim_ready = "READY" in raw
        except ModemError:
            info.sim_ready = False
        try:
            raw = self.command("AT+CSQ", 4)
            m = re.search(r"\+CSQ:\s*(\d+)", raw)
            if m:
                v = int(m.group(1))
                info.signal = None if v == 99 else v
        except ModemError:
            pass
        reg = False
        for q in ("AT+CREG?", "AT+CEREG?"):
            try:
                raw = self.command(q, 4)
                m = re.search(r"\+C[E]?REG:\s*\d+,(\d+)", raw)
                if m and m.group(1) in ("1", "5"):
                    reg = True
                    break
            except ModemError:
                continue
        info.registered = reg
        try:
            self.command("AT+COPS=3,0", 3, check=False)
            raw = self.command("AT+COPS?", 6)
            m = re.search(r'\+COPS:\s*\d+,\d+,"([^"]*)"', raw)
            if m:
                info.operator = m.group(1)
        except ModemError:
            pass
        return info

    def probe(self) -> ModemInfo:
        """Portni ochib AT ga javob berishini tekshiradi va modem ma'lumotini o'qiydi.
        Port xatosida (EPIPE va h.k.) portni qayta ochib, boshqa rejim bilan urinadi."""
        last: Exception | None = None
        for attempt in range(4):
            try:
                self.open(raw_control=attempt % 2 == 1)
                ok = False
                for _ in range(3):
                    try:
                        self.command("ATE0", 1.5)
                        ok = True
                        break
                    except ModemError as e:
                        last = e
                        time.sleep(0.2)
                if not ok:
                    raise ModemError(f"AT ga javob bermadi ({last})" if last else "AT ga javob bermadi")
                self.command("AT+CMEE=1", 2, check=False)
                self.info = self._read_info(full=True)
                return self.info
            except (serial.SerialException, OSError, ModemError) as e:
                last = e
                self.close()
                time.sleep(0.5)
        self.close()
        raise ModemError(f"Port ochilmadi: {type(last).__name__}: {last}")

    def refresh_status(self) -> ModemInfo:
        with self.lock:
            try:
                self.command("AT", 2)
            except ModemError:
                self.command("AT", 3)  # ikkinchi urinish
            self.info = self._read_info(full=False)
            return self.info

    # ---------- SMS ----------
    def send_sms(self, number: str, parts: list[str], encoding: str) -> int:
        total = len(parts)
        ref = int(time.time()) & 0xFF
        sent = 0
        with self.lock:
            try:
                self.command("AT+CMGF=0", 3)
            except ModemError as e:
                raise SendError(f"PDU rejimi yoqilmadi: {e}", 0, fatal=True) from e
            for i, text in enumerate(parts, start=1):
                pdu, tpdu_len = gsm.build_pdu(number, text, encoding, ref=ref, seq=i, total=total)
                try:
                    self._send_pdu(pdu, tpdu_len)
                except SendError as e:
                    e.parts_sent = sent
                    raise
                sent += 1
        return sent

    def _send_pdu(self, pdu: str, tpdu_len: int) -> None:
        assert self.ser is not None
        try:
            self.ser.reset_input_buffer()
            self.ser.write(f"AT+CMGS={tpdu_len}\r".encode())
            prompt = self._read_until(lambda b: True if ("> " in b or ">" in b[-3:] or _FINAL_RE.search(b)) else None, 10)
            if ">" not in prompt:
                raise SendError(f"Modem '>' belgisini bermadi: {prompt.strip()[:120]}", 0, fatal=not prompt.strip())
            self.ser.write(pdu.encode() + b"\x1a")
            raw = self._read_until(self._final_matcher, 60)
        except (serial.SerialException, OSError) as e:
            raise SendError(f"Port xatosi: {e}", 0, fatal=True) from e
        m = _FINAL_RE.search(raw)
        if not m:
            try:
                self.ser.write(b"\x1b")
            except Exception:
                pass
            raise SendError("Modem javobi kutilganidan uzoq (timeout)", 0)
        if m.group(1) != "OK":
            raise SendError(f"Modem xatosi: {m.group(1)}", 0)

    # ---------- USSD ----------
    def ussd(self, sequence: str, timeout: float = 30) -> str:
        steps = [s.strip() for s in sequence.split(",") if s.strip()]
        if not steps:
            raise ModemError("USSD kodi bo'sh")
        reply = ""
        with self.lock:
            self.command('AT+CSCS="GSM"', 3, check=False)
            try:
                self.command("AT+CUSD=2", 3, check=False)  # eski sessiyani yopish
            except ModemError:
                pass
            for step in steps:
                if not self.ser:
                    raise ModemError("Port yopiq")
                self.ser.reset_input_buffer()
                self.ser.write(f'AT+CUSD=1,"{step}",15\r'.encode())
                reply = self._wait_cusd(timeout)
            try:
                self.command("AT+CUSD=2", 3, check=False)
            except ModemError:
                pass
        return reply

    def _wait_cusd(self, timeout: float) -> str:
        pat_text = re.compile(r'\+CUSD:\s*(\d+)\s*,\s*"(.*?)"\s*,\s*(\d+)', re.S)
        pat_err = re.compile(r"\+CME ERROR:[^\r\n]*|\r\nERROR\r\n")

        def matcher(b: str):
            if pat_text.search(b) or pat_err.search(b) or re.search(r"\+CUSD:\s*\d+\s*\r\n", b):
                return True
            return None

        raw = self._read_until(matcher, timeout)
        m = pat_text.search(raw)
        if m:
            return _decode_ussd(m.group(2), int(m.group(3)))
        e = pat_err.search(raw)
        if e:
            raise ModemError(f"USSD xatosi: {e.group(0).strip()}")
        if re.search(r"\+CUSD:", raw):
            return ""
        raise ModemError("USSD javobi kelmadi (timeout)")

    def raw_command(self, cmd: str, timeout: float = 8) -> str:
        cmd = cmd.strip()
        if not cmd.upper().startswith("AT"):
            raise ModemError("Faqat AT buyruqlar ruxsat etiladi")
        with self.lock:
            raw = self.command(cmd, timeout, check=False)
        return raw.replace("\r", "").strip()


def _decode_ussd(text: str, dcs: int) -> str:
    """USSD javobini dekodlash (modemga qarab oddiy matn, UCS2 yoki 7-bit hex bo'lishi mumkin)."""
    t = text.strip()
    is_hex = bool(re.fullmatch(r"[0-9A-Fa-f]+", t)) and len(t) % 2 == 0 and len(t) >= 8
    if is_hex and dcs in (72, 8) or (is_hex and dcs == 72):
        try:
            return bytes.fromhex(t).decode("utf-16-be")
        except Exception:
            return text
    if is_hex and dcs in (15, 0, 17):
        # ba'zi modemlar 7-bit paketlangan hex qaytaradi
        try:
            data = bytes.fromhex(t)
            return gsm.decode_gsm7(gsm.unpack7(data, len(data) * 8 // 7))
        except Exception:
            return text
    return text

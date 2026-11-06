"""ATModem ni pty (soxta serial port) orqali sinash: haqiqiy modemga o'xshash AT dialogi."""
import os
import select
import threading
import time

import pytest

from app.modem.at_modem import ATModem
from app.modem.base import ModemError, SendError, parse_quota
from app.services import gsm

pytestmark = pytest.mark.skipif(not hasattr(os, "openpty"), reason="pty kerak (Linux)")


class FakeModem(threading.Thread):
    def __init__(self, fail_send=False, cusd_text="Sizda 85 ta SMS qoldi. 30 kun"):
        super().__init__(daemon=True)
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.pdus: list[str] = []
        self.cmds: list[str] = []
        self.fail_send = fail_send
        self.cusd_text = cusd_text
        self._halt = False

    def w(self, s: str):
        os.write(self.master, s.encode("latin-1"))

    def run(self):
        buf = b""
        mode = "cmd"
        while not self._halt:
            r, _, _ = select.select([self.master], [], [], 0.1)
            if not r:
                continue
            buf += os.read(self.master, 4096)
            while True:
                if mode == "pdu":
                    if b"\x1a" not in buf:
                        break
                    pdu, buf = buf.split(b"\x1a", 1)
                    self.pdus.append(pdu.decode())
                    mode = "cmd"
                    if self.fail_send:
                        self.w("\r\n+CMS ERROR: 330\r\n")
                    else:
                        self.w("\r\n+CMGS: 7\r\n\r\nOK\r\n")
                    continue
                if b"\r" not in buf:
                    break
                line, buf = buf.split(b"\r", 1)
                cmd = line.decode().strip()
                if not cmd:
                    continue
                self.cmds.append(cmd)
                self.handle(cmd)
                if cmd.startswith("AT+CMGS="):
                    mode = "pdu"

    def handle(self, cmd: str):
        ok = "\r\nOK\r\n"
        table = {
            "AT+CGSN": "\r\n860123456789012\r\n" + ok,
            "AT+CIMI": "\r\n478070000000001\r\n" + ok,
            "AT+CNUM": '\r\n+CNUM: "","+998901112233",145\r\n' + ok,
            "AT+CPIN?": "\r\n+CPIN: READY\r\n" + ok,
            "AT+CSQ": "\r\n+CSQ: 21,99\r\n" + ok,
            "AT+CREG?": "\r\n+CREG: 0,1\r\n" + ok,
            "AT+CEREG?": "\r\nERROR\r\n",
            "AT+COPS?": '\r\n+COPS: 0,0,"Ucell",7\r\n' + ok,
            "AT+CGMM": "\r\nE3372\r\n" + ok,
        }
        if cmd in table:
            self.w(table[cmd])
        elif cmd.startswith("AT+CMGS="):
            self.w("\r\n> ")
        elif cmd.startswith('AT+CUSD=1,'):
            self.w(ok)
            time.sleep(0.2)
            self.w(f'\r\n+CUSD: 2,"{self.cusd_text}",15\r\n')
        else:
            self.w(ok)

    def close(self):
        self._halt = True
        self.join(timeout=2)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture()
def fake():
    f = FakeModem()
    f.start()
    yield f
    f.close()


def test_probe_reads_info(fake):
    m = ATModem(fake.path)
    info = m.probe()
    m.close()
    assert info.imei == "860123456789012"
    assert info.operator == "Ucell"
    assert info.number == "+998901112233"
    assert info.signal == 21 and info.registered and info.sim_ready


def test_send_single_and_multipart(fake):
    m = ATModem(fake.path)
    m.probe()
    sp = gsm.split_message("Salom dunyo")
    assert m.send_sms("+998901234567", sp.parts, sp.encoding) == 1
    sp = gsm.split_message("b" * 400)
    assert m.send_sms("+998901234567", sp.parts, sp.encoding) == 3
    m.close()
    assert len(fake.pdus) == 4
    assert fake.pdus[0].startswith("0011000C91")
    assert "AT+CMGF=0" in fake.cmds
    assert fake.pdus[1].startswith("0051")  # UDHI


def test_send_error_reports_parts(fake):
    fake.fail_send = True
    m = ATModem(fake.path)
    m.probe()
    sp = gsm.split_message("x")
    with pytest.raises(SendError) as e:
        m.send_sms("+998901234567", sp.parts, sp.encoding)
    assert "CMS ERROR" in str(e.value) and e.value.parts_sent == 0
    m.close()


def test_ussd_and_quota_parse(fake):
    m = ATModem(fake.path)
    m.probe()
    reply = m.ussd("*100#")
    m.close()
    assert "85 ta SMS" in reply
    assert parse_quota(reply) == 85


def test_ussd_multi_step(fake):
    m = ATModem(fake.path)
    m.probe()
    m.ussd("*100#,1,2")
    m.close()
    sent = [c for c in fake.cmds if c.startswith("AT+CUSD=1")]
    assert len(sent) == 3 and '"1"' in sent[1] and '"2"' in sent[2]


def test_raw_command_whitelist(fake):
    m = ATModem(fake.path)
    m.probe()
    assert "E3372" in m.raw_command("AT+CGMM")
    with pytest.raises(ModemError):
        m.raw_command("ls -la")
    m.close()


def test_no_response_port_is_rejected():
    master, slave = os.openpty()  # javob bermaydigan port
    try:
        m = ATModem(os.ttyname(slave))
        with pytest.raises(ModemError):
            m.probe()
    finally:
        os.close(master)
        os.close(slave)


def test_parse_quota_variants():
    assert parse_quota("Sizda 1 250 ta SMS qoldi") == 1250
    assert parse_quota("SMS: 120 dona") == 120
    assert parse_quota("Остаток: 45 смс") == 45
    assert parse_quota("Qoldiq 77", r"Qoldiq\s+(\d+)") == 77
    assert parse_quota("hech narsa yo'q") is None

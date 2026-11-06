import re
from dataclasses import dataclass, field


class ModemError(Exception):
    pass


class SendError(ModemError):
    def __init__(self, message: str, parts_sent: int = 0, fatal: bool = False):
        super().__init__(message)
        self.parts_sent = parts_sent
        self.fatal = fatal  # True: modem uzilgan/ishlamayapti


@dataclass
class ModemInfo:
    imei: str
    port: str
    imsi: str = ""
    operator: str = ""
    number: str = ""
    signal: int | None = None  # CSQ 0..31
    registered: bool = False
    sim_ready: bool = True


class BaseModem:
    is_mock = False

    def __init__(self, port: str):
        self.port = port
        self.info: ModemInfo | None = None
        self.fail_count = 0

    @property
    def imei(self) -> str:
        return self.info.imei if self.info else ""

    def probe(self) -> ModemInfo:  # pragma: no cover
        raise NotImplementedError

    def refresh_status(self) -> ModemInfo:  # pragma: no cover
        raise NotImplementedError

    def send_sms(self, number: str, parts: list[str], encoding: str) -> int:  # pragma: no cover
        raise NotImplementedError

    def ussd(self, sequence: str, timeout: float = 30) -> str:  # pragma: no cover
        raise NotImplementedError

    def raw_command(self, cmd: str, timeout: float = 8) -> str:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass


_DEFAULT_QUOTA_PATTERNS = [
    r"(\d[\d ]*)\s*(?:ta\s+|шт\.?\s*)?(?:sms|смс)",
    r"(?:sms|смс)[^\d]{0,25}(\d[\d ]*)",
]


def parse_quota(reply: str, custom_regex: str = "") -> int | None:
    """USSD javobidan qolgan SMS sonini ajratib oladi."""
    if not reply:
        return None
    patterns = ([custom_regex] if custom_regex else []) + _DEFAULT_QUOTA_PATTERNS
    for p in patterns:
        try:
            m = re.search(p, reply, re.IGNORECASE | re.DOTALL)
        except re.error:
            continue
        if m:
            grp = m.group(1) if m.groups() else m.group(0)
            digits = re.sub(r"\D", "", grp)
            if digits:
                return int(digits)
    return None

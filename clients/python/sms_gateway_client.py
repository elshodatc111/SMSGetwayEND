"""
SMS Gateway — Python klienti (faqat standart kutubxona, qo'shimcha o'rnatish shart emas).

Ishlatish:
    from sms_gateway_client import SmsGateway, verify_webhook

    sms = SmsGateway("https://sms.elshod.date", "sk_sms_XXXX")
    msg = sms.send("+998901234567", "Tasdiqlash kodi: 482910", external_id="otp-123")
    print(msg["id"], msg["status"])           # queued
    print(sms.status(msg["id"])["status"])    # sent / failed / queued ...
"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request


class SmsGatewayError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"[{status}] {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class SmsGateway:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base_url + "/api/v1" + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode())["error"]
            except Exception:
                err = {"code": "http_error", "message": str(e)}
            raise SmsGatewayError(e.code, err.get("code", "error"), err.get("message", "")) from None

    def send(self, to: str, text: str, external_id: str | None = None) -> dict:
        body = {"to": to, "text": text}
        if external_id:
            body["external_id"] = external_id
        return self._request("POST", "/sms/send", body)

    def status(self, message_id: int) -> dict:
        return self._request("GET", f"/sms/{message_id}")

    def cancel(self, message_id: int) -> dict:
        return self._request("DELETE", f"/sms/{message_id}")

    def usage(self) -> dict:
        return self._request("GET", "/me/usage")

    def wait(self, message_id: int, timeout: float = 60, interval: float = 1.5) -> dict:
        """Xabar yakunlanguncha kutadi (sent/failed/expired/cancelled)."""
        end = time.time() + timeout
        while True:
            m = self.status(message_id)
            if m["status"] in ("sent", "failed", "expired", "cancelled") or time.time() > end:
                return m
            time.sleep(interval)


def verify_webhook(secret: str, timestamp: str, body: bytes, signature: str, tolerance: int = 300) -> bool:
    """Webhook imzosini tekshiradi. Flask/FastAPI'da: headers['X-SMS-Timestamp'], headers['X-SMS-Signature']."""
    try:
        if abs(time.time() - int(timestamp)) > tolerance:
            return False
    except ValueError:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

"""Parol, API kalit va maskalash yordamchilari (faqat standart kutubxona)."""
import base64
import hashlib
import hmac
import re
import secrets

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def new_api_key() -> tuple[str, str, str]:
    """(to'liq kalit, hash, ko'rsatish uchun prefiks)"""
    key = "sk_sms_" + secrets.token_urlsafe(32)
    return key, hash_key(key), key[:12]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def mask_digits(text: str) -> str:
    """4 va undan ko'p raqamli ketma-ketlikni yulduzcha bilan almashtiradi (OTP kodlari)."""
    return re.sub(r"\d{4,}", lambda m: "*" * len(m.group(0)), text)


def new_csrf() -> str:
    return secrets.token_urlsafe(24)

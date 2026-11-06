"""Ilova sozlamalari (.env yoki muhit o'zgaruvchilaridan o'qiladi)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "1.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "SMS Gateway"
    data_dir: str = "./data"
    database_url: str = ""  # bo'sh bo'lsa: <data_dir>/sms_gateway.db
    secret_key: str = ""  # bo'sh bo'lsa: <data_dir>/secret.key ga avtomatik yaratiladi
    admin_username: str = "admin"
    admin_password: str = ""  # faqat birinchi ishga tushirishda (admin yaratish uchun)
    timezone: str = "Asia/Tashkent"
    cookie_secure: bool = False  # HTTPS (Cloudflare) orqali ishlaganda True qiling
    enable_docs: bool = False  # /docs (Swagger) sahifasi

    # Modemlar har doim haqiqiy USB qurilmalardan avtomatik qidiriladi
    scan_interval: int = 15
    serial_baudrate: int = 115200
    serial_ports: str = ""  # qo'lda port ro'yxati, vergul bilan (masalan: /dev/ttyUSB2). Bo'sh = avto
    serial_exclude: str = ""  # tegilmasin: boshqa dastur ishlatadigan portlar (masalan: /dev/ttyUSB0)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_path / 'sms_gateway.db').resolve().as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()

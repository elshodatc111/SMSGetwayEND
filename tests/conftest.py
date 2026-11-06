import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="smsgw_")
os.environ.update(
    DATA_DIR=_tmp,
    MODEM_MODE="mock",
    MOCK_MODEMS="1",
    MOCK_DELAY="0.05",
    ADMIN_USERNAME="admin",
    ADMIN_PASSWORD="testpass123",
    SCAN_INTERVAL="1",
    SECRET_KEY="test-secret-key",
)

import re
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Project
from app.security import new_api_key
from app.services.settings_store import set_setting


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        # modem sinxronlashini kutish
        for _ in range(50):
            r = c.get("/health")
            if r.json().get("modems_usable", 0) >= 1:
                break
            time.sleep(0.1)
        yield c


@pytest.fixture()
def project(client):
    """Yangi loyiha va uning API kaliti."""
    key, h, prefix = new_api_key()
    with SessionLocal() as db:
        n = db.query(Project).count()
        p = Project(name=f"test-{n}-{int(time.time()*1000)}", api_key_hash=h, key_prefix=prefix)
        db.add(p)
        db.commit()
        pid = p.id
    return {"id": pid, "key": key, "headers": {"Authorization": f"Bearer {key}"}}


@pytest.fixture(autouse=True)
def relaxed_rate_limits(client):
    # oldingi testlardan qolgan navbat tugashini kutish
    for _ in range(100):
        if client.get("/health").json().get("queue", 0) == 0:
            break
        time.sleep(0.1)
    time.sleep(0.6)  # modem pauzasi (send_interval)
    with SessionLocal() as db:
        set_setting(db, "rate_per_minute", 0)
        set_setting(db, "rate_per_hour", 0)
        db.commit()


def wait_status(client, headers, msg_id, wanted=("sent", "failed", "expired"), timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/v1/sms/{msg_id}", headers=headers)
        if r.json()["status"] in wanted:
            return r.json()
        time.sleep(0.1)
    return client.get(f"/api/v1/sms/{msg_id}", headers=headers).json()


@pytest.fixture()
def wait(client):
    return lambda headers, msg_id, **kw: wait_status(client, headers, msg_id, **kw)


@pytest.fixture()
def admin(client):
    """Kirgan admin klienti (alohida cookie jar)."""
    c = TestClient(app)  # lifespan'siz: ish vaqti (runtime) umumiy client'niki
    r = c.post("/admin/login", data={"username": "admin", "password": "testpass123"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    yield c


def csrf_of(c) -> str:
    html = c.get("/admin/settings").text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

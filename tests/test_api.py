import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.database import SessionLocal
from app.models import Modem, Project
from app.services.settings_store import set_setting


def send(client, project, to="+998901234567", text="Salom", **extra):
    return client.post("/api/v1/sms/send", json={"to": to, "text": text, **extra}, headers=project["headers"])


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert client.get("/health").status_code == 200


def test_auth_required(client):
    r = client.post("/api/v1/sms/send", json={"to": "+998901234567", "text": "x"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    r = client.post("/api/v1/sms/send", json={"to": "+998901234567", "text": "x"}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_send_flow(client, project, wait):
    r = send(client, project, text="Tasdiqlash kodi: 482910")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued" and body["parts"] == 1 and body["to"] == "+998901234567"
    final = wait(project["headers"], body["id"])
    assert final["status"] == "sent", final
    assert final["parts_sent"] == 1 and final["sent_at"]
    usage = client.get("/api/v1/me/usage", headers=project["headers"]).json()
    assert usage["today"]["sent"] == 1 and usage["today"]["parts"] == 1
    assert usage["month"]["parts"] == 1


def test_text_masked_after_send(client, project, wait):
    r = send(client, project, text="Kod: 123456")
    wait(project["headers"], r.json()["id"])
    with SessionLocal() as db:
        from app.models import Message
        assert db.get(Message, r.json()["id"]).text == "Kod: ******"


def test_validation(client, project):
    assert send(client, project, to="123").json()["error"]["code"] == "invalid_phone"
    assert send(client, project, text="   ").status_code in (422,)
    assert send(client, project, text="x" * 1200).json()["error"]["code"] == "text_too_long"
    r = client.post("/api/v1/sms/send", json={"text": "x"}, headers=project["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_idempotency(client, project, wait):
    a = send(client, project, external_id="order-1")
    b = send(client, project, external_id="order-1")
    assert a.status_code == 202 and b.status_code == 200
    assert a.json()["id"] == b.json()["id"] and b.json()["duplicate"] is True
    wait(project["headers"], a.json()["id"])


def test_apostrophe_keeps_gsm7(client, project, wait):
    r = send(client, project, text="Oʻzbekiston Respublikasi")
    assert r.json()["encoding"] == "gsm7"
    wait(project["headers"], r.json()["id"])


def test_multipart_counts_parts(client, project, wait):
    r = send(client, project, text="a" * 400)
    assert r.json()["parts"] == 3
    final = wait(project["headers"], r.json()["id"])
    assert final["status"] == "sent" and final["parts_sent"] == 3
    assert client.get("/api/v1/me/usage", headers=project["headers"]).json()["today"]["parts"] == 3


def test_number_rate_limit(client, project):
    with SessionLocal() as db:
        set_setting(db, "rate_per_minute", 1)
        db.commit()
    assert send(client, project).status_code == 202
    r = send(client, project)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert send(client, project, to="+998931112233").status_code == 202


def test_project_limits(client, project):
    with SessionLocal() as db:
        db.get(Project, project["id"]).daily_limit = 2
        db.commit()
    assert send(client, project, text="a" * 200).status_code == 202  # 2 qism
    r = send(client, project)
    assert r.status_code == 429 and r.json()["error"]["code"] == "project_daily_limit"


def test_disabled_project(client, project):
    with SessionLocal() as db:
        db.get(Project, project["id"]).is_active = False
        db.commit()
    assert send(client, project).status_code == 403


def test_cancel_and_get(client, project):
    with SessionLocal() as db:
        m = db.query(Modem).first()
        m.is_enabled = False
        db.commit()
    try:
        r = send(client, project)  # modem yoqilmagan -> 503
        assert r.status_code == 503 and r.json()["error"]["code"] == "no_modem"
    finally:
        with SessionLocal() as db:
            db.query(Modem).first().is_enabled = True
            db.commit()
    other = client.get("/api/v1/sms/999999", headers=project["headers"])
    assert other.status_code == 404


def test_modem_quota(client, project, wait):
    with SessionLocal() as db:
        m = db.query(Modem).first()
        m.quota_remaining = 1
        m.quota_checked_at = __import__("app.timeutil", fromlist=["now_utc"]).now_utc()
        db.commit()
    try:
        a = send(client, project)
        assert a.status_code == 202
        assert wait(project["headers"], a.json()["id"])["status"] == "sent"
        b = send(client, project)
        assert b.status_code == 429 and b.json()["error"]["code"] == "quota_exceeded"
    finally:
        with SessionLocal() as db:
            m = db.query(Modem).first()
            m.quota_remaining = None
            m.quota_checked_at = None
            db.commit()


def test_webhook_delivery(client, project, wait):
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.headers, body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with SessionLocal() as db:
        p = db.get(Project, project["id"])
        p.webhook_url = f"http://127.0.0.1:{srv.server_port}/hook"
        p.webhook_secret = "whsec_test"
        db.commit()
    r = send(client, project, external_id="wh-1")
    wait(project["headers"], r.json()["id"])
    end = time.time() + 10
    while not received and time.time() < end:
        time.sleep(0.2)
    srv.shutdown()
    assert received, "webhook kelmadi"
    headers, body = received[0]
    expected = "sha256=" + hmac.new(b"whsec_test", headers["X-SMS-Timestamp"].encode() + b"." + body, hashlib.sha256).hexdigest()
    assert headers["X-SMS-Signature"] == expected
    data = json.loads(body)
    assert data["event"] == "sms.status" and data["data"]["status"] == "sent" and data["data"]["external_id"] == "wh-1"

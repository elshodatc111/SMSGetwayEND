import re

from tests.conftest import csrf_of

PAGES = ["/admin", "/admin/status", "/admin/projects", "/admin/modems", "/admin/messages", "/admin/send", "/admin/settings"]


def test_requires_login(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/login"
    assert client.get("/admin/login").status_code == 200


def test_bad_login(client):
    r = client.post("/admin/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_all_pages_render(admin):
    for url in PAGES:
        r = admin.get(url)
        assert r.status_code == 200, (url, r.text[:300])
        assert "Traceback" not in r.text


def test_csrf_enforced(admin):
    r = admin.post("/admin/projects", data={"name": "X"})
    assert r.status_code == 400


def test_project_lifecycle(admin):
    tok = csrf_of(admin)
    r = admin.post("/admin/projects", data={"csrf_token": tok, "name": "KinderDesk-test", "daily_limit": "50", "monthly_limit": "", "webhook_url": ""})
    assert r.status_code == 200
    key = re.search(r"(sk_sms_[A-Za-z0-9_\-]+)", r.text).group(1)
    pid = int(re.search(r'/admin/projects/(\d+)/edit', r.text).group(1))
    # kalit bilan API ishlaydi
    s = admin.post("/api/v1/sms/send", json={"to": "901234567", "text": "salom"}, headers={"Authorization": f"Bearer {key}"})
    assert s.status_code == 202
    assert admin.get(f"/admin/projects/{pid}").status_code == 200
    # kalitni yangilash -> eskisi ishlamaydi
    r = admin.post(f"/admin/projects/{pid}/regenerate", data={"csrf_token": tok})
    new_key = re.search(r"(sk_sms_[A-Za-z0-9_\-]+)", r.text).group(1)
    assert new_key != key
    assert admin.post("/api/v1/sms/send", json={"to": "901234567", "text": "x"}, headers={"Authorization": f"Bearer {key}"}).status_code == 401
    # o'chirish
    admin.post(f"/admin/projects/{pid}/toggle", data={"csrf_token": tok})
    assert admin.post("/api/v1/sms/send", json={"to": "901234567", "text": "x"}, headers={"Authorization": f"Bearer {new_key}"}).status_code == 403


def test_modem_pages_and_at_console(admin):
    html = admin.get("/admin/modems").text
    mid = int(re.search(r"/admin/modems/(\d+)", html).group(1))
    assert admin.get(f"/admin/modems/{mid}").status_code == 200
    tok = csrf_of(admin)
    r = admin.post(f"/admin/modems/{mid}/edit", data={"csrf_token": tok, "label": "Test modem", "sim_number": "+998900000000", "is_enabled": "on", "monthly_limit": "500", "manual_remaining": "", "ussd_code": "*100#", "ussd_regex": ""}, follow_redirects=True)
    assert "Saqlandi" in r.text and "Test modem" in r.text
    r = admin.post(f"/admin/modems/{mid}/ussd", data={"csrf_token": tok}, follow_redirects=True)
    assert "Qolgan SMS" in r.text
    r = admin.post(f"/admin/modems/{mid}/at", data={"csrf_token": tok, "cmd": "AT+CSQ"}, follow_redirects=True)
    assert "+CSQ" in r.text
    r = admin.post(f"/admin/modems/{mid}/at", data={"csrf_token": tok, "cmd": "rm -rf /"}, follow_redirects=True)
    assert "XATO" in r.text


def test_send_test_sms_and_journal(admin):
    tok = csrf_of(admin)
    r = admin.post("/admin/send", data={"csrf_token": tok, "to": "+998909998877", "text": "Sinov"}, follow_redirects=True)
    assert "Navbatga qo&#39;yildi" in r.text or "Navbatga qo'yildi" in r.text
    j = admin.get("/admin/messages?q=909998877")
    assert j.status_code == 200 and "+998909998877" in j.text
    c = admin.get("/admin/messages.csv?q=909998877")
    assert c.status_code == 200 and "+998909998877" in c.text


def test_settings_and_password(admin):
    tok = csrf_of(admin)
    r = admin.post("/admin/settings", data={"csrf_token": tok, "rate_per_minute": "0", "rate_per_hour": "0", "message_ttl_seconds": "300", "max_attempts": "3", "retry_delay_seconds": "15", "send_interval_ms": "100", "log_retention_days": "90", "ussd_refresh_minutes": "360", "max_parts": "6", "normalize_apostrophes": "on", "mask_text": "on"}, follow_redirects=True)
    assert "Sozlamalar saqlandi" in r.text
    r = admin.post("/admin/settings/password", data={"csrf_token": tok, "current": "bad", "new": "newpassword1", "repeat": "newpassword1"}, follow_redirects=True)
    assert "noto&#39;g&#39;ri" in r.text or "noto'g'ri" in r.text

"""Admin panel (bitta admin, o'zbekcha interfeys)."""
import csv
import hmac
import io
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .api import client_ip
from .config import VERSION, get_settings
from .database import get_db
from .models import ALL_STATUSES, CANCELLED, QUEUED, AdminUser, Message, Modem, Project
from .runtime import get_runtime
from .security import hash_password, hash_key, new_api_key, new_csrf, new_webhook_secret, verify_password
from .services import stats
from .services.messaging import ApiError, create_message, finish_message, webhook_payload
from .services.settings_store import SETTING_DEFS, all_settings, get_setting, set_setting
from .services.stats import queue_info
from .services.status import human_duration, human_size, modem_rows, system_status
from .services.webhook import send_webhook
from .timeutil import fmt, local_day, local_month, now_utc

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

STATUS_LABELS = {
    "queued": ("Navbatda", "warn"),
    "sending": ("Yuborilmoqda", "info"),
    "sent": ("Yuborildi", "ok"),
    "failed": ("Xato", "err"),
    "cancelled": ("Bekor qilindi", "muted"),
    "expired": ("Muddati o'tdi", "err"),
}
WEBHOOK_LABELS = {"none": "—", "pending": "kutilmoqda", "delivered": "yetkazildi", "failed": "xato"}

def _asset_version() -> str:
    """Statik fayllar o'zgarsa, kesh (Cloudflare/brauzer) yangilanishi uchun versiya."""
    from pathlib import Path
    d = Path(__file__).resolve().parent / "static"
    try:
        return str(int(max(f.stat().st_mtime for f in d.iterdir() if f.is_file())))
    except Exception:  # noqa: BLE001
        return VERSION


templates.env.globals.update(
    ASSET_V=_asset_version(),
    fmt=fmt,
    human_duration=human_duration,
    human_size=human_size,
    STATUS_LABELS=STATUS_LABELS,
    WEBHOOK_LABELS=WEBHOOK_LABELS,
    VERSION=VERSION,
)

router = APIRouter(prefix="/admin", include_in_schema=False)

_login_fails: dict[str, deque] = defaultdict(deque)
ADMIN_TEST_PROJECT = "Admin test (tizim)"


class LoginRequired(Exception):
    pass


def require_admin(request: Request) -> str:
    user = request.session.get("admin")
    if not user:
        raise LoginRequired()
    return user


async def csrf_protect(request: Request):
    form = await request.form()
    token = str(form.get("csrf_token") or "")
    if not token or not hmac.compare_digest(token, request.session.get("csrf", "")):
        raise HTTPException(400, "CSRF token xato. Sahifani yangilab qayta urinib ko'ring")


def flash(request: Request, level: str, text: str) -> None:
    msgs = request.session.get("flash", [])
    msgs.append([level, text])
    request.session["flash"] = msgs


def render(request: Request, name: str, active: str = "", status_code: int = 200, **ctx):
    if "csrf" not in request.session:
        request.session["csrf"] = new_csrf()
    flashes = request.session.pop("flash", [])
    return templates.TemplateResponse(
        request,
        name,
        {"csrf_token": request.session["csrf"], "flashes": flashes, "active": active, "admin": request.session.get("admin"), **ctx},
        status_code=status_code,
    )


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _int_or_none(v: str | None) -> int | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        n = int(v)
    except ValueError:
        raise HTTPException(400, f"Son kutilgan edi: {v!r}")
    return max(0, n) or None


# ---------------------------------------------------------------- kirish
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("admin"):
        return redirect("/admin")
    return render(request, "login.html")


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    ip = client_ip(request)
    q = _login_fails[ip]
    now = time.time()
    while q and now - q[0] > 300:
        q.popleft()
    if len(q) >= 5:
        flash(request, "err", "Juda ko'p noto'g'ri urinish. 5 daqiqadan so'ng qayta urinib ko'ring.")
        return render(request, "login.html", status_code=429)
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user and verify_password(password, user.password_hash):
        request.session.clear()
        request.session["admin"] = user.username
        request.session["csrf"] = new_csrf()
        q.clear()
        return redirect("/admin")
    q.append(now)
    flash(request, "err", "Login yoki parol noto'g'ri")
    return render(request, "login.html", status_code=401)


@router.post("/logout")
def logout(request: Request, _=Depends(csrf_protect)):
    request.session.clear()
    return redirect("/admin/login")


# ---------------------------------------------------------------- dashboard / holat
@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    projects = list(db.scalars(select(Project).order_by(Project.name)))
    rows = []
    for p in projects:
        t = stats.project_totals(db, p.id)
        rows.append({"p": p, "t": t})
    today = {"sent": sum(r["t"]["today"]["sent"] for r in rows), "failed": sum(r["t"]["today"]["failed"] for r in rows), "parts": sum(r["t"]["today"]["parts"] for r in rows)}
    month = {"sent": sum(r["t"]["month"]["sent"] for r in rows), "failed": sum(r["t"]["month"]["failed"] for r in rows), "parts": sum(r["t"]["month"]["parts"] for r in rows)}
    st = system_status(db)
    last = list(db.scalars(select(Message).order_by(Message.id.desc()).limit(10)))
    series = stats.daily_series(db, None, 14)
    return render(request, "dashboard.html", "dashboard", rows=rows, today=today, month=month, st=st, last=last, series=series, max_parts=max([d["parts"] for d in series] + [1]))


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    return render(request, "status.html", "status", st=system_status(db), cfg=get_settings())


# ---------------------------------------------------------------- loyihalar
@router.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    rows = [{"p": p, "t": stats.project_totals(db, p.id)} for p in db.scalars(select(Project).order_by(Project.name))]
    return render(request, "projects.html", "projects", rows=rows)


@router.post("/projects")
def project_create(
    request: Request,
    name: str = Form(...),
    daily_limit: str = Form(""),
    monthly_limit: str = Form(""),
    webhook_url: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
    __=Depends(csrf_protect),
):
    name = name.strip()
    if not name or len(name) > 100:
        flash(request, "err", "Loyiha nomi noto'g'ri")
        return redirect("/admin/projects")
    if db.scalar(select(Project).where(Project.name == name)):
        flash(request, "err", "Bunday nomli loyiha mavjud")
        return redirect("/admin/projects")
    webhook_url = webhook_url.strip()
    if webhook_url and not re.match(r"^https?://", webhook_url):
        flash(request, "err", "Webhook URL http:// yoki https:// bilan boshlanishi kerak")
        return redirect("/admin/projects")
    key, key_hash, prefix = new_api_key()
    p = Project(
        name=name,
        api_key_hash=key_hash,
        key_prefix=prefix,
        daily_limit=_int_or_none(daily_limit),
        monthly_limit=_int_or_none(monthly_limit),
        webhook_url=webhook_url,
        webhook_secret=new_webhook_secret() if webhook_url else "",
    )
    db.add(p)
    db.commit()
    return _project_detail(request, db, p, new_key=key)


def _get_project(db: Session, pid: int) -> Project:
    p = db.get(Project, pid)
    if not p:
        raise HTTPException(404, "Loyiha topilmadi")
    return p


def _project_detail(request: Request, db: Session, p: Project, new_key: str | None = None):
    recent = list(db.scalars(select(Message).where(Message.project_id == p.id).order_by(Message.id.desc()).limit(15)))
    series = stats.daily_series(db, p.id, 30)
    return render(
        request,
        "project_detail.html",
        "projects",
        p=p,
        t=stats.project_totals(db, p.id),
        period=stats.project_period_usage(db, p.id),
        series=series,
        max_parts=max([d["parts"] for d in series] + [1]),
        months=stats.monthly_series(db, p.id, 12),
        recent=recent,
        new_key=new_key,
        base_url=str(request.base_url).rstrip("/"),
    )


@router.get("/projects/{pid}", response_class=HTMLResponse)
def project_page(pid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    return _project_detail(request, db, _get_project(db, pid))


@router.post("/projects/{pid}/edit")
def project_edit(
    pid: int,
    request: Request,
    name: str = Form(...),
    daily_limit: str = Form(""),
    monthly_limit: str = Form(""),
    webhook_url: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
    __=Depends(csrf_protect),
):
    p = _get_project(db, pid)
    name = name.strip()
    other = db.scalar(select(Project).where(Project.name == name, Project.id != p.id))
    if not name or other:
        flash(request, "err", "Nom bo'sh yoki band")
        return redirect(f"/admin/projects/{pid}")
    webhook_url = webhook_url.strip()
    if webhook_url and not re.match(r"^https?://", webhook_url):
        flash(request, "err", "Webhook URL http:// yoki https:// bilan boshlanishi kerak")
        return redirect(f"/admin/projects/{pid}")
    p.name = name
    p.daily_limit = _int_or_none(daily_limit)
    p.monthly_limit = _int_or_none(monthly_limit)
    p.webhook_url = webhook_url
    if webhook_url and not p.webhook_secret:
        p.webhook_secret = new_webhook_secret()
    db.commit()
    flash(request, "ok", "Saqlandi")
    return redirect(f"/admin/projects/{pid}")


@router.post("/projects/{pid}/regenerate")
def project_regenerate(pid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    p = _get_project(db, pid)
    key, key_hash, prefix = new_api_key()
    p.api_key_hash, p.key_prefix = key_hash, prefix
    db.commit()
    return _project_detail(request, db, p, new_key=key)


@router.post("/projects/{pid}/toggle")
def project_toggle(pid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    p = _get_project(db, pid)
    p.is_active = not p.is_active
    db.commit()
    flash(request, "ok", "Loyiha yoqildi" if p.is_active else "Loyiha o'chirildi (API rad etadi)")
    return redirect(f"/admin/projects/{pid}")


@router.post("/projects/{pid}/webhook-secret")
def project_webhook_secret(pid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    p = _get_project(db, pid)
    p.webhook_secret = new_webhook_secret()
    db.commit()
    flash(request, "ok", "Yangi webhook secret yaratildi")
    return redirect(f"/admin/projects/{pid}")


@router.post("/projects/{pid}/webhook-test")
def project_webhook_test(pid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    p = _get_project(db, pid)
    if not p.webhook_url:
        flash(request, "err", "Webhook URL kiritilmagan")
        return redirect(f"/admin/projects/{pid}")
    payload = {"event": "sms.test", "data": {"project": p.name, "message": "Sinov xabari", "time": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")}}
    ok, info = send_webhook(p.webhook_url, p.webhook_secret, payload)
    flash(request, "ok" if ok else "err", f"Webhook sinovi: {'muvaffaqiyatli' if ok else 'xato'} ({info})")
    return redirect(f"/admin/projects/{pid}")


# ---------------------------------------------------------------- modemlar
@router.get("/modems", response_class=HTMLResponse)
def modems_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    rt = get_runtime()
    return render(request, "modems.html", "modems", modems=modem_rows(db), cfg=get_settings(), last_scan=rt.pool.last_scan, note=rt.pool.last_scan_note, report=dict(rt.pool.port_report))


@router.post("/modems/rescan")
def modems_rescan(request: Request, _=Depends(require_admin), __=Depends(csrf_protect)):
    get_runtime().pool.request_scan()
    flash(request, "ok", "Qayta skanerlash boshlandi (bir necha soniya)")
    return redirect("/admin/modems")


def _get_modem(db: Session, mid: int) -> Modem:
    m = db.get(Modem, mid)
    if not m:
        raise HTTPException(404, "Modem topilmadi")
    return m


@router.get("/modems/{mid}", response_class=HTMLResponse)
def modem_page(mid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    m = _get_modem(db, mid)
    return render(
        request,
        "modem_detail.html",
        "modems",
        m=m,
        remaining=stats.modem_remaining(db, m),
        used=stats.modem_used_month(db, m.id),
        at_result=request.session.pop("at_result", None),
    )


@router.post("/modems/{mid}/edit")
def modem_edit(
    mid: int,
    request: Request,
    label: str = Form(""),
    sim_number: str = Form(""),
    is_enabled: str | None = Form(None),
    monthly_limit: str = Form(""),
    manual_remaining: str = Form(""),
    ussd_code: str = Form(""),
    ussd_regex: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
    __=Depends(csrf_protect),
):
    m = _get_modem(db, mid)
    if ussd_regex.strip():
        try:
            re.compile(ussd_regex.strip())
        except re.error as e:
            flash(request, "err", f"Regex xato: {e}")
            return redirect(f"/admin/modems/{mid}")
    m.label = label.strip()[:100]
    m.sim_number = sim_number.strip()[:30]
    m.is_enabled = bool(is_enabled)
    m.monthly_limit = _int_or_none(monthly_limit)
    m.ussd_code = ussd_code.strip()
    m.ussd_regex = ussd_regex.strip()
    mr = manual_remaining.strip()
    if mr:
        try:
            m.quota_remaining = max(0, int(mr))
            m.quota_checked_at = now_utc()
        except ValueError:
            flash(request, "err", "Qoldiq son bo'lishi kerak")
            return redirect(f"/admin/modems/{mid}")
    db.commit()
    flash(request, "ok", "Saqlandi")
    return redirect(f"/admin/modems/{mid}")


@router.post("/modems/{mid}/ussd")
def modem_ussd(mid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    m = _get_modem(db, mid)
    ok, msg = get_runtime().pool.refresh_quota(m.imei)
    flash(request, "ok" if ok else "err", f"USSD: {msg}")
    return redirect(f"/admin/modems/{mid}")


@router.post("/modems/{mid}/at")
def modem_at(mid: int, request: Request, cmd: str = Form(...), db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    m = _get_modem(db, mid)
    try:
        out = get_runtime().pool.raw_command(m.imei, cmd)
    except Exception as e:  # noqa: BLE001
        out = f"XATO: {e}"
    request.session["at_result"] = {"cmd": cmd.strip()[:100], "out": out[:3000]}
    return redirect(f"/admin/modems/{mid}#at")


# ---------------------------------------------------------------- jurnal
def _message_query(project_id: str, status: str, q: str, date_from: str, date_to: str):
    stmt = select(Message)
    if project_id.isdigit():
        stmt = stmt.where(Message.project_id == int(project_id))
    if status in ALL_STATUSES:
        stmt = stmt.where(Message.status == status)
    digits = re.sub(r"\D", "", q or "")
    if digits:
        stmt = stmt.where(Message.to_number.like(f"%{digits}%"))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from or ""):
        stmt = stmt.where(Message.day >= date_from)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to or ""):
        stmt = stmt.where(Message.day <= date_to)
    return stmt


@router.get("/messages", response_class=HTMLResponse)
def messages_page(
    request: Request,
    project_id: str = "",
    status: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    per = 50
    stmt = _message_query(project_id, status, q, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, (total + per - 1) // per)
    page = min(max(1, page), pages)
    rows = list(db.scalars(stmt.order_by(Message.id.desc()).limit(per).offset((page - 1) * per)))
    projects = list(db.scalars(select(Project).order_by(Project.name)))
    qs = "&".join(f"{k}={v}" for k, v in (("project_id", project_id), ("status", status), ("q", q), ("date_from", date_from), ("date_to", date_to)) if v)
    return render(request, "messages.html", "messages", rows=rows, projects=projects, total=total, page=page, pages=pages, f={"project_id": project_id, "status": status, "q": q, "date_from": date_from, "date_to": date_to}, qs=qs)


@router.get("/messages.csv")
def messages_csv(
    project_id: str = "",
    status: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    stmt = _message_query(project_id, status, q, date_from, date_to).order_by(Message.id.desc()).limit(50000)

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "vaqt", "loyiha", "raqam", "holat", "qism", "modem", "urinish", "xato", "matn", "external_id"])
        yield "﻿" + buf.getvalue()
        for m in db.scalars(stmt):
            buf = io.StringIO()
            csv.writer(buf).writerow([m.id, fmt(m.created_at), m.project.name, m.to_number, m.status, m.parts, m.modem.operator if m.modem else "", m.attempts, m.error or "", m.text, m.external_id or ""])
            yield buf.getvalue()

    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="sms_jurnal_{local_day()}.csv"'})


@router.post("/messages/{mid}/cancel")
def message_cancel(mid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    m = db.get(Message, mid)
    if m and m.status == QUEUED:
        finish_message(db, m, CANCELLED, "Admin tomonidan bekor qilindi", 0)
        flash(request, "ok", f"#{mid} bekor qilindi")
    else:
        flash(request, "err", "Faqat navbatdagi xabarni bekor qilish mumkin")
    return redirect(request.headers.get("referer") or "/admin/messages")


# ---------------------------------------------------------------- SMS yuborish (admin)
MAX_BATCH = 100


def _admin_project(db: Session) -> Project:
    p = db.scalar(select(Project).where(Project.name == ADMIN_TEST_PROJECT))
    if p is None:
        _, key_hash, prefix = new_api_key()
        p = Project(name=ADMIN_TEST_PROJECT, api_key_hash=key_hash, key_prefix=prefix)
        db.add(p)
        db.commit()
    return p


def _parse_numbers(raw: str) -> list[str]:
    seen, out = set(), []
    for token in re.split(r"[\s,;]+", raw or ""):
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


@router.get("/send", response_class=HTMLResponse)
def send_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    _admin_project(db)
    projects = list(db.scalars(select(Project).where(Project.is_active == True).order_by(Project.name)))  # noqa: E712
    q = queue_info(db)
    return render(
        request, "send.html", "send",
        projects=projects, admin_project=ADMIN_TEST_PROJECT, queue_size=q["size"],
        interval=get_setting(db, "send_interval_seconds"), max_batch=MAX_BATCH,
    )


@router.post("/send")
def send_submit(
    request: Request,
    numbers: str = Form(...),
    text: str = Form(...),
    project_id: int = Form(0),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
    __=Depends(csrf_protect),
):
    p = db.get(Project, project_id) if project_id else None
    if p is None:
        p = _admin_project(db)
    nums = _parse_numbers(numbers)
    if not nums:
        flash(request, "err", "Telefon raqam kiritilmadi")
        return redirect("/admin/send")
    if len(nums) > MAX_BATCH:
        flash(request, "err", f"Bir martada ko'pi bilan {MAX_BATCH} ta raqam")
        return redirect("/admin/send")
    queued, errors, parts = [], [], 0
    for n in nums:
        try:
            msg, _dup = create_message(db, p, n, text, None)
            queued.append(msg.id)
            parts += msg.parts
        except ApiError as e:
            errors.append(f"{n}: {e.message}")
            if e.code in ("no_modem", "quota_exceeded", "project_disabled", "empty_text", "text_too_long"):
                break  # qolganlari ham xuddi shu sababdan o'tmaydi
    if queued:
        interval = get_setting(db, "send_interval_seconds")
        eta = int(queue_info(db)["size"] * interval)
        flash(request, "ok", f"{len(queued)} ta SMS navbatga qo'yildi ({p.name}). Hammasi yuborilishi taxminan {human_duration(eta)} oladi.")
    if errors:
        shown = "; ".join(errors[:5]) + (f" … (yana {len(errors) - 5} ta xato)" if len(errors) > 5 else "")
        flash(request, "err", f"Qo'yilmadi: {shown}")
    return redirect("/admin/send")


# ---------------------------------------------------------------- sozlamalar
@router.get("/docs", response_class=HTMLResponse)
def docs_page(request: Request, _=Depends(require_admin)):
    base = str(request.base_url).rstrip("/")
    return render(request, "docs.html", "docs", base=base)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    return render(request, "settings.html", "settings", defs=SETTING_DEFS, values=all_settings(db))


@router.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db), _=Depends(require_admin), __=Depends(csrf_protect)):
    form = await request.form()
    for key, kind, default, _label, _help in SETTING_DEFS:
        if kind == "bool":
            set_setting(db, key, key in form)
        else:
            raw = str(form.get(key, "")).strip()
            try:
                set_setting(db, key, max(0, int(raw)))
            except ValueError:
                set_setting(db, key, default)
    db.commit()
    flash(request, "ok", "Sozlamalar saqlandi")
    return redirect("/admin/settings")


@router.post("/settings/password")
def change_password(
    request: Request,
    current: str = Form(...),
    new: str = Form(...),
    repeat: str = Form(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
    __=Depends(csrf_protect),
):
    u = db.scalar(select(AdminUser).where(AdminUser.username == user))
    if not u or not verify_password(current, u.password_hash):
        flash(request, "err", "Joriy parol noto'g'ri")
    elif len(new) < 8:
        flash(request, "err", "Yangi parol kamida 8 belgi bo'lishi kerak")
    elif new != repeat:
        flash(request, "err", "Yangi parollar mos kelmadi")
    else:
        u.password_hash = hash_password(new)
        db.commit()
        flash(request, "ok", "Parol o'zgartirildi")
    return redirect("/admin/settings")

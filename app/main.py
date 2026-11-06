import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import runtime as rt_mod
from .admin import LoginRequired, router as admin_router, templates
from .api import router as api_router
from .config import VERSION, get_settings
from .database import SessionLocal, init_db
from .models import AdminUser
from .runtime import Runtime
from .security import hash_password
from .services.messaging import ApiError
from .services.status import health

BASE_DIR = Path(__file__).resolve().parent
cfg = get_settings()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


def _load_secret() -> str:
    if cfg.secret_key:
        return cfg.secret_key
    path = cfg.data_path / "secret.key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return key


def _bootstrap_admin() -> None:
    with SessionLocal() as db:
        if db.scalar(select(AdminUser).limit(1)):
            return
        password = cfg.admin_password
        generated = False
        if not password:
            password = secrets.token_urlsafe(10)
            generated = True
        db.add(AdminUser(username=cfg.admin_username, password_hash=hash_password(password)))
        db.commit()
    if generated:
        log.warning("=" * 60)
        log.warning("ADMIN yaratildi. Login: %s  Parol: %s", cfg.admin_username, password)
        log.warning("Kirgach parolni Sozlamalar bo'limida o'zgartiring.")
        log.warning("=" * 60)
    else:
        log.info("Admin yaratildi: %s", cfg.admin_username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_admin()
    runtime = Runtime(cfg)
    rt_mod.runtime = runtime
    runtime.start()
    log.info("SMS Gateway %s ishga tushdi (USB modemlar avtomatik qidiriladi)", VERSION)
    yield
    runtime.stop()
    rt_mod.runtime = None


app = FastAPI(
    title=cfg.app_name,
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs" if cfg.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if cfg.enable_docs else None,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=_load_secret(),
    session_cookie="sms_admin",
    max_age=7 * 24 * 3600,
    same_site="lax",
    https_only=cfg.cookie_secure,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(api_router)
app.include_router(admin_router)


def _api_error(status: int, code: str, message: str, extra: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **(extra or {})}})


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return _api_error(exc.status, exc.code, exc.message, exc.extra)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    if request.url.path.startswith("/api/"):
        return _api_error(422, "validation_error", "So'rov ma'lumotlari noto'g'ri", {"details": details})
    return JSONResponse(status_code=422, content={"detail": details})


@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _api_error(exc.status_code, code, str(exc.detail))
    if request.url.path.startswith("/admin"):
        return templates.TemplateResponse(
            request, "error.html", {"code": exc.status_code, "detail": exc.detail, "csrf_token": "", "flashes": [], "active": "", "admin": request.session.get("admin")}, status_code=exc.status_code
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin")


@app.get("/health", include_in_schema=False)
def root_health():
    with SessionLocal() as db:
        h = health(db)
    return JSONResponse(status_code=503 if h["status"] == "down" else 200, content=h)

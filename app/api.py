"""Loyihalar uchun ochiq REST API: /api/v1"""
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import CANCELLED, QUEUED, Message, Project
from .security import hash_key
from .services import stats
from .services.messaging import ApiError, create_message, finish_message, serialize
from .services.status import health

router = APIRouter(prefix="/api/v1", tags=["SMS API"])

# noto'g'ri kalit bilan urinishlarni cheklash (IP bo'yicha)
_fail_log: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def get_project(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Project:
    ip = client_ip(request)
    q = _fail_log[ip]
    now = time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 20:
        raise ApiError(429, "too_many_attempts", "Noto'g'ri kalit bilan juda ko'p urinish. Bir daqiqadan so'ng urinib ko'ring")

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()
    project = db.scalar(select(Project).where(Project.api_key_hash == hash_key(token))) if token else None
    if project is None:
        q.append(now)
        raise ApiError(401, "invalid_api_key", "API kalit noto'g'ri yoki yuborilmagan")
    if not project.is_active:
        raise ApiError(403, "project_disabled", "Loyiha o'chirilgan")
    return project


class SendRequest(BaseModel):
    to: str = Field(..., description="Telefon raqam, masalan +998901234567")
    text: str = Field(..., min_length=1, max_length=2000, description="SMS matni")
    external_id: str | None = Field(default=None, max_length=100, description="Loyihadagi o'z ID (takror yuborishdan himoya)")


@router.post("/sms/send", status_code=202)
def send_sms(body: SendRequest, project: Project = Depends(get_project), db: Session = Depends(get_db)):
    msg, duplicate = create_message(db, project, body.to, body.text, body.external_id)
    data = serialize(msg)
    data["duplicate"] = duplicate
    return JSONResponse(status_code=200 if duplicate else 202, content=data)


@router.get("/sms/{message_id}")
def get_sms(message_id: int, project: Project = Depends(get_project), db: Session = Depends(get_db)):
    msg = db.get(Message, message_id)
    if msg is None or msg.project_id != project.id:
        raise ApiError(404, "not_found", "Xabar topilmadi")
    return serialize(msg)


@router.get("/sms")
def list_sms(
    external_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    q = select(Message).where(Message.project_id == project.id)
    if external_id:
        q = q.where(Message.external_id == external_id)
    if status:
        q = q.where(Message.status == status)
    rows = db.scalars(q.order_by(Message.id.desc()).limit(limit))
    return {"items": [serialize(m) for m in rows]}


@router.delete("/sms/{message_id}")
def cancel_sms(message_id: int, project: Project = Depends(get_project), db: Session = Depends(get_db)):
    msg = db.get(Message, message_id)
    if msg is None or msg.project_id != project.id:
        raise ApiError(404, "not_found", "Xabar topilmadi")
    if msg.status != QUEUED:
        raise ApiError(409, "not_cancellable", f"Faqat navbatdagi xabarni bekor qilish mumkin (hozir: {msg.status})")
    finish_message(db, msg, CANCELLED, "Bekor qilindi", 0)
    return serialize(msg)


@router.get("/me/usage")
def my_usage(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    totals = stats.project_totals(db, project.id)
    period = stats.project_period_usage(db, project.id)
    return {
        "project": project.name,
        "today": {**totals["today"], "accepted_parts": period["day_parts"], "limit": project.daily_limit},
        "month": {**totals["month"], "accepted_parts": period["month_parts"], "limit": project.monthly_limit},
        "total": totals["total"],
    }


@router.get("/health")
def api_health(db: Session = Depends(get_db)):
    h = health(db)
    code = 503 if h["status"] == "down" else 200
    return JSONResponse(status_code=code, content=h)

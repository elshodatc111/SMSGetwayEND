@echo off
REM Windows'da ishlab chiqish uchun: soxta modem bilan ishga tushirish
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements-dev.txt
if not exist .env (
  copy .env.example .env
  echo .env yaratildi. MODEM_MODE=mock qilib qo'yildi.
  powershell -Command "(Get-Content .env) -replace 'MODEM_MODE=auto','MODEM_MODE=mock' | Set-Content .env"
)
echo.
echo Admin panel: http://127.0.0.1:8000/admin   (login: admin, parol: .env dagi ADMIN_PASSWORD)
echo.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

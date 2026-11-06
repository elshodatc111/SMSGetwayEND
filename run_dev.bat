@echo off
REM Windows'da ishlab chiqish uchun: panelni ochish (USB modem ulangan bo'lsa u avtomatik topiladi)
cd /d %~dp0
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
if not exist .venv (
  echo Virtual muhit yaratilmoqda...
  %PY% -m venv .venv
)
call .venv\Scripts\activate
echo Kutubxonalar o'rnatilmoqda...
pip install -r requirements-dev.txt
if not exist .env (
  copy .env.example .env
  echo .env yaratildi.
)
echo.
echo Admin panel: http://127.0.0.1:8000/admin   (login: admin, parol: .env dagi ADMIN_PASSWORD)
echo.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pause

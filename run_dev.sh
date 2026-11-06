#!/usr/bin/env bash
# Linux/macOS: ishlab chiqish rejimi (modem ulanmagan bo'lsa ham panel ochiladi)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements-dev.txt
[ -f .env ] || cp .env.example .env
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

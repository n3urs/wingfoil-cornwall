#!/usr/bin/env bash
# Start the dashboard. First run sets up the virtualenv.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Setting up (one time)…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

[ -f .env ] || cp .env.example .env

echo "Dashboard → http://localhost:8787"
exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8787 "$@"

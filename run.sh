#!/usr/bin/env bash
# One-shot setup + run script.
set -e
cd "$(dirname "$0")/backend"

if [[ ! -d .venv ]]; then
  echo "→ Creating virtual environment…"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ Installing dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [[ ! -f data/app.db ]]; then
  echo "→ Seeding demo database…"
  python seed.py
fi

echo "→ Starting server at http://localhost:8000"
# Note: --reload is intentionally off so we don't trip the inotify watch limit
# on systems with many file watchers. Set RELOAD=1 to enable.
if [[ "${RELOAD:-0}" == "1" ]]; then
  exec uvicorn app:app --host 0.0.0.0 --port 8000 --reload
else
  exec uvicorn app:app --host 0.0.0.0 --port 8000
fi

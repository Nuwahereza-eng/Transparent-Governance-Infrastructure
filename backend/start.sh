#!/usr/bin/env bash
# Render start script. Runs from the `backend/` directory (rootDir).
set -e

# Render mounts a 1 GB persistent disk at /var/data.
# We point the app's DATA_DIR there via the GOVTRUST_DATA_DIR env var so that
# both the SQLite file and uploaded evidence files survive across deploys.
DATA_DIR="${GOVTRUST_DATA_DIR:-/var/data}"
mkdir -p "$DATA_DIR/uploads"

# Seed the demo database on first boot only.
if [[ ! -f "$DATA_DIR/app.db" ]]; then
  echo "→ First boot detected. Seeding demo database at $DATA_DIR/app.db"
  python seed.py
fi

# Render injects $PORT.
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'

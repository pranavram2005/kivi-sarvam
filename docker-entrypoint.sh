#!/bin/sh
# Start the hosted instance.
#
# Two things happen before the server comes up, and both are conditional, so
# restarting the container never destroys anything a reviewer put there.
set -e

DB_FILE="${KIVI_DATABASE_URL#sqlite:////}"
DB_FILE="/${DB_FILE}"
DB_DIR="$(dirname "$DB_FILE")"

mkdir -p "$DB_DIR"

if [ -s "$DB_FILE" ]; then
  echo "[kivi] database present at $DB_FILE — leaving it alone"
else
  echo "[kivi] no database at $DB_FILE — seeding the 500-record corpus"
  python scripts/seed.py
fi

# Railway (and most hosts) inject the port to bind. Fall back to 8000 locally.
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"

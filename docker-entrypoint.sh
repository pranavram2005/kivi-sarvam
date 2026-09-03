#!/bin/sh
# Start the hosted instance.
#
# Two things happen before the server comes up, and both are conditional, so
# restarting the container never destroys anything a reviewer put there.
set -e

# Ask the application where its database lives rather than parsing
# KIVI_DATABASE_URL again here. Two parsers that disagree is exactly how a
# container ends up seeding a path the app never reads: the correct *local*
# form `sqlite:///data/kivi.db` has three slashes and means a path relative to
# the repository, so shell code that strips four slashes leaves the prefix on,
# checks "/sqlite:///data/kivi.db", never finds it, and reseeds on every
# restart -- while the mounted volume sits empty and unused.
DB_FILE="$(python -c 'from backend.config import get_settings; print(get_settings().db_path)')"
DB_DIR="$(dirname "$DB_FILE")"

# A database inside the image is not persistent. Say so loudly: the symptom
# otherwise is silent, and only shows up as a reviewer's imported corpus
# vanishing on a restart hours later.
case "$DB_FILE" in
  /data/*) ;;
  *)
    echo "[kivi] WARNING: database resolves to $DB_FILE"
    echo "[kivi]          That is inside the container, not the mounted volume,"
    echo "[kivi]          so everything is discarded on restart."
    echo "[kivi]          Fix: remove KIVI_DATABASE_URL from this deployment's"
    echo "[kivi]          variables so the image default applies"
    echo "[kivi]          (sqlite:////data/kivi.db - four slashes, absolute),"
    echo "[kivi]          and mount a volume at /data."
    ;;
esac

mkdir -p "$DB_DIR"

if [ -s "$DB_FILE" ]; then
  echo "[kivi] database present at $DB_FILE - leaving it alone"
else
  echo "[kivi] no database at $DB_FILE - seeding the 500-record corpus"
  python scripts/seed.py
fi

# Railway (and most hosts) inject the port to bind. Fall back to 8000 locally.
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"

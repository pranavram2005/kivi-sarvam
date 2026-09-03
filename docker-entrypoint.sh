#!/bin/sh
# Start the hosted instance.
#
# Everything before `exec` is idempotent and resumable, so a container that is
# killed midway - by a healthcheck timeout, a redeploy, an out-of-memory kill -
# finishes the job on its next boot instead of being stuck forever.
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
mkdir -p "$DB_DIR"

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

# Migrations are idempotent and cheap; running them every boot means a volume
# carrying an older schema is brought forward rather than failing at runtime.
python scripts/migrate.py >/dev/null

# Ask the database what state it is actually in. The previous version of this
# script tested `[ -s "$DB_FILE" ]` - "does a non-empty file exist" - which is
# true the moment the 500 transcripts are imported, before any of them have
# been through extraction. A container killed in that window came back up,
# saw a non-empty file, declared itself seeded, and served 500 permanently
# unprocessed dictations with no memories behind them.
count_state() {
  python - <<'PY'
from backend.config import get_settings
from backend.database.db import get_connection

user_id = get_settings().default_user_id
conn = get_connection()
try:
    total = conn.execute(
        "SELECT COUNT(*) FROM transcripts WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM transcripts WHERE user_id = ? AND processed_at IS NULL",
        (user_id,),
    ).fetchone()[0]
except Exception:
    total, pending = 0, 0
print(total, pending)
PY
}

STATE="$(count_state)"
TOTAL="${STATE% *}"
PENDING="${STATE#* }"

if [ "$TOTAL" -eq 0 ]; then
  echo "[kivi] empty database at $DB_FILE - importing the 500-record corpus"
  python scripts/import_corpus.py data/development_corpus.jsonl
  STATE="$(count_state)"
  TOTAL="${STATE% *}"
  PENDING="${STATE#* }"
fi

if [ "$PENDING" -gt 0 ]; then
  echo "[kivi] $PENDING of $TOTAL transcript(s) awaiting extraction - processing"
  python scripts/process_corpus.py
  echo "[kivi] extraction complete"
else
  echo "[kivi] $TOTAL transcript(s) present, all processed - leaving the database alone"
fi

# Railway (and most hosts) inject the port to bind. Fall back to 8000 locally.
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"

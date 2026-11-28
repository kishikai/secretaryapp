#!/usr/bin/env bash
# Start command for hosts with a throwaway disk, such as Render's free plan.
# - Restores the database from Litestream storage (if LITESTREAM_BUCKET is set), then keeps
#   streaming changes to it while the app runs.
# - Creates the first secretary from SECRETARY_ADMIN_USERNAME / SECRETARY_ADMIN_PASSWORD, since
#   there may be no shell to run `python -m app.admin create-user`.
set -euo pipefail
cd "$(dirname "$0")/.."   # /app in the image

db="${SECRETARY_DB_PATH:-/data/secretary.db}"
app_cmd="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-10.0.0.0/8}"

if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  # B2 endpoints look like s3.<region>.backblazeb2.com; the region is the second part.
  export LITESTREAM_REGION="${LITESTREAM_REGION:-$(echo "$LITESTREAM_ENDPOINT" | cut -d. -f2)}"
  echo "Restoring database from storage (if a copy exists)"
  litestream restore -config deploy/litestream.yml -if-db-not-exists -if-replica-exists "$db"
fi

# Create the tables, use WAL mode (needed by Litestream), then add the first secretary if asked.
python - <<PY
import sqlite3
from app.db import init_db
init_db("$db")
sqlite3.connect("$db").execute("PRAGMA journal_mode=WAL").fetchone()
PY
if [ -n "${SECRETARY_ADMIN_USERNAME:-}" ] && [ -n "${SECRETARY_ADMIN_PASSWORD:-}" ]; then
  printf '%s\n' "$SECRETARY_ADMIN_PASSWORD" \
    | python -m app.admin create-user "$SECRETARY_ADMIN_USERNAME" --role secretary --password-stdin \
    || echo "First secretary not created (it probably exists already)"
fi

if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  exec litestream replicate -config deploy/litestream.yml -exec "$app_cmd"
fi
exec $app_cmd

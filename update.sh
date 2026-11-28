#!/usr/bin/env bash
# Update the running app to the latest code: ./update.sh
# Takes a backup, pulls, rebuilds and restarts. Your data is kept.
set -euo pipefail
cd "$(dirname "$0")"

if grep -q '^TS_AUTHKEY=' .env 2>/dev/null; then
  dc=(docker compose -f compose.tailscale.yaml)
else
  dc=(docker compose)
fi

echo "==> Backing up the database"
"${dc[@]}" exec -T app python -m app.backup --dir /backups || echo "(backup skipped: app not running)"

echo "==> Pulling latest code"
git pull --ff-only

echo "==> Rebuilding and restarting"
"${dc[@]}" up -d --build

docker image prune -f >/dev/null
echo "==> Done"
"${dc[@]}" ps

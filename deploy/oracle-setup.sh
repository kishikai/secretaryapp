#!/usr/bin/env bash
# One-time set-up of a fresh Oracle Cloud Ubuntu VM (Always Free Ampere A1) for Secretary.
#   curl -fsSL https://raw.githubusercontent.com/kishikai/secretaryapp/main/deploy/oracle-setup.sh | bash
# Installs Docker and Git, clones the app into ~/secretaryapp and adds a nightly backup.
# With Tailscale (Serve or Funnel) no Oracle ports need opening.
set -euo pipefail

echo "==> Installing Docker and Git"
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

dir="$HOME/secretaryapp"
if [ ! -d "$dir/.git" ]; then
  echo "==> Cloning the app"
  git clone https://github.com/kishikai/secretaryapp.git "$dir"
fi
[ -f "$dir/.env" ] || cp "$dir/.env.example" "$dir/.env"
chmod +x "$dir/update.sh"

echo "==> Adding nightly backup (02:00)"
job="0 2 * * * cd $dir && docker compose -f compose.tailscale.yaml exec -T app python -m app.backup --dir /backups"
( crontab -l 2>/dev/null | grep -vF "app.backup" ; echo "$job" ) | crontab -

cat <<MSG

Done. Next:
  1. Log out and back in (so you can use docker without sudo).
  2. nano ~/secretaryapp/.env   # paste TS_AUTHKEY; uncomment TS_SERVE_FILE for public
  3. cd ~/secretaryapp && docker compose -f compose.tailscale.yaml up -d --build
  4. docker compose -f compose.tailscale.yaml exec app python -m app.admin create-user yourname --role secretary
To update later: ~/secretaryapp/update.sh
MSG

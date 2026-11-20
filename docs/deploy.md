# Deploying Secretary

Everything runs in Docker: one container for the app (API and web pages together), and one for either Tailscale or Caddy in front of it. Your data lives in Docker volumes, so it survives restarts and updates.

You need a machine that stays on (a small Linux server, a spare PC or a Mac) with [Docker](https://docs.docker.com/get-docker/) and Git installed.

## A. Private, on your tailnet (recommended)

Only devices signed in to your Tailscale network can open the app, at `https://secretary.<your-tailnet>.ts.net`, with a real HTTPS certificate. There's no domain to buy and no ports to open.

**One-time Tailscale settings** (in the [admin console](https://login.tailscale.com/admin)):

1. *DNS*: turn on **MagicDNS** and **HTTPS Certificates**.
2. *Settings → Keys*: **Generate auth key** (it doesn't need to be reusable). Copy the `tskey-auth-…` value.

**On the machine:**

```bash
git clone https://github.com/kishikai/secretaryapp.git
cd secretaryapp
git checkout claude/relaxed-lovelace-2rvf5r   # until it's merged
cp .env.example .env
nano .env                                     # paste your key after TS_AUTHKEY=
docker compose -f compose.tailscale.yaml up -d --build
```

The first build takes a few minutes. Then create the first secretary account (you'll be asked for a password of at least 12 characters):

```bash
docker compose -f compose.tailscale.yaml exec app python -m app.admin create-user yourname --role secretary
```

Open `https://secretary.<your-tailnet>.ts.net` on any device in your tailnet. The *Machines* page of the Tailscale admin console shows the exact name. The first visit can take up to a minute while the certificate is issued.

To share it with a secretary, invite them to your tailnet (or share just this machine with them from the admin console). Then create their account with `create-user` as above, choosing `--role secretary`, `assistant` or `viewer`.

## B. Public, with your own domain

Use this only if people must reach the app without Tailscale. Point a domain's DNS (an `A` record) at the server, open ports 80 and 443, then:

```bash
cp .env.example .env
nano .env        # set SITE_ADDRESS=your.domain and SECRETARY_COOKIE_SECURE=1, remove the TS_ lines
docker compose up -d --build
docker compose exec app python -m app.admin create-user yourname --role secretary
```

Caddy gets and renews the HTTPS certificate automatically.

In the commands below, add `-f compose.tailscale.yaml` after `docker compose` if you used set-up A.

## Every day: nothing

The containers restart by themselves after a reboot or crash.

## Backups (set this up)

A nightly backup at 02:00, via `crontab -e` on the machine:

```cron
0 2 * * * cd /path/to/secretaryapp && docker compose -f compose.tailscale.yaml exec -T app python -m app.backup --dir /backups
```

It keeps the newest 14. To copy them off the machine (do this regularly):

```bash
docker compose -f compose.tailscale.yaml cp app:/backups ./backups-copy
```

**Restore** a backup:

```bash
docker compose -f compose.tailscale.yaml stop app
docker compose -f compose.tailscale.yaml cp ./backups-copy/secretary-YYYYMMDDT....db app:/data/secretary.db
docker compose -f compose.tailscale.yaml start app
```

## Updating

```bash
git pull
docker compose -f compose.tailscale.yaml up -d --build
```

Database changes are applied automatically when the app starts. Take a backup first.

## The message collector

On the computer where you copy Messenger messages, follow "Clipboard collector" in the README with `--api-url https://secretary.<your-tailnet>.ts.net`. That computer must be on your tailnet. Create its token with:

```bash
docker compose -f compose.tailscale.yaml exec app python -m app.admin create-token my-laptop
```

## Good to know

- The app runs as one process on purpose (SQLite and the sign-in limiter assume it). It's sized for one organisation.
- The image leaves out the optional PaddleOCR engine (about 1.2 GB). Screenshot OCR answers "OCR engine not installed" until it's added; clipboard messages work without it.
- Logs: `docker compose -f compose.tailscale.yaml logs -f app`.

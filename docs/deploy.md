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

## Free hosting on Oracle Cloud (with Tailscale Funnel)

Oracle's Always Free tier runs the app 24/7 at no cost. Tailscale Funnel makes it public at `https://secretary.<your-tailnet>.ts.net`, with no domain to buy and no Oracle ports to open. Leave out step 3 to keep it private to your tailnet.

**1. Create the VM** (at [cloud.oracle.com](https://cloud.oracle.com)): *Compute → Instances → Create instance*.

- *Image*: **Canonical Ubuntu 24.04**.
- *Shape*: **Ampere → VM.Standard.A1.Flex**, 2 OCPU and 12 GB memory (within the free limit). Avoid the AMD micro: 1 GB of RAM is too little to build the app.
- *SSH keys*: download the private key (or paste your own public key).
- Leave the networking defaults. If it says "out of capacity", try another availability domain or try again later.

Then connect: `ssh -i your-key.key ubuntu@<public-ip>`.

**2. Set up the machine** (installs Docker and Git, clones the app, adds a nightly backup):

```bash
curl -fsSL https://raw.githubusercontent.com/kishikai/secretaryapp/main/deploy/oracle-setup.sh | bash
```

Log out and back in afterwards.

**3. Turn on Funnel** in the [Tailscale admin console](https://login.tailscale.com/admin): under *DNS*, turn on **MagicDNS** and **HTTPS Certificates**. Under *Access controls*, make sure the policy grants the `funnel` attribute (new tailnets do by default; the console offers to add it if not). Then generate an auth key under *Settings → Keys*.

**4. Configure and start:**

```bash
cd ~/secretaryapp
nano .env      # paste the key after TS_AUTHKEY=, and uncomment TS_SERVE_FILE=tailscale-funnel.json
docker compose -f compose.tailscale.yaml up -d --build
docker compose -f compose.tailscale.yaml exec app python -m app.admin create-user yourname --role secretary
```

The first build takes a few minutes. Then open `https://secretary.<your-tailnet>.ts.net` from any device.

**Updating:** `~/secretaryapp/update.sh` backs up the database, pulls the latest code, rebuilds and restarts.

**Keep it from being reclaimed:** Oracle may stop Always Free VMs that stay almost idle for 7 days. Upgrading the account to *Pay As You Go* prevents this, and you still pay nothing within the Always Free limits.

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
./update.sh
```

It takes a backup, pulls the latest code, rebuilds and restarts. Database changes are applied automatically when the app starts.

## The message collector

On the computer where you copy Messenger messages, follow "Clipboard collector" in the README with `--api-url https://secretary.<your-tailnet>.ts.net`. That computer must be on your tailnet. Create its token with:

```bash
docker compose -f compose.tailscale.yaml exec app python -m app.admin create-token my-laptop
```

## Good to know

- The app runs as one process on purpose (SQLite and the sign-in limiter assume it). It's sized for one organisation.
- The image leaves out the optional PaddleOCR engine (about 1.2 GB). Screenshot OCR answers "OCR engine not installed" until it's added; clipboard messages work without it.
- Logs: `docker compose -f compose.tailscale.yaml logs -f app`.

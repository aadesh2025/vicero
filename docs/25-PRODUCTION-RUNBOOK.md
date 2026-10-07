# docs/25 — Production runbook (temporary host)

> Everything needed to run the live site over SSH, with no laptop. **No secret values appear here** —
> only variable names. Decisions behind this: ADR-107 (host and deploy model) and ADR-108 (off-server
> backups) in `docs/DECISIONS.md`.

## 1. What is running where

| Item | Value |
|---|---|
| Host | Cloud on Fire (temporary, 1-2 months), 4 vCPU / ~3.9 GB RAM / 59 GB disk, 4 GB swap, Ubuntu 24.04 |
| IP / SSH | `87.232.72.3`, user `root`, key-only (password login is off) |
| Web | https://viceroai.duckdns.org |
| API | https://viceroai-api.duckdns.org (`/healthz`, `/readyz`) |
| Code on server | `/opt/vicero`, a plain clone of https://github.com/aadesh2025/vicero (`master`, public HTTPS, read-only, **no token stored**) |
| Secrets on server | `/opt/vicero/.env` (mode 600). Never in git. |
| Compose | `infra/docker-compose.prod.yml` + `infra/docker-compose.ghcr.yml` (the pull-not-build override) |
| Images | `ghcr.io/aadesh2025/vicero-api:master`, `ghcr.io/aadesh2025/vicero-web:master` (public) |
| Firewall | ufw: 22, 80, 443 only. Caddy is the only container publishing ports. |

**Never publish 5432, 6379, 11434 or 5678.** Docker bypasses ufw for published ports.

## 2. Deploy model: pull, never build

The box has too little RAM to build images (`docker build` would exhaust it). GitHub Actions builds them.

1. Change code on GitHub (or push from any machine) to `master`.
2. GitHub → Actions → **Release (build · push · smoke · deploy)** → Run workflow (branch `master`).
   Every push to `master` also triggers this workflow, so the images may already be building.
   The web image bakes in `NEXT_PUBLIC_API_BASE_URL` from the GitHub **repo variable** of that name
   (`https://viceroai-api.duckdns.org`). If that variable is missing it silently falls back to
   `http://localhost:8000` and the site breaks.
3. Wait for the run to go green (about 2 minutes).
4. `ssh vicero /opt/vicero/deploy.sh` and wait for `[deploy] PASS`.

Release workflow notes: build cache lines were removed (they failed on this setup); images are tagged
`master`, `sha-<7 chars>`, and `<version>` on `v*` tags. Its own "deploy" job is a template that only
prints a message; the real deploy is `deploy.sh`.

## 3. The ops scripts (`ops/`, linked into `/opt/vicero/`)

| Command | What it does |
|---|---|
| `/opt/vicero/deploy.sh` | `git pull --ff-only` → fresh DB backup → pull images (4 retries) → `alembic upgrade head` → `up -d --no-build` → wait for api, web, caddy and both HTTPS endpoints → **PASS/FAIL**. Prints the previous image digests first and appends to `/opt/vicero/.deploy-history`. Stops before restarting anything if the migration fails. |
| `/opt/vicero/rollback.sh api=<ref> [web=<ref>]` | Redeploy a tag (`sha-1a2b3c4`) or digest (`sha256:…`) with the same health checks. Images only; no migration is run. |
| `/opt/vicero/status.sh` | Containers, memory, disk, newest local and Drive backup, certificate expiry, HTTPS health, running images, repo state. Read-only. |
| `/opt/vicero/set-env.sh KEY` | Set one `.env` value at a hidden prompt, keep mode 600, recreate only the services that use it. Refuses `POSTGRES_*` (see §6). |

Common settings, always together: `docker compose --env-file ../.env -f docker-compose.prod.yml -f docker-compose.ghcr.yml …`
from `/opt/vicero/infra`, and **always `--no-build`**. `--env-file` is mandatory for `${VAR}` interpolation.

## 4. Rollback

Pick the digests `deploy.sh` printed under "previous images" (or any `sha-…` tag from the GHCR package page):

```
ssh vicero "/opt/vicero/rollback.sh api=sha256:<old> web=sha256:<old>"
```

`deploy.sh` records the digests of the containers that are *running* (not just the local `:master` tag) in `/opt/vicero/.deploy-history` before it pulls, so "previous images" is always the real previous version.

**Release log** (rollback target = the version before each release):

| Deployed | Release | Rollback target (api / web) |
|---|---|---|
| 2026-10-07 | refresh-token race fix (`bfd7e85`) | `sha256:758ff685239af3f8eecb9ed6d16429e2f7e5f6803ef87e6a78c636948d6da626` / `sha256:ba316fb359bd8339b8bf45e549a84ec80c977017f5ad3f493afcefe87b7a1071` |

The pin lasts until the next `deploy.sh`, which returns to the moving `:master` tag. **The database only
moves forward** (never `alembic downgrade`). If a migration was the problem, restore from a backup (§5)
rather than downgrading.

## 5. Backups and restore

* **Local:** the `backup` container runs `infra/scripts/backup.sh` every 24 h (DB dump + uploads archive,
  14 days) into the `backups` volume. `deploy.sh` also takes one before every deploy.
* **Off-server:** `/etc/cron.d/vicero-offsite` runs `/usr/local/bin/vicero-offsite-backup.sh` at 03:30 UTC:
  fresh backup → `rclone copy` to `gdrivecrypt:` (Google Drive folder `vicero-backups`, **encrypted names
  and contents**) → delete Drive copies older than 14 days. Log: `/var/log/vicero-offsite.log`.
  The Drive login uses scope `drive.file`. The `rclone.conf` (mode 600) holds the Drive token and the obscured
  crypt passwords. **The crypt password and salt exist only in your password manager** — without them the Drive
  copies cannot be opened.

Restore test (never against the live database):

```
# 1. fetch + decrypt the newest dump from Drive
F=$(rclone lsf gdrivecrypt: --include 'vicero_vicero_*.sql.gz' | sort | tail -1)
rclone copyto "gdrivecrypt:$F" /tmp/dump.sql.gz && gzip -t /tmp/dump.sql.gz
# 2. restore into a throwaway database and compare
docker exec vicero-prod-postgres-1 psql -U vicero -d postgres -c "CREATE DATABASE restore_test"
gunzip -c /tmp/dump.sql.gz | docker exec -i vicero-prod-postgres-1 psql -U vicero -d restore_test -q
docker exec vicero-prod-postgres-1 psql -U vicero -d restore_test -tAc "select count(*) from users"
docker exec vicero-prod-postgres-1 psql -U vicero -d postgres -c "DROP DATABASE restore_test"
rm /tmp/dump.sql.gz
```

A real disaster restore (replace the live DB) is a deliberate, separate act: stop `api worker beat`,
restore into a fresh database, switch over, start the services. Do it only with a current backup of the
broken state first. The uploads archive (`vicero_uploads_*.tar.gz`) restores into the `uploads` volume.

## 6. Rotating keys

Run `ssh -t vicero /opt/vicero/set-env.sh NAME` and type the value at the hidden prompt.

| Name | Notes |
|---|---|
| `GROQ_API_KEY` | Create a new key at console.groq.com, run set-env, then delete the old key there. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud Console → project `botfroge` → Credentials. Authorized redirect URI must be `https://viceroai-api.duckdns.org/v1/auth/oauth/google/callback`. |
| `SECRET_KEY` | Signs every user out. |
| `SMTP_*`, `EMAIL_BACKEND` | Currently `EMAIL_BACKEND=console` (verification links appear in `docker logs vicero-prod-api-1`). |
| `POSTGRES_PASSWORD` | **Do not** change it in `.env` alone: the existing database keeps its old password and every service is locked out. `set-env.sh` refuses. Change it with `ALTER USER` inside Postgres first. |
| `NEXT_PUBLIC_*` | Baked into the web image: change the GitHub repo variable, re-run Release, then `deploy.sh`. |
| rclone Drive token | `rclone config reconnect gdrive:` on the server; get the token with `rclone authorize "drive" "<base64 from the server prompt>"` on a PC. |

## 7. Starting from nothing (new server)

1. Ubuntu 24.04, `apt install docker.io docker-compose-v2 git curl ufw rclone`, ufw allow 22/80/443, 4 GB swapfile.
2. `git clone https://github.com/aadesh2025/vicero /opt/vicero`; create `/opt/vicero/.env` from `.env.prod.example`
   (generate secrets with `openssl rand -hex 32`; memory limits as in ADR-107), `chmod 600`.
3. `ln -s ops/deploy.sh /opt/vicero/deploy.sh` (and the other three), then start Postgres, Redis and Ollama first
   (`dc up -d --no-build postgres redis ollama`), restore the newest Drive dump (§5) or let `deploy.sh` run the
   migrations on an empty database, and run `deploy.sh`.
4. Reinstall the off-server backup (ADR-108) and point DNS (DuckDNS) at the new IP.

## 8. Known risks

* **Temporary host**, 1-2 months, then migrate (docs/24 is the plan for the real move).
* **About 10% packet loss** to some hosts: image pulls are slow and need retries (the scripts retry).
* **Slow cold start:** after a reboot the stack needs about 5 minutes to become healthy; a 502 in that window is normal.
* **API runs `--workers 1`** because OAuth state is per-process (ADR-107). Move it to Redis before scaling.
* `EMAIL_BACKEND=console`: no real email is sent yet.
* The Drive refresh token and obscured crypt passwords sit in `rclone.conf`; root on the box can read them.
* Local backups share the box with the data; the Drive copy is the one that survives losing the server.
* Rotate any key that was ever pasted into a chat (Groq key, Google client secret).

## 9. Recovery access

* **SSH:** `Host vicero` config in §10. Add a key from a new computer by appending its **public** key
  (one line) to `/root/.ssh/authorized_keys`; remove a lost one by deleting its line. Always keep a second working
  session open while editing that file.
* **Console:** if SSH is lost, use the host provider's panel (Cloud on Fire → your server → console / VNC)
  and log in as root there. Confirm you can open it **before** you need it.

## 10. `~/.ssh/config` for any computer

```
Host vicero
    HostName 87.232.72.3
    User root
    IdentityFile ~/.ssh/vicero_hetzner
    IdentitiesOnly yes
    ServerAliveInterval 30
    ConnectTimeout 15
```

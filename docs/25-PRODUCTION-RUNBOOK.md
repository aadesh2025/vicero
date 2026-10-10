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
| 2026-10-08 | Automations + private n8n (R2 security fix, commit-before-response, R3; `bb95b95`) | `sha256:5a038c43a4429fe856917a1926a950465171933e9a829487a102fc11e9c74530` / `sha256:7e443a96f47749c17079618f54efbac2948e7e6ec606ca15ed54a65d73de950a` (the migration `0031_automations` only adds tables: rollback = these images; restore a backup rather than downgrade) |
| 2026-10-08 | ops clean-up: `deploy.sh` always pulls images (`pull --policy always`, merged PR via `fix/deploy-pull-always`), server on `0e3015a`; api `sha256:a8d8b6cf1fe2c0b2b2ce8052c7a6bb61f75d075ccd65195ba4ed778f616b5614`, web `sha256:3cd07465a0f928d82fa99a4277dc1c03e0c57de4ba15fc4188dc292d1d849de8` | `sha256:52571237a6dcb4413834060c347e901aea6681cb719cf51086fcf4235c8d82e5` / `sha256:5e0146b4c42f88b1d812407d25aeb839cdb394678e44f0745567e75362aa85eb` (no new migration; head stays `0031_automations`) |
| 2026-10-09 | offline IP-to-country fallback for the display currency (PR #12, `ac3d754`); api `sha256:2e35b6156c2694336a3c2a628ffe0552cb7a71a2cbc3c0d443360b6dbb7b88cc`, web `sha256:2ba2e92740e9cff53a3d5505b12c29b59650bf791940068160b301372fce97ca` | `sha256:a8d8b6cf1fe2c0b2b2ce8052c7a6bb61f75d075ccd65195ba4ed778f616b5614` / `sha256:3cd07465a0f928d82fa99a4277dc1c03e0c57de4ba15fc4188dc292d1d849de8` (no migration; head stays `0031_automations`; the fallback is off until its database path is set) |
| 2026-10-09 | n8n editor gate password reset (`N8N_EDITOR_GATE_HASH` replaced, only the `caddy` container recreated; no image change, no migration) | n/a (no rollback needed; the previous hash was not kept) |
| 2026-10-09 | public legal pages + Meta data-deletion callback (PR #15, merge `1161343`; migration `0032_data_deletion_requests` adds one table); api `sha256:a7e186950675a571688a88e10caf07b7e19f5ed1617cd2094fa4d2fab8bea4cd`, web `sha256:ab32de79b892bbb71ca5479422e283b4dfa4ce0d38be69928fcc54b0c3b7bccb` | `sha256:2e35b6156c2694336a3c2a628ffe0552cb7a71a2cbc3c0d443360b6dbb7b88cc` / `sha256:2ba2e92740e9cff53a3d5505b12c29b59650bf791940068160b301372fce97ca` (restore a backup rather than downgrade; head moves `0031` -> `0032`) |
| 2026-10-10 | one-click Meta connect for WhatsApp, Messenger, Instagram (PR #16, merge `40680a0`; migration `0033_meta_one_click_connect` adds 6 columns + a partial unique index on `channels`, dry-run on a restored production backup, 0 channels existed); api `sha256:a6d169ed381fa5209a0b3c33633d8c08c2161f5a528b902c44829ad90db97d9c`, web `sha256:6cd758027020f7130e9bc43cf028a7671f9f08083c645bc4ff99835225ec75cf`. Needs `META_APP_ID` and `META_EMBEDDED_SIGNUP_CONFIG_ID` set before the buttons enable (docs/26) | `sha256:a7e186950675a571688a88e10caf07b7e19f5ed1617cd2094fa4d2fab8bea4cd` / `sha256:ab32de79b892bbb71ca5479422e283b4dfa4ce0d38be69928fcc54b0c3b7bccb` (older images ignore the new columns; do not `alembic downgrade`; head stays `0033`) |

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

## 11. n8n and the Automations feature (ADR-111, docs/26)

n8n runs privately on the same host: image pinned (`N8N_VERSION`, default `2.43.1`), 768m, its own `n8n` database and role
in the existing Postgres, **no published port**. Clients never see or log into it; they see only the Automations page.
Staff reach the editor at `https://viceroai-n8n.duckdns.org` behind a second password (Caddy `basic_auth`) plus n8n's own
owner login. `/webhook*`, `/webhook-test*`, forms and MCP are answered `404` publicly; Vicero calls n8n at
`http://n8n:5678` on the compose network.

**First-time setup (once)**

1. `ssh vicero /opt/vicero/ops/n8n-setup.sh` (type the editor password at the hidden prompt, or add `--random-gate`: the
   password goes to `/root/n8n-editor-gate.txt`, root only; read it once and delete the file). It creates the secrets in
   `.env` (names only are printed), the `n8n` role/database (the role cannot connect to the Vicero database) and the gate.
2. **Save `N8N_ENCRYPTION_KEY` in your password manager** (`grep '^N8N_ENCRYPTION_KEY=' /opt/vicero/.env`, once).
   Losing it makes every credential saved in n8n unreadable.
3. `/opt/vicero/deploy.sh`. `docker compose up` refuses to run until step 1 is done, so a half-configured n8n cannot take
   the stack down. Back up first (the script does).
4. Open the editor, pass the gate, create the **owner** account (you type its password), then
   Settings → n8n API → create a key and run `/opt/vicero/set-env.sh N8N_API_KEY`.
5. Check from outside: only 22/80/443 answer (`nmap -Pn -p 22,80,443,5432,5678,6379,11434 87.232.72.3`).

**Reboot / health:** `status.sh` also reports the n8n container, the editor (`401` without the gate) and the public
`/webhook/x` (`404`).

**Memory is tight (3.7 GB host).** `status.sh` prints `ALERT WARNING` under 600 MB available and `ALERT CRITICAL` under
300 MB, and any OOM-killed container. In this order, one step at a time, asking before each:
1. lower Celery worker concurrency (`--concurrency=2` in `docker-compose.ghcr.yml`);
2. lower `N8N_MEM_LIMIT` (e.g. `640m`);
3. stop Ollama if embeddings are not needed right now;
4. move to a bigger plan (docs/24).
Nothing else is changed without asking.

**Logs and retention:** n8n keeps about 7 days of executions; Vicero keeps its own run log for 30 days and deletes older
rows daily. A failed run's text is replaced by a short plain-language message before it is stored.

**Known limits**
* Stock Caddy cannot rate-limit logins (needs the `caddy-ratelimit` module and a custom image); the gate password stands
  in front of the editor instead.
* n8n's Sustainable Use License limits how n8n may be offered to third parties. Clients never use n8n themselves and see no
  n8n branding; review the license before selling "n8n" as a feature (ADR-111).

**Secrets limit (ADR-111, RISK-REGISTER R17).** Vicero does not send a client's secrets to n8n on each call: n8n stores webhook
input in its execution data (about 7 days). A client's third-party access lives in a **per-org named credential inside n8n**,
named `ORG-<org_id> | <service>` (encrypted with `N8N_ENCRYPTION_KEY`); never put a key in a workflow's input, output or name.
Keep `EXECUTIONS_DATA_MAX_AGE` short (168 h). Vicero's own log keeps only a redacted, truncated summary for 30 days.

## 12. How to build a client automation (checklist)

Real automations are built only from a client's request in the queue (Admin → Client automations).

1. **Request received.** Read it in the queue (org, agent, what they asked). Set it to *building*, or *rejected* with a plain
   note the client will read. Ask the client for anything missing; never ask for a password in chat.
2. **Plan check.** The request was already refused with an upgrade message if the plan has no free slot (Pro 5, Business 20;
   Trial/Starter 0). Check the org's plan and monthly run cap (2,000 / 10,000) before promising anything.
3. **Build** in the n8n editor.
   * Name `ORG-<org_id> | <client name> | <purpose>` and tag it with the org slug. No emails, phones or other personal data in names or tags.
   * No Code nodes. Reuse a shared workflow when the capability fits several clients; a per-org workflow only when needed.
   * Credentials: one per org, named `ORG-<org_id> | <service>`. Never paste a secret into a node.
   * Webhook trigger (if an agent will call it): verify Vicero's signature first (see `infra/n8n/`); async workflows echo `callback_token`.
   * End with the signed **report run to Vicero** step (`POST /internal/automations/runs`, `AUTOMATION_REPORT_SECRET`); the global
     Error Workflow reports failures the same way.
4. **Test** with sample data in the editor: one success and one forced failure. Check the failure reads as a plain sentence on the
   client's Automations page (never the raw n8n text).
5. **Register** (Admin → Client automations → *Register workflow*, or `POST /v1/admin/automation-registry`): org, agent (optional),
   the n8n workflow id, the `/webhook/<path>` if an agent calls it. One workflow belongs to exactly one org.
6. **Activate** the workflow in n8n, confirm the first real run appears in the client's history and the request shows *active*.
   To stop it: *Pause* in the registry (agents stop calling it at once); the run history stays.

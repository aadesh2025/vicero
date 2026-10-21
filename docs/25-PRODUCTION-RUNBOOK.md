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
| Firewall | ufw: 22, 80, 443 only. Caddy is the only container publishing to the internet; Postgres publishes `127.0.0.1:5433` (loopback, SSH tunnel only, §13). |

**Never publish 5432, 6379, 11434 or 5678 on a public address.** The one exception is Postgres on `127.0.0.1:5433` (§13). Docker bypasses ufw for published ports.

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
| 2026-10-18 | Widget layouts + interactive blocks (docs/27, ADR-128 to ADR-131, section 17) on `ee12c2a`; migration 0037 was already applied by the 16:51Z deploy; new api `sha256:bd1228b4530960de35c3f470d506476746491272c059b9e435b0d4d5b0f1004f`, web `sha256:7429887ec3306ae0dbbec6638888c2e2fb6d3ea2ea35383a509444d0a6151163`; the 16:51Z deploy (`d2ab407`, images and catalog, token diet) is the step before this one | api `sha256:7ababe9911fdaf23c32730aaa0a4b7870ce809826ab56dd29e65bb3d9dcfdd1b` / web `sha256:352561a2cb1ea8c45e63e47bf641e244d86ba49dddf4fa8b0b681a328d831509` |
| 2026-10-20 | docs/28 Agent tools, MCP, API and webhooks T1-T5 (ADR-132 to ADR-137) on `c41a8a0`; migrations 0038-0041 (the first attempt on `3ea1804` rolled back in 0039, see ADR-137); api `sha256:60f1bb7325e6e46756ce9093f6bc3337def1fa7d479644449f4e21f0ab369921`, web `sha256:d81bfd55de88ce870f1adb2aa1efb44699d0b4675f49b67efaa74dfeaa2742c0`; `MCP_TOOL_TIMEOUT_SECONDS=30` set with `set-env.sh` afterwards | api `sha256:bd1228b4530960de35c3f470d506476746491272c059b9e435b0d4d5b0f1004f` / web `sha256:7429887ec3306ae0dbbec6638888c2e2fb6d3ea2ea35383a509444d0a6151163` (migrations 0038-0041 are additive: roll back with these images, never `alembic downgrade`; restore a backup only if a migration is the problem) |
| 2026-10-20 | MCP results sent once and capped at 8000 chars, readable MCP errors (PR #47, `73a99ba`); no migration (head stays `0041_tool_behaviour`); api `sha256:34ac61384597cc12bf590397ad04e921d23d2c0c8a96e9b3f6dcc62fa2efbcbc`, web `sha256:24d35e01a30deb2a13ef61ad8ffcce3888621767019bce690d48069a3fff5923`; done after freeing the disk (section 18) | api `sha256:60f1bb7325e6e46756ce9093f6bc3337def1fa7d479644449f4e21f0ab369921` / web `sha256:d81bfd55de88ce870f1adb2aa1efb44699d0b4675f49b67efaa74dfeaa2742c0` |
| 2026-10-09 | n8n editor gate password reset (`N8N_EDITOR_GATE_HASH` replaced, only the `caddy` container recreated; no image change, no migration) | n/a (no rollback needed; the previous hash was not kept) |

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

**Two public exceptions (AUROZEN lead-gen, in the repo from 2026-10-21):** `POST /webhook/leads` and `/webhook/next-job` on the n8n domain skip the 404 rule and the editor gate (the `@leadgen` block in `infra/caddy/Caddyfile`), because an outside scraper calls them. Each workflow checks an `x-token` header against a token written inside its own Code node and rejects a wrong one. Known limits (2026-10-21 review, not changed): the token compare is not constant-time; the token lives in the workflow, not in an n8n credential; and in "next job" the check runs after the two Google Sheets reads, so an unauthenticated call still costs two sheet reads. Moving the check before the reads is the first fix. The n8n container also sets `NODE_OPTIONS=--dns-result-order=ipv4first` because this host has no usable IPv6 (ADR-119).

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

## 13. Visual DB access (read-only, SSH tunnel)

Postgres is bound to the server's loopback only (`127.0.0.1:5433`, compose `postgres.ports`). It is not reachable from the
internet; the only way in is an SSH tunnel with your key. The `viewer` role can only `SELECT`, and secret columns are hidden
(`ops/viewer-setup.sh`, ADR-115). No password is stored in git or on the server.

**Create the role** (prints the password once; save it in your password manager):
```
ssh vicero /opt/vicero/ops/viewer-setup.sh
```
New password: `ssh vicero /opt/vicero/ops/viewer-setup.sh --rotate`. Remove access: `... --drop`. `deploy.sh` re-applies the grants
after every migration (no-op if the role does not exist); a table added later is invisible to `viewer` until then.

**DBeaver on Windows**
1. Database > New Database Connection > PostgreSQL > Next.
2. Main tab: Host `localhost`, Port `5433`, Database `vicero`, Username `viewer`, Password = the one shown by the script. Tick Save password.
3. SSH tab: tick Use SSH Tunnel. Host `87.232.72.3`, Port `22`, User `root`, Authentication Public Key,
   Private key `C:\Users\user\.ssh\vicero_hetzner` (passphrase only if the key has one).
4. Click Test tunnel configuration, then Test Connection (accept the host key the first time). Finish.
5. Browse `vicero` > Schemas. Tables with secret columns appear under the `viewer` schema (safe columns only); the rest under `public`.

**Check it is not public** (from any other computer): `nmap -Pn -p 5432,5433 87.232.72.3` must show both closed/filtered, and on the
server `ss -ltn | grep 5433` must show `127.0.0.1:5433` and nothing else. `tests/test_infra_prod_compose.py` fails if compose publishes
anything but Caddy and that loopback port.

**Migrations and the viewer views (ADR-137).** Postgres refuses to retype or drop a column a view reads, and prod has `viewer.*` views while dev does not. Migrations that change columns wrap the change in `views_suspended("table")` (`app/db/migration_helpers.py`). `deploy.sh` runs `viewer-setup.sh --grants-only` after every migration, which rebuilds every view from `ops/viewer-grants.sql`. What the viewer can read on the docs/28 tables: `connections` (not `secrets_enc`, `default_headers`), `mcp_servers` (not `auth_config_enc`, `url_or_command`), `inbound_hooks` (not `secret_enc`, `public_id`), `tools` (not `config`), `handoffs` (not `payload`); `agent_tool_bindings`, `agent_mcp_bindings`, `inbound_hook_events` and `tool_runs` are readable whole (no secrets in them). CI test: `tests/test_viewer_migrations.py`.

## 14. DNS for the containers (ADR-119)

From the Hetzner network `1.1.1.1` and `8.8.8.8` time out on UDP/53, while `9.9.9.9`, `1.0.0.1` and `213.133.98.98` answer in
under 200 ms. Docker's embedded resolver forwards to the host's resolvers in order, so every uncached lookup
(`api.groq.com`, `graph.facebook.com`) cost about 4 s and made the L2/L3 guard calls time out. `docker-compose.prod.yml` now
gives `api`, `worker` and `beat` their own `dns:` (those three, in that order) and `dns_opt: ["timeout:1", "attempts:2"]`.
Applied by `deploy.sh` (it recreates the three containers); no daemon restart.

**Check** (the first lookup of each host must be under 0.5 s):
```
ssh vicero "docker exec vicero-prod-worker-1 python scripts/dns_check.py"
ssh vicero "docker exec vicero-prod-worker-1 cat /etc/resolv.conf"   # 127.0.0.11 inside Docker; the upstreams are the three above
```

**Host-wide (optional, later).** To fix the same for every container (n8n, caddy, ollama) put this in `/etc/docker/daemon.json`
(merge with what is there; do not overwrite other keys):
```
{ "dns": ["9.9.9.9", "1.0.0.1", "213.133.98.98"] }
```
It only takes effect when the Docker daemon restarts, which restarts or stops every container. Do it in a maintenance window
(`systemctl restart docker`, or `live-restore` if enabled), never as part of a deploy. Until then the per-service setting above
is the fix. User-defined compose networks use Docker's embedded resolver, which honours the service-level `dns:` for its
upstreams.


## 15. Booking tools (n8n) (ADR-120, docs/27)

Install for an agent and verify it: see `docs/27-BOOKING-TOOLS.md`. Google Calendar is connected from the dashboard (Tools tab); the redirect URI to register in Google Cloud is `https://viceroai-api.duckdns.org/api/google/callback`. Short form:
```
ssh vicero "cd /opt/vicero && git pull -q --ff-only && docker cp infra/n8n/workflows vicero-prod-api-1:/tmp/n8n-workflows"
ssh vicero "docker exec vicero-prod-api-1 python scripts/install_booking_tools.py --agent-id <AGENT_UUID> --dir /tmp/n8n-workflows --publish"
ssh vicero "docker exec vicero-prod-api-1 python scripts/test_booking_tools.py --agent-id <AGENT_UUID>"
```
Rollback: deactivate the three `ORG-<id> | Booking | ...` workflows in n8n (or disable the tools in the agent). The `bookings` table is additive and can stay.

## 16. Images and the product catalog (ADR-124, docs/26)

**Before deploying**
1. Migration `0036_media_and_products` is additive (three tables, one defaulted column); `deploy.sh` runs it. Safe with the API up.
2. Add to `/opt/vicero/.env`: `MEDIA_URL_SIGNING_KEY` (a long random value; if empty it falls back to `SECRET_KEY`, which works but ties image links to the JWT key). Defaults are fine for the rest (`.env.example`, `docs/ENV.md`).
3. A `GEMINI_API_KEY` (or the key for whatever `VISION_PROVIDER` names) must exist, otherwise photos are stored but described as "could not be loaded".
4. **Volume:** `MEDIA_DIR` (default `./var/media`) must be a named volume shared by `api` and `worker`, like `uploads` (ADR-068). Add it to `docker-compose.prod.yml` for both services, create the path in the image owned by the app user, and add it to the backup script next to `UPLOADS_DIR`. Without it the worker saves a photo the API cannot show.
5. **Proxy body size:** Caddy allows large bodies by default; if a `request_body max_size` is set, raise it to 12MB for `/v1/products/*/images` and `/v1/inbox/conversations/*/images`.
6. The API's public URL (`API_BASE_URL`) must be the real HTTPS address: it is the host in every image link Meta downloads.

**Rollback:** set `MEDIA_FEATURE_ENABLED=false` and restart `api` and `worker`. Inbound photos then get "I can read text and photos for now - could you type your question?", and the catalog tools are not offered. Nothing is deleted.

**Watch the logs for** `media_fetch_failed`, `media_rejected`, `vision_failed`, `media_send_rejected`. `vision_described` carries the cost of each photo.

**Live check (WhatsApp, Instagram, Messenger):** send a photo and see a sensible answer; ask for a product photo and see it arrive after the text; on WhatsApp, a photo after 24 hours is refused with the usual window message; an operator photo from the inbox arrives; the photo is gone from the inbox after 30 days ("Photo deleted after 30 days").


## 17. Widget layouts and interactive blocks (docs/27, ADR-128 to ADR-131)

**Release checklist (prepared, not run)**

| What | Detail |
|---|---|
| Commits | `715203c` layouts, `a31613c` blocks, `059eaeb` esbuild minify, `fef8269` docs, plus the merge of `origin/master` and the follow-up fixes (see `git log`). Content is identical to branch `widget/docs27-on-master` (same work, cleaner history); push one or the other, not both. |
| Migration | `0037_message_blocks`: adds nullable `messages.blocks` (JSONB). Additive, safe with the API running. Its `downgrade()` drops the column (verified up, down, up on a scratch database). `deploy.sh` runs it. |
| Widget bundle | Rebuilt by `cd packages/widget && npm ci && npm run build` (esbuild minify, 67.5 KB, 19.0 KB gzip). The committed `apps/web/public/widget.js` is what the web image serves; nothing is built on the server. |
| New env vars | None for docs/27. If docs/26 (images and catalog) and the token-diet PRs (#31, #33, #34) are not yet live, this deploy also ships them: do the section 16 checklist first (`MEDIA_URL_SIGNING_KEY`, vision key, media volume). |
| Behaviour change for existing agents | None. Layouts and blocks are off until an owner picks a design or switches Interactive blocks on (Channels tab). A cached old `widget.js` keeps working (tested). |

**Deploy (you run these; nothing here is automatic)**

1. Push to `master` (a normal push). Wait for GitHub Actions **Release (build, push, smoke, deploy)** to go green, about 2 minutes.
2. `ssh vicero /opt/vicero/status.sh` (read-only; confirm disk, backups and health first).
3. `ssh vicero /opt/vicero/deploy.sh` and wait for `[deploy] PASS`. Copy the "previous images" digests it prints into the release log row above.
4. Check the migration: `ssh vicero "docker exec vicero-prod-api-1 alembic current"` should print `0037_message_blocks (head)`.
5. Check the site: open an existing embedded widget (looks and behaves as before), then Channels tab, pick a design, and watch the live preview.
6. Optional block check on a test agent: switch **Interactive blocks** on, ask for options, a booking time and a contact form, and tap through.

**Rollback**

- Images only: `ssh vicero "/opt/vicero/rollback.sh api=sha256:<previous> web=sha256:<previous>"`. The old API ignores the extra column, so no database step is needed. The runbook rule stands: the database only moves forward.
- Per agent, immediately and without a deploy: turn **Interactive blocks** off or set the design back to **Original** (a save is live).
- Source: `git revert` the docs/27 commits on `master`. Only if you also want the column gone, after the images are rolled back: `alembic downgrade -1` (drops `messages.blocks`; blocks already stored are lost; plain-text replies keep their text because `content` carries the fallback text).

**Watch** `blocks_discarded` (a turn whose blocks were withdrawn by the output guard), `block_rejected` (the model proposed an invalid block) and the widget `GET /v1/public/agents/*/conversations/*/messages` rate (60/min per visitor).

**Known limits:** the model can invent booking days and times (it has no availability tool in this release; day labels are derived from the date, times are not checked); block text with phone numbers or emails the organisation has not published is withheld by the PII guard; blocks need a tool-calling model.

## 18. Freeing disk safely (RISK-REGISTER R21, 2026-10-20)

`deploy.sh` prints free disk before and after, and **refuses to start under 3 GB free** (`DEPLOY_MIN_FREE_MB`, default 3072). `status.sh` warns above 85% use and is critical above 95%.

Where the space goes: Docker here uses the containerd image store, so `/var/lib/docker` is small (about 0.5 GB) and the images live under `/var/lib/containerd`. Every release leaves the previous api (1.6 GB) and web (0.4 GB) images behind as untagged ("dangling") images; 52 of them took about 25 GB. The backups volume is only about 5 MB.

**Safe, in this order** (look first with `df -h /`, `docker system df`, `docker images -a --digests`):
1. Keep the **running** images and the **rollback target** (the digests `deploy.sh` printed as "previous images"; also in the release log). Remove every other dangling image one by one: `docker rmi <id>` (Docker refuses an image a container uses). Do not use `docker image prune`: it also removes the rollback target.
2. `journalctl --vacuum-size=200M`, `apt-get clean`.
3. Before trimming local backups, confirm the newest dump is on Drive (`status.sh`, "newest on Drive").

**Automatic (from 2026-10-20):** after a `deploy.sh` PASS it calls `prune_old_images` (`ops/lib.sh`). It removes untagged images only, one by one with `docker rmi`, and prints each one removed plus the free disk after. It keeps every image a container is using, the `:master` tags, and the digests of the last 3 release pairs in `.deploy-history` that still exist on disk (the running pair plus the 2 before it; `KEEP_RELEASE_PAIRS` changes the number). It never touches a volume and never fails a deploy that already passed. To preview on the server: `PRUNE_DRY_RUN=1 bash -c '. /opt/vicero/ops/lib.sh; prune_old_images'` (removes nothing).

**Never run:** `docker volume prune`, `docker system prune --volumes`, or anything touching the volumes `pgdata`, `uploads`, `media`, `caddydata`, `redisdata`, `ollamadata`, `n8ndata`.

`du -xh --max-depth=2 /` takes many minutes here (millions of image files); run it in the background, or only on `/var/log /var/cache /var/lib/apt /opt`.

## 19. Not verified live: tool approval follow-up (docs/28 T4, 2026-10-21)

Covered by automated tests but never run on production: the **follow-up assistant message** after a person approves or declines a tool call in the Inbox, and the **Approve button click** itself. Live, only the API calls behind the buttons were exercised (`POST /v1/inbox/tool-approvals/{id}/decide`): approve ran the stored call, decline did not.

**The 5-minute check** (needs the owner; no one else should type the production password):
1. In Chrome, log in to https://viceroai.duckdns.org as the owner (a staff account). Open Admin > Workspaces, create or pick a throwaway workspace, and use **Change plan** to put it on Pro (audited, with a note).
2. In that workspace: Settings > Connections, add `https://jsonplaceholder.typicode.com` with no auth. On a new agent, Tools tab: add an API tool `delete_todo` (DELETE `/todos/{{id}}`, input `id` integer) with **approval required** on. Model tab: tools on, tool choice "required first turn". **Publish** the agent (the follow-up is only sent for a published agent; publishing also needs a verified email).
3. Open the agent's widget chat as a visitor and send: `Yes, I confirm: call delete_todo now for to-do number 7.`
4. Inbox > Tool approvals: **click Approve**. Expect: the call runs (Tool runs shows `success`), and within a few seconds the **conversation gets a new assistant message** that reports the outcome. Repeat with a second request and click **Decline**: nothing runs, and the follow-up says so.
5. Delete the throwaway workspace (Settings > Delete workspace). Note the date and result here.

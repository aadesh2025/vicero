# docs/16 — Local → VPS migration runbook

> **Update 2026-09-25 — read `docs/19-ORACLE-SINGLE-VPS-PLAN.md` first.** ADR-096 supersedes ADR-086: production uses the
> bundled Postgres on the Oracle VM again (Supabase is at most an off-box backup target). The database sections of this
> runbook apply again. Its box sizing (4 OCPU / 24 GB) is stale — Oracle Always Free is now 2 OCPU / 12 GB — and its s7
> backup gap for `uploads` was closed by ADR-082. Where this doc and docs/19 disagree, docs/19 wins.

> *(Historical — obsolete as of 2026-09-25, see the update above: the database sections apply again under ADR-096.)* ~~**⚠️ NEEDS A REVISION PASS — do not follow the database sections as written.**~~ This runbook assumes a
> **self-hosted Postgres container on the VM** throughout (compose `postgres` service, local `pg_dump`
> backups, no published DB port, `DATABASE_URL` pointing at a sibling container). The decision since made
> (**ADR-086, 2026-09-24**) is **Supabase-managed Postgres**, with the application containers still on Oracle.
> That changes the connection string, pooler mode, TLS and backups, and adds an RLS/Data-API exposure to close
> first. Revise this doc once the Supabase decision is concrete — note ADR-086 records that **auth stays Vicero's own (option a) — only the database moves.** Everything that is not about the database is unaffected.

> **Scope.** Moving Vicero from "runs on my Windows machine" to "runs on a public VPS with real
> clients on it." Target box in this doc: **Oracle Cloud Always Free, VM.Standard.A1.Flex, 4 OCPU /
> 24 GB RAM, ARM (aarch64), Ubuntu 22.04**.
>
> **Read `docs/15-DEPLOYMENT-CAPACITY.md` first** for the sizing arithmetic and topology options.
> This doc is the *how*, not the *whether*. docs/15 §4 Option 1 (single VPS, reduced scope) is the
> topology assumed throughout.
>
> **Status: NOT YET EXECUTED.** Nothing in here has been run against a real Oracle box. Every
> section marked ⚠️ is a claim verified against the code in this repo; every section marked 🔶 is
> a prediction that needs confirming on first deploy. Do not read a 🔶 as a fact.

---

## 0. The headline, before anything else

**The migration is much smaller than you think, and one specific thing is much bigger.**

Small, because `infra/docker-compose.prod.yml` already exists and is mature: ten services, TLS via
Caddy, one-shot migrations, memory limits (ADR-069), nightly `pg_dump`, non-root images, no
published database port. You are not writing a deployment — you are filling in a `.env` and
pointing DNS at a box.

Big, because of five things the compose file cannot fix for you:

| # | Issue | Severity |
|---|---|---|
| 1 | **CORS will block the widget on every client site** | ⛔ blocks the product |
| 2 | **n8n is not in the production compose at all** | ⛔ blocks automations |
| 3 | **Embeddings cannot leave Ollama — there is no other adapter** | ⚠️ constrains the box |
| 4 | `NEXT_PUBLIC_API_BASE_URL` is baked at **build** time | ⚠️ silent wrong-URL bugs |
| 5 | Uploads volume is **not** covered by the backup script | ⚠️ data loss on restore |

Each is expanded below with the code that proves it. Sections 1–3 are what you do; sections 4–13
are what breaks and why.

---

## 1. Provisioning the Oracle box

### 1.1 Create the instance

1. Sign up at `oracle.com/cloud/free`. A card is required for identity verification; Always Free
   resources are not charged.
2. Create Compute instance:
   - Shape: **VM.Standard.A1.Flex**, 4 OCPU, 24 GB RAM (marked "Always Free eligible")
   - Image: **Ubuntu 22.04** (aarch64 build)
   - Boot volume: 100–200 GB (Always Free allows 200 GB total block storage)
3. **Download the SSH private key at creation.** Oracle shows it once. This is what you and
   Claude Code will use to reach the box.

⚠️ **This box is not the box docs/15 sized for, and the difference is in the wrong direction.**
docs/15 §4 Option 1 specifies **16 GB / 8 vCPU x86**. Oracle Always Free gives **24 GB / 4 OCPU
ARM** — more RAM than needed, *half the cores*. docs/15 §3.4's central argument is that **CPU
contention, not RAM, is the binding constraint** on this stack. So the resource this deployment has
least of is precisely the one docs/15 says will run out first, and §8's inline query embedding plus
§11's Celery ingest both land on it. The memory ceilings in the compose file will look comfortable
while the machine is CPU-bound. Watch `docker stats` CPU%, not memory, on first load.

> 🔶 **The known failure mode.** Oracle's free ARM capacity is frequently exhausted —
> `Out of host capacity` on instance creation is common and can persist for days in a region.
> Mitigations: try a different availability domain, try a different home region at signup (the home
> region cannot be changed later), retry at off-peak hours. If this blocks you for more than a week,
> docs/15 §4 Option 1 costs €4–6/mo on Hetzner and is not worth losing a month over.

### 1.2 Two firewalls, not one — this is the classic Oracle trap

Oracle Ubuntu images ship with **restrictive `iptables` rules preinstalled**, *on top of* the
cloud-level Security List. Opening the Security List alone leaves ports still closed and produces a
"DNS resolves, TLS times out" symptom that looks like a Caddy problem and is not.

Both layers need ports **80** and **443**:

```bash
# Layer 1 — Oracle console: VCN → Security Lists → Ingress Rules
#   0.0.0.0/0 → TCP 80
#   0.0.0.0/0 → TCP 443

# Layer 2 — on the box itself
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

**Do not open 5432, 6379, 11434, or 5678 to the internet.** Postgres and Redis publish no host port
in the prod compose by design; Ollama has no authentication of any kind (see the `expose:`-not-
`ports:` comment in `docker-compose.prod.yml`); n8n is covered in §6.

### 1.3 Base software

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
```

### 1.4 Swap — worth doing even at 24 GB

The memory ceilings in the prod compose sum to more than the host has, deliberately (they are
ceilings, not a budget). A few GB of swap converts a rare simultaneous spike from an OOM kill into
a slow minute:

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 2. DNS and the domain map

You need a domain. Two A records, both pointing at the instance's public IP:

| Hostname | Serves | Container |
|---|---|---|
| `app.yourdomain.com` | Dashboard, **and `/widget.js`** | `web` (Next.js :3000) |
| `api.yourdomain.com` | REST, WebSocket, widget chat, channel webhooks | `api` (FastAPI :8000) |
| `n8n.yourdomain.com` | n8n editor + webhook receiver | see §6 — **not in the compose** |

⚠️ **`widget.js` is served by the *web* container, not the API.** It lives at
`apps/web/public/widget.js`, so the embed script URL is on `app.yourdomain.com` while the chat calls
go to `api.yourdomain.com`. That split is the reason §4 exists.

**Point DNS before the first `up`.** Caddy requests Let's Encrypt certificates on boot via the
HTTP-01 challenge; if DNS is not yet resolving, it fails and backs off, and you will be reading
Caddy logs for a problem that is a DNS propagation delay.

---

## 3. Environment: the two-file rule

⚠️ This trips everyone once, and the compose header documents it because it was got wrong here
first. **Two different mechanisms read environment variables, from two different files.**

| Mechanism | Reads from | Variables |
|---|---|---|
| `env_file: ../.env` → container env | `../.env` (repo root) | `SECRET_KEY`, `API_BASE_URL`, `WEB_BASE_URL`, `CORS_ORIGINS`, LLM keys, `EMBEDDING_*`, channel tokens |
| `${VAR}` interpolation → Compose itself | shell, or `infra/.env`, or `--env-file` | `POSTGRES_PASSWORD`, `DOMAIN`, `API_DOMAIN`, `ACME_EMAIL`, `NEXT_PUBLIC_API_BASE_URL`, all `*_MEM_LIMIT` |

So the deploy command is:

```bash
cd /opt/vicero/infra
docker compose --env-file ../.env -f docker-compose.prod.yml up -d --build
```

`--env-file ../.env` is **not optional**. Without it, interpolation fails loudly before anything
starts (`required variable POSTGRES_PASSWORD is missing a value`) — which is the one good thing
about this trap: it cannot fail silently.

### 3.1 The variables that must change from their local values

Every one of these is `localhost` in `.env.example`. Each has a *different* consequence when left
that way, which is why they are listed separately rather than as "update the URLs":

```bash
# ── Core identity ────────────────────────────────────────────────────────────
ENV=prod
SECRET_KEY=<64+ random chars — openssl rand -hex 32>   # NOT the dev value
POSTGRES_PASSWORD=<strong, unique>                      # NOT "vicero"

# ── Hostnames (interpolation — put these in infra/.env too, or pass --env-file)
DOMAIN=app.yourdomain.com
API_DOMAIN=api.yourdomain.com
ACME_EMAIL=you@yourdomain.com
NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com

# ── Container-side URLs (../.env) ────────────────────────────────────────────
API_BASE_URL=https://api.yourdomain.com
WEB_BASE_URL=https://app.yourdomain.com
CORS_ORIGINS=https://app.yourdomain.com    # ⛔ see §4 before accepting this line
N8N_BASE_URL=https://n8n.yourdomain.com    # see §6
VICERO_API_BASE_URL=https://api.yourdomain.com   # scripts/provision-client.mjs
```

⚠️ **`API_BASE_URL` and `WEB_BASE_URL` are not decoration.** Verified in code:

- `app/channels/service.py:38` builds every **channel webhook URL** from `settings.api_base_url`.
  Left at `localhost:8000`, you will register `http://localhost:8000/v1/channels/...` with Meta and
  Telegram. They will accept it and no message will ever arrive.
- `app/tools/n8n_tool.py:91` sets the n8n **callback URL** from the same setting. n8n calls back
  into nothing.
- `app/modules/auth/service.py:193,227,262` and `app/modules/orgs/service.py:243,332` build the
  **verify / reset / magic-link / invitation-accept** links from `settings.web_base_url`. Left at
  localhost, every email you send a client contains a link that only works on your laptop.

None of these fail at startup. They fail later, in front of a client.

---

## 4. ⛔ CORS: the widget will not work on client sites without a decision

**This is the single most likely thing to break your first client install.**

The evidence, from `apps/api/app/main.py:151-158`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # In dev, accept any localhost port so the web dev server works whatever port it grabs.
    allow_origin_regex=r"http://localhost:\d+" if not settings.is_prod else None,
    allow_credentials=True,
    ...
)
```

Read the middle line carefully. In dev, **any** localhost origin is allowed, which is why the widget
works on your machine and in `widget-demo.html`. Under `ENV=prod` that regex becomes `None`, and the
only permitted origins are whatever is in `CORS_ORIGINS`.

The widget runs on **the client's own website** — `https://theirshop.com` — and posts to
`POST https://api.yourdomain.com/v1/public/agents/{public_key}/chat`. That is a cross-origin request
from an origin that is not in your `CORS_ORIGINS`. The browser blocks it.

⚠️ Note what *does* work, because it will mislead you while debugging: the widget **logo** route
(`app/modules/public/router.py:47`) sets `Access-Control-Allow-Origin: *` explicitly. So the bubble
renders with the right logo and colours, and then chat fails. "The widget appears but doesn't
reply" is this bug, not a model or key problem.

### Options

| Option | What it means | Trade-off |
|---|---|---|
| **A. Add each client origin to `CORS_ORIGINS`** | `CORS_ORIGINS=https://app.yourdomain.com,https://client1.com,https://client2.com` | Zero code. Manual edit + API restart per client. Fine for the first 5–10 clients. |
| **B. Wildcard the public chat routes** | Mount a second CORS policy scoped to `/v1/public/*` with `allow_origin_regex=".*"` and `allow_credentials=False` | Correct long-term shape. Needs code + tests. Public routes are already keyed by `public_key`, so origin is not the security boundary. |
| **C. Per-agent allowed-domain list** | Store permitted origins on the agent; echo `Access-Control-Allow-Origin` per request | The real answer. This is checklist item **12.2**, recorded as failing in CLAUDE.md §11 (2026-08-10): *"no per-agent embed domain restriction"*. |

**Recommendation: A now, C later.** Do not ship B and call 12.2 done — B removes the CORS error and
grants no origin control, which is a different thing from the restriction 12.2 asks for.

> ⚠️ Whichever you pick, `allow_credentials=True` and `allow_origin_regex=".*"` are **mutually
> incompatible** per the CORS spec — browsers reject a wildcard `Access-Control-Allow-Origin` on a
> credentialed request. If you go with B, the public-route policy must set
> `allow_credentials=False`. The widget authenticates by `public_key` in the URL, not by cookie, so
> it does not need credentials — but confirm that before flipping it.

---

## 5. The widget: what changes and what does not

Good news first. From `apps/web/src/components/builder/tabs/channels-tab.tsx:106`:

```ts
const webOrigin = typeof window !== "undefined" ? window.location.origin : "";
const snippet = `<script
  src="${webOrigin}/widget.js"
  data-agent="${publicKey}"
  data-api="${API_BASE}"
  defer></script>`;
```

`webOrigin` is read from the browser at runtime, so the moment the dashboard is on
`https://app.yourdomain.com`, the generated snippet says so. **No code change needed.**

⚠️ `API_BASE` is not runtime — it comes from `NEXT_PUBLIC_API_BASE_URL`, which
`apps/web/Dockerfile` takes as a **build arg** and Next.js inlines into the bundle at build time.
Consequences:

- Setting it in `.env` after the image is built **does nothing**. It is compiled in.
- Changing your API domain later requires `docker compose build web`, not a restart.
- If you build with the default (`https://api.localhost`), every client's embed snippet will carry
  `data-api="https://api.localhost"` and every widget will fail. Nothing warns you.

⚠️ And the fallback if `data-api` is ever absent, from `packages/widget/src/widget.js:25-27`:

```js
var API = (script && script.getAttribute("data-api")) ||
          (location.protocol + "//" + location.hostname + ":8000");
```

On a client's site that resolves to `https://theirshop.com:8000` — their server, port 8000. Harmless
because it just fails, but the error message will point at the client's domain and waste an hour.
**Always ship the snippet with an explicit `data-api`.**

### Checklist for the first embed

- [ ] `NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com` set **before** `docker compose build`
- [ ] Snippet copied from the live dashboard, not from a local one
- [ ] Client's origin handled per §4
- [ ] `https://app.yourdomain.com/widget.js` returns JS (not the Next.js 404 page)

---

## 6. ⛔ n8n is not in the production compose

Verified. `infra/docker-compose.prod.yml` declares ten services: `postgres`, `redis`, `migrate`,
`api`, `worker`, `beat`, `web`, `caddy`, `ollama`, `backup`. **There is no `n8n`.** The dev compose
(`infra/docker-compose.yml`) has one; production does not.

This is deliberate, not an oversight — `apps/api/tests/test_infra_prod_compose.py:36` reads:

```python
_EXTERNAL_HOSTS = {"n8n"}
```

which is the allowlist that stops the "every internal hostname must be a declared service" test
(added after PROD-2, the missing-Ollama outage) from failing on n8n. n8n is *expected* to be
external.

⚠️ So: `N8N_BASE_URL` defaults to `http://n8n:5678` in `x-api-env`, and if you deploy as-is with
automations configured, that hostname does not resolve inside the compose network. **This is the
exact shape of PROD-2** — correct in dev, silently broken in production, no symptom that names the
cause. Do not let it repeat.

### 6.1 Running n8n alongside

Add a compose file of your own (keep it separate from `docker-compose.prod.yml` so the test above
keeps meaning what it says) on the same Docker network, and give it a Caddy block:

```
# infra/caddy/Caddyfile — add alongside the existing two blocks
{$N8N_DOMAIN} {
	encode zstd gzip
	reverse_proxy n8n:5678
}
```

n8n's own environment must know its public URL, or the webhook URLs it displays in the editor will
be wrong (this is n8n's behaviour, not ours):

```yaml
environment:
  N8N_HOST: n8n.yourdomain.com
  N8N_PROTOCOL: https
  WEBHOOK_URL: https://n8n.yourdomain.com/
  N8N_BASIC_AUTH_ACTIVE: "true"
  N8N_BASIC_AUTH_USER: ${N8N_BASIC_AUTH_USER}
  N8N_BASIC_AUTH_PASSWORD: ${N8N_BASIC_AUTH_PASSWORD}   # ⚠️ change from "vicero"
  N8N_DIAGNOSTICS_ENABLED: "false"    # it exits if telemetry DNS fails — see dev compose comment
  GENERIC_TIMEZONE: UTC
volumes:
  - n8ndata:/home/node/.n8n
```

### 6.2 Both webhook directions must be re-pointed

Per CLAUDE.md §6, the integration runs both ways, and **both ends currently hold localhost URLs**:

| Direction | Held where | Action |
|---|---|---|
| Vicero → n8n | `webhook_url` **stored per bound tool in the database**, inside the `tools.config` JSONB (`app/tools/n8n_tool.py:79`) — *not* a top-level column | ⚠️ Existing rows point at `http://localhost:5678/...`. Must be updated or re-bound. |
| n8n → Vicero | `callback_url` built at call time from `settings.api_base_url` | Fixed by setting `API_BASE_URL` (§3.1). |

⚠️ The stored `webhook_url` is the one that bites. CLAUDE.md's 2026-07-31 entry notes the runtime
uses the stored `webhook_url`, so bound tools keep working after other changes — which also means
they keep pointing at localhost after the move. Every existing binding needs re-binding, or a SQL
update, against the new n8n host.

⚠️ **Also still open from 2026-07-31: the n8n API key 403s on all tag endpoints**, so workflow
tagging never ran, and per ADR-042 (deny-by-default) untagged workflows are invisible to every org.
Mint a key with tag scopes and run `scripts/tag-n8n-workflows.mjs` on the new instance, or
automations will be invisible in the dashboard even once the URLs are right.

---

## 7. Where the data actually lives

All state is in **Docker named volumes** on the VPS's boot disk. Nothing is on a managed service.

| Volume | Contains | Backed up? |
|---|---|---|
| `pgdata` | Postgres 16 + pgvector: all tenants, agents, conversations, contacts, **and the `chunks.embedding` vectors** | ✅ nightly `pg_dump` |
| `uploads` | Uploaded PDFs/docs **and** the persisted `.docling.json` beside each | ❌ **no** |
| `redisdata` | Cache + Celery broker (AOF persisted) | ❌ (acceptable — rebuildable) |
| `ollamadata` | `nomic-embed-text` weights | ❌ (acceptable — re-pullable) |
| `caddydata` | Let's Encrypt certificates | ❌ (acceptable — re-issuable) |
| `backups` | Nightly `pg_dump` output, `BACKUP_RETENTION_DAYS` rotation | — |

⚠️ **The vectors are in Postgres, not a separate store.** `chunks.embedding` is `vector(768)`.
There is no external vector database to migrate — restoring `pgdata` restores the knowledge base's
embeddings with it. That is the good news.

⚠️ **Two real backup gaps, both already recorded in docs/15 §8.3:**

1. `infra/scripts/backup.sh` runs `pg_dump` only. The `uploads` volume is not covered. Restore the
   database alone and every `documents` row points at a file that no longer exists — the KB looks
   intact in the dashboard and every citation 404s.
2. Backups are written to the `backups` volume **on the same disk as the database.** A boot-volume
   failure or an accidental `docker volume prune` takes both. Free-tier Oracle gives you 200 GB of
   block storage and no offsite anything.

**Minimum viable fix before a paying client:**

```bash
# Add uploads to the nightly job, and get both off the box.
docker run --rm -v vicero-prod_uploads:/data -v /opt/backups:/out alpine \
  tar czf /out/uploads_$(date -u +%Y%m%dT%H%M%SZ).tar.gz -C /data .
# then rclone/scp /opt/backups to anywhere that is not this VPS
```

---

## 8. ⚠️ Embeddings: you cannot move them off Ollama

**Correcting a plausible assumption:** Vicero's LLM stack is Groq-first and free-tier friendly, so
the natural instinct is "use Groq for embeddings too and drop Ollama from the box." **That does not
work.** From `apps/api/app/llm/registry.py:181-184`:

```python
if provider in ("ollama", "openai", "gemini"):
    # All non-fake providers currently route through Ollama's local endpoint (free-first).
    return OllamaEmbeddingProvider(model or "nomic-embed-text", dim=dim, transport=transport)
```

and `app/llm/embeddings.py:13-16` says it in as many words: *"`openai` and `gemini` are in here
because there is no adapter for either — they are accepted and then routed to the local Ollama
endpoint anyway."*

So `EMBEDDING_PROVIDER=openai` changes **nothing** except the label. There is a startup warning for
exactly this (added in the PROD-2 work) because there is otherwise no symptom.

Consequences for the Oracle box:

- **Ollama must run on the VPS.** Budget ~4 GB ceiling (already the default `OLLAMA_MEM_LIMIT`).
- 🔶 **ARM CPU-only inference for `nomic-embed-text`** — a 137M-parameter embedder, so this should
  be fine on 4 Ampere cores, but it is **unmeasured on ARM**. Measure query-embed latency on the
  first deploy: `retrieval.search()` embeds the visitor's query **inline on every RAG turn**, so
  this sits directly on the p50 first-token path (NFR-1, 417 ms). This is why the compose file
  deliberately sets no CPU limit on `ollama`.
- Changing to a model with a different dimension is **a migration, not an env var** —
  `chunks.embedding` is `vector(768)`.

If ARM embedding latency turns out to be unacceptable, the fix is to **write a real OpenAI or Gemini
embedding adapter behind `build_embedding_provider`** (the factory is already the right seam), plus
a migration if the dimension differs. It is not a config change.

---

## 9. 🔶 ARM (aarch64) compatibility

Every image in the prod compose, and whether a multi-arch `linux/arm64` build is expected:

| Image | Expectation |
|---|---|
| `pgvector/pgvector:pg16` | 🔶 verify — this is the one to check first |
| `redis:7` | 🔶 official, multi-arch expected |
| `caddy:2` | 🔶 official, multi-arch expected |
| `ollama/ollama` | 🔶 ARM is a first-class target for Ollama |
| `postgres:16` (backup service) | 🔶 official, multi-arch expected |
| `node:20-bookworm-slim` (web) | 🔶 official, multi-arch expected |
| `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` (api) | 🔶 verify |
| `n8nio/n8n` | 🔶 verify |

**Verify before the real deploy, not during it** — one command, five minutes:

```bash
for img in pgvector/pgvector:pg16 redis:7 caddy:2 ollama/ollama postgres:16 \
           node:20-bookworm-slim ghcr.io/astral-sh/uv:python3.11-bookworm-slim n8nio/n8n; do
  echo -n "$img: "; docker manifest inspect "$img" 2>/dev/null \
    | grep -c '"architecture": "arm64"' || echo "MANIFEST FAILED"
done
```

⚠️ Beyond base images, the risk is **Python wheels**. `apps/api` pins `numpy<2.5` and pulls
`tiktoken`, `libphonenumber`, `trafilatura`, `pyyaml`. Most publish `manylinux_aarch64` wheels;
anything that does not will be compiled from source on first build — slow, and it needs build
tooling present. If `docker compose build api` fails on a missing wheel, that is this, and
`build-essential` in the Dockerfile is the usual fix.

`docling` is not a concern: `DOCLING_ENABLED` is off (docs/14 K1-5) and `docling-serve` is not in
the production compose.

---

## 10. Auth: what changes

Mostly nothing — but three things are worth confirming rather than assuming.

**Refresh-token cookie.** `apps/web/src/app/api/auth/_bff.ts:19-21`:

```ts
httpOnly: true,
secure: process.env.NODE_ENV === "production",
sameSite: "lax",
```

`NODE_ENV=production` is set in the prod compose's `web` service, so `secure: true` applies and the
cookie requires HTTPS — correct, and it means **the dashboard cannot work over plain HTTP.** If you
test on the raw IP before DNS/TLS is up, login will appear to succeed and then immediately bounce.
That is not the AuthGate bug from 2026-07-31; it is the cookie being refused.

**`sameSite: "lax"` with a cross-subdomain BFF.** Dashboard on `app.` and API on `api.` is fine here
because the cookie is set by the **Next.js BFF on `app.`** and only ever read by it — the browser
never sends it to `api.`. 🔶 Confirm on first deploy anyway; it is one login attempt.

**OAuth redirect URIs.** Google and GitHub OAuth apps have registered callback URLs pointing at
localhost. Update them in each provider's console to `https://app.yourdomain.com/...`, or social
login fails with a provider-side mismatch error that never reaches your logs.

**`SECRET_KEY`.** It signs JWTs **and encrypts provider API keys at rest** (CLAUDE.md §8). Changing
it after clients exist invalidates every session *and* makes every stored credential undecryptable.
Set it once, at first deploy, and put it in your password manager.

---

## 11. Knowledge base uploads: the path that was already broken in production

⚠️ **Read this even though it is fixed** — the fix is deployment-shaped, so a hand-rolled compose
file reintroduces it.

docs/15 PROD-1 (fixed 2026-08-17, ADR-068): `api` and `worker` are separate containers with separate
filesystems. The api writes the uploaded file; the worker reads it back to ingest it. Without a
**shared** volume the worker gets `Stored file is missing` and the document lands `failed`. It was
invisible in dev (one host directory) and invisible to smoke tests (URL and pasted-text ingest never
touch the filesystem).

The fix in `docker-compose.prod.yml` is `uploads:/app/var/uploads` on **both** `api` and `worker`,
plus `UPLOAD_DIR: /app/var/uploads` in `x-api-env`. Do not "simplify" this.

⚠️ There is a second, subtler half: `/app/var/uploads` is created in the same `RUN` as the `useradd`
in `apps/api/Dockerfile`. Docker seeds an empty named volume from the image's directory *including
ownership*, but creates a **root-owned** mountpoint if the path is absent from the image — and the
api runs as uid 10001. Get the ordering wrong and you swap "file not found" for "permission denied
at the first client upload." A test pins the ordering.

Also relevant on a VPS:

- `MAX_PDF_PAGES` defaults to **800**. Real client PDFs can exceed this; the error names both the
  count and the limit, so it is actionable.
- 🔶 **Caddy's default request body limit.** Caddy 2 does not impose a small default, but the
  practical upload ceiling on the deployed stack is untested. Try a 50 MB PDF on day one; if it
  fails at the proxy, `request_body { max_size 100MB }` in the API's Caddyfile block is the fix.
- Ingest is a Celery task on `worker` (concurrency 4, 3 GB ceiling). A large batch on 4 ARM cores
  competing with inline query embedding is the CPU contention docs/15 §3.4 is about. 🔶 Unmeasured.

---

## 12. Migrating existing local data

If the local Postgres has real content worth keeping (the `aurozenai` client agent, its KB, its
conversations):

```bash
# 1. On the laptop — dump
docker compose exec -T postgres pg_dump -U vicero --no-owner --no-privileges vicero \
  | gzip -9 > vicero_local.sql.gz

# 2. Copy the UPLOADS TOO — the dump does not contain them.
#    ⚠️ Locally these are NOT in a Docker volume. Per CLAUDE.md §12 the API runs natively from
#    `apps/api`, and `upload_dir` defaults to the relative `./var/uploads` — so the real files are
#    at `apps/api/var/uploads` (303 files at time of writing). A second, near-empty
#    `var/uploads` exists at the repo root from runs started from the wrong directory.
#    CHECK BOTH before assuming which one is live:
#      ls apps/api/var/uploads | wc -l ;  ls var/uploads | wc -l
tar czf uploads_local.tar.gz -C apps/api/var/uploads .

# 3. Ship both to the VPS
scp -i oracle.key vicero_local.sql.gz uploads_local.tar.gz ubuntu@<VPS_IP>:/tmp/

# 4. On the VPS — restore AFTER `migrate` has run once
gunzip -c /tmp/vicero_local.sql.gz \
  | docker compose -f docker-compose.prod.yml exec -T postgres psql -U vicero vicero
docker run --rm -v vicero-prod_uploads:/data -v /tmp:/in alpine \
  tar xzf /in/uploads_local.tar.gz -C /data
```

⚠️ **Then fix the URLs that are stored as data, not config.** A restore carries localhost into
production in at least these places:

- `tools` rows: n8n `webhook_url` → `http://localhost:5678/...` (§6.2)
- `channels` rows: any webhook URL registered while `API_BASE_URL` was localhost
- Provider credentials encrypted under the **old** `SECRET_KEY` — if you generated a new one (you
  should have), these are undecryptable and must be re-entered per provider in Settings

Audit with:

```sql
-- webhook_url lives inside the `config` JSONB, not as a column (§6.2)
SELECT id, name, config->>'webhook_url' AS webhook_url
FROM tools
WHERE config->>'webhook_url' LIKE '%localhost%';
```

⚠️ **Do not `alembic upgrade head` against a database you did not create.** CLAUDE.md's 2026-08-17
note records exactly this hazard on the dev machine, where 5432 belonged to another project.

---

## 13. Deploy sequence

```bash
# On the VPS
git clone <repo> /opt/vicero && cd /opt/vicero
cp .env.example .env && nano .env          # §3.1 — every localhost value
cp .env infra/.env                          # interpolation vars (§3) — or use --env-file

cd infra
docker compose --env-file ../.env -f docker-compose.prod.yml config   # fails loudly if a var is missing
docker compose --env-file ../.env -f docker-compose.prod.yml up -d --build
```

**First boot takes a while and one service looks broken while it is not:** `ollama` pulls
`nomic-embed-text` on start, and its healthcheck has `start_period: 300s` because it must download
weights before it can pass. `worker` depends on it with `service_started`, **not**
`service_healthy` — deliberately, so a slow model download does not hold back webhooks, email and
campaigns. A queued ingest waits in Redis. That is a delay, not an outage.

### Verification, in order

```bash
docker compose -f docker-compose.prod.yml ps          # all up; migrate Exited(0) is correct
curl -sS https://api.yourdomain.com/healthz            # 200
curl -sSI https://app.yourdomain.com/widget.js         # 200, JS content-type
docker compose -f docker-compose.prod.yml logs ollama | tail   # model pulled
docker compose -f docker-compose.prod.yml exec ollama ollama list   # nomic-embed-text present
```

Then, end-to-end, in this order — each depends on the last:

- [ ] Sign up / log in on `app.yourdomain.com` (proves TLS + secure cookie + CORS for the dashboard)
- [ ] Invite a user; **click the link in the real email** (proves `WEB_BASE_URL` + SMTP)
- [ ] Create an agent, upload a PDF, watch it reach `ready` (proves shared uploads volume + Ollama)
- [ ] Ask the agent a question that needs the PDF; confirm citations (proves the whole RAG path)
- [ ] Embed the widget on a **real external domain** (proves §4 — expect this to fail first)
- [ ] Bind an n8n tool and fire it (proves §6, both directions)
- [ ] Connect one channel and send a real message (proves `API_BASE_URL`)
- [ ] `docker compose exec backup sh /scripts/backup.sh` and confirm a file lands

### Rollback

The whole stack is `docker compose down` plus a `pg_restore`. Keep the previous image tags. The one
irreversible action is an Alembic migration — `migrate` is one-shot and runs before `api` starts, so
take a `pg_dump` **before** deploying a version with a new migration.

---

## 14. Claude Code on the VPS

Two workable shapes:

| Shape | How | Trade-off |
|---|---|---|
| **Claude Code installed on the VPS** | SSH in once yourself, install it, run sessions on the box | Full shell, no SSH round-trip per command. Simplest. Needs its own auth on the box. |
| **Claude Code on your PC, SSH to the VPS** | Give it the key + host; it runs `ssh ubuntu@vps "..."` | Everything stays on your machine. Slower per command, and multi-step debugging is clumsy. |

For a solo operator, install on the box.

⚠️ **This Cowork session cannot do either** — it has no SSH tool and no access to your Oracle
account. Provisioning and the first SSH are yours; after that Claude Code takes over.

⚠️ **Guardrails, given it is now a live system with client data:**

- Never let an agent run `docker volume prune`, `docker compose down -v`, or `alembic downgrade`
  without an explicit human confirmation. Any of those destroys client data.
- `.env` on the VPS holds every provider key, `SECRET_KEY`, and the DB password. It is gitignored;
  keep it that way.
- Take a backup before any session that will touch migrations.

---

## 15. What this doc does not solve

Recorded honestly, because a migration runbook that reads as "and then you are done" is the failure
mode:

- **§4 CORS needs a decision from you.** Option A is a manual step per client, forever, until C is
  built. C is checklist 12.2, still open.
- **Uploads are still not backed up by `backup.sh`** (docs/15 §8.3). §7 gives a manual command; it
  is not automated and not offsite.
- **Everything is on one box with no failover.** Oracle can and does reclaim idle Always Free
  instances. Read their idle-reclamation policy before putting a paying client on one.
- **Nothing here is measured on ARM.** Every 🔶 in §9 and §11 is a prediction. The first deploy is
  the measurement.
- **n8n tag scopes are still blocked** (CLAUDE.md 2026-07-31). Automations stay invisible per
  ADR-042 until a key with tag scopes exists and the tagging script runs.
- **The Groq free tier is exhaustible.** CLAUDE.md's 2026-08-10 entry records a 61-probe checklist
  run hitting the daily cap — at which point the L3 safeguard model 429s and distress grading
  silently stops while dashboards look normal. One shared platform key currently serves every
  client and both guard models. This becomes a real availability problem with more than one client
  on the box.

---

## Confidence

**Medium-high on the mechanics, medium on the box.**

High confidence in §3–§8 and §11–§13: every claim there is cited to a file and line in this repo and
was read directly, and the production compose is mature enough that the migration really is mostly
configuration.

Lower confidence in §1 and §9: Oracle's free-ARM capacity availability, ARM image and wheel
compatibility, and embedding latency on Ampere cores are all unverified here. The assumption that a
137M-parameter embedder is comfortable on 4 ARM cores is reasonable and untested.

The assumption most likely to be wrong: that 24 GB and **4** ARM cores comfortably run Postgres +
Redis + API (2 uvicorn workers) + 4 Celery workers + Next.js + Caddy + Ollama + n8n **together under
real load**. RAM is fine and is not the question. docs/15 §4 Option 1 assumed **8** vCPU, docs/15
§3.4 argues CPU contention is the binding constraint, this box has half those cores, and n8n is an
addition docs/15 §3 never costed at all. If something on this deployment disappoints, that is where
to look first.

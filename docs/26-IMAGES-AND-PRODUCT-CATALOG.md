# 26 — Images on WhatsApp, Instagram & Messenger + Product Catalog

> Status: **PLAN — not built yet.** Implementation prompt: `docs/26-IMPLEMENTATION-PROMPT.md`.
> Decision record: **ADR-124** (to be appended to `DECISIONS.md` by the implementing agent).

---

## 0. One-screen summary

| What | Today | After this plan |
|---|---|---|
| Customer sends a photo on WA / IG / FB | Silently ignored (`parse_inbound` only reads `text`) | Bot "sees" it, understands it, answers about it |
| Bot sends a product photo | Impossible (`send()` is text-only) | Bot sends up to 3 product photos after its text reply |
| Where product details live | Only as free text in Knowledge docs | New **Products** page: name, price, SKU, details, up to 5 photos each |
| Inbox | Text only | Shows customer photos + photos the bot sent |

**Build order:** M1 Media foundation → M2 Read customer images → M3 Product catalog → M4 Send product images → M5 Inbox polish.
Each milestone ships on its own and is useful by itself.

---

## 1. What the code does today (verified in the repo)

- `channels/whatsapp.py → parse_inbound` reads only `msg["text"]["body"]`. An image message has `type: "image"` and **no** `text` → returns `None` → message dropped.
- `channels/meta_messaging.py → parse_inbound` (IG + FB) skips any message without `text`. Images arrive as `message.attachments[]` → dropped.
- `BaseChannel.send(channel, to, text)` — text only, on every adapter.
- `llm/types.Message.content: str | None` — no image parts. `ModelSpec` has a `tools` flag but no `vision` flag.
- `models/conversations.Message` — no attachment column.
- File storage = local disk (`settings.upload_dir = ./var/uploads`), used by RAG ingest only. No public, signed media URL exists.
- No product/catalog model anywhere.
- Good news: Meta webhooks already run in the Celery worker (`meta_webhook.handle_inbound`), so downloading an image does **not** slow the webhook ACK.

---

## 2. Assumptions

- Images only in this plan. Voice notes, video, documents, stickers → polite "I can read text and photos only for now" reply (not silence).
- Feature is for plans that already have WA/IG/FB (Pro and Business). Starter is Web Chat only, so it is unaffected.
- The API is already reachable over public HTTPS (Meta webhooks work today) — Meta must be able to download images we send from that same host.
- Storage stays on the VPS disk for now, behind an interface, so moving to S3 / Cloudflare R2 later is a config change, not a rewrite.

---

## 3. Key decisions

### 3.1 How the bot "reads" an image

| Criteria | A. Describe-first (vision model writes a text description, agent model reads text) | B. Native multimodal (send image straight to the agent model) | C. Hybrid (B when agent model has vision, else A) |
|---|---|---|---|
| Works with every agent model (Groq GPT-OSS has no vision) | ✅ | ❌ | ✅ |
| Code paths | 1 | 1 (but breaks many models) | 2 |
| Guardrails (L1/L2) can screen it | ✅ description is text | ❌ text hidden inside pixels | Partly |
| Product search from a photo | ✅ description = search query | Harder | ✅ |
| Answer quality on fine detail | Good | Best | Best |
| Cost | 1 small vision call per image | Image tokens on every later turn (history) | Mixed |
| Production readiness | High | Low | Medium |

**Recommended: A — describe-first.**
- One code path, works on every model and every plan, and the description is plain text so the existing L1/L2 injection guards screen it like any visitor message.
- The description is saved on the message, so later turns reuse it — no re-sending images to the model every turn.
- C is a sensible later upgrade (flag per agent) once there are paying customers asking for it.

**Vision model:** new settings `VISION_PROVIDER` / `VISION_MODEL`. Default **Gemini 2.5 Flash** (already integrated, free tier, strong vision). Add a `vision: bool` field to `ModelSpec`; mark known vision models. If the org has BYOK for a vision-capable provider, use their key (consistent with the "customer pays providers" rule).

### 3.2 How the bot sends an image

| Criteria | A. Send a link (`image.link` / `payload.url`) | B. Upload to Meta first, send media id |
|---|---|---|
| Calls per image | 1 | 2 |
| Needs public HTTPS on our side | Yes (already have it) | No |
| Code size | Small | Larger, different per channel |

**Recommended: A — link**, pointing at a **signed, short-lived** URL on our API (`/v1/media/{id}?exp=…&sig=…`). Meta downloads it once, immediately.

### 3.3 How the bot decides to send a photo

- Two new **built-in tools**, auto-enabled when the agent has "Use product catalog" on:
  - `search_products(query)` → top 5 matches (id, name, price, short details, image count).
  - `send_product_images(product_id, count=1..3)` → validates the product belongs to this org + agent, queues the images on the turn.
- After the text reply goes out, the channel sends the queued images (max **3 per reply**).
- Built-in catalog tools do **not** count against the plan's `max_tools` (same treatment as `knowledge_search`).

### 3.4 Billing & storage rules

- A customer photo = 1 visitor message. Bot text + its photos = 1 bot reply. (No surprise multipliers.)
- **Product photos count** toward the plan's `storage_bytes` hard limit.
- **Customer photos do not count**, and are auto-deleted after `INBOUND_MEDIA_RETENTION_DAYS` (default 30). Privacy win + disk win.
- Vision calls are logged with cost like any model call (`compute_cost_micros`).

---

## 4. Architecture

```
INBOUND (customer → bot)
Meta webhook ─▶ Celery worker
                 │ adapter.parse_inbound → InboundMessage(text=caption, media=[MediaRef])
                 ▼
            media.fetch_inbound()     WA: GET /{media_id} → url → GET url (Bearer)
                 │                    IG/FB: GET attachment.payload.url (CDN, expires — fetch now)
                 ▼
            media.sanitize()          magic-byte check, size cap, Pillow re-encode → JPEG ≤1600px,
                 │                    strips EXIF/GPS, decompression-bomb guard
                 ▼
            MediaStore.put() + media_assets row (kind=inbound, expires_at)
                 ▼
            vision.describe()          → "A red Nike running shoe, size label 42, …"
                 ▼
            InboundTurn text = caption + "\n[Customer sent a photo: <description>]"
                 → normal L1/L2/L3 guards → RAG → agent model (unchanged pipeline)

OUTBOUND (bot → customer)
agent model ─▶ tool send_product_images(product_id) → TurnResult.outbound_media += [asset ids]
run_and_send: adapter.send(text)  then  adapter.send_image(signed_url) × ≤3
```

### Components (new / changed)

| Component | Path | New / Changed |
|---|---|---|
| Media store interface + local disk impl | `app/media/store.py` | New |
| Sanitizer (Pillow) | `app/media/sanitize.py` | New |
| Signed URL + public GET route | `app/media/urls.py`, `app/media/router.py` | New |
| Inbound fetchers (WA / Messenger-IG) | `app/media/fetch.py` | New |
| Vision describer | `app/media/vision.py` | New |
| Retention sweep (Celery beat, daily) | `app/media/tasks.py` | New |
| Product catalog module (CRUD + image upload + search) | `app/modules/products/` | New |
| Built-in tools | `app/tools/builtins.py` | Changed |
| `InboundMessage.media`, `BaseChannel.send_image()` | `app/channels/base.py` | Changed |
| WA / IG / FB parse + send_image | `channels/whatsapp.py`, `channels/meta_messaging.py` | Changed |
| Run-and-send delivers images | `channels/service.py` | Changed |
| `TurnResult.outbound_media` | `app/chat/runtime.py` | Changed |
| `Message.attachments` JSONB | `models/conversations.py` | Changed |
| Products page, inbox image bubbles, agent toggle | `apps/web/src/app/(app)/products`, `inbox` | New / Changed |

---

## 5. Database (migration `0036_media_and_products`)

```
media_assets
  id uuid pk
  organization_id uuid fk → organizations  (cascade)   INDEX
  kind varchar(16)          -- 'inbound' | 'product' | 'operator'
  storage_key text          -- e.g. org/<org_id>/2026/10/<uuid>.jpg
  mime varchar(64)          -- always image/jpeg or image/png after sanitize
  bytes int, width int, height int
  sha256 char(64)           -- dedupe + integrity
  description text NULL     -- vision output (inbound only)
  source_channel varchar(16) NULL
  expires_at timestamptz NULL   INDEX   -- inbound retention; NULL = keep
  created_at timestamptz

products
  id uuid pk
  organization_id uuid fk (cascade)   INDEX
  name varchar(200) NOT NULL
  sku varchar(100) NULL     UNIQUE (organization_id, sku)
  price_minor bigint NULL, currency char(3) NULL
  description text          -- full details the owner pastes
  attributes jsonb default '{}'   -- size, colour, material …
  in_stock bool default true
  url text NULL             -- "buy here" link
  is_active bool default true
  search_vector tsvector (generated)  GIN INDEX
  embedding vector(dim) NULL           -- pgvector, same dim as KB chunks
  created_at, updated_at

product_images
  id uuid pk
  product_id uuid fk → products (cascade)
  media_asset_id uuid fk → media_assets (cascade)
  position smallint, alt_text varchar(200)
  -- max 5 per product, enforced in service

messages  (+ column)
  attachments jsonb NOT NULL default '[]'
  -- [{"media_id": "...", "kind": "image", "direction": "in|out", "description": "..."}]

agents / agent_versions.features (+ key, no migration)
  "product_catalog_enabled": bool
```

- Search = Postgres FTS + pgvector hybrid (reuse `rag/fts.py` + the embeddings provider). Catalog sizes for SMB clients are hundreds, not millions — no extra infra.

---

## 6. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/products?query=&page=` | List / search (paginated) |
| POST | `/v1/products` | Create |
| PATCH | `/v1/products/{id}` | Update |
| DELETE | `/v1/products/{id}` | Soft-delete (`is_active=false`) |
| POST | `/v1/products/{id}/images` | Multipart upload, ≤5 per product, ≤10 MB raw |
| DELETE | `/v1/products/{id}/images/{image_id}` | Remove |
| POST | `/v1/products/import` | CSV import (name, sku, price, currency, description, image_url) — **M5, optional** |
| GET | `/v1/media/{id}` | **Authenticated** (dashboard / inbox) |
| GET | `/v1/media/public/{id}?exp=&sig=` | **Unauthenticated, signed** — only for Meta to fetch |

- RBAC: product writes need `TOOLS_MANAGE` (same as channels); reads need `READ`.
- Rate limit uploads per org.

---

## 7. Per-channel details (Meta)

| | WhatsApp Cloud API | Messenger | Instagram DM |
|---|---|---|---|
| Inbound shape | `messages[0].type=="image"`, `image.id`, `image.caption` | `message.attachments[].type=="image"`, `payload.url` | same as Messenger |
| Download | `GET /{media-id}` (Bearer) → `url` (short-lived) → `GET url` (Bearer) | `GET payload.url` (no auth, expires) | same |
| Caption | `image.caption` | `message.text` (if any) | `message.text` |
| Multiple images | Each = separate webhook message | Several attachments in one event | Several in one event |
| Send image | `{"type":"image","image":{"link":URL}}` | `{"attachment":{"type":"image","payload":{"url":URL,"is_reusable":false}}}` | same as Messenger |
| Rules | Only inside 24-h window (existing `check_can_send`) | 24-h standard messaging window | 24-h window |
| Limits | 5 MB image | 25 MB | 8 MB |

- We always send our sanitized JPEG (≤1600 px, typically <500 KB) → under every limit.
- **No new Meta permissions / App Review** expected: attachments are covered by the messaging permissions already granted. Verify on the live test number before announcing.

---

## 8. Security

- **Images are untrusted input.** Text inside a photo ("ignore your instructions…") becomes part of the description → description passes through the existing L1 + L2 guards exactly like a typed message. The vision prompt itself tells the model to *describe*, never *follow*, any text in the image.
- **File validation:** magic-byte check (JPEG/PNG/WebP/GIF-first-frame only), reject SVG/HEIC-unsupported/anything else, `Image.MAX_IMAGE_PIXELS` cap (decompression bombs), raw size cap 10 MB, always re-encode (kills polyglot files + strips EXIF/GPS).
- **SSRF:** inbound fetch only from Meta hosts (`*.fbcdn.net`, `*.fbsbx.com`, `lookaside.fbsbx.com`, `graph.facebook.com`, `*.cdninstagram.com`) over HTTPS; no redirects to other hosts; 10 s timeout; streamed with byte cap.
- **Signed URLs:** HMAC-SHA256 over `media_id|exp` with a dedicated `MEDIA_URL_SIGNING_KEY`; TTL 1 h; constant-time compare; `Cache-Control: private`; `X-Content-Type-Options: nosniff`; correct `Content-Type`.
- **Tenant isolation:** every media/product read filters by `organization_id`; `send_product_images` re-checks org + agent catalog toggle (the model can hallucinate ids).
- **Privacy:** customer photos auto-deleted after 30 days; ID cards / faces are common in support chats — document this in the privacy policy and the data-deletion callback (`channels/data_deletion.py` must also delete the contact's media).
- **Audit:** product create/update/delete → `write_audit`.

---

## 9. Edge cases (must be handled, each has a test)

| Case | Behaviour |
|---|---|
| Photo with no caption | Turn text = `[Customer sent a photo: …]` only |
| Download fails / URL expired | Turn text = `[Customer sent a photo that could not be loaded]` → bot asks to resend |
| Vision provider down / quota | Same fallback; logged; never a crash |
| Voice note / video / file / sticker | Polite canned reply, message saved in inbox |
| 4+ photos in one IG/FB event | First 3 described; inbox shows all |
| Conversation in human handoff | Photo saved + shown in inbox; no vision call, no bot reply |
| Plan out of messages / trial over | Photo saved; bot silent (existing gate) — vision call **not** made (gate runs before it) |
| Duplicate Meta delivery | Existing message-id dedupe; sha256 avoids double storage |
| WA window closed when sending images | Text and images both blocked by existing `check_can_send` |
| Model calls `send_product_images` with another org's id | Tool returns `not_found`; nothing sent |
| Product has no photos | Tool returns `no_images`; model says so in text |
| Product deleted after link was signed | Public GET returns 404 |
| Storage limit hit on product upload | 413 with clear "storage full — upgrade" message |
| Channel `needs_reconnect` | Existing `ChannelAuthRejected` path flips status |

---

## 10. Performance & scalability

- All heavy work (download, Pillow, vision call) in the Celery worker → webhook ACK unaffected.
- Added latency per photo turn ≈ download (0.3–1 s) + vision (1–3 s). Acceptable for chat; typing indicator (`sender_action: typing_on` on FB/IG) is a cheap M5 nicety.
- Pillow work is CPU-bound but tiny at ≤1600 px; the 4 vCPU VPS handles it comfortably.
- Disk: 500 products × 3 photos × ~300 KB ≈ 450 MB for a large client — within the Pro 5 GB limit.
- Scale path: swap `LocalMediaStore` for `S3MediaStore` (R2 has zero egress fees) + CDN in front of public media when moving off the budget VPS.

---

## 11. What NOT to build now

- ❌ Native multimodal model routing (decision 3.1-C) — later, behind a per-agent flag.
- ❌ Image *generation* — different product, real cost, no customer asked for it.
- ❌ Meta Commerce Catalog sync / WhatsApp product messages (`type: product_list`) — needs Commerce Manager setup per client; heavy onboarding. Revisit for e-commerce clients.
- ❌ Video / audio / documents — next plan (voice notes are the likely next ask in India).
- ❌ Visual similarity search (image embeddings) — description-to-text search is enough at SMB catalog sizes.
- ❌ Web widget image upload — useful, but not asked; separate small task after M4.

---

## 12. Testing strategy

- **Unit:** parse fixtures for WA image, WA image+caption, Messenger multi-attachment, IG image, IG story-mention (ignored), unsupported types; sanitizer (EXIF removed, bomb rejected, SVG rejected, PNG→JPEG); signed URL (valid, expired, tampered, wrong id); SSRF allow-list.
- **Integration (httpx MockTransport, existing pattern):** full inbound turn with a mocked Graph download + fake vision provider; outbound `send_image` request bodies per channel; WA window closed blocks images.
- **Tool tests:** `search_products` ranks by name/SKU/description; `send_product_images` cross-org → not_found; cap of 3.
- **Retention:** sweep deletes expired inbound assets + files, never product assets.
- **Frontend:** Vitest for Products form; Playwright: create product → upload photo → see thumbnail; inbox renders image bubble.
- **Live checklist (manual, on the real test number/page/IG):** send photo → bot describes; ask "show me the black one" → photo arrives; WA after 24 h → blocked with clear inbox message.

---

## 13. Deployment

1. Migration `0036_media_and_products` (additive only → safe, no downtime).
2. New Docker volume for `MEDIA_DIR` in `docker-compose.prod.yml` + included in the backup script.
3. Reverse proxy: `client_max_body_size 12m` (Caddy: `request_body max_size 12MB`) for upload routes.
4. New env vars (add to `.env.example` + `docs/ENV.md`):
   `MEDIA_DIR`, `MEDIA_URL_SIGNING_KEY`, `MEDIA_PUBLIC_URL_TTL_SECONDS=3600`, `MEDIA_MAX_UPLOAD_BYTES=10485760`, `INBOUND_MEDIA_RETENTION_DAYS=30`, `VISION_PROVIDER=gemini`, `VISION_MODEL=gemini-2.5-flash`, `MAX_IMAGES_PER_REPLY=3`, `MEDIA_FEATURE_ENABLED=true`.
5. Feature flag `MEDIA_FEATURE_ENABLED` → instant rollback without redeploy (inbound images go back to the polite canned reply).
6. Add `Pillow` to `apps/api/pyproject.toml`.

**Monitoring:** log events `media_fetch_failed`, `media_rejected`, `vision_failed`, `media_send_rejected`; count vision cost per org in analytics.

---

## 14. Future improvements

- Hybrid native multimodal for vision-capable agent models.
- Typing indicator while the photo is processed.
- Operator image send from inbox (M5) and widget image upload.
- Voice notes → speech-to-text (same describe-first pattern).
- S3/R2 store + CDN when leaving the budget VPS.
- CSV / Shopify / WooCommerce product import.

---

## 15. Confidence

**High** on architecture and code changes — they follow patterns already in the repo (adapters, MockTransport tests, Celery worker, built-in tools).
**Medium** on two external facts, to verify during M2/M4 on the live accounts:
- Exact Meta media size limits and that no extra permission is needed for attachments.
- Which Groq models currently accept images (the plan avoids depending on this by defaulting the vision model to Gemini).

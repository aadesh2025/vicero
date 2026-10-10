# docs/26 — Meta one-click connect (WhatsApp, Messenger, Instagram)

> Customers connect their own WhatsApp number, Facebook Page and Instagram account from the agent's **Channels** tab
> with a login button, never pasting a token. Decision record: **ADR-113**. API reference: `docs/07-INTEGRATIONS.md`.
> This file is the **setup checklist for the Meta dashboard** and a troubleshooting guide. It could not be verified against
> a live Meta app when it was written: menu names in Meta's dashboard change, so treat the labels as a guide.

## 1. How it works (one minute)

```
WhatsApp   browser: FB.login(config_id) ─ popup ─▶ code + (waba_id, phone_number_id)
           API /v1/channels/meta/whatsapp/connect : exchange code ▶ debug_token ▶ list the WABA's numbers
                (the number must be in it) ▶ subscribed_apps ▶ register (if not CONNECTED) ▶ save channel
Messenger  browser: FB.login(scopes) ─▶ short-lived user token
Instagram  API /pages : long-lived token ▶ /me/accounts ▶ keep Page tokens server-side (10 min, single use)
           browser: pick Pages ▶ API /connect : subscribed_apps(messages,messaging_postbacks) ▶ save channel(s)
Inbound    Meta ─▶ POST /api/meta/webhook (one URL for the whole app) ─▶ verify signature ─▶ route by
           (type, external_id) ─▶ Celery task ─▶ existing adapter + bot turn + reply
```

Tokens are encrypted in `channels.config` and are never returned by any endpoint. A phone number / Page / Instagram account can
belong to one channel; a second workspace trying to connect it gets a `409`.

## 2. Server configuration (names only; values live in `/opt/vicero/.env`)

| Variable | Notes |
|---|---|
| `META_APP_ID` | Public app id. |
| `META_APP_SECRET` | Already set in production (used by the data-deletion callback). |
| `META_VERIFY_TOKEN` | You choose it; type the same value into the Meta webhook form. |
| `META_EMBEDDED_SIGNUP_CONFIG_ID` | WhatsApp only. Without it Messenger/Instagram still work. |
| `META_GRAPH_VERSION` | Default `v23.0`. Change it here only; check Meta's changelog for the currently supported version. |
| `META_APP_LIVE` | `true` after Meta approves the app (removes the "only testers" notice). |

Set each with `ssh -t vicero /opt/vicero/set-env.sh NAME` (hidden prompt; recreates `api worker beat`). Do **not** paste values
into chat or commits. Migration `0033_meta_one_click_connect` is applied by `deploy.sh`.

Nothing is baked into the web image: the browser reads the app id and configuration id from `GET /v1/channels/meta/config`, so no
GitHub repo variable or rebuild is needed.

## 3. Meta dashboard checklist (app "VICERO", App ID `2121978665422309`)

**3.1 Basic settings**
- [ ] Privacy Policy URL `https://viceroai.duckdns.org/privacy`, Terms of Service URL `https://viceroai.duckdns.org/terms`
- [ ] User data deletion → *Data deletion request URL* `https://viceroai-api.duckdns.org/api/meta/data-deletion`
- [ ] Deauthorize callback URL `https://viceroai-api.duckdns.org/api/meta/deauthorize`
- [ ] App domains: `viceroai.duckdns.org`; category and app icon set

**3.2 Facebook Login (for Messenger + Instagram)**
- [ ] Product added; *Login with the JavaScript SDK* enabled
- [ ] *Allowed domains for the JavaScript SDK*: `https://viceroai.duckdns.org`

**3.3 WhatsApp Embedded Signup**
- [ ] WhatsApp product added; *Facebook Login for Business* with a **configuration** created from the WhatsApp Embedded Signup
      template (permissions `whatsapp_business_management`, `whatsapp_business_messaging`); copy its id into
      `META_EMBEDDED_SIGNUP_CONFIG_ID`
- [ ] Same JavaScript-SDK allowed domain as above

**3.4 Webhooks** (one callback URL for the whole app)
- [ ] Callback URL `https://viceroai-api.duckdns.org/api/meta/webhook`, verify token = the value of `META_VERIFY_TOKEN`
- [ ] **WhatsApp Business Account** object → subscribe field `messages`
- [ ] **Page** object → fields `messages`, `messaging_postbacks`
- [ ] **Instagram** object → fields `messages`, `messaging_postbacks`
- [ ] "Verify and save" succeeds (it calls `GET /api/meta/webhook`); if not, see §5

Per-customer subscription of each Page / WABA to the app is done by Vicero at connect time (`subscribed_apps`); the three
checkboxes above only enable the *fields* for the app.

**3.5 Permissions** (App Review → Advanced Access, needed before non-tester customers can connect)

| Permission | Why |
|---|---|
| `whatsapp_business_management` | Embedded Signup, list numbers, subscribe the WABA |
| `whatsapp_business_messaging` | Send and receive WhatsApp messages |
| `pages_show_list` | List the Pages the customer manages |
| `pages_messaging` | Messenger send/receive |
| `pages_manage_metadata` | Subscribe the Page to the app |
| `instagram_basic` | Read the linked Instagram professional account |
| `instagram_manage_messages` | Instagram DMs |

`business_management` is **not** requested. Add it only if Meta review or a customer's Business Portfolio setup proves it is
required, and then add it to `PAGE_SCOPES` in `apps/web/src/lib/meta/facebook-sdk.ts`.

**3.6 Testing before approval**
- [ ] *App roles → Roles*: add every tester (Administrators/Developers/Testers) by their Facebook account; they must accept
- [ ] WhatsApp → API setup: use the Meta test number for first runs
- [ ] Instagram account is *Professional* and linked to the Facebook Page; the Page admin is a tester

## 4. Manual QA checklist (needs a live Meta app)

- [ ] `GET /v1/channels/meta/config` (signed in) → `enabled: true`, no secret in the body
- [ ] Dashboard "Verify and save" for the webhook succeeds; a wrong verify token is rejected
- [ ] Connect WhatsApp with a test number → channel shows *Connected*; a message to the number gets a bot reply in the inbox
- [ ] Reconnect the same number → same channel row, no duplicate
- [ ] Connect the same number from a second workspace → clear "already connected" message, first workspace unaffected
- [ ] Connect Messenger (one Page) → DM the Page → reply arrives
- [ ] Connect Instagram → DM the account → reply arrives; a Page with no Instagram link cannot be ticked
- [ ] Disconnect Instagram while Messenger stays connected → Messenger still answers (Page subscription kept)
- [ ] Disconnect Messenger → badge *Disconnected*, token gone from `channels.config`, number/Page free for another workspace
- [ ] Remove the app in Facebook settings (Deauthorize) → channels flip to *Needs reconnect* within seconds
- [ ] Revoke the token (change the Facebook password) → after the next daily check (or run `channels.meta_health`) the channel shows
      *Needs reconnect* once and a `channel.needs_reconnect` webhook event is delivered
- [ ] A *Needs reconnect* channel: operator reply in the inbox shows a clear error, nothing is sent
- [ ] The old manual flow and per-channel webhook URLs still work for an existing manual channel
- [ ] Meta's *Data deletion* and *Deauthorize* test buttons return 200

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Buttons are greyed out with "isn't set up on this server" | `META_APP_ID`, `META_APP_SECRET` or `META_VERIFY_TOKEN` is empty in the **api** container. Check without printing values: `docker exec vicero-prod-api-1 sh -c 'for v in META_APP_ID META_APP_SECRET META_VERIFY_TOKEN META_EMBEDDED_SIGNUP_CONFIG_ID; do [ -n "$(printenv $v)" ] && echo "$v set" || echo "$v EMPTY"; done'`. WhatsApp also needs `META_EMBEDDED_SIGNUP_CONFIG_ID`. |
| Webhook "Verify and save" fails | Verify token differs from `META_VERIFY_TOKEN`, or the API container was not recreated after `set-env.sh`. The GET answers `403` on any mismatch. |
| Messages never arrive | Webhook field not subscribed (§3.4); the Page/WABA is not subscribed (reconnect the channel); app in development mode and the sender is not a tester; the channel is *paused* or not *Connected*. Check the API log for `meta_webhook` lines. |
| Webhook returns 401 | `X-Hub-Signature-256` did not match: `META_APP_SECRET` in prod is not this app's secret. |
| "The Meta window didn't open" | Browser blocked the pop-up, or the Facebook script was blocked by an ad blocker. Allow pop-ups, retry. |
| "Meta didn't report which number you connected" | The Embedded Signup flow ended without finishing, or the JS-SDK allowed domain is missing in §3.2/§3.3. |
| "That phone number is not part of the WhatsApp Business Account" | The browser's ids did not match what Meta says the token can access. Retry the whole sign-up; this check is deliberate. |
| `meta.graph_error` with Meta's message | Surfaced verbatim (never a token). Typical: expired authorization code (retry), missing permission (App Review / tester role), number needs verification in WhatsApp Manager. |
| "already connected to another Vicero workspace" | The account is attached elsewhere. Disconnect it there, or contact support if it is yours. |
| Channel shows *Needs reconnect* | Token revoked/expired, password change, or the user removed the app. Press **Reconnect**. |
| Replies stop after ~24 h on WhatsApp | The 24-hour customer-service window (unchanged); use an approved template. |
| `GRAPH` errors after a version bump | Set `META_GRAPH_VERSION` to a version Meta currently supports; it is read at request time by the adapters and by the Facebook SDK (`FB.init`). |

## 6. Operations notes

- **Rate limit:** the shared webhook allows 1200 requests/min per source IP (all customers share Meta's IPs).
- **Dedupe:** Meta retries are dropped by message id for 24 h (Redis; in-memory fallback in dev).
- **No Content-Security-Policy** is set by Next.js or Caddy for the web app today, so the Facebook script and popup need no
  allow-list. If a CSP is added later it must allow `connect.facebook.net` and `*.facebook.com` for script, frame and connect.
- **Health task:** `channels.meta_health` runs daily from Celery beat; run it by hand with
  `docker exec vicero-prod-worker-1 celery -A app.worker.celery_app call channels.meta_health`.
- **Rollback:** the migration only adds columns/an index. Roll back by redeploying the previous images (`rollback.sh`); the
  extra columns are ignored by older code. Do not `alembic downgrade` in production.

## 7. First live test (you as an app tester)

Before you start: you are listed under **App roles** in the Meta app and have accepted the invitation; `META_APP_ID` and
`META_EMBEDDED_SIGNUP_CONFIG_ID` are set; the webhook is saved in the Meta dashboard (§3.4).

**Open two terminals and leave them running while you test** (names only are matched; nothing prints a secret):

```
ssh vicero "docker logs -f --since 1m vicero-prod-api-1 2>&1 | grep --line-buffered -E 'meta|webhook|channel'"
ssh vicero "docker logs -f --since 1m vicero-prod-worker-1 2>&1 | grep --line-buffered -E 'meta_inbound|channel|error|Traceback'"
```

Healthy signs: `POST /api/meta/webhook ... 200` in the api log, then `meta_inbound_task_start` in the worker log within a second.
Warnings worth knowing: `meta_graph_failed` (Meta refused a call; the status/code is logged, never a token),
`meta_inbound_ignored_inactive` (channel is not *Connected*), `channel_delivery_failed` (the reply could not be sent).
Do not paste raw api logs anywhere: uvicorn's access line prints the webhook handshake query string, which includes the verify token.

**A. WhatsApp (your own test number)**
1. Dashboard -> your agent -> **Channels** -> **Connect WhatsApp**. The Meta window opens (allow pop-ups).
2. Finish Embedded Signup with your test number. Expect "WhatsApp connected" and a *Connected* badge with the verified name.
3. From **another phone**, WhatsApp-message that number: "hello". Expect a bot reply within a few seconds and the conversation in the inbox.

**B. Messenger + Instagram (a Page you admin, with a linked Instagram professional account)**
1. **Connect Messenger** -> Facebook login (accept every permission) -> tick the Page -> **Connect selected**.
2. DM the Page from another Facebook account: expect a reply and the conversation in the inbox.
3. **Connect Instagram** -> pick the same Page (its Instagram shows next to it) -> connect. DM the Instagram account from another
   account: expect a reply.

**C. Disconnect and clean-up**
1. **Disconnect** the Instagram channel (confirm): Messenger must keep answering (the Page subscription is kept).
2. **Disconnect** Messenger and WhatsApp. Expect badge *Disconnected* and no more replies.
3. Check the token is gone and the account is free (counts and names only, no values):
   `ssh vicero "docker exec vicero-prod-postgres-1 psql -U vicero -d vicero -c \"select type, status, enabled, external_id is not null as has_id, config ? 'access_token' or config ? 'page_access_token' as has_token from channels\""`
   Expect `status=disconnected`, `has_id=f`, `has_token=f` for each.
4. Connect the same number/Page again: it must work (the account was freed).

**D. Quick failure checks (optional)**
- Connect the same Page from a second workspace: clear "already connected" message.
- In Facebook settings -> Business integrations, remove the app: within seconds the channels show *Needs reconnect*.

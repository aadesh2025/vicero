# 23 — Self-Serve Payment (Stripe) — **DEFERRED**

> **Status: deferred spec. DO NOT EXECUTE.**
>
> This phase is blocked on a **business decision and a payment account that do not exist yet**
> (§1). It is written down now so the work is not re-derived later, and so `docs/22` can be built
> in a way that this drops into without rework.
>
> **Build `docs/22-BILLING-PAID-PLANS.md` first.** Plans are granted by admin today. That system
> is not a throwaway: §3 (pricing table), §4 (`plans.py`), §6 (billing period) and §11
> (enforcement matrix) of docs/22 are **shared** with this phase and are not rewritten here.
> This file only adds *"a customer can pay by card and get the plan without you"*.
>
> Do not start any phase in this file unless asked for it **by name**, and not before the
> blocker in §1 is resolved.

---

## 1. The blocker — why this is deferred

Checked against Stripe's own documentation on 2026-09-28:

| Stripe rule (India) | Consequence |
|---|---|
| *"Stripe is available by invite only in India"* — businesses cannot sign up through the website | No self-serve Stripe account, **not even test mode** |
| *"The Stripe account must be a registered Indian business (sole proprietorship, limited liability partnership, or company). The Stripe business can't be an individual."* | A personal bank account with no registered business is **not eligible** |
| Payouts are made **in INR** | Fine, but plan for FX on USD pricing |
| Services exports need an RBI **transaction purpose code** (IEC optional for services unless AMEX) | For this product: **P0807 — off-site software exports** |

Sources: <https://docs.stripe.com/india-accept-international-payments> ·
<https://support.stripe.com/questions/2026-document-collection-for-india-accounts>

### 1.1 Unblocking paths

| Path | Entity needed | Rough time | Notes |
|---|---|---|---|
| **Merchant of Record** (Paddle / Lemon Squeezy / Dodo) | Usually none | Days | MoR is the legal seller; handles US sales tax; pays out to a normal Indian bank account. Higher fee (~5%). **Most likely first move.** |
| Indian sole proprietorship (Udyam/GST + current account) → request a Stripe invite | Yes (India) | Weeks | Still gated on Stripe approving the invite. |
| US LLC (e.g. Stripe Atlas) → Stripe US | Yes (US) | 1–2 weeks | Cleanest for USD SaaS; adds annual US tax filings. Talk to a CA/CPA. |

**Whichever is chosen, §7 keeps the code change to one adapter file.**

---

## 2. Scope of this phase

**In scope:** a customer selects a plan → pays by card → their plan activates automatically,
without staff action; renewals, upgrades, downgrades, cancellation, failed payments.

**Out of scope (already built in docs/22):** the plans themselves, entitlements, enforcement,
feature gating, pricing page, usage meters, admin grant/revoke.

**Admin grants survive this phase.** They remain the path for hand-sold clients, comps, demos and
refund fixes. `Organization.plan_source` distinguishes them: `admin` vs `stripe`. A Stripe webhook
**must not silently overwrite** an `admin` grant — see §6.4.

---

## 3. Data model — on top of docs/22 §5

The `Subscription` model already exists and is unused (`app/models/platform.py`):
`organization_id, stripe_customer_id, stripe_subscription_id, plan, status, current_period_end`.

Migration `00XX_stripe_billing`:

```sql
ALTER TABLE subscriptions ADD COLUMN cancel_at_period_end BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE subscriptions ADD COLUMN price_id             VARCHAR(255) NULL;
ALTER TABLE subscriptions ADD COLUMN past_due_since       TIMESTAMPTZ NULL;
CREATE UNIQUE INDEX ux_subscriptions_org       ON subscriptions (organization_id);
CREATE UNIQUE INDEX ux_subscriptions_stripe_id ON subscriptions (stripe_subscription_id)
       WHERE stripe_subscription_id IS NOT NULL;
CREATE INDEX        ix_subscriptions_customer  ON subscriptions (stripe_customer_id);

CREATE TABLE stripe_events (          -- webhook idempotency (§6.3)
    id           VARCHAR(255) PRIMARY KEY,   -- Stripe's evt_… id
    type         VARCHAR(100) NOT NULL,
    received_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ  NULL,
    error        TEXT         NULL
);
```

The unique index on `organization_id` makes "one subscription per org" a **database** guarantee,
not a code convention.

---

## 4. Stripe objects (dashboard, once, by the human)

Test mode first, then live. Price IDs differ between modes — hence env vars, never literals.

| Product | Price | Recurring | Env var |
|---|---|---|---|
| BotForge Starter | $49.00 USD | monthly | `STRIPE_PRICE_STARTER` |
| BotForge Pro | $99.00 USD | monthly | `STRIPE_PRICE_PRO` |
| BotForge Business | $199.00 USD | monthly | `STRIPE_PRICE_BUSINESS` |

Also enable:
- **Customer portal** (Settings → Billing → Customer portal): plan switching between the three
  prices, cancellation, payment-method updates. This gives upgrade / downgrade / cancel / invoice
  history **for free** — do not build those screens.
- **Webhook endpoint** → `https://<domain>/v1/billing/webhook`, subscribed to exactly the events
  in §6.1. Copy the signing secret to `STRIPE_WEBHOOK_SECRET`.
- **Stripe Tax** only once a CA confirms US sales-tax obligations.

Annual prices ($490 / $990 / $1,990) are a later step. The code must never compute "+30 days" —
always read the period from the subscription object.

---

## 5. Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/billing/checkout` | **owner/admin only** | Creates a Checkout Session, returns `{url}`. |
| `POST` | `/v1/billing/portal` | **owner/admin only** | Returns a Customer Portal URL. |
| `GET` | `/v1/billing/subscription` | user | Plan, status, period end, `cancel_at_period_end`. |
| `POST` | `/v1/billing/webhook` | **none — signature-verified** | Stripe → us. The only thing that grants access. |

```python
@router.post("/v1/billing/checkout")
async def create_checkout(
    body: CheckoutIn,                      # {"plan": "pro"} — a PLAN NAME, never a price id
    ctx: OrgContext = Depends(require_org_role("owner", "admin")),
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    if body.plan not in PAID_PLANS:
        raise AppError("invalid_plan", "Unknown plan.", 400)

    price_id = settings.price_id_for(body.plan)      # env lookup
    customer_id = await service.ensure_stripe_customer(session, ctx.org, ctx.user)

    s = await stripe_client.checkout_sessions_create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(ctx.org.id),
        subscription_data={"metadata": {"organization_id": str(ctx.org.id)}},
        metadata={"organization_id": str(ctx.org.id), "plan": body.plan},
        success_url=f"{settings.app_base_url}/billing?checkout=success",
        cancel_url=f"{settings.app_base_url}/billing/upgrade?checkout=cancelled",
        allow_promotion_codes=True,
        idempotency_key=f"checkout:{ctx.org.id}:{body.plan}:{ctx.user.id}",
    )
    return CheckoutOut(url=s.url)
```

Rules baked in — keep all of them:
- The client sends a **plan name**; the server resolves the **price ID**. A client sending a price
  ID directly could buy a price you never published.
- `organization_id` goes on **both** `client_reference_id` and `subscription.metadata`, because
  `checkout.session.completed` and `customer.subscription.*` are different payloads and both must
  resolve the org.
- `ensure_stripe_customer` is idempotent: reuse `subscriptions.stripe_customer_id` if present,
  else create and persist **before** the Checkout Session.
- Only `owner`/`admin` may start a checkout — a viewer must not be able to charge the org.

---

## 6. The webhook — the only thing that grants access

### 6.1 Events (exactly these; 200 and ignore everything else)

| Event | Action |
|---|---|
| `checkout.session.completed` | Link `stripe_customer_id`/`stripe_subscription_id` to the org. **Do not set the plan here** — take it from the subscription event. |
| `customer.subscription.created` | Set plan + status + period. |
| `customer.subscription.updated` | **Source of truth.** Plan + status + period + `cancel_at_period_end`. |
| `customer.subscription.deleted` | Move org to the expired state. |
| `invoice.paid` | Reset the message window to the invoice period (docs/22 §6). |
| `invoice.payment_failed` | Mark `past_due`, stamp `past_due_since`, email the owner. **Do not cut access yet.** |

### 6.2 Handler

```python
@router.post("/v1/billing/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    payload = await request.body()                       # RAW bytes — never request.json() first
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe_client.construct_event(payload, sig, settings.stripe_webhook_secret)
    except SignatureVerificationError:
        log.warning("stripe.webhook.bad_signature")
        raise HTTPException(status_code=400, detail="invalid signature")

    if not await service.claim_event(session, event["id"], event["type"]):
        return {"received": True, "duplicate": True}

    try:
        await service.handle_event(session, event)
    except Exception:
        await service.mark_event_failed(session, event["id"])
        log.exception("stripe.webhook.failed", extra={"event_id": event["id"]})
        raise HTTPException(status_code=500, detail="handler failed")   # let Stripe retry
    await service.mark_event_processed(session, event["id"])
    return {"received": True}
```

### 6.3 Idempotency & ordering

- `claim_event()` = `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id`. No row ⇒ already seen ⇒
  200 and stop. Stripe retries failed deliveries for days.
- Every handler must **also** be naturally idempotent: plan/status/period are set-to-value writes,
  never increments.
- Events arrive **out of order**. Ignore a `customer.subscription.*` payload whose period is older
  than what is stored.

### 6.4 Plan resolution — never trust a name off the wire

```python
def plan_for_price(price_id: str) -> str | None:
    return {
        settings.stripe_price_starter:  "starter",
        settings.stripe_price_pro:      "pro",
        settings.stripe_price_business: "business",
    }.get(price_id)
```

- Read the price ID from `subscription["items"]["data"][0]["price"]["id"]`.
- **Unknown price ID ⇒ change nothing**, log ERROR, keep the previous plan. An unrecognised price
  is a config mistake; downgrading a paying customer over it is worse than doing nothing.
- The plan name in Checkout `metadata` is analytics only. It must never decide entitlements.
- **Admin grants win over Stripe downgrades.** If `plan_source == "admin"` and the incoming Stripe
  plan is *lower*, keep the admin plan, set `plan_source = "admin"`, and log a WARN — a staff comp
  must not be silently cancelled by a lapsed card. An *upgrade* from Stripe is applied normally
  and flips `plan_source` to `stripe`.

### 6.5 Status → access

| `subscription.status` | Entitlements |
|---|---|
| `active`, `trialing` | the paid plan in full |
| `past_due` (within `BILLING_PAST_DUE_GRACE_DAYS`, default **7**) | the paid plan **in full** — Stripe's dunning retries the card; most recover without noticing |
| `past_due` past grace, `unpaid`, `incomplete_expired` | `_EXPIRED` (read-only, bot stops) |
| `canceled` | paid plan until `current_period_end`, then `_EXPIRED` |
| unknown / missing | the paid plan (**fail open**) + a loud WARN |

### 6.6 Org resolution order

1. `subscription.metadata.organization_id`
2. `checkout.session.client_reference_id`
3. `subscriptions.stripe_customer_id` lookup

None resolve ⇒ log ERROR, return **200** (so Stripe stops retrying an unprocessable payload),
raise an internal alert. **Never guess an org.**

---

## 7. Provider abstraction — the thing that makes §1 survivable

`CLAUDE.md` §8 discourages needless abstraction, so this one is justified explicitly: **the
provider is genuinely undecided** (§1.1), and all candidates speak the same three verbs.

`app/billing/provider.py`, with exactly **one** implementation plus a fake:

```python
class BillingProvider(Protocol):
    async def ensure_customer(self, org, user) -> str: ...
    async def create_checkout(self, org, plan, customer_id) -> str: ...   # returns a URL
    async def portal_url(self, customer_id) -> str: ...
    def parse_event(self, payload: bytes, signature: str) -> BillingEvent: ...
```

`BillingEvent` is **our** normalised shape — `{kind, org_id, plan, status, period_end,
external_ids}` — not Stripe's. Only `StripeProvider` may import the `stripe` SDK; nothing outside
`app/billing/` may import it at all (**add an import test pinning that**).

Swapping to a merchant of record = write one adapter. Entitlements, enforcement and UI: untouched.

---

## 8. Frontend delta (on top of docs/22 §10)

- `/pricing` and `/billing/upgrade` CTAs change from **"Contact us"** to **"Subscribe"** →
  `POST /v1/billing/checkout` → `window.location.href = url`.
- `/billing` gains **"Manage billing"** → `POST /v1/billing/portal`.
- **Post-checkout: `/billing?checkout=success` grants nothing.** It shows *"Payment received —
  activating your plan…"* and polls `GET /v1/me/entitlements` every 2s for up to 30s until `plan`
  changes; then *"Payment received. Your plan will activate within a few minutes."*
  Activation is typically 1–3 seconds behind the redirect, and the UI waits for the server rather
  than pretending.

---

## 9. Security rules — non-negotiable

1. **Never grant a plan from a browser redirect.** `/billing?checkout=success` is a URL anyone can
   type. Only a signature-verified webhook (or an authenticated read of the subscription) may
   change `Organization.plan`.
2. **Verify the signature against the raw body.** Calling `request.json()` before
   `construct_event` breaks verification — and code that then "falls back" to trusting the payload
   is a total auth bypass.
3. **The webhook route is unauthenticated by design** — therefore: exempt from CSRF and session
   middleware, rate-limited by IP, body-size capped, not reachable at a path a proxy rewrites.
4. **Never trust a plan, price or amount sent by a client.** Price ID → plan, server-side.
5. **Idempotency everywhere.** Stripe *will* deliver duplicates.
6. **Never log secrets.** No full payloads, no `sk_live_…`, no customer PII. Log event id, type,
   org id.
7. **`STRIPE_SECRET_KEY` is server-only.** With hosted Checkout the browser needs no Stripe key
   at all.
8. **Role check on money paths:** only `owner`/`admin` may open checkout or the portal.
9. **Test and live keys must never mix.** Startup asserts: `APP_ENV=production` ⇒ keys and price
   IDs must be `live`, else refuse to boot. A test key in production silently accepts fake cards.
10. Everything in **docs/22 §12** still applies — admin grants remain staff-only.

---

## 10. Phases (when unblocked)

| Phase | Scope | Done when |
|---|---|---|
| **S0** | `provider.py` protocol + `FakeProvider`; migration; `Subscription` service. | Fake provider drives a full subscribe→renew→cancel cycle in tests. |
| **S1** | `StripeProvider`, `ensure_customer`, `/checkout`, `/portal`, `/subscription`. | Checkout URL created in Stripe **test mode**, paid with `4242 4242 4242 4242`. |
| **S2** | **The webhook.** Signature verification, event claim, six handlers, status→access, grace window, out-of-order guard, admin-grant precedence (§6.4). | `stripe trigger` for each event flips the org correctly; replay is a no-op; tampered signature 400s. |
| **S3** | Frontend delta (§8), post-checkout polling. | Playwright: buy Pro in test mode → locked n8n panel unlocks without a manual refresh. |
| **S4** | Annual prices, proration UX, dunning emails, Stripe Tax. | — |

---

## 11. Testing

`STRIPE_FORCE_FAKE=true` swaps in the in-memory fake (mirrors the existing `LLM_FORCE_FAKE`
convention). **CI never calls the Stripe network.**

Local, with a real test-mode account:
```bash
stripe login
stripe listen --forward-to localhost:8000/v1/billing/webhook   # prints whsec_… → STRIPE_WEBHOOK_SECRET
stripe trigger customer.subscription.updated
```
Cards: `4242 4242 4242 4242` succeeds · `4000 0000 0000 0341` fails on charge (drives
`invoice.payment_failed`) · `4000 0025 0000 3155` forces 3-D Secure. International cards to Indian
businesses require 3DS.

Cases that must exist: invalid signature ⇒ 400 and nothing written · duplicate event ⇒ handler runs
once · out-of-order update ignored · unknown price ⇒ plan unchanged + ERROR · `past_due` inside
grace keeps access, past grace expires · `canceled` keeps access to `current_period_end` ·
upgrade mid-period unlocks immediately · **downgrade never deletes data** (docs/22 §11 rule 2) ·
**Stripe downgrade does not override an admin grant** (§6.4) · non-admin ⇒ 403 on `/checkout`.

---

## 12. Env vars (only when this phase runs)

```bash
STRIPE_SECRET_KEY=sk_test_xxx          # Dashboard → Developers → API keys
STRIPE_WEBHOOK_SECRET=whsec_xxx        # Webhook endpoint signing secret (or `stripe listen`)
STRIPE_PRICE_STARTER=price_xxx         # $49/mo  (test and live differ!)
STRIPE_PRICE_PRO=price_xxx             # $99/mo
STRIPE_PRICE_BUSINESS=price_xxx        # $199/mo
STRIPE_FORCE_FAKE=false                # true = in-memory fake, for CI/offline dev
BILLING_PAST_DUE_GRACE_DAYS=7
APP_BASE_URL=http://localhost:3001     # Checkout success/cancel URLs
```

Per `CLAUDE.md` §7: a missing key **logs a loud warning and stubs the provider** — never blocks the
build. With no key configured, `/v1/billing/checkout` returns a clear 503 `billing_not_configured`
and everything else, including admin grants, keeps working.

---

## 13. Open questions — answer before S1

1. **Provider.** Stripe (needs a registered business) or a merchant of record? §1.1.
2. **Admin grants after launch.** Keep them for hand-sold clients (recommended — §6.4 assumes yes)
   or retire them?
3. **Extra-message packs.** Keep as admin-granted (docs/22 §7), or sell as Stripe metered usage?
   Metered billing is a second billing mode with its own failure modes — recommended to keep
   manual until the subscription path is proven in production.
4. **Trial → paid.** Does subscribing mid-trial end the 10-day trial immediately (recommended) or
   run alongside?
5. **Currency.** Price in USD with INR settlement, or price in INR for Indian clients too?

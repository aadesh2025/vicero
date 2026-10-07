# Claude Code task — Redesign Login + Create Account (Design A)

> Paste this whole file into Claude Code, or just say:
> **"Read `designreference/login-v2/CLAUDE_CODE_PROMPT.md` and do the task in it."**

---

## 1. What you are doing (one line)

Restyle the existing auth pages to **Design A — "the login that talks back"**:
a bright, clean sign-in form on the left + an always-dark, animated **chatbot preview panel** on the right.
**Only the look changes. All auth logic stays exactly as it is.**

---

## 2. Read these first (in this order)

1. `designreference/login-v2/login-design-A.preview.html` — open in a browser. **This is the target look (desktop).**
2. `designreference/login-v2/login-design-A-mobile.preview.html` — **target look (mobile).**
3. `designreference/login-v2/login-design-A.dc.html` — same design as source (exact colors, sizes, keyframes, copy, SVG paths).
4. Current code:
   - `apps/web/src/app/(auth)/layout.tsx`
   - `apps/web/src/app/(auth)/login/page.tsx`
   - `apps/web/src/app/(auth)/signup/page.tsx`
   - `apps/web/src/components/auth/oauth-buttons.tsx`
   - `apps/web/src/components/auth/form-bits.tsx`
   - `apps/web/src/components/brand/logo.tsx`
   - `apps/web/src/app/globals.css` (theme tokens + the AA notes at the top — follow them)
5. `CLAUDE.md` — Definition of Done still applies.

The design files are a **visual reference**, not code to paste. Rebuild them with the project's own stack
(Next.js App Router, Tailwind tokens, shadcn/ui, Framer Motion, lucide-react).

---

## 3. HARD RULES — do NOT change any of this

- ❌ `apps/web/src/lib/api/auth.ts` — no edits.
- ❌ Backend (`apps/api/**`) — no edits.
- ❌ Routes: `/login`, `/signup`, `/forgot-password`, `/magic`, `/oauth/callback`, `/oauth/verify`, `/reset-password`, `/verify-email` stay the same.
- ❌ OAuth behaviour in `oauth-buttons.tsx`: keep `start(provider)`, the `busy` state, the spinner, `oauthAuthorizeUrl()`, the 501 "isn't set up on this server yet" message. **Providers stay Google + Facebook only.** You may restyle the buttons.
- ❌ Login logic: `login(email, password)` → `router.replace(params.get("next") || "/dashboard")`, the `oauthErrorMessage(params.get("error"))` initial error, magic-link flow and its exact notice text.
- ❌ Signup logic: `signup(email, password, name)` → `/onboarding`, the `auth.email_taken` message, `minLength={8}`, magic-link notice text.
- ❌ `autoComplete` values, `required`, input `id`s, `FormError` / `FormNotice` / `SubmitButton` behaviour (restyle allowed).
- ❌ No new npm packages. Framer Motion and lucide-react are already installed.
- ❌ No `localStorage`, no tracking, no new env vars.

If a test breaks because of a selector, fix the **test selector**, never the auth behaviour.

---

## 4. Layout — the new `(auth)/layout.tsx`

Replace the centered single-card layout with a **split layout** used by ALL auth pages:

```
┌──────────────────────────────────────────────────────────────────┐
│ [V] VICERO                    │ ┌───────────────────────────────┐ │
│                               │ │ AI CUSTOMER CONVERSATIONS     │ │
│      (animated logo tile)     │ │ One assistant.                │ │
│      Welcome back.            │ │ Every channel.                │ │
│      subline                  │ │ Zero missed leads. (blue)     │ │
│  [ Google ]  [ Facebook ]     │ │   ┌───────────────────────┐   │ │
│  ───── OR WITH EMAIL ─────    │ │   │ Vicero Assistant  chat│ ◀ toast "New lead captured"
│  [ Work email          ]      │ │   │ bubbles animate in…   │   │ │
│  [ Password        👁 ]       │ │   │ chips + composer      │   │ │
│            Forgot password?   │ │   └───────────────────────┘   │ │
│  [ Sign in  ▶ ]  (black)      │ │ • Learns from your docs …     │ │
│  [ ✉ Email me a sign-in link ]│ └───────────────────────────────┘ │
│  New to Vicero? Start free…   │   (rounded-[32px], always dark)   │
│  Terms · Privacy · Help       │                                   │
└──────────────────────────────────────────────────────────────────┘
       LEFT ~ 40%                         RIGHT ~ 60%
```

- Page background: `bg-bg` + a subtle **dot grid** (22px spacing, 1px dots, `--border` colour). Replace the old `glow-accent` / `bg-grid` for auth pages only.
- Outer padding 20px, gap 20px.
- **Left column** (`flex-1`, min ~440px): brand row at top (use the existing `<Logo />`), form centered vertically, `max-w-[400px]`, footer links at bottom.
- **Right column** = new `AuthShowcase` component (see §6). Takes the rest of the width, `min-h-[calc(100vh-40px)]`, `rounded-[32px]`.
- **Below `lg` (1024px):** hide the right panel. Show the **mini chat card** from the mobile design above the heading instead (2 bubbles).
- Let each page pass its own heading/copy; the layout only gives the frame + showcase.
- Other auth pages (forgot/reset/magic/verify/oauth) just inherit the new frame — keep their content, only make their cards fit the new left column (no box/border needed, same as login).

---

## 5. Visual spec (left side)

| Element | Spec |
|---|---|
| Font | Existing `--font-display` (Plus Jakarta Sans). **Do not add fonts.** |
| Animated logo tile | 60×60, `rounded-[20px]`, near-black (`#0B0D14`) tile, white V-pill + blue triangle inside. Around it: 84×84 dashed ring (1.5px, `--border-strong`) that slowly spins (24s) with a 7px blue dot riding it. Margin-bottom 28px. |
| H1 | 38px, weight 800, tracking `-0.02em`, leading 1.1 |
| Subline | 16px, `text-muted`, leading 1.55 |
| Social buttons | 2-col grid, gap 12px, height 50px, `rounded-[14px]`, `border-border bg-surface`, 15px semibold, brand icon 18px. Hover `bg-surface-3`. |
| Divider | "OR WITH EMAIL", 12px, semibold, `tracking-[0.08em]`, `text-faint`, 1px lines |
| Inputs | **Label inside the box** (12px semibold muted) above the value (16px). Box: `rounded-[14px] border bg-surface px-4 py-2.5`. Focus: border `accent-strong` + 4px ring `accent/14%`. Keep a real `<label htmlFor>` — don't fake it. |
| Password | Same box + an eye button on the right (44×44, `aria-label="Show password"` / `"Hide password"`, `aria-pressed`). Toggles `type` between `password` / `text`. **New, UI-only.** Use lucide `Eye` / `EyeOff`. |
| Forgot password? | Right-aligned under password, 13px, accent link |
| Primary button | 54px tall, `rounded-[14px]`, **inverse**: light theme `bg-text text-bg` (near-black), dark theme the reverse (white bg, black text). Label + small **brand triangle** pointing right (blue). Spinner via existing `SubmitButton busy`. |
| Magic-link button | 48px, `rounded-[14px]`, **dashed** 1px border `--border-strong`, mail icon + text |
| Bottom link | "New to Vicero? **Start your free trial**" (login) / "Already have an account? **Sign in**" (signup) |
| Footer | Terms · Privacy · Help, 12px, `text-faint` |

Light + dark: left side **follows the app theme tokens** (it must work in both).
The right showcase panel is **always dark** in both themes (that's the "mixed premium" idea).

---

## 6. The right panel — `components/auth/auth-showcase.tsx` (new)

Always dark: bg `#0B0D14` with a faint diagonal hatch
`repeating-linear-gradient(135deg, rgba(255,255,255,.035) 0 2px, transparent 2px 12px)`, padding 40px, `overflow-hidden`.

Contents, top to bottom:
1. Pill label: **"AI CUSTOMER CONVERSATIONS"** (12px, 1px white/16% border).
2. Headline (44px, 800): **"One assistant. / Every channel. / Zero missed leads."** — last line `#60A5FA`.
3. **Chat window** (max-w 480px, centered, `rounded-[26px]`, bg `#14171F`, border white/10%, big shadow):
   - Header: white 38px tile with logo mark, "**Vicero Assistant**", green dot + "Online · replies instantly", channel pills **Web · WhatsApp · Instagram**.
   - Messages (bot = `#2563EB` bubble left, `rounded 18 18 18 6`; user = `#262A35` right, `rounded 18 18 6 18`), 14px.
   - Quick-reply chips (outline blue/45%): **Train on my website · Connect WhatsApp · Set up hand-off**.
   - Fake composer: "Ask Vicero anything…" + white send tile with the blue triangle. **Decorative only:** `aria-hidden`, not focusable.
4. **Floating toast** (top-right, overlapping the panel): white card, green check tile, "**New lead captured**" / "From Instagram · sent to your inbox". Slides in, then floats gently.
5. Bottom row of 3 points with blue dots: **Learns from your docs · One inbox for every channel · Hands off to humans**.
6. Giant faint logo mark watermark (opacity 6%, ~620px) bottom-right, `aria-hidden`.

Props: `variant: "login" | "signup"` — changes only the conversation script (see §8).
Whole panel is decorative → wrap in `aria-hidden="true"` except nothing in it should be focusable.

---

## 7. Animations (Framer Motion) — copy timings from the `.dc.html` keyframes

| What | Animation |
|---|---|
| Logo mark | Build `components/brand/animated-logo-mark.tsx`: an **inline SVG** with two paths (pill + triangle). Pill flies in from top-left (−140, −110, −18°) → place, 1s, ease `[.2,.8,.2,1]`. Triangle from top-right (+160, −90, 60°, scale .5) → place, delay .2s. Then triangle glows (drop-shadow blue) on a 3.4s loop. SVG paths: copy from `login-design-A.dc.html` (viewBox `0 0 1254 1254`). **If a vector SVG of the logo exists in `public/brand`, use its real paths instead** — the ones in the design are a hand-traced approximation. |
| Ring | Rotate 360° / 24s, linear, infinite |
| Chat bubbles | Each fades + slides up 14px, 0.6s. Delays: 0.6s, 1.5s, 2.4s, 3.4s, then **typing dots** bubble 4.2s→5.6s, then final bot message 5.6s, chips 6.3s, toast 6.8s |
| Typing dots | 3 dots bounce (staggered .15s) |
| Toast | Slide in from right 30px, then float ±8px / 5s loop |

**Must respect `prefers-reduced-motion`** (`useReducedMotion()`): show everything in its final state, no movement.
Play the sequence once on mount; don't loop the conversation.

---

## 8. Copy (use exactly)

### Login (`/login`)
- H1: **Welcome back.**
- Sub: **Your assistants kept every conversation going while you were away. Sign in to pick them up.**
- Email label: **Work email**, placeholder `you@company.com`
- Password label: **Password**
- Button: **Sign in**
- Ghost: **Email me a sign-in link instead**
- Bottom: **New to Vicero? Start your free trial** → `/signup`

Showcase script (login):
1. Bot: "Hi! I'm the assistant on your website. Ask me anything."
2. User: "Can you answer from our pricing PDF?"
3. Bot: "Yes. I learn from your docs, site and FAQs, then reply on web, WhatsApp and Instagram."
4. User: "And if they need a real person?"
5. (typing…)
6. Bot: "I hand the chat to your team with the full history, so no one asks twice."

### Create account (`/signup`)
- H1: **Start your free trial.**
- Sub: keep the existing real line — **10 days, 500 messages, no card needed.**
- Fields: **Full name** (optional), **Work email**, **Password** (+ eye toggle) with hint **At least 8 characters, and nothing easy to guess.**
- Button: **Create account**
- Ghost: **Sign up with an email link instead**
- Bottom: **Already have an account? Sign in** → `/login`

Showcase script (signup):
1. Bot: "Welcome to Vicero! Let's build your first assistant."
2. User: "How long does setup take?"
3. Bot: "Add your website or upload a doc, and I'm ready to answer on your site."
4. User: "Can I connect WhatsApp later?"
5. (typing…)
6. Bot: "Anytime. Web, WhatsApp and Instagram all land in one inbox."
Chips: **Add my website · Upload a doc · Connect a channel**

Toast stays the same on both.

---

## 9. Files to create / change

| File | Action |
|---|---|
| `src/app/(auth)/layout.tsx` | Rewrite to split layout (§4) |
| `src/components/auth/auth-showcase.tsx` | **New** — right panel (§6) |
| `src/components/auth/mini-chat-card.tsx` | **New** — mobile/tablet 2-bubble card |
| `src/components/brand/animated-logo-mark.tsx` | **New** — animated SVG mark (§7) |
| `src/components/auth/password-input.tsx` | **New** — input + eye toggle (shared by login, signup, reset-password) |
| `src/components/auth/oauth-buttons.tsx` | **Restyle only** (§5). Logic untouched. |
| `src/components/auth/form-bits.tsx` | Restyle `SubmitButton` to the inverse style + triangle; keep API |
| `src/app/(auth)/login/page.tsx` | New markup/copy, same handlers |
| `src/app/(auth)/signup/page.tsx` | New markup/copy, same handlers |
| Other `(auth)` pages | Only remove the old card box so they sit nicely in the left column |

The layout needs to know login vs signup for the showcase variant: read `usePathname()` in a small
client wrapper, or pass it from each page — your choice, write the decision in `docs/DECISIONS.md`.

---

## 10. Accessibility checklist

- [ ] Every input has a real `<label htmlFor>`.
- [ ] Eye button: `aria-label` changes Show/Hide, `aria-pressed`.
- [ ] Contrast ≥ 4.5:1 for all text in BOTH themes (check `text-faint` on the dot-grid bg and placeholder colours). Follow the AA notes at the top of `globals.css`.
- [ ] Touch targets ≥ 44px.
- [ ] Showcase panel `aria-hidden`, nothing inside focusable.
- [ ] Tab order: Google → Facebook → email → password → eye → forgot → Sign in → magic link → signup link.
- [ ] `prefers-reduced-motion` honoured.
- [ ] Existing `e2e/08-accessibility.spec.ts` (axe) passes.

---

## 11. Testing (per CLAUDE.md Definition of Done)

- [ ] `tsc --noEmit` and `eslint` clean.
- [ ] `vitest` passes (incl. `auth-gate.test.tsx`).
- [ ] Existing Playwright passes, especially `24-signup-and-workspace.spec.ts`, `08-accessibility.spec.ts`, `30-trial-ui.spec.ts`.
- [ ] **New** `e2e/31-auth-redesign.spec.ts`:
  - `/login` shows Google + Facebook buttons, email, password, "Sign in".
  - Eye button toggles password field type.
  - "Start your free trial" → `/signup`; "Sign in" on signup → `/login`.
  - Wrong password still shows the existing error.
  - At 390px width the showcase is hidden and the mini chat card is visible.
- [ ] Take new screenshots into `var/redesign/after-v2/{light,dark}/login.png` and `signup.png` and compare by eye with `designreference/login-v2/login-design-A.preview.html`.

---

## 12. Done = all true

- [ ] Login + signup look like Design A (desktop + mobile), light and dark themes.
- [ ] Google + Facebook sign-in, email/password, magic link, forgot password, `?next=` redirect, `?error=` OAuth message — **all still work exactly as before.**
- [ ] No backend or `lib/api` changes.
- [ ] All tests green. Commit: `feat(web): redesign login and signup (design A — chat showcase)`.
- [ ] Add a short note to `docs/DECISIONS.md`.

**Do not stop to ask for approval — follow CLAUDE.md §1. Only stop for a hard blocker.**

# 20 — Full UI Redesign: Dual Theme (Light + Dark) Across the Whole SaaS

> **Paste into Claude Code:**
> "Read `docs/20-UI-REDESIGN-DUAL-THEME.md` and execute it phase by phase (R0 → R8).
> Look at the design references it names before writing any code."

**Status:** approved direction, ready to build. **Owner decision needed:** only the items in §16.

---

## 0. What this is (read first)

- **Goal:** replace today's black-and-white look with a **colourful, meaningful, dual-theme UI** (Light + Dark) on **every page** of the web app, not just the dashboard.
- **Style target:** calm, modern SaaS, like the reference `designreference/UI_resign.jfif` (light and dark versions of the same dashboard):
  - soft neutral background
  - white (light) or graphite (dark) cards
  - one strong blue primary
  - colour that **means something**: status, channel, AI
- **This is a visual redesign only.**
  - ❌ No API, database, business-logic or routing changes.
  - ❌ No new product features, except the small Dashboard quick-actions rail in §9.3.
  - ❌ Do not touch `apps/api`, `app/chat`, `app/rag` or anything in docs/11 or docs/17 scope.
  - ✅ Colours, typography, spacing, components, page layouts, charts, empty and loading states.
- **Work in phases (§14).** Commit after each phase. Every phase ends with typecheck, lint, vitest and Playwright green (CLAUDE.md §2).
- **CLAUDE.md §1 autonomy applies within this doc.** Only stop for the decisions listed in §16.

---

## 1. Design references (look at these before coding)

| File | What to take from it |
|---|---|
| `designreference/UI_resign.jfif` | **Primary style reference.** Same dashboard in light and dark: layout rhythm, card style, pastel status pills, bordered cards, calm neutrals, blue primary button |
| `designreference/inboxdesign.png` | Inbox layout reference (three-pane conversation view) |
| `designreference/prototype/botforge-dual-theme-prototype.dc.html` | **Our approved clickable prototype** (Dashboard, Conversations, Channels, light/dark toggle). It's a design-canvas file, not runnable standalone. **Read its source:** every colour token and style string is in the `renderVals()` block at the bottom. Treat its values as the spec when this doc and the prototype agree; **this doc wins** if they differ |
| `docs/05-FRONTEND.md` | Existing frontend conventions. Keep them |

> The references are for **style**, not content. Use BotForge's real pages, data and copy.
> Don't copy any third-party product's layout pixel-for-pixel.

---

## 2. What exists today (verified in the repo, 2026-09-26)

- **The theme system is already token-based.** This is good news: most of the redesign is **re-mapping values**, not rewriting classes.
  - `apps/web/src/app/globals.css` defines CSS variables as RGB triplets under `:root` (light) and `.dark` (dark).
  - `tailwind.config.ts` maps them: `bg`, `surface`/`-2`/`-3`, `border`/`-strong`, `text`, `muted`, `faint`, `accent`/`-2`/`-soft`/`-strong` (and its alias `ember`), `on-accent`, `glow`, `success`, `warn`, `error`, `info`, `ring`, and shadow and radius tokens.
  - `next-themes` with `attribute="class"`, `defaultTheme="light"`, `enableSystem={false}` (`src/lib/providers.tsx`).
  - The toggle is `src/components/shell/theme-toggle.tsx`.
- **Why it looks black and white today:** the light `--accent` is **grey `#1F2937`**. The dark accent is ember orange `#FF6A3D`. So the two themes don't even share a brand colour.
- **Fonts:** Space Grotesk (display), Inter (body), JetBrains Mono (`src/app/layout.tsx`).
- **Hard-coded colours are rare:** almost no Tailwind palette classes (`bg-blue-500` etc.) and about 4 files with hex values. Find them in R0 and move them to tokens.
- **Components:**
  - `src/components/ui/*` (shadcn-style primitives)
  - `shell/` (sidebar, topbar, org switcher, user menu, theme toggle)
  - `dashboard/` (stat-card, usage-chart, panels)
  - `analytics/` (bar-list, channel-breakdown, agent-breakdown)
  - `builder/` (agent builder tabs, workflow canvas)
  - `inbox/`, `contacts/`, `knowledge/`, `settings/`, `plan/` (trial meter, locked states), `brand/logo.tsx`
- **Charts are custom SVG, with no chart library.** Keep it that way and restyle them.
- **Channel keys used in code:** `widget`/`web`, `whatsapp`, `instagram`, `facebook` (Messenger), `telegram`, `email`, `slack`, `discord`.

---

## 3. Design principles (rules for every decision)

1. **Colour = meaning.** Every coloured thing answers "what is this?" (status, channel, AI, primary action). No decorative rainbow.
2. **Neutral base, colour on top.** About 80% of the screen is neutral: background, cards, text. Colour lives in icons, chips, pills, chart lines, badges and one primary button.
3. **One primary action per view.** Only one solid blue button per screen area.
4. **Same meaning in both themes.** Green is always success, WhatsApp is always green. Only the shade changes between light and dark.
5. **Dark mode is designed, not inverted.** It has its own surfaces and slightly brighter accents.
6. **Readable first.** Text passes WCAG AA (4.5:1 body, 3:1 for 18px+ or bold 14px+). Brand colours that fail as text get a darker (light mode) or lighter (dark mode) **text shade**.
7. **Never colour alone.** Status always has text or an icon too.
8. **Tokens only.** No raw hex in components. Everything goes through CSS variables so a future rebrand or rename is a one-file change.

---

## 4. Colour tokens: the full spec

Write every value as an **RGB triplet** in `globals.css` (existing convention: `--x: R G B; /* #HEX */`). Keep **existing token names**, so hundreds of classes keep working. Add the new tokens listed below and map them in `tailwind.config.ts`.

### 4.1 Base (surfaces and text)

Dark mode is **pure neutral black** — every base value below is R=G=B, no navy/blue tint
(2026-09-28 feedback, matching `designreference/UI_resign.jfif`'s dark dashboard exactly).

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--bg` | `#F7F8FC` | `#000000` | App background |
| `--surface` | `#FFFFFF` | `#111111` | Cards, sidebar, topbar, dialogs |
| `--surface-2` | `#F8FAFC` | `#1A1A1A` | Hover, table zebra, inset panels |
| `--surface-3` | `#F1F5F9` | `#222222` | Deep inset, segmented-control track, progress track |
| `--border` | `#E5E7EB` | `#262626` | Card borders, dividers |
| `--border-strong` | `#D1D5DB` | `#333333` | Inputs, focused outlines (non-ring) |
| `--text` | `#111827` | `#F5F5F5` | Headings and body |
| `--muted` | `#475569` | `#D4D4D4` | Secondary text, labels, nav items |
| `--faint` | `#5B6B82` (darker than the original `#64748B` — that cleared plain `surface` but not `surface-3`/`bg`, per the R8 axe pass) | `#A3A3A3` | Captions, timestamps, placeholders (AA everywhere it's used) |
| `--sidebar` (new) | `#FFFFFF` | `#0A0A0A` | Sidebar and AI rail background |

### 4.2 Primary (was grey or ember; now blue in both themes)

| Token | Light | Dark | Notes |
|---|---|---|---|
| `--accent` | `#2563EB` | `#60A5FA` | Links, active nav text, focus, chart primary line |
| `--accent-strong` | `#2563EB` | `#2563EB` (not `#3B82F6` — see below) | **Filled** primary buttons |
| `--accent-2` | `#1D4ED8` | `#93C5FD` | Hover and pressed |
| `--accent-soft` | `#EFF6FF` | `#1C2632` | Active nav background, info pill background, selected rows |
| `--on-accent` | `#FFFFFF` | `#FFFFFF` | Text on filled primary |
| `--ring` | `#2563EB` | `#60A5FA` | Focus ring |

> ⚠️ **`--accent-soft` changes meaning.** Today it's a *text* colour; after the redesign it's a *background tint*. In R1, grep every `accent-soft`/`ember-soft` usage. Where it colours **text**, switch to `text-accent`. Keep the `ember` alias pointing at `accent` (see the comment in `tailwind.config.ts`).
> Dark-mode button text: **never** put white text on `#60A5FA` (fails AA) — **and not on `#3B82F6` either** (measured 3.68:1 by the R8 axe pass, still under 4.5:1). `--accent-strong` dark is `#2563EB`, the same hex as light: it's the darkest blue that still reads as "the accent" and clears AA with white text.

### 4.3 Meaning colours (5 only)

Each has a **main** colour (icons, lines, dots), a **text** shade (AA as text on surface and on soft), and a **soft** background. Dark "soft" values are pre-blended solids — the main colour at 14% over `--surface` (`#111111`), 16% for the two brand colours that wash out at 14% (Instagram, Email), 18% for the darkest one (Messenger) — so they stay plain RGB tokens. Recompute with `round(main*a + 0x11*(1-a))` per channel if a main colour ever changes; don't hand-tune by eye.

| Meaning | Token base | Light main / text / soft | Dark main / text / soft |
|---|---|---|---|
| **AI / agents** (new) | `--ai` | `#7C3AED` / `#6D28D9` / `#F3E8FF` | `#A78BFA` / `#A78BFA` / `#262232` |
| **Success** | `--success` | `#16A34A` / `#15803D` / `#F0FDF4` | `#4ADE80` / `#4ADE80` / `#192E21` |
| **Warning** | `--warn` | `#D97706` / `#B45309` / `#FFFBEB` | `#FBBF24` / `#FBBF24` / `#322914` |
| **Error** | `--error` | `#DC2626` / `#B91C1C` / `#FEF2F2` | `#F87171` / `#F87171` / `#311E1E` |
| **Info** | `--info` | `#2563EB` / `#1D4ED8` / `#EFF6FF` | `#60A5FA` / `#60A5FA` / `#1C2632` |

- New tokens to add per meaning: `--<m>-text` and `--<m>-soft`, plus `--ai`, `--ai-text`, `--ai-soft`.
- Tailwind: `success: { DEFAULT, text, soft }`, same for `warn`, `error`, `info`, `ai`.
- ❌ Do **not** add "Data" (cyan) or "Insight" (pink) meaning colours. They clash with Telegram and Instagram (decision recorded, see §16).

### 4.4 Meaning map (where each colour goes)

| Colour | Always means | Examples |
|---|---|---|
| Blue (accent) | Primary action, current place, info | "New agent" button, active nav, links, "In progress" pill, chart primary series |
| Purple (ai) | Something the AI does or is | Agent avatars and icons, AI Builder rail, "AI resolved" labels, AI suggestions, trial/plan card, agent-type chips |
| Green | Success, healthy, up-trend | Resolved, Connected, Published, Active, ↑ good delta, "Resolved by AI" chart series |
| Amber | Needs attention soon | Handoff, Pending, Trial ending, Draft, rate-limit warnings, ↓ delta where down is *good* is still green (colour follows good/bad, not arrow direction) |
| Red | Failed, blocked, destructive | Unanswered, Failed ingest, Disconnected with error, Delete buttons, 402 plan-limit hit, Inbox count badge |
| Neutral grey | Inactive, unknown, archived | Not connected, Archived, Disabled |

### 4.5 Charts

| Token | Light | Dark |
|---|---|---|
| `--chart-grid` | `#E9ECF2` | `#262626` |
| `--chart-axis` | `#64748B` | `#A3A3A3` |
| Series 1 | accent | accent |
| Series 2 | success | success |
| Series 3 | ai | ai |
| Series 4 | warn | warn |
| Series 5 | `#0891B2` / `#22D3EE` (teal, **charts only**) | same |

- **Line charts:** 2.5px line plus an **area fill with a vertical gradient** from the series colour at 22% (light) or 30% (dark) opacity down to 0. Dotted gridlines. On hover, a vertical guide line, a ringed dot per series, and a tooltip card (`surface` with border and `shadow-pop`).
- **Bars / bar-list:** rounded 4px, track `surface-3`, fill = series or channel colour.
- **Channel charts** (`channel-breakdown`, dashboard "By channel") use **channel colours** (§5), never series colours.
- Use `vector-effect="non-scaling-stroke"` on scalable SVG lines.

---

## 5. Channel colour system (every social or messaging surface)

**Rule:** wherever a channel is named or shown, **its label text, icon, chip, avatar tint and chart bar use that channel's colour**. Channel colours are used **only in channel contexts**, never as general UI colour.

Brand colours that fail as text get separate **text** shades per theme. Dots, logos and bars keep the exact brand colour.

| Channel (code key) | Brand / dot | Light text | Dark text | Light soft | Dark soft |
|---|---|---|---|---|---|
| Website (`widget`, `web`) | `#2563EB` | `#1D4ED8` | `#60A5FA` | `#EFF6FF` | `#1C2632` |
| WhatsApp (`whatsapp`) | `#25D366` | `#15803D` | `#4ADE80` | `#ECFDF3` | `#142C1D` |
| Instagram (`instagram`) | gradient `#F58529 → #DD2A7B → #8134AF` (solid fallback `#DD2A7B`) | `#C13584` | `#F472B6` | `#FDF2F8` | `#321522` |
| Messenger (`facebook`) | `#0866FF` | `#0759DB` | `#4D94FF` | `#EEF4FF` | `#0F203C` |
| Telegram (`telegram`) | `#229ED9` | `#1A7FB0` | `#4FC3F7` | `#ECF7FD` | `#13252D` |
| Email (`email`) | `#F59E0B` | `#B45309` | `#FBBF24` | `#FFFBEB` | `#352810` |
| Slack (`slack`) | `#611F69` | `#611F69` | `#D79FDA` | `#F8EEF9` | `#1C131D` |
| Discord (`discord`) | `#5865F2` | `#4752C4` | `#8B95F8` | `#EEF0FE` | `#1B1D31` |
| Unknown / other | `faint` | `muted` | `muted` | `surface-2` | `surface-2` |

**Implementation:**

- **CSS variables** for both themes: `--ch-<key>`, `--ch-<key>-text`, `--ch-<key>-soft`. Instagram also gets `--ch-instagram-gradient` as a plain CSS custom property holding the gradient, used as `background-image`.
- **One source of truth:** `src/lib/channels.ts` exports `CHANNEL_META` (key → label, lucide icon, css var names) and `channelMeta(key)` with an unknown fallback. Replace every ad-hoc channel label or colour switch in the app with it.
- **Shared components** in `src/components/shared/`:
  - `<ChannelBadge channel size>`: pill with dot, label and tinted background.
  - `<ChannelDot channel>`
  - `<ChannelIcon channel>`: brand-tinted square with a white glyph.
  - `<ChannelText channel>`
- Brand logos: use lucide generic icons or simple SVG glyphs. **Don't** embed trademarked logos you don't have files for. Brand colour plus the channel name is enough.

---

## 6. Typography

- **Replace** Space Grotesk and Inter with **Plus Jakarta Sans** (display and body; `next/font/google`, weights 400/500/600/700/800). **Keep JetBrains Mono** for code, keys and IDs. Keep the `--font-display`, `--font-body` and `--font-mono` variable names.
- **Scale** (Tailwind classes or tokens):

| Use | Size / weight | Notes |
|---|---|---|
| Page title (h1) | 22px / 800, tracking −0.02em | One per page |
| Section / card title (h2) | 15px / 800 | |
| Body | 14px / 500–600 | |
| Label / nav | 13–13.5px / 600 (active 800) | |
| Caption / timestamp | 12px / 600, `faint` | |
| Overline (nav group, table head) | 10.5–11px / 800, UPPER, tracking 0.08em, `faint` | |
| KPI number | 26–28px / 800, tracking −0.02em | `tabular-nums` |

- Numbers in tables, KPIs and charts use `font-variant-numeric: tabular-nums`.

---

## 7. Shape, spacing, elevation

| Token | Value |
|---|---|
| `--radius` | `0.75rem` (12px) for inputs and buttons (`rounded-lg`) |
| Card radius | 14px (`rounded-[14px]` or new `rounded-card`) |
| Pills / chips | fully rounded |
| Card | `bg-surface border border-border shadow-card`, padding 16–20px |
| Shadows | Keep the existing themed shadow tokens. Light: very soft. Dark: **no shadow**, borders only |
| Spacing | 4px grid. Page padding 24px; gap between cards 14–16px; inside cards 12–16px |
| Layout widths | Sidebar 240px (collapsible to 72px icon rail), AI rail 300px (Dashboard only, §9.3), content fluid, min 1024px desktop. Below `lg`, sidebar becomes a drawer |

---

## 8. Component spec (restyle `src/components/ui/*` and shared pieces)

Every component must look right in **both themes** and cover these states: default, hover, focus-visible (2px `ring` with 2px offset), active, disabled, loading.

| Component | Spec |
|---|---|
| **Button** | `primary` (accent-strong fill, white text), `secondary` (surface, border, text), `ghost` (transparent, muted text, surface-2 hover), `destructive` (error fill; confirm dialogs only), `ai` (ai fill, white text; AI Builder actions only). Heights: sm 32, md 38, lg 44. Icon-only buttons need `aria-label` |
| **Badge / status pill** | `soft` variant = `<m>-soft` background + `<m>-text` text, rounded-full, 12px/800. Use the mapping table below, and one `<StatusPill status>` component |
| **Card** | See §7. Header row: title (h2) left, "See all" ghost button or actions right |
| **Stat card (KPI)** | Tinted icon chip (34px, `<m>-soft` background, `<m>` icon) + label + big number + delta (green up-good / red down-bad / neutral) + 80×32 sparkline in the same meaning colour |
| **Table / list rows** | Head row: overline style. Rows 52–56px, zebra `surface-2` on alternate rows **or** hover only (pick one app-wide: **hover only** plus a divider). Selected row: `accent-soft` |
| **Tabs** | Segmented control: track `surface-3`, active pill = `surface` + `shadow-card` (light) or `surface-2` (dark), active text `text` weight 800 |
| **Inputs / select / textarea** | `surface` background, `border-strong`, 38px, focus ring accent, placeholder `faint`, error state = error border + `error-text` helper |
| **Dialog / dropdown / tooltip** | `surface`, border, `shadow-pop`, radius 14. Dark mode: surface-2 so it lifts from cards |
| **Switch / slider** | On = accent. AI-related toggles (for example "AI auto-reply") = ai |
| **Skeleton** | `surface-3` shimmer. Must match the final layout (no layout jump) |
| **Empty state** | Icon in a tinted chip (meaning colour of the page), one-line title, one-line help, one primary action |
| **Toast** | Surface card with a coloured leading icon (success/warn/error/info). No full-colour toasts |
| **Plan / trial meter** (`components/plan`) | `ai-soft` card, gradient bar `ai → accent`, days-left pill amber, "Upgrade" secondary button with ai text |
| **Locked (402 plan_limit) feature** | Existing locked states restyled: neutral card, lock icon in an `ai-soft` chip, "Upgrade to unlock" ai-text link. No red. It's an upsell, not an error |
| **Avatar** | Contacts: tinted by **channel** of the latest conversation (channel soft background + channel text initials). Agents: `ai-soft` + `ai` icon |
| **Logo** (`brand/logo.tsx`) | Gradient `#7C3AED → #2563EB` mark. **Read the product name from one constant** (rename pending, §16). Never hard-code the name in new components |

**Status → colour mapping** (one `STATUS_TONE` map in `src/lib/status.ts`):

| Status values | Tone |
|---|---|
| resolved, connected, published, active, completed, success, verified | success |
| open, in_progress, running, processing, syncing, info | info |
| handoff, pending, draft, trial_ending, needs_review, queued | warn |
| unanswered, failed, error, disconnected_error, plan_limit_block, expired | error |
| ai_resolved, ai_suggested, agent | ai |
| archived, disabled, not_connected, unknown | neutral |

---

## 9. App shell

### 9.1 Sidebar (`shell/sidebar.tsx`, `sidebar-nav.tsx`)

- `--sidebar` background, right border.
- **Top:** logo mark + name + small tagline, then the org switcher as a bordered button (colored initial square).
- **Groups** with overline headers: **Build** (Agents, Knowledge, Automations), **Operate** (Conversations, Inbox, Channels/Integrations if routed, CRM/Contacts, Analytics), **Workspace** (Settings, Billing), **Platform** (Admin, *staff only; keep the existing gating*).
- **Item:** 18px lucide icon + label, 8×12 padding, radius 10.
  - Active: `accent-soft` background, `accent` text, weight 800, `aria-current="page"`.
  - Inactive: `muted`; hover `surface-2`.
- **Badges:** Inbox count = error-soft/error-text pill. Plan-limit handoffs are included in that count, as they are today.
- **Bottom:** trial/plan card (§8), then the user row (avatar, name, role).
- **Collapsed mode:** icons only with tooltips.

### 9.2 Topbar (`shell/topbar.tsx`)

- **Left:** page title + one-line subtitle (move page titles here, or keep them in the page header; be consistent).
- **Right:** search (⌘K), notifications (red dot), **theme toggle as a labelled button** (sun/moon + "Light"/"Dark"), then the page primary action (for example "New agent").

### 9.3 AI Builder rail (Dashboard only; small, no new backend)

❌ **Removed from `/dashboard` per 2026-09-28 feedback.** The component (`components/dashboard/
ai-builder-rail.tsx`) still exists, built and tested as described below, but is no longer
imported by the dashboard page — kept in case it's wanted elsewhere later rather than deleted.

- A 300px right rail on `/dashboard`, collapsible, collapse state remembered in `localStorage`.
- **Content:**
  - Header: "AI Builder" in `ai` colour with a sparkle icon.
  - Greeting bubble (`ai-soft`).
  - **4 quick-action cards that link to existing routes**, as fixed pastel gradient cards
    (2026-09-28 feedback, matching `designreference/UI_resign.jfif`'s coloured meeting cards —
    `--gradient-lavender/-mint/-blue/-peach` in `globals.css`, 135deg, text always
    `--on-pastel` in **both** themes, never the theme's own `--text`):
    - Build a new agent → agent create flow (lavender)
    - Connect a channel → the channels/credentials settings page (mint)
    - Create an automation → `/automations` (blue)
    - Review waiting chats → `/inbox` (peach)
  - One insight card (success-soft) **only if real data exists**; otherwise hide it.
- ❌ **No chat input yet.** The conversational AI builder is a separate future feature. Don't ship a fake input.
- Hidden on screens under 1280px. Hidden entirely for plans where the linked features are locked? No: show them with the lock treatment from §8 — swap the sub-label for the lock reason and route to `/billing/upgrade`, keep the gradient.

#### 9.3.1 "Today" gauge card (2026-09-28 feedback; ADR-100)

A semicircle gauge card on `/dashboard`, matching the reference's "Time Off 10 OUT OF 20" card.
Layout: a 3-column grid where the gauge card spans both KPI rows in column 1, and the four
existing KPI cards fill a 2×2 grid across columns 2–3 (`<TodayCard>` +
`<Gauge>`/`components/charts/gauge.tsx`, wired in `dashboard-stats.tsx`).

- **Title** "Today", subtitle = today's date, a "See all" link. (`/conversations` has no date
  filter to link into — it's a per-agent chat console, not the filterable browser §10.8
  assumed exists — so this links to the plain list rather than a query param the page would
  silently ignore.)
- **Gauge:** track `surface-3`, fill a gradient arc (`--accent` → `--gauge-to`, a new token:
  light `#60A5FA`, dark `#BFDBFE`), round caps. Centre = conversations started **today** (local
  midnight to now) — just the number; the "OUT OF {peak}" caption under it was removed per
  2026-09-28 feedback. `peak` (busiest single day in the last 30 days, floored at 10) still
  drives the arc's fill fraction (`today / peak`, capped at 100%) and the gauge's own
  accessible name, just not shown as on-screen text.
- **"Today" is the browser's timezone** (no org timezone is stored yet — ADR-100). The
  frontend computes local midnight and sends it as a UTC instant; refetches every 60s
  (`refetchInterval`), so the window itself rolls over once a real midnight passes without a
  page reload.
- ❌ **Removed per 2026-09-28 feedback:** the three `<StatusPill>` rows below the gauge
  (Resolved by AI / Handed to human / Unanswered). `today_snapshot` (ADR-100) still returns all
  three counts — nothing to fix server-side — the card just doesn't render them anymore.
- **States:** loading = skeleton gauge; zero conversations = an empty-looking arc plus a muted
  "No chats yet today."

#### 9.3.2 "Activity" bar chart (2026-09-28 feedback; ADR-101)

Replaces the dashboard's line chart (`UsageChart`) with a bar chart matching
`designreference/ACTIVITYNEW DAHSBOARDDESING.png` — `components/dashboard/activity-chart.tsx`
(header/period/metric state) + `components/charts/activity-bars.tsx` (presentational SVG),
backed by `GET /v1/analytics/timeseries` (ADR-101). Scoped to `/dashboard` only — `/analytics`
and the agent Analytics tab keep `UsageChart` and the old `/series` endpoint.

- **Header:** "Activity" title; big total for the selected period + unit (e.g. "6
  conversations"); a delta pill vs. the equal-length prior period ("▲ 8%" success / "▼ 5%"
  error / "New" when the prior period was zero — never a divide-by-zero ∞); a segmented
  Daily/Weekly/Monthly/Range control; a filter icon opening a metric menu (Conversations
  default, Messages, Tokens, Cost) — this replaces the old chart's metric tabs.
- **Periods:** Daily = last 14 days, Weekly = last 12 Monday-start weeks, Monthly = last 12
  months. Range opens two native `<input type="date">` fields; granularity auto-picks day
  (≤31 days), week (≤182 days) or month (beyond that) — see `granularityForPeriod` in
  `lib/activity-chart-math.ts`. The current bucket is labelled "Today"/"This week"/"This
  month" instead of its date.
- **Bar style** (tokens added to `globals.css`, both themes — see the file's own comment
  block for the exact hex/blend): `--bar-fill`/`--bar-stripe` (diagonal SVG `<pattern>`),
  `--bar-border`, `--bar-cap` (2px lighter top edge) for normal bars; `--bar-gradient-from/-to`
  (vertical gradient) for the selected bar, which also carries its value in `--on-accent` text
  and a delta-vs-previous-bar pill above it. Rounded (12px), ~30% gap, a 6px stub instead of a
  zero-height bar so a quiet day is visibly zero rather than a gap. Grows in on mount via the
  `animate-bar-grow` Tailwind keyframe (`motion-reduce:animate-none` disables it) — CSS-driven,
  not JS state, so there's no "set state in an effect" to avoid.
- **Selection:** click or hover a bar to select it; left/right arrow keys move the selection
  while the chart group has focus. Defaults to the current bucket on every dataset change
  (period/metric/range), reset during render rather than in an effect (React's documented
  "adjusting state when an input changes" pattern), so switching tabs never flashes the
  previous dataset's selection first.
- **Accuracy (ADR-101):** `/v1/analytics/timeseries?metric=&granularity=&from=&to=&tz=` buckets
  by the *caller's* local calendar day (`tz` = the browser's IANA zone, same "no stored org
  timezone yet" gap as ADR-100's Today gauge), zero-fills every bucket in range, and returns
  `previous_period_total` for the header's delta. Conversations = started in the bucket;
  messages = all messages sent in it; tokens/cost = summed from the same rows, matching
  `/series`'s existing definitions — see ADR-101 for why this is a new endpoint rather than
  `/series` with more params.
- **Dashboard density (2026-09-28 feedback):** tightened padding/gaps around the Today card,
  the KPI grid, the page header and this chart (220px → 180px chart height) so Today + the KPI
  row + Activity fit without scrolling halfway through the chart on a typical laptop viewport.

### 9.4 Theme behaviour

- Keep `next-themes` with `attribute="class"`. Default **light**. **Add "System"** to the toggle (Light / Dark / System) by setting `enableSystem` to true. Keep the stored preference.
- Keep `disableTransitionOnChange`. Only colour transitions of 150ms on hover elements; no global colour animation.
- **No flash on load:** keep the next-themes script. Verify with a hard refresh in both themes.

---

## 10. Page-by-page redesign (every route)

Every page gets the shell, the page header pattern (title + subtitle + one primary action), tokens-only colours, **both themes checked**, and loading, empty and error states. The specifics per page follow.

### 10.1 Auth — `(auth)`: login, signup, forgot-password, reset-password, magic, verify-email, oauth/callback, oauth/verify

- Centered 420px card on `bg`, logo above it, and a soft background glow (a very faint `accent-soft`/`ai-soft` radial, **auth pages only**).
- **OAuth buttons follow provider brand rules:**
  - **Google:** white/surface button, border, official multicolour "G" mark, "Continue with Google".
  - **Facebook:** filled `#1877F2`, white "f" mark and text, in both themes.
- Divider "or with email"; inputs per §8; primary "Sign in" / "Start free trial".
- **Errors:** error-soft banner with icon. Keep the existing messages and no-enumeration copy.
- **Signup:** a small "10-day free trial · no card needed" line with a success check. Reuse existing copy and limits from docs/18.
- Verify-email and magic pages: a big tinted icon (info or success), one line, a resend link.
- Callback pages: centered spinner (accent) + text.

### 10.2 Onboarding — `/onboarding`

- Stepper across the top: done = success, current = accent, future = neutral.
- **Step cards** with the relevant meaning colour: create agent = ai, connect channel = the channel grid using channel colours, add knowledge = info.
- A "Skip for now" ghost button.

### 10.3 Dashboard — `/dashboard` (see the prototype)

- **KPI row (4 cards):** Conversations (accent), Resolution rate (success), Tokens used (ai), Est. cost (warn). Each has icon, number, delta and sparkline.
- **Activity card:** line and area chart, series Conversations (accent) and Resolved by AI (success); range tabs 7/30/90 days (existing data); legend dots.
- **By channel card:** bar list in **channel colours**; channel names in channel text colour; "See all" link.
- **Recent conversations:** rows with contact avatar (channel-tinted), name, snippet, `<ChannelBadge>`, agent name, `<StatusPill>`, relative time.
- **Agents panel** (existing): agent rows with ai avatar chip, status pill, channel dots showing where each agent is live.
- AI Builder rail (§9.3). Trial card in the sidebar.

### 10.4 Agents — `/agents`

- Grid of agent cards: `ai-soft` icon chip, name, model/provider chip (neutral), status pill (Published = success, Draft = warn, Disabled = neutral), channel dots, 30-day conversations mini-stat, "Test" and "Edit" buttons.
- Toggle between card and list view. Empty state: ai-tinted with a "Create your first agent" primary action.
- The plan-limit lock on "New agent" uses the §8 locked treatment (trial = 1 agent).

### 10.5 Agent detail / builder — `/agents/[id]` (`components/builder/*`)

- **Header** (`builder-header`): agent name + status pill + publish button (primary) + "Test" (secondary).
- **Tabs** (segmented control, §8) across the existing builder tabs: Persona, Knowledge, Tools, Channels, Campaigns, etc. Keep them all.
- **Playground** (`playground.tsx`): chat preview.
  - Visitor bubbles: `surface-2`.
  - Agent bubbles: `ai-soft` with `ai` avatar.
  - System/tool events: neutral mono chips.
  - Sources: info chips.
- **Workflow canvas** (`builder/workflow-canvas`):
  - Canvas background `bg` with a dotted grid (`chart-grid` colour).
  - **Node colours by node type:** Trigger = info, AI/LLM step = ai, Tool / HTTP / n8n = warn-tinted border, Condition = neutral, Channel send = that channel's colour, End = success.
  - Nodes: `surface` card with a coloured 3px **top** bar, a type icon chip, and a title.
  - Edges: `border-strong`, selected edge `accent`. Minimap in `surface`.
- **Channels tab:** grid of `<ChannelIcon>` cards (connected = success pill; not connected = neutral + "Connect" button outlined in channel colour).
- **Model/temperature controls:** slider accent; token and cost hints `faint`.

### 10.6 Knowledge — `/knowledge`, `/knowledge/[id]`, `/knowledge/help-center`

- **Source list** with type icons: File = info, Website/URL = accent, Text = neutral, Q&A = ai.
- Ingestion status pills: queued/processing = info with a spinner, ready = success, failed = error with a retry button.
- **Detail:** chunks list in `surface-2` rows, mono metadata, search box.
- **Help center editor:** article list plus preview. Public help pages keep the **client's brand colour override** if one exists (see §10.17).

### 10.7 Automations — `/automations`

- List of workflows/n8n connections: trigger icon (info), last-run status pill, run count, success-rate mini bar (success/error split).
- **n8n connection card:** n8n brand orange `#EA4B71` dot + "n8n" label (tool context), connected pill.
- Locked-on-trial state per §8 (workflows and n8n are trial-locked).
- Run history drawer: timeline of steps with status colours.

### 10.8 Conversations — `/conversations` (see the prototype)

- **Filter chips row:** All + one chip per channel. Chip text = channel text colour, active chip = channel soft background + channel-coloured border, with a count per chip.
- **Secondary filters:** status (StatusPill colours), agent, date range.
- **Table/list:** contact (channel-tinted avatar), snippet, `<ChannelBadge>`, agent, `<StatusPill>`, time. Row click opens the thread.
- **Thread view:** same bubble rules as the playground (§10.5). The header shows `<ChannelBadge>` and the contact.

### 10.9 Inbox — `/inbox`, `/inbox/[cid]` (see `designreference/inboxdesign.png`)

- **Three panes:**
  - Queue list: each item has a channel dot, contact, waiting time (amber over 5 min, red over 30 min) and a reason badge. **"Plan limit" reason** = neutral lock chip (from docs/18).
  - Thread (center)
  - Contact/CRM side panel (right)
- **Composer:** surface with border, "Reply as human" primary, AI-suggested reply chips in `ai-soft` (if the feature exists), canned responses and macros menu.
- **Handoff banner** at the top of the thread: warn-soft "Bot paused — you're replying" with a "Hand back to AI" ai-coloured button.
- **Unread:** bold name + accent dot.

### 10.10 CRM / Contacts — `/contacts`, `/contacts/[id]`

- Table: avatar (channel-tinted by last channel), name, channels used (multiple `<ChannelDot>`s), tags (neutral chips), last seen, lifecycle stage pill (lead = info, customer = success, churn-risk = warn).
- **Detail:** header card, identity per channel (a row per channel in its colour), conversation timeline (channel-coloured markers), notes.

### 10.11 Analytics — `/analytics` (`components/analytics/*`)

- Top KPI row (same stat-card pattern).
- `channel-breakdown` → channel colours (bars plus donut option).
- `agent-breakdown` → ai-toned bars.
- `team-performance` → accent/success.
- `bar-list` → series tokens.
- Date-range segmented control. Export button (secondary).
- All charts follow §4.5, including tooltips and a "No data yet" empty chart state (dotted grid + muted text, not a blank box).

### 10.12 Billing — `/billing/upgrade`

- Plan cards: current plan with a "Current" neutral pill, recommended plan with an accent border + "Recommended" accent-soft pill. Feature checklist with success checks; locked features greyed.
- Trial status banner at the top (ai-soft, days-left amber pill).
- Keep the existing contact email/WhatsApp CTA (`NEXT_PUBLIC_UPGRADE_*`). The WhatsApp CTA uses the WhatsApp channel colour.

### 10.13 Settings — `/settings/*` (profile, org, api-keys, credentials, webhooks, canned-responses, macros, audit)

- **Settings layout:** left sub-nav (same active style as the sidebar) + content cards. One card per setting group, with a save bar that sticks at the bottom when dirty.
- **API keys:** mono key prefix, created/last-used `faint`, revoke = destructive ghost → confirm dialog.
- **Credentials** (channel and provider keys): grouped by kind.
  - **Channel credentials use channel colours** (icon + name).
  - LLM providers use neutral chips with the provider name (no invented brand colours).
  - Status: valid = success, invalid = error, unset = neutral.
- **Webhooks:** endpoint list with a delivery-success mini bar; the deliveries log uses status colours (2xx success, 4xx warn, 5xx/timeout error).
- **Canned responses / macros:** list + editor. Variables highlighted as ai-soft chips.
- **Audit log:** table. Action type chip: create = success, update = info, delete = error, auth = ai, plan/limit = warn. Mono actor/IP.
- **Org:** members table with role pills (owner = ai, admin = accent, editor = info, viewer/operator = neutral).

### 10.14 Admin — `/admin` (staff only, keep the gating)

- Same shell and a **"Staff" warn-soft pill** in the header, so staff never confuse it with a customer view.
- Org table with plan pills: legacy = neutral, trial = ai, trial_expired = error, paid tiers = success. Include usage bars.

### 10.15 Invitations — `/invitations/accept`

- Auth-style centered card, org avatar, role pill, accept (primary) / decline (ghost).

### 10.16 Docs site — `(docs)/docs/*`, `/docs/api/reference`

- **Same tokens and fonts**, a prose theme mapped to tokens (the typography plugin already uses `solid()`, so update those vars). Code blocks: `surface-2` + mono; inline code `surface-3`.
- **Callouts:** note = info, tip = success, warning = warn, danger = error.
- API reference: HTTP method pills (GET = success, POST = info, PATCH/PUT = warn, DELETE = error).

### 10.17 Public help center — `/help/[agentKey]`, `/help/[agentKey]/[slug]`

- Customer-facing. **If the agent/org has a brand colour setting, it overrides `--accent` on these pages only.** Otherwise use our tokens.
- Search hero, article cards, and a light theme by default. Dark only if the visitor's system prefers it.

### 10.18 Vault — `(vault)/vault/*` (internal)

- Tokens and fonts only; minimal restyle. No new structure.

### 10.19 Root `/` page

- If it's a redirect, leave it. If it renders a landing page, apply the tokens and fonts only. The marketing site redesign is out of scope.

### 10.20 Chat widget (embeddable) — **out of scope**

- The widget renders on **clients' websites with their own branding**. Don't apply app tokens to it. Only check that its existing brand-colour setting still works.

---

## 11. Dark mode rules (checklist)

- Surfaces are pure neutral (R=G=B, no navy tint) and step up in lightness: `bg #000000` <
  `sidebar #0A0A0A` < `surface #111111` < `surface-2 #1A1A1A` < `surface-3 #222222`. Popovers
  use `surface-2`.
- **Pure black `--bg` is intentional here** (matching the reference dashboard exactly) — the
  usual "no pure black" guidance applies to *text*, not the page background. No pure white
  backgrounds in dark mode (images excepted).
- Colours get **brighter**, not more saturated. Soft backgrounds are low-alpha blends (the pre-blended values in §4.3 and §5).
- No drop shadows in dark mode; separate surfaces with borders.
- The Instagram gradient stays the same in both themes. It's a brand element.
- Check that every chart gridline, axis label and tooltip is visible in dark mode.

---

## 12. Accessibility and quality bar

- Text contrast: AA everywhere (§3.6). Run an automated check (axe via Playwright) on every route, in **both themes**.
- Focus-visible ring on every interactive element. Tab order is sane; the sidebar and the rail are landmarks (`nav`, `aside`, `main`).
- Real `<button>`/`<a>`/`<label>`; no clickable divs. Icon-only buttons have `aria-label`.
- Status is never colour-only (pill text or an icon is always present).
- Respect `prefers-reduced-motion` (disable sparkline/chart draw animations).
- **Performance:** no new heavy dependencies. Fonts through `next/font` (self-hosted, `display: swap`). Inline SVG charts.

---

## 13. Guardrails (do not break)

- **Keep every `data-testid`, `role`, accessible name, route and API call unchanged.** E2E and unit tests depend on them. If a test asserts on a class name or colour, update the test to assert on role/text instead, and note it in the commit.
- Don't touch `apps/api`, auth logic, plan-limit logic, metering, or the widget's runtime.
- Don't rename token names that are already used (`accent`, `ember`, `surface`…). Add new ones instead.
- No hard-coded hex or Tailwind palette classes in components after R8. Add a lint check or grep test that fails on `#[0-9a-f]{6}` or `-(red|blue|…)-[0-9]{3}` inside `src/components` and `src/app` (exceptions allowed for brand SVG files).
- **Product name:** read from one constant (rename pending). Don't add new hard-coded "BotForge" strings.
- Record decisions in `docs/DECISIONS.md` (ADR: "UI redesign — dual theme tokens"). Update `docs/05-FRONTEND.md` with the new token, channel and status systems, and `docs/PROGRESS.md` per phase.

---

## 14. Phases (execute in order, commit after each)

| Phase | Scope | Done when |
|---|---|---|
| **R0 — Baseline** | Playwright script that screenshots **every route in §10, in light and dark**, into `var/redesign/before/`. Grep report of hard-coded colours, `accent-soft` text usages, and channel/status ad-hoc maps | Screenshots + report committed (screenshots git-ignored; the report in `docs/`) |
| **R1 — Tokens + fonts** | Rewrite `globals.css` values (§4), add the new tokens (ai, *-text, *-soft, sidebar, chart, channel vars), update `tailwind.config.ts`, swap fonts (§6), fix `accent-soft` text usages, enable "System" theme | App builds; every page renders in both themes with no invisible text; tests green |
| **R2 — Primitives** | Restyle `components/ui/*`, add `StatusPill`, `STATUS_TONE`, `CHANNEL_META`, `ChannelBadge`/`Dot`/`Icon`/`Text`, chart primitives (line+area, bar-list, donut, sparkline, tooltip) | Vitest for the new components (renders per channel/status, unknown fallback); visual check in both themes |
| **R3 — Shell** | Sidebar, topbar, org switcher, user menu, theme toggle (Light/Dark/System), trial card, collapsed sidebar, mobile drawer | Keyboard-navigable; staff-only Admin still gated; e2e green |
| **R4 — Dashboard + Analytics** | §10.3, §10.11, AI Builder rail (§9.3) | Matches prototype intent in both themes |
| **R5 — Operate pages** | Conversations, Inbox, Contacts (§10.8–10.10) | Channel colours everywhere a channel appears; handoff/plan-limit states styled |
| **R6 — Build pages** | Agents, Agent builder (all tabs + playground + workflow canvas), Knowledge, Automations (§10.4–10.7) | Node colour system on the canvas; locked states per §8 |
| **R7 — Everything else** | Auth, onboarding, billing, settings (all sub-pages), admin, invitations, docs, help center, vault, root (§10.1, 10.2, 10.12–10.19) | Every route in the §10 list restyled |
| **R8 — QA + lock-in** | Re-run the R0 screenshots into `var/redesign/after/`; axe contrast check both themes; the hard-coded-colour lint check; full test suite; docs updated | Zero axe contrast violations, zero hard-coded colours, all tests green, before/after screenshot pairs reported to the human |

**Report format after each phase** (plain English, short):

- What changed
- Screenshots path
- Anything that looked wrong and how you fixed it
- Anything you skipped and why

---

## 15. Testing strategy

- **Unit (Vitest):** `channelMeta` / `STATUS_TONE` fallbacks; `ChannelBadge` renders the correct label for every channel key and for unknown; `StatusPill` maps every status; the theme toggle cycles Light → Dark → System.
- **E2E (Playwright):**
  - Existing flows unchanged and green.
  - New `e2e/40-theme.spec.ts`: toggle the theme, reload, and the preference persists; no flash (check the `html` class before hydration).
  - Screenshot pass per route in both themes (R0/R8).
  - axe accessibility scan per route in both themes; fail on **contrast** and **name/role** violations.
- **Manual (human):** click through the dashboard, conversations, inbox and agent builder in both themes after R4, R5 and R6.

---

## 16. Decisions (already made, and open)

**Made (record in DECISIONS.md):**

- Primary = blue (`#2563EB` light / `#60A5FA` dark). AI = purple. 5 meaning colours only.
- Channel colours only in channel contexts, with separate text shades for AA.
- Font: Plus Jakarta Sans + JetBrains Mono.
- The AI Builder rail ships as **quick links only**; no chat input until that feature exists.
- The widget and client-branded public help pages keep client branding.

**Open (ask the human only if blocking; otherwise use the default in brackets):**

1. **Product name** is being changed. [Default: keep the current name via the single constant; don't block.]
2. **Default theme for new users.** [Default: Light, with System available.]
3. **Table style**: zebra or hover-only. [Default: hover-only + dividers.]

---

## 17. Out of scope

- The marketing website, the chat widget runtime, email templates (a separate pass later), any backend or API change, the conversational AI builder feature, and new analytics metrics.

# 27 — Widget Styles + Interactive Blocks: Implementation Prompt

> Paste-ready prompt for Claude Code. Visual + behavior reference:
> `designreference/vicero-widget-lab.html` (open it in a browser first).

---

## 0. Your role

- You are working inside the existing Vicero repo. Read `CLAUDE.md` first and follow it (Definition of Done, Conventional Commits, DECISIONS.md, PROGRESS.md).
- Load these repo skills before coding: `engineering-verification` (add-feature mode) and `session-log`.
- Also read: `docs/05-FRONTEND.md`, `docs/04-API-SPEC.md`, `docs/17-AGENTIC-RUNTIME-AND-BUILDER.md`, `docs/26-IMAGES-AND-PRODUCT-CATALOG.md`, `docs/11-SAFETY-GUARDRAILS.md`, `packages/widget/README.md`, `apps/web/content/docs/widget/install.mdx`.

---

## 1. Goal (one paragraph)

Add **10 new selectable widget designs** and **interactive chat blocks** (buttons, cards, forms, date picker, rating…) to the existing widget and to the Widget Designer page. The working AI agent, streaming chat, citations, guardrails, human handoff and every currently embedded widget must keep working **exactly as today**.

---

## 2. Absolute rules — do NOT break these

- **Zero visual or behavior change for existing agents.** An agent that never touches the new settings must render pixel-for-pixel like today (same classes, same CSS, same launcher).
- **Additive only.** No renamed or removed config keys, API fields, SSE events, DOM classes, or `window.Vicero` methods.
- **Keep these DOM hooks** (e2e tests depend on them): `.bf-launcher`, `.bf-pill`, `.bf-square`, `.bf-panel`, `.bf-bubble`, `.bf-bot`, `.bf-user`, `.bf-x`, `.bf-transparent` host class.
- **Keep the transport untouched:** `POST /v1/public/agents/{public_key}/chat` SSE stream, conversation id handling, retry-on-new-conversation, citations rendering, the guardrail "discard everything streamed this turn" logic (docs/11 §4-L5, ADR-049), WebSocket subscription (`ensureSubscription`), campaigns, `setUser`, session persistence in `localStorage`.
- **Keep the SDK:** `Vicero.open/close/toggle/sendMessage/setUser/on` unchanged.
- **Keep Shadow DOM isolation.** No styles leak to or from the host page.
- **Keep the builder preview protocol:** `postMessage({type:"bf-preview-config", config})` into `widget-preview.html` must still update live and never change open/closed state.
- **Never hand-edit built files** (`packages/widget/dist/*`, `apps/web/public/widget.*`). Edit `packages/widget/src/*` and run the build (read `packages/widget/build.mjs` to see how outputs are produced and copied).
- **No new runtime dependencies** in the widget. It stays a single dependency-free vanilla bundle.
- **No remote web fonts.** The demo uses Geist/Jakarta from Google Fonts for preview only. In the real widget map presets to the existing font keys (`system`, `inter`, `arial`, `georgia`, `courier`). Loading third-party fonts inside customer sites is a privacy and performance problem.
- **Security for blocks:** render all block text with `textContent` (never `innerHTML` for agent-supplied strings), allow only `https:` URLs, cap sizes (see §6), never execute anything from a block.

---

## 3. What exists today (verify by reading, do not assume)

- Widget source: `packages/widget/src/widget.js` (~900 lines) + `widget.css`; build script `packages/widget/build.mjs`.
- Theme is applied through CSS variables (`--bf-*`) in `applyTheme()`; static stylesheet in `styles()`; launcher variants in `launcherContent()`; panel built in `build()`.
- Config is fetched from `GET /v1/public/agents/{public_key}/config`.
- Backend:
  - `apps/api/app/modules/public/schemas.py` → `WidgetConfigIn` (camelCase, `extra="forbid"`), `WidgetTheme` (snake_case public response), enums `WIDGET_STYLES`, `FONT_FAMILIES`, `FLOATING_BUTTON_STYLES`, `INPUT_BAR_BUTTONS`.
  - `apps/api/app/models/widget_configs.py` → `WidgetConfig.theme` is **JSONB, unversioned, a save is live**. No migration is needed for new theme keys.
  - Find where `WidgetConfigIn` is merged and where `WidgetTheme` is built for the public config (search for `_theme`, `widget-config`).
- Tests to keep green: `apps/api/tests/test_widget.py`, `test_widget_config.py`, `test_public_contacts_widget_path.py`, `apps/web/e2e/03-widget.spec.ts`, `apps/web/e2e/09-widget-customization.spec.ts`.
- Find the Widget Designer page in `apps/web/src` (search for `inputBarButtons`, `floatingButtonStyle`, `widget-config`, `widget-preview`). Existing controls stay.

---

## 4. The 10 designs (from the demo)

| `widgetLayout` value | Name | Structural traits (everything else is just colors) |
|---|---|---|
| `classic` | Classic | Brand-color header, normal bubbles |
| `glass` | Glass | Translucent blurred panel, dark pill input |
| `hairline` | Hairline | No bubbles, thin rules, options as a ruled list with arrows |
| `paper` | Paper | Serif, centered title, lettered options (a. b. c.) |
| `pebble` | Pebble | Big round bubbles, centered avatar header |
| `console` | Console | Mono type, `[1]` numbered options, dashed borders |
| `concierge` | Concierge | Home screen with greeting + topic tiles, collapses after first message |
| `command` | Command | Search bar on top, centered on screen, keyboard hints |
| `drawer` | Side drawer | Full-height right/left panel with Chat / Help / Status tabs |
| `block` | Bold block | Thick outlines, hard shadows |

Design decisions (already made, follow them):

- **New field `widgetLayout`** (enum above, `null` = today's rendering). Do **not** reuse or extend `widgetStyle` (`solid | transparent` stays as-is, it is a separate surface treatment and already live for customers).
- **New field `cornerRadius`**: int 0–28, `null` = legacy radii.
- **New field `density`**: `compact | comfortable | roomy`, `null` = legacy spacing.
- Presets (accent, font, radius, launcher, mode) are applied **by the designer UI when the user picks a layout** and saved as normal config values. At runtime the widget only reads config. No hidden preset logic in the widget.
- Colors keep using the existing keys (`primaryColor`, `backgroundColor`, `textColor`, `bubbleColor`, `typingAreaColor`, `floatingButtonColor`) and the existing auto contrast (`onColor`).
- `drawer` and `command` change the panel's fixed positioning (full height / centered). Keep the existing `max-width:767px` full-screen mobile behavior for all layouts.
- Header name/subtitle already come from the agent. Do not add new fields for them.

---

## 5. Implementation plan — 3 phases, each ends green

### Phase 1 — Visual layouts (no chat-protocol change)

1. **API**
   - Add `widgetLayout`, `cornerRadius`, `density` to `WidgetConfigIn` with validators (enum / range, `None` allowed).
   - Add `widget_layout`, `corner_radius`, `density` to `WidgetTheme` (all default `None`).
   - Map them through the merge and public-config code paths. Unknown values are rejected on write with the existing typed error.
2. **Widget**
   - Add layout CSS as scoped rules keyed on a class on the panel (e.g. `.bf-l-glass`). Include only rules for layouts; keep legacy rules untouched.
   - `applyTheme()` sets `--bf-radius` / density variables **only when the config provides them**.
   - Concierge home screen and Drawer tabs are the only layouts that add DOM. Build that DOM only for those layouts. Drawer "Help" and "Status" panes show content from existing agent data (suggested prompts as help rows; status can be hidden if no data exists). Do **not** hard-code demo copy.
   - Respect `prefers-reduced-motion`, keep visible focus rings, `role="dialog"`, `aria-live` on the message list, `Esc` closes (all layouts), Command layout supports ↑/↓/Enter over suggestion rows.
   - If the demo uses `color-mix()` / `:has()` / container queries, check browser support for the widget's target browsers and add fallbacks where needed.
3. **Preview**: `widget-preview.html` must render all 10 layouts through the existing `bf-preview-config` message.
4. **Designer UI** (`apps/web/src`, the existing widget page)
   - Add a **Layout picker** (visual cards, like the left rail in the demo) above the existing controls.
   - Add **Corners** slider (0–28) and **Spacing** segmented control.
   - Picking a layout applies that layout's preset values (see demo `STYLES[].preset`) but the user can still change everything. Add "Reset to layout defaults".
   - All existing controls (colors, mode, font, launcher, position, input bar buttons, logo, allowed sites) keep working and keep their saved values.
5. **Tests (must exist and pass)**
   - pytest: legacy payload still valid; new fields validated; invalid layout rejected; public config returns `null` defaults for an untouched agent.
   - Playwright: for **each of the 10 layouts** → embed, open, send a message, receive the streamed answer, citations still render, close with `Esc`, mobile viewport is full screen. Screenshot per layout.
   - **Regression snapshot:** an untouched agent's widget computed styles (launcher + panel) are identical to before this change.
   - Existing `03-widget` and `09-widget-customization` specs pass unmodified.
6. Commit: `feat(widget): 10 selectable layouts + corner radius + density`.

### Phase 2 — Interactive blocks (opt-in per agent)

Goal: the agent can answer with tappable UI instead of only text. Default **off**. Agents with it off behave exactly as today.

1. **Block protocol (additive)**
   - New SSE event `block` on the existing chat stream. Old widgets ignore unknown events, so cached old `widget.js` keeps working.
   - Block types and caps:

     | `type` | Purpose | Key fields | Caps |
     |---|---|---|---|
     | `options` | Quick replies / single choice | `items[{id,label,sub?}]`, `multi?`, `progress?{step,total}` | ≤ 8 items, label ≤ 60 chars |
     | `cards` | Product / item carousel | `items[{id,title,subtitle?,image_url?,price?,badge?,actions[]}]` | ≤ 6 cards, https images only |
     | `form` | Collect details | `fields[{id,label,type:text/email/tel,required}]`, `submit_label` | ≤ 5 fields, no password/card/ID types |
     | `datetime` | Day + time slots | `days[]`, `slots[]` supplied by the agent/tool | ≤ 14 days, ≤ 12 slots |
     | `counter` | Quantity / guests | `label`, `min`, `max`, `default` | max ≤ 20 |
     | `rating` | 1–5 stars + reason chips | `prompt`, `reasons_good[]`, `reasons_bad[]` | — |
     | `steps` | Status timeline | `steps[{label,state,time?}]`, `eta?` | ≤ 8 steps |
     | `handoff` | Human agent joined card | `agent_name`, `state` | — |

   - Every block has `id` and `fallback_text` (plain-text version). Inbox, transcripts and exports show `fallback_text`.
   - **User interaction goes back as a normal user chat message** with an extra `block_reply: {block_id, value}` field. The LLM loop sees a plain user message, so the agent runtime and guardrails need no special path.
   - Interacted blocks become read-only and show the chosen value. After reload they render in resolved state.
2. **Agent side**
   - Read `docs/17` and the agentic runtime code. Add **tools** the agent can call: `present_options`, `present_cards` (reads from the product catalog in docs/26), `request_details`, `pick_datetime`, `ask_rating`. Register them **only** when the agent's setting `interactiveBlocks` is true.
   - Guardrails: a turn discarded by the safety layer must also discard its blocks. Validate block payloads server-side against the schema and caps; drop invalid blocks and fall back to `fallback_text`.
   - Persist blocks with the assistant message (use the existing message metadata JSONB if present; otherwise one additive nullable JSONB column via Alembic with a working downgrade).
3. **Widget side**
   - Block renderers for all types using the shared building blocks from the demo (`options`, `product card`, `days/slots`, `counter`, `form`, `receipt/steps`, `stars`). Each respects the active layout (CSS handles the look).
   - Form validation inline. Disable controls after submit. Keyboard accessible (arrow keys in carousels/options, Enter to select).
   - Unknown block type → render `fallback_text` as a normal bot bubble.
4. **Designer UI**: add an **Interactive blocks** section (master switch + per-type toggles, same as the demo). Saved as `interactiveBlocks` (JSON: type → bool) on the widget config; master switch gates the tools in the agent runtime.
5. **Tests**
   - pytest: schema validation and caps for every block type; invalid blocks dropped; discarded turn drops blocks; tools not registered when the flag is off; agent with flag off produces byte-identical request/stream behavior to before.
   - Playwright: with a stubbed agent that emits each block type, verify render, click-through, reply goes back as a user message, resolved state after reload, old-style agent unaffected.
6. Commit: `feat(widget): interactive blocks (opt-in)`.

### Phase 3 — Docs, hardening, handoff

- Update: `packages/widget/README.md`, `apps/web/content/docs/widget/install.mdx`, `docs/04-API-SPEC.md`, `docs/05-FRONTEND.md`, `docs/DECISIONS.md` (ADRs: separate `widgetLayout` field, additive `block` SSE event, blocks as user messages, no web fonts), `docs/PROGRESS.md`, `docs/SESSION-HANDOFF.md`.
- Report widget bundle size (raw + gzip) before and after. Keep Phase 1 growth small; if large, inject only the active layout's CSS.
- Add any new env var to `.env.example` and `docs/ENV.md` (none expected).

---

## 6. Edge cases to handle

- Customer page has a cached **old** `widget.js` + agent saved with new fields → must still work (unknown keys ignored).
- New `widget.js` + agent with **no** new fields → identical to today.
- `drawer` / `command` layouts on mobile → fall back to the normal full-screen panel.
- Very long names, RTL text, 320 px wide screens, huge cart/list payloads, missing images (neutral placeholder, no layout jump).
- Form fields must never log or store values beyond the normal message pipeline; no password/card/Aadhaar-type fields.
- Block replies must not bypass rate limiting or guardrails (they are normal chat turns).
- Concierge home screen disappears after first message and must not reappear on reload of an existing conversation.
- Dark and light mode for every layout. Auto contrast text on any user-chosen color.

---

## 7. Verification checklist (run all, report results)

- [ ] `ruff`, `mypy`, `pytest` (full API suite) green
- [ ] `tsc --noEmit`, `eslint`, `vitest` green
- [ ] Playwright: all existing specs + new per-layout and block specs green
- [ ] Widget build runs; built outputs are updated by the build, not by hand
- [ ] Untouched-agent snapshot identical to pre-change
- [ ] Manual: open `widget-preview.html`, cycle all 10 layouts in light + dark, desktop + 390 px mobile
- [ ] Manual: real agent answers with streaming, citations, quick replies, file attach, handoff, campaigns still work
- [ ] Bundle size before / after reported

---

## 8. Final report format (point-wise, simple words)

- What changed, per phase (files touched)
- What was tested and the results
- Anything skipped or risky, and why
- Exact steps to roll back (all changes are additive, so reverting the commits is enough)

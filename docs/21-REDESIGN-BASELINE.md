# 21 — UI redesign baseline (R0 audit)

Companion to `docs/20-UI-REDESIGN-DUAL-THEME.md`. Captured 2026-09-26 before any redesign code.

## Screenshots

`var/redesign/before/{light,dark}/<route>.png` — 35 routes × 2 themes at 1440px, full page (git-ignored).
Regenerate or compare with the same script:

```
# from apps/web, against the keyless E2E API (see CLAUDE.md §12) on :8010 and web on :3001
E2E_API_URL=http://localhost:8010 REDESIGN_SHOTS=before npx playwright test e2e/redesign-shots.spec.ts
E2E_API_URL=http://localhost:8010 REDESIGN_SHOTS=after  npx playwright test e2e/redesign-shots.spec.ts   # R8
```

`after` also runs an axe `color-contrast` check per route/theme and fails on any violation
(report at `var/redesign/after/contrast.txt`). Without `REDESIGN_SHOTS` the spec is skipped, so
it never runs in the normal suite.

Not captured (need special state): expired-trial and plan-locked variants, OAuth callback
spinners, the inbox handoff banner (the fake LLM never hands off). R5/R6/R7 cover these with
stubbed API responses in component tests.

## Hard-coded colours (outside `globals.css`)

| Where | What | Action |
|---|---|---|
| `components/auth/oauth-buttons.tsx` | Google "G" (4 fills) + Facebook `#1877F2` | **Keep** — provider brand marks (docs/20 §10.1). Allow-listed in the R8 check |
| `components/builder/tabs/channels-tab.tsx:164-170,438-439` | Widget-customisation fallbacks and backdrop swatches | **Keep** — these are the *client's widget* colours, not app UI (docs/20 §10.20). Allow-listed |
| `lib/api/agent-mapping.ts:65` | Default widget `primaryColor` `#1F2937` | **Keep** — widget data default, not UI. Allow-listed |
| `components/builder/tabs/model-tab.test.tsx:76` | Test fixture | Keep |
| `lib/providers.tsx:17` | Comment only | Reword in R1 |

Tailwind palette classes (`bg-red-500`, …): **none**. The app is already token-only.

## `accent-soft` / `ember-soft` usage

86 references. **83 are `text-accent-soft` / `text-ember-soft`** (text colour), 1 is the focus
outline in `globals.css`, 2 are the token definitions. There are zero background/border uses.
R1 therefore swaps all text usages to `text-accent` mechanically and repurposes `--accent-soft`
as the background tint. Files: 50 (auth pages, builder tabs, dashboard panels, inbox, plan,
settings nav, `ui/badge`, `ui/button`, …).

## Ad-hoc channel / status maps to fold into the shared systems

| File | Map | Replaced by |
|---|---|---|
| `lib/channel-meta.ts` | `CHANNEL_META`, `channelMeta()` (labels + icons, no colours, no `email`) | Extended in place with colour vars + `email` (see ADR) |
| `lib/display.ts` | `channelLabel` (mock `Channel` type), `agentStatusMeta`, `convoStatusMeta`, `apiAgentStatusMeta`, `apiConvoStatusMeta` | `STATUS_TONE` in `lib/status.ts` |
| `components/shared/channel-icon.tsx` | mock-`Channel` icon map | `<ChannelIcon>` backed by `channelMeta` |
| `components/inbox/inbox-view.tsx`, `reply-box.tsx`, `channel-not-connected.tsx`, `attention-queue.tsx` | channel/status branching | `<ChannelBadge>` / `<StatusPill>` |
| `components/dashboard/conversations-panel.tsx`, `analytics/channel-breakdown.tsx`, `contacts/*` | channel labels/icons | shared channel components |
| `app/(app)/billing/upgrade/page.tsx`, `builder/tabs/channels-tab.tsx` | WhatsApp/Instagram branches | shared channel components |

## Environment notes for anyone re-running this

- The stock `:8000` docker API is self-serve (trial workspace auto-created at signup), so the
  E2E `createAccount` helper 402s against it. Use the keyless E2E API on `:8010`
  (`LLM_FORCE_FAKE=true SELF_SERVE_ENABLED=false ALLOW_SELF_SERVE_ORGS=true`) and start the dev
  web with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8010`. CORS only allows `localhost:3001`,
  which the `botforge-web-1` docker container also binds — stop it while iterating on source.

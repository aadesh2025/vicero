# @vicero/widget

The embeddable Vicero chat widget — a single, dependency-free `widget.js` that renders a
launcher bubble + chat panel inside a **Shadow DOM** (full style isolation) and streams
replies over the public chat endpoint.

## Embed

```html
<script
  src="https://YOUR_HOST/widget.js"
  data-agent="PUBLIC_KEY"
  data-api="https://YOUR_API_HOST"
  defer></script>
```

- `data-agent` (required): the agent's `public_key` (`bf_pub_…`).
- `data-api` (optional): the Vicero API base. Defaults to `http(s)://<page-host>:8000`.

The widget loads `GET /v1/public/agents/{public_key}/config` for its theme, welcome message,
and quick replies, then chats over `POST /v1/public/agents/{public_key}/chat` (SSE stream).

## Features

Launcher bubble (bottom-right/left), streaming tokens with a typing indicator, sanitized
markdown, quick-reply chips, file attach (text files are inlined as context), custom accent
color + light/dark mode, "Powered by" toggle, session persistence (`localStorage`), mobile
full-screen, and keyboard support (Enter to send, Esc to close).

## Layouts, corners, spacing (docs/27)

Ten selectable designs, chosen in the dashboard (Channels tab, "Design") and saved on the widget
config, so an embed never changes: `classic`, `glass`, `hairline`, `paper`, `pebble`, `console`,
`concierge`, `command`, `drawer`, `block`. Three additive config keys (all `null` = the original
look, byte-for-byte): `widgetLayout`, `cornerRadius` (0-28 px) and `density`
(`compact | comfortable | roomy`). `widgetStyle` (`solid | transparent`) is separate and only applies
to the original look; a layout owns the whole surface.

- The widget carries **no preset logic**. Picking a layout in the designer saves ordinary values
  (accent, font, radius, launcher, colors); the widget only reads what is saved.
- Layout CSS lives in one extra `<style>` that exists only for the active layout. An agent with no
  layout/radius/density gets no class, no CSS variable, no extra `<style>` and no extra DOM.
- Only Concierge (home screen), Drawer (Chat / Help tabs) and Command (keyboard hints) add DOM.
  Help rows are the agent's suggested prompts; there is no hard-coded copy and no Status tab.
- Fonts map to the existing keys (`system`, `inter`, `arial`, `georgia`, `courier`). **No remote web
  fonts** are ever loaded inside a customer's site.
- Esc closes from anywhere in the panel; Command walks the suggestion rows with the arrow keys;
  phone widths (<= 480 px) are full screen for every layout.
- DOM hooks kept for tests and host CSS: `.bf-launcher`, `.bf-pill`, `.bf-square`, `.bf-panel`,
  `.bf-bubble`, `.bf-bot`, `.bf-user`, `.bf-x`, host class `.bf-transparent`.

## Interactive blocks (docs/27, opt-in per agent)

When the agent's `interactiveBlocks.enabled` switch is on, the agent can answer with tappable UI:
`options`, `cards`, `form`, `datetime`, `counter`, `rating`, `steps`, `handoff`. Off (the default)
nothing below runs.

- **Stream:** a new SSE event `{type: "block", block: {...}}` on the existing chat stream, sent after the
  reply text and only for a turn the output guard let through. An older cached `widget.js` ignores it.
- **A tap is a normal user message.** The widget posts `message` (what the agent reads) plus
  `block_reply: {block_id, value}` (so the server can mark the block answered). Guardrails and rate
  limits see an ordinary turn.
- **Resolved state:** answered blocks are read-only and show the choice; after a reload the widget
  fetches `GET /v1/public/agents/{key}/conversations/{id}/messages?visitor_id=` (blocks-enabled agents
  only) and renders them resolved.
- **Safety:** every block string is written with `textContent`; only `https:` images (or the widget's
  own API origin for signed catalogue photos) load; caps are enforced server-side; forms never accept
  password / card / ID fields. An unknown or malformed block degrades to its plain-text version.
- Keyboard: arrows move through options, days, slots and carousel cards; Enter / Space activates.

## JS SDK

`window.Vicero` is available after load:

```js
Vicero.open();
Vicero.close();
Vicero.toggle();
Vicero.sendMessage("Hello!");
Vicero.setUser({ id: "u_123", name: "Sam", email: "sam@acme.com", metadata: { plan: "pro" } });
Vicero.on("ready", ({ config }) => {});
Vicero.on("open", () => {});
Vicero.on("message", ({ role, content }) => {}); // user + assistant turns
Vicero.on("response", ({ content }) => {});        // final assistant reply
```

## Build

```bash
cd packages/widget && npm run build
```

Copies (and minifies with esbuild if available) `src/widget.js` → `dist/widget.js` and
`apps/web/public/widget.js` so the web app serves it at `/widget.js`. Never edit the built files.

Size (shipped, unminified because `build.mjs` does not find esbuild from this package):
40,040 B (12.4 KB gzip) before docs/27, 102,843 B (27.1 KB gzip) after. With esbuild minification the same
sources are 22.8 KB → 66.8 KB (8.0 KB → 18.9 KB gzip).

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
`apps/web/public/widget.js` so the web app serves it at `/widget.js`.

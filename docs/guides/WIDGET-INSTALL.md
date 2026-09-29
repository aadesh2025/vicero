# Widget install guide

The Vicero chat widget is a single, dependency-free `widget.js` served by the web app at
`/widget.js`. It renders inside a **Shadow DOM** (so it never inherits or leaks your site's CSS),
streams replies from the public API, and exposes a small `window.Vicero` SDK.

## 1. Get your agent's public key

1. In the dashboard, open your agent → **Channels** tab.
2. Configure appearance (colour, position, launcher text, mode, branding) — it autosaves.
3. Copy the **embed snippet**. The `data-agent` value is the agent's **public key**; it is safe
   to expose in client-side HTML (it only grants access to that one published agent's public chat).

## 2. Add the snippet to your site

Paste this just before `</body>` on any page:

```html
<script
  src="https://YOUR_WEB_HOST/widget.js"
  data-agent="pk_your_agent_public_key"
  data-api="https://YOUR_API_HOST"
  defer
></script>
```

- `src` — where `widget.js` is hosted (your Vicero web host; `http://localhost:3000/widget.js` in dev).
- `data-agent` — **required**. The agent's public key from the Channels tab.
- `data-api` — the Vicero API base URL. If omitted it defaults to `<page-host>:8000`, so set it
  explicitly in production.

The agent must be **published** for the widget to load its config. A launcher bubble appears in the
corner; clicking it opens the chat panel.

## 3. Control it from JavaScript (SDK)

Once loaded, `window.Vicero` is available:

```js
Vicero.open();                          // open the panel
Vicero.close();                         // close it
Vicero.toggle();                        // toggle
Vicero.sendMessage("Hello from my site"); // programmatically send a user message
Vicero.setUser({ name: "Jane", email: "jane@acme.com" }); // attach visitor identity
Vicero.on("message", (m) => console.log("reply:", m));    // subscribe to events
```

Example — open the widget from your own button:

```html
<button onclick="Vicero.toggle()">Chat with us</button>
```

## 4. Human handoff

If the agent has handoff enabled, a visitor can ask to "talk to a human". The conversation then
appears in the dashboard **Inbox**; when an operator takes over and replies, the reply is pushed
to the open widget in real time over a WebSocket. Handing back resumes the bot.

## 5. Theming

Appearance is driven by the agent's Channels config (`persona.widget`): accent colour, launcher
side (left/right), launcher label, open-by-default, and whether to show the "Powered by Vicero"
footer. Change it in the Channels tab; the widget picks it up from `/v1/public/agents/{key}/config`.

## 6. Cross-origin notes

The public endpoints are CORS-enabled. In development the API accepts any `http://localhost:<port>`
origin; in production set `CORS_ORIGINS` to include the sites that embed the widget (or serve the
widget from the same origin as the API to avoid CORS entirely).

## Try it locally

`apps/web/public/widget-demo.html` is a plain page with nothing but the widget script. Serve the
web app, open `http://localhost:3000/widget-demo.html`, and follow the on-page instructions to
drop in a real public key.

/*!
 * Vicero embeddable chat widget — vanilla, dependency-free, Shadow-DOM isolated.
 * Embed:
 *   <script src="https://YOUR_HOST/widget.js" data-agent="PUBLIC_KEY" data-api="https://API_HOST" defer></script>
 * SDK: window.Vicero = { open, close, toggle, sendMessage, on, setUser }
 *
 * All theming is applied via CSS custom properties set from the fetched config, so the
 * stylesheet is static and never needs a per-agent rebuild. In preview mode
 * (data-preview-mode="true") the parent page posts { type: "bf-preview-config", config }
 * to update the live widget instantly without saving.
 */
(function () {
  "use strict";
  if (window.__viceroWidgetLoaded) return;
  window.__viceroWidgetLoaded = true;

  var script =
    document.currentScript ||
    (function () {
      var s = document.getElementsByTagName("script");
      return s[s.length - 1];
    })();
  var PUBLIC_KEY = script && script.getAttribute("data-agent");
  var PREVIEW = script && script.getAttribute("data-preview-mode") === "true";
  var API =
    (script && script.getAttribute("data-api")) ||
    (location.protocol + "//" + location.hostname + ":8000");
  API = API.replace(/\/$/, "");
  if (!PUBLIC_KEY && !PREVIEW) {
    console.error("[Vicero] missing data-agent (public key) on the widget script tag");
    return;
  }

  var STORE_KEY = "vicero:conv:" + PUBLIC_KEY;
  var VISITOR_KEY = "vicero:vid:" + PUBLIC_KEY;

  function stored(key) {
    try {
      return localStorage.getItem(key) || null;
    } catch (e) {
      return null;
    }
  }
  function store(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      /* private mode / storage disabled — the widget still works, just doesn't resume */
    }
  }
  function forget(key) {
    try {
      localStorage.removeItem(key);
    } catch (e) {
      /* ignore */
    }
  }

  // A stable id for this browser, stored beside the conversation id and sent on every turn.
  // The server resumes a conversation only for the visitor that started it, so without this
  // the API would mint a fresh anonymous id per request and no anonymous chat could continue
  // past its first message. Not a credential — it is a continuity key, and the server treats
  // it as one: the worst it can do is resume a conversation on the device that created it.
  function visitorId() {
    var id = stored(VISITOR_KEY);
    if (!id) {
      id =
        "w-" +
        Date.now().toString(36) +
        "-" +
        Math.random().toString(36).slice(2, 10);
      store(VISITOR_KEY, id);
    }
    return id;
  }

  var listeners = {};
  var state = {
    open: false,
    config: null,
    conversationId: stored(STORE_KEY),
    visitor: {},
    sending: false,
    hasConversation: false,
    ws: null,
    layout: null,
  };

  function emit(event, payload) {
    (listeners[event] || []).forEach(function (cb) {
      try {
        cb(payload);
      } catch (e) {
        /* ignore listener errors */
      }
    });
  }

  // ── Minimal, safe markdown (escape first, then a tiny subset) ──────────────
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function renderMarkdown(text) {
    var html = escapeHtml(text);
    html = html.replace(/```([\s\S]*?)```/g, function (_m, code) {
      return "<pre><code>" + code.replace(/\n$/, "") + "</code></pre>";
    });
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_m, t, u) {
      return '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + t + "</a>";
    });
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  // ── Theme helpers ──────────────────────────────────────────────────────────
  // Pick a foreground (near-black or white) that meets WCAG contrast on the given bg color.
  function onColor(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return "#ffffff";
    var n = parseInt(m[1], 16);
    var srgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (c) {
      c /= 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    var lum = 0.2126 * srgb[0] + 0.7152 * srgb[1] + 0.0722 * srgb[2];
    return 1.05 / (lum + 0.05) >= (lum + 0.05) / 0.05 ? "#ffffff" : "#111318";
  }

  var FONT_STACKS = {
    system: "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif",
    inter: "Inter,-apple-system,'Segoe UI',Roboto,sans-serif",
    arial: "Arial,Helvetica,sans-serif",
    georgia: "Georgia,'Times New Roman',serif",
    courier: "'Courier New',Courier,monospace",
  };

  function logoUrl(theme) {
    var u = theme.logo_url;
    if (!u) return "";
    return u.charAt(0) === "/" ? API + u : u;
  }

  // Resolve the full palette from a theme, honoring mode defaults + explicit overrides.
  function palette(theme) {
    theme = theme || {};
    var dark = theme.mode !== "light";
    var accent = theme.primary_color || "#1F2937";
    var btn = theme.floating_button_color || accent;
    return {
      accent: accent,
      onAccent: onColor(accent),
      bg: theme.background_color || (dark ? "#16181D" : "#FFFFFF"),
      bg2: dark ? "#1E2127" : "#F4F5F7",
      text: theme.text_color || (dark ? "#E7E9EE" : "#14161A"),
      muted: dark ? "#9AA0AB" : "#5A616B",
      border: dark ? "#2A2E37" : "#E3E6EA",
      bubble: theme.bubble_color || accent,
      onBubble: onColor(theme.bubble_color || accent),
      input: theme.typing_area_color || (dark ? "#1E2127" : "#F4F5F7"),
      btn: btn,
      onBtn: onColor(btn),
      font: FONT_STACKS[theme.font_family] || FONT_STACKS.system,
      dark: dark,
    };
  }

  // Apply a theme to the (already built) widget via CSS custom properties + classes — no rebuild.
  function applyTheme(theme) {
    if (!host) return;
    var p = palette(theme);
    var vars = {
      "--bf-accent": p.accent,
      "--bf-on-accent": p.onAccent,
      "--bf-bg": p.bg,
      "--bf-bg2": p.bg2,
      "--bf-text": p.text,
      "--bf-muted": p.muted,
      "--bf-border": p.border,
      "--bf-bubble": p.bubble,
      "--bf-on-bubble": p.onBubble,
      "--bf-input": p.input,
      "--bf-btn": p.btn,
      "--bf-on-btn": p.onBtn,
      "--bf-font": p.font,
    };
    for (var k in vars) host.style.setProperty(k, vars[k]);
    // A layout owns the whole surface, so the separate solid/transparent treatment only applies
    // to the default (layout-less) look.
    var layout = layoutOf(theme);
    root.host.classList.toggle("bf-transparent", !layout && theme.widget_style === "transparent");
    root.host.classList.toggle("bf-left", theme.position === "bottom-left");
    applyLayout(theme, p, layout);
  }

  // ── Layouts (docs/27) ─────────────────────────────────────────────────────
  // Everything below is inert for an agent that never sets widget_layout / corner_radius /
  // density: no class, no CSS variable, no extra <style> and no extra DOM is created, so the
  // legacy widget renders exactly as before. The look of a layout lives in one scoped
  // stylesheet (injected only for the ACTIVE layout) keyed on `.bf-l-<name>`.
  var LAYOUTS = ["classic", "glass", "hairline", "paper", "pebble", "console", "concierge", "command", "drawer", "block"];
  var DENSITY = {
    compact: { pad: "12px", gap: "6px", mp: "7px 11px", fs: "13.5px" },
    comfortable: { pad: "16px", gap: "10px", mp: "9px 13px", fs: "14px" },
    roomy: { pad: "20px", gap: "14px", mp: "11px 15px", fs: "14.5px" },
  };
  var LAYOUT_VARS = [
    "--bf-r", "--bf-rm", "--bf-rc", "--bf-rb", "--bf-pad", "--bf-gap", "--bf-mp", "--bf-fs", "--bf-soft",
    "--bf-aline", "--bf-g1", "--bf-g2", "--bf-g3", "--bf-g4", "--bf-g5",
  ];

  function rgbOf(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return [0, 0, 0];
    var n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  // `pct`% of colour a laid over colour b — replaces CSS color-mix() so old browsers work.
  function mixHex(a, b, pct) {
    var x = rgbOf(a), y = rgbOf(b), t = pct / 100;
    return "#" + [0, 1, 2].map(function (i) {
      var v = Math.round(x[i] * t + y[i] * (1 - t));
      return (v < 16 ? "0" : "") + v.toString(16);
    }).join("");
  }
  function rgba(hex, a) {
    var c = rgbOf(hex);
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")";
  }
  function layoutOf(theme) {
    return theme && LAYOUTS.indexOf(theme.widget_layout) !== -1 ? theme.widget_layout : null;
  }

  // Corners + spacing only (no layout chosen): the legacy structure with new radii/padding.
  function tuneCss() {
    return (
      ".bf-tuned{border-radius:var(--bf-r)}" +
      ".bf-tuned .bf-msgs{padding:var(--bf-pad,16px);gap:var(--bf-gap,10px)}" +
      ".bf-tuned .bf-bubble{padding:var(--bf-mp,9px 12px);border-radius:var(--bf-rm);font-size:var(--bf-fs,14px)}" +
      ".bf-tuned .bf-chip{border-radius:min(999px,calc(var(--bf-r)*2 + 2px))}" +
      ".bf-tuned .bf-ta,.bf-tuned .bf-iconbtn,.bf-tuned .bf-send{border-radius:var(--bf-rb)}" +
      "@media (max-width:480px){.bf-tuned{border-radius:0}}"
    );
  }

  // Shared by every layout; a layout then overrides what makes it different.
  function layoutBaseCss() {
    return (
      "@keyframes bfup{from{opacity:0;transform:translateY(8px) scale(.985)}to{opacity:1;transform:none}}" +
      "@keyframes bffade{from{opacity:0}to{opacity:1}}" +
      "@keyframes bfmsg{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}" +
      "@keyframes bfblink{50%{opacity:0}}" +
      ".bf-lay{box-shadow:0 30px 70px -24px rgba(10,14,20,.38),0 4px 14px -4px rgba(10,14,20,.12);animation:bfup .22s ease-out;line-height:1.5}" +
      ".bf-lay :focus-visible{outline:2px solid var(--bf-accent);outline-offset:2px}" +
      ".bf-lay .bf-head{padding:13px var(--bf-pad,16px);gap:10px;position:relative;flex-wrap:wrap;background:var(--bf-bg);color:var(--bf-text);border-bottom:1px solid var(--bf-border)}" +
      ".bf-lay .bf-title{font-weight:600;font-size:14.5px;line-height:1.25;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
      ".bf-lay .bf-avatar{width:34px;height:34px}" +
      ".bf-lay .bf-ini{display:none;width:34px;height:34px;border-radius:50%;background:var(--bf-accent);color:var(--bf-on-accent);place-items:center;font-weight:600;font-size:13px;flex:none}" +
      ".bf-lay .bf-x{color:inherit;width:32px;height:32px;border-radius:8px;opacity:.75;font-size:20px}" +
      ".bf-lay .bf-x:hover{opacity:1;background:" + "rgba(127,127,127,.18)}" +
      ".bf-lay .bf-msgs{padding:var(--bf-pad,16px);gap:var(--bf-gap,10px);scrollbar-width:thin}" +
      ".bf-lay .bf-row{align-items:flex-end;animation:bfmsg .2s ease-out}" +
      ".bf-lay .bf-bubble{max-width:84%;padding:var(--bf-mp,9px 13px);border-radius:var(--bf-rm);font-size:var(--bf-fs,14px)}" +
      ".bf-lay .bf-bot .bf-bubble{background:var(--bf-bg2);border:0}" +
      ".bf-lay .bf-chips{padding:0 var(--bf-pad,16px) 8px}" +
      ".bf-lay .bf-chip{border:1px solid var(--bf-aline);background:var(--bf-bg);color:var(--bf-accent);padding:7px 12px;font-weight:500;font-size:13px;text-align:left;border-radius:min(999px,calc(var(--bf-r)*2 + 2px));transition:background .15s}" +
      ".bf-lay .bf-chip:hover{background:var(--bf-soft)}" +
      ".bf-lay .bf-foot{border:0;padding:10px var(--bf-pad,16px) 6px;gap:4px}" +
      ".bf-lay .bf-inrow{align-items:center;gap:2px;background:var(--bf-input);border:1px solid var(--bf-border);border-radius:min(var(--bf-r),22px);padding:4px 4px 4px 8px;transition:border-color .15s,box-shadow .15s}" +
      ".bf-lay .bf-inrow:focus-within{border-color:var(--bf-aline);box-shadow:0 0 0 3px var(--bf-soft)}" +
      ".bf-lay .bf-ta{border:0;background:transparent;min-height:34px;padding:7px 6px;border-radius:0}" +
      ".bf-lay .bf-ta:focus-visible{outline:none}" +
      ".bf-lay .bf-iconbtn{width:32px;height:32px;border:0;background:transparent;border-radius:8px;color:var(--bf-muted);font-size:16px}" +
      ".bf-lay .bf-iconbtn:hover{background:var(--bf-soft)}" +
      ".bf-lay .bf-send{width:34px;height:34px;border-radius:min(var(--bf-r),17px)}" +
      ".bf-lay .bf-brand{padding:4px 0 6px}" +
      ".bf-lay .bf-kbd,.bf-lay .bf-hero,.bf-lay .bf-tabs,.bf-lay .bf-pane{display:none}" +
      "@media (prefers-reduced-motion:reduce){.bf-lay,.bf-lay .bf-row{animation:none}.bf-lay .bf-typing i{animation:none}}"
    );
  }

  function layoutCss(name) {
    var L = ".bf-l-" + name;
    switch (name) {
      case "classic":
        return (
          L + " .bf-head{background:var(--bf-accent);color:var(--bf-on-accent);border-bottom:0;padding-top:16px;padding-bottom:16px}" +
          L + " .bf-ini{display:grid;background:rgba(255,255,255,.22);color:var(--bf-on-accent)}" +
          L + " .bf-ini[hidden]{display:none}"
        );
      case "glass":
        return (
          L + "{background:var(--bf-g1);-webkit-backdrop-filter:blur(24px) saturate(1.5);backdrop-filter:blur(24px) saturate(1.5);border-color:var(--bf-g3)}" +
          L + " .bf-head,.bf-l-glass .bf-msgs,.bf-l-glass .bf-foot{background:transparent}" +
          L + " .bf-head{border-bottom-color:var(--bf-g3)}" +
          L + " .bf-bot .bf-bubble,.bf-l-glass .bf-chip{background:var(--bf-g2);border:1px solid var(--bf-g3)}" +
          L + " .bf-inrow{background:var(--bf-g4);border:0;border-radius:999px}" +
          L + " .bf-ta{color:var(--bf-bg)}" +
          L + " .bf-ta::placeholder{color:var(--bf-g5)}" +
          L + " .bf-iconbtn{color:var(--bf-bg)}" +
          L + " .bf-iconbtn:hover{background:rgba(255,255,255,.14)}" +
          L + " .bf-send{border-radius:50%}"
        );
      case "hairline":
        return (
          L + "{box-shadow:0 20px 50px -30px rgba(10,14,20,.35)}" +
          L + " .bf-head{border-bottom:0;padding-top:18px}" +
          L + " .bf-avatar,.bf-l-hairline .bf-ini:not([hidden]){width:26px;height:26px;border-radius:7px;font-size:11px}" +
          L + " .bf-ini:not([hidden]){display:grid}" +
          L + " .bf-bot .bf-bubble{background:transparent;padding:0 2px;max-width:100%}" +
          L + " .bf-user .bf-bubble{background:transparent;color:var(--bf-text);border:1px solid var(--bf-border)}" +
          L + " .bf-typing{padding-left:2px}" +
          L + " .bf-chips{flex-direction:column;gap:0;margin:0 var(--bf-pad,16px) 8px;padding:0;border-top:1px solid var(--bf-border)}" +
          L + " .bf-chip{display:flex;justify-content:space-between;width:100%;border:0;border-bottom:1px solid var(--bf-border);border-radius:0;background:transparent;color:var(--bf-text);padding:11px 2px}" +
          L + " .bf-chip::after{content:'\\2192';color:var(--bf-muted);transition:transform .15s}" +
          L + " .bf-chip:hover{background:transparent}" +
          L + " .bf-chip:hover::after{transform:translateX(3px);color:var(--bf-accent)}" +
          L + " .bf-foot{padding-top:6px}" +
          L + " .bf-inrow{background:transparent;border:0;border-top:1px solid var(--bf-border);border-radius:0;padding:8px 0 4px;box-shadow:none}" +
          L + " .bf-send{background:transparent;color:var(--bf-accent)}"
        );
      case "paper":
        return (
          L + " .bf-head{justify-content:center;text-align:center;padding-block:18px 14px}" +
          L + " .bf-avatar,.bf-l-paper .bf-ini{display:none!important}" +
          L + " .bf-title{flex:none;font-size:19px;font-weight:400;letter-spacing:-.01em;overflow:visible}" +
          L + " .bf-x{position:absolute;right:10px;top:12px}" +
          L + " .bf-msgs{gap:14px}" +
          L + " .bf-bot .bf-bubble{background:transparent;padding:0;font-size:15.5px;line-height:1.6;max-width:100%}" +
          L + " .bf-user .bf-bubble{background:transparent;color:var(--bf-muted);font-style:italic;padding:0;border-radius:0;text-align:right}" +
          L + " .bf-chips{counter-reset:p;flex-direction:column;gap:2px}" +
          L + " .bf-chip{counter-increment:p;display:flex;border:0;background:transparent;color:var(--bf-text);padding:5px 0;border-radius:0;font-size:15px}" +
          L + " .bf-chip::before{content:counter(p,lower-alpha) '.';color:var(--bf-accent);font-style:italic;min-width:20px}" +
          L + " .bf-chip:hover{background:transparent;color:var(--bf-accent)}" +
          L + " .bf-inrow{background:transparent;border:0;border-bottom:1px solid var(--bf-text);border-radius:0;padding-left:0;box-shadow:none}" +
          L + " .bf-send{background:transparent;color:var(--bf-accent)}"
        );
      case "pebble":
        return (
          L + " .bf-head{flex-direction:column;justify-content:center;text-align:center;gap:6px;padding-block:16px 12px;background:var(--bf-bg2);border-bottom-color:transparent}" +
          L + " .bf-avatar,.bf-l-pebble .bf-ini:not([hidden]){width:46px;height:46px;font-size:16px}" +
          L + " .bf-ini:not([hidden]){display:grid}" +
          L + " .bf-title{flex:none;overflow:visible}" +
          L + " .bf-x{position:absolute;right:8px;top:8px}" +
          L + " .bf-bubble{border-radius:max(8px,calc(var(--bf-r) - 4px));padding:10px 15px}" +
          L + " .bf-bot .bf-bubble{border-bottom-left-radius:6px}" +
          L + " .bf-user .bf-bubble{border-bottom-right-radius:6px}" +
          L + " .bf-chip{border:0;background:var(--bf-soft)}" +
          L + " .bf-inrow{border-radius:999px;background:var(--bf-bg2);border-color:transparent}" +
          L + " .bf-send{border-radius:50%}"
        );
      case "console":
        return (
          L + "{box-shadow:0 30px 70px -24px rgba(0,0,0,.55)}" +
          L + " .bf-head{padding-block:10px;background:var(--bf-bg2)}" +
          L + " .bf-head::before{content:'';width:10px;height:10px;border-radius:50%;background:var(--bf-accent);box-shadow:16px 0 0 var(--bf-border),32px 0 0 var(--bf-border);margin-right:34px}" +
          L + " .bf-avatar,.bf-l-console .bf-ini{display:none!important}" +
          L + " .bf-title{font-size:12.5px;font-weight:500;flex:1;text-align:center}" +
          L + " .bf-title::before{content:'~/';color:var(--bf-muted)}" +
          L + " .bf-x{width:24px;height:24px;font-size:16px}" +
          L + " .bf-msgs{gap:8px;font-size:13px}" +
          L + " .bf-row.bf-user{flex-direction:row}" +
          L + " .bf-bubble{background:transparent!important;border:0;padding:0;border-radius:0;max-width:100%}" +
          L + " .bf-user .bf-bubble{color:var(--bf-accent)}" +
          L + " .bf-user .bf-bubble::before{content:'\\203A ';color:var(--bf-muted)}" +
          L + " .bf-chips{flex-direction:column;gap:1px;counter-reset:t}" +
          L + " .bf-chip{counter-increment:t;display:flex;border:0;border-radius:0;background:transparent;color:var(--bf-text);padding:3px 6px;margin-left:-6px;font-size:13px}" +
          L + " .bf-chip::before{content:'[' counter(t) ']';color:var(--bf-accent);margin-right:8px}" +
          L + " .bf-chip:hover{background:var(--bf-soft)}" +
          L + " .bf-inrow{background:transparent;border:0;border-top:1px dashed var(--bf-border);border-radius:0;padding:8px 0 2px;box-shadow:none}" +
          L + " .bf-inrow::before{content:'\\203A';color:var(--bf-accent);margin-right:8px;font-weight:600}" +
          L + " .bf-send{background:transparent;color:var(--bf-accent);border-radius:0}" +
          L + " .bf-typing i{border-radius:0;width:7px;height:12px;background:var(--bf-accent);animation:bfblink 1s steps(1) infinite}" +
          L + " .bf-typing i+i{display:none}"
        );
      case "concierge":
        return (
          L + " .bf-head{background:var(--bf-accent);color:var(--bf-on-accent);border-bottom:0}" +
          L + " .bf-x{margin-left:auto}" +
          L + " .bf-hero{width:100%;order:5}" +
          L + " .bf-hero-h{font-size:24px;font-weight:700;letter-spacing:-.02em;line-height:1.15}" +
          L + " .bf-hero-p{opacity:.85;font-size:13.5px;margin-top:4px}" +
          L + ".bf-home .bf-head{padding:20px var(--bf-pad,16px) 26px}" +
          L + ".bf-home .bf-title,.bf-l-concierge.bf-home .bf-avatar{display:none}" +
          L + ".bf-home .bf-hero{display:block;margin-top:14px}" +
          L + ".bf-home .bf-msgs{display:none}" +
          L + ".bf-home .bf-chips{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:var(--bf-pad,16px);align-content:start;overflow-y:auto}" +
          L + ".bf-home .bf-chip{flex-direction:column;align-items:flex-start;gap:10px;padding:13px;border-radius:var(--bf-rc);border:1px solid var(--bf-border);color:var(--bf-text);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.05);display:flex}" +
          L + ".bf-home .bf-chip::before{content:'\\2192';width:30px;height:30px;border-radius:9px;background:var(--bf-soft);color:var(--bf-accent);display:grid;place-items:center}" +
          L + ".bf-home .bf-chip:last-child:nth-child(odd){grid-column:span 2}" +
          L + ".bf-home .bf-chip:hover{border-color:var(--bf-accent);background:var(--bf-bg)}" +
          L + ":not(.bf-home) .bf-chips{display:none}"
        );
      case "command":
        return (
          L + " .bf-head{position:absolute;right:6px;top:6px;padding:0;background:transparent;border:0;z-index:2}" +
          L + " .bf-title,.bf-l-command .bf-avatar,.bf-l-command .bf-ini{display:none!important}" +
          L + " .bf-foot{order:-1;padding:10px 44px 0 10px}" +
          L + " .bf-inrow{background:transparent;border:0;border-bottom:1px solid var(--bf-border);border-radius:0;padding:8px 6px 12px;box-shadow:none}" +
          L + " .bf-inrow::before{content:'';width:20px;height:20px;flex:none;margin-right:10px;background:var(--bf-accent);-webkit-mask:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M20 20l-3.5-3.5'/%3E%3C/svg%3E\") center/contain no-repeat;mask:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M20 20l-3.5-3.5'/%3E%3C/svg%3E\") center/contain no-repeat}" +
          L + " .bf-ta{font-size:16.5px}" +
          L + " .bf-send{border-radius:8px;width:30px;height:30px}" +
          L + " .bf-chips{flex-direction:column;gap:2px;padding:6px 10px}" +
          L + " .bf-chip{display:flex;width:100%;border:0;background:transparent;color:var(--bf-text);border-radius:var(--bf-rb);padding:9px 10px;font-weight:500}" +
          L + " .bf-chip::after{content:'\\21B5';margin-left:auto;color:var(--bf-muted);opacity:0}" +
          L + " .bf-chip:hover,.bf-l-command .bf-chip:focus-visible{background:var(--bf-soft)}" +
          L + " .bf-chip:hover::after,.bf-l-command .bf-chip:focus-visible::after{opacity:1}" +
          L + " .bf-bot .bf-bubble{background:transparent;padding:2px 4px;color:var(--bf-muted);font-size:13px;max-width:100%}" +
          L + " .bf-kbd{display:flex;gap:14px;padding:8px 14px;border-top:1px solid var(--bf-border);font-size:11px;color:var(--bf-muted)}" +
          L + " .bf-kbd kbd{font-family:inherit;font-size:10.5px;border:1px solid var(--bf-border);border-radius:4px;padding:0 5px;margin-right:4px;background:var(--bf-bg2)}" +
          L + " .bf-brand{position:absolute;right:14px;bottom:0;padding:8px 0}" +
          "@media (min-width:481px){" +
          L + ",:host(.bf-left) " + L + "{left:50%;right:auto;bottom:auto;top:12vh;transform:translateX(-50%);width:440px;height:auto;max-height:min(520px,76vh);animation-name:bffade}}"
        );
      case "drawer":
        return (
          L + " .bf-head{padding-bottom:0;row-gap:12px}" +
          L + " .bf-ini:not([hidden]){display:grid;border-radius:10px}" +
          L + " .bf-avatar{border-radius:10px}" +
          L + " .bf-tabs{display:flex;width:100%;gap:18px;order:6}" +
          L + " .bf-tabs button{border:0;background:transparent;color:var(--bf-muted);padding:6px 0 10px;font-size:13px;font-weight:500;border-bottom:2px solid transparent;margin-bottom:-1px;cursor:pointer;font-family:inherit}" +
          L + " .bf-tabs button.on{color:var(--bf-text);border-bottom-color:var(--bf-accent)}" +
          L + " .bf-pane{flex:1;overflow:auto;padding:var(--bf-pad,16px)}" +
          L + ".bf-tab-help .bf-pane{display:block}" +
          L + ".bf-tab-help .bf-msgs," + L + ".bf-tab-help .bf-chips," + L + ".bf-tab-help .bf-foot{display:none}" +
          L + " .bf-pane h4{margin:0 0 10px;font-size:12px;color:var(--bf-muted);font-weight:500}" +
          L + " .bf-artrow{display:flex;align-items:center;width:100%;text-align:left;background:transparent;border:0;border-bottom:1px solid var(--bf-border);padding:12px 2px;color:var(--bf-text);font-size:13.5px;cursor:pointer;font-family:inherit}" +
          L + " .bf-artrow::after{content:'\\203A';margin-left:auto;color:var(--bf-muted)}" +
          ":host(.bf-hl-drawer.bf-open) .bf-launcher{display:none}" +
          "@media (min-width:481px){" +
          L + ",:host(.bf-left) " + L + "{top:0;bottom:0;right:0;left:auto;width:400px;height:100vh;max-height:100vh;border-radius:0;border-width:0 0 0 1px;animation-name:bffade}" +
          ":host(.bf-left) " + L + "{right:auto;left:0;border-width:0 1px 0 0}}"
        );
      case "block":
        return (
          L + "{border:2px solid var(--bf-text);box-shadow:7px 7px 0 var(--bf-text);font-weight:500}" +
          L + " .bf-head{background:var(--bf-accent);color:var(--bf-on-accent);border-bottom:2px solid var(--bf-text)}" +
          L + " .bf-title{text-transform:uppercase;letter-spacing:.04em;font-weight:800;font-size:14px}" +
          L + " .bf-avatar,.bf-l-block .bf-ini:not([hidden]){border:2px solid var(--bf-text);background:var(--bf-bg);color:var(--bf-text);border-radius:var(--bf-rb)}" +
          L + " .bf-ini:not([hidden]){display:grid}" +
          L + " .bf-bubble{border:2px solid var(--bf-text)!important}" +
          L + " .bf-bot .bf-bubble{background:var(--bf-bg)}" +
          L + " .bf-user .bf-bubble{box-shadow:3px 3px 0 var(--bf-text)}" +
          L + " .bf-chip{border:2px solid var(--bf-text);color:var(--bf-text);box-shadow:3px 3px 0 var(--bf-text)}" +
          L + " .bf-chip:hover{transform:translate(-1px,-1px);box-shadow:4px 4px 0 var(--bf-text);background:var(--bf-bg)}" +
          L + " .bf-inrow{border:2px solid var(--bf-text);box-shadow:none}" +
          L + " .bf-send{border:2px solid var(--bf-text)}" +
          ":host(.bf-hl-block) .bf-launcher{border:2px solid #0a0a0a;box-shadow:4px 4px 0 #0a0a0a}" +
          "@media (max-width:480px){" + L + "{box-shadow:none;border-width:0}}"
        );
    }
    return "";
  }

  // Phone widths keep the legacy full-screen panel for every layout (docs/27 §2, §6).
  var LAYOUT_MOBILE_CSS =
    "@media (max-width:480px){.bf-panel.bf-lay,:host(.bf-left) .bf-panel.bf-lay{width:100vw;max-width:100vw;height:100vh;max-height:100vh;top:auto;bottom:0;right:0;left:0;transform:none;border-radius:0}}";

  // The one extra <style>, created only when something needs it and rewritten only when the
  // set of needed rules changes (so a colour tweak in the builder never re-parses CSS).
  function setExtraCss(layout, tuned) {
    var key = (tuned ? "t" : "") + ":" + (layout || "");
    if (state.extraKey === key) return;
    state.extraKey = key;
    if (!tuned) {
      if (els.extra && els.extra.parentNode) els.extra.parentNode.removeChild(els.extra);
      els.extra = null;
      return;
    }
    if (!els.extra) {
      els.extra = document.createElement("style");
      root.appendChild(els.extra);
    }
    els.extra.textContent = layout
      ? tuneCss() + layoutBaseCss() + layoutCss(layout) + LAYOUT_MOBILE_CSS
      : tuneCss();
  }

  function applyLayout(theme, p, layout) {
    var radius =
      typeof theme.corner_radius === "number" && theme.corner_radius >= 0 && theme.corner_radius <= 28
        ? Math.round(theme.corner_radius)
        : null;
    var dens = DENSITY[theme.density] || null;
    var tuned = !!(layout || radius !== null || dens);

    LAYOUTS.forEach(function (l) {
      els.panel.classList.remove("bf-l-" + l);
      host.classList.remove("bf-hl-" + l);
    });
    els.panel.classList.toggle("bf-lay", !!layout);
    els.panel.classList.toggle("bf-tuned", tuned);
    if (layout) {
      els.panel.classList.add("bf-l-" + layout);
      host.classList.add("bf-hl-" + layout);
    }

    var v = {};
    if (tuned) {
      var r = radius !== null ? radius : 16;
      v["--bf-r"] = r + "px";
      v["--bf-rm"] = Math.min(r, 18) + "px";
      v["--bf-rc"] = Math.min(r, 14) + "px";
      v["--bf-rb"] = Math.min(r, 10) + "px";
    }
    if (dens) {
      v["--bf-pad"] = dens.pad;
      v["--bf-gap"] = dens.gap;
      v["--bf-mp"] = dens.mp;
      v["--bf-fs"] = dens.fs;
    }
    if (layout) {
      // The legacy palette derives these from the mode alone; a layout derives them from the
      // actual background/text so a custom background still gets matching surfaces.
      host.style.setProperty("--bf-bg2", mixHex(p.text, p.bg, 5));
      host.style.setProperty("--bf-border", mixHex(p.text, p.bg, 11));
      host.style.setProperty("--bf-muted", mixHex(p.text, p.bg, 58));
      if (!theme.typing_area_color) host.style.setProperty("--bf-input", mixHex(p.text, p.bg, 4));
      v["--bf-soft"] = mixHex(p.accent, p.bg, 12);
      v["--bf-aline"] = mixHex(p.accent, mixHex(p.text, p.bg, 11), 40);
      v["--bf-g1"] = rgba(p.bg, 0.58);
      v["--bf-g2"] = rgba(p.bg, 0.7);
      v["--bf-g3"] = rgba(p.text, 0.1);
      v["--bf-g4"] = rgba(p.text, 0.85);
      v["--bf-g5"] = rgba(p.bg, 0.6);
    }
    LAYOUT_VARS.forEach(function (k) {
      if (v[k] !== undefined) host.style.setProperty(k, v[k]);
      else host.style.removeProperty(k);
    });
    // Screen readers announce new messages in a layout; the legacy DOM is left untouched.
    if (layout) els.msgs.setAttribute("aria-live", "polite");
    else els.msgs.removeAttribute("aria-live");
    setExtraCss(layout, tuned);
    state.layout = layout;
    ensureLayoutDom(layout);
  }

  // Concierge / Drawer / Command are the only layouts that add DOM; nothing is built otherwise.
  function ensureLayoutDom(layout) {
    if (state.domLayout === layout) return;
    state.domLayout = layout;
    var stale = els.panel.querySelectorAll("[data-bf-lay]");
    for (var i = 0; i < stale.length; i++) stale[i].parentNode.removeChild(stale[i]);
    els.hero = els.tabs = els.pane = els.kbd = null;
    els.panel.classList.remove("bf-home", "bf-tab-help");
    if (!layout) return;
    var head = els.panel.querySelector(".bf-head");
    var ini = document.createElement("span");
    ini.className = "bf-ini";
    ini.setAttribute("data-bf-lay", "");
    ini.setAttribute("aria-hidden", "true");
    head.insertBefore(ini, head.firstChild);
    els.ini = ini;
    if (layout === "concierge") {
      els.hero = document.createElement("div");
      els.hero.className = "bf-hero";
      els.hero.setAttribute("data-bf-lay", "");
      els.hero.innerHTML = '<div class="bf-hero-h"></div><div class="bf-hero-p"></div>';
      head.appendChild(els.hero);
    } else if (layout === "drawer") {
      els.tabs = document.createElement("nav");
      els.tabs.className = "bf-tabs";
      els.tabs.setAttribute("data-bf-lay", "");
      els.tabs.setAttribute("aria-label", "Sections");
      head.appendChild(els.tabs);
      els.pane = document.createElement("div");
      els.pane.className = "bf-pane";
      els.pane.setAttribute("data-bf-lay", "");
      els.msgs.parentNode.insertBefore(els.pane, els.chips);
    } else if (layout === "command") {
      els.kbd = document.createElement("div");
      els.kbd.className = "bf-kbd";
      els.kbd.setAttribute("data-bf-lay", "");
      els.kbd.setAttribute("aria-hidden", "true");
      els.kbd.innerHTML = "<span><kbd>↑↓</kbd>navigate</span><span><kbd>↵</kbd>select</span><span><kbd>esc</kbd>close</span>";
      els.panel.appendChild(els.kbd);
    }
  }

  function setTab(id) {
    els.panel.classList.toggle("bf-tab-help", id === "help");
    if (!els.tabs) return;
    var bs = els.tabs.querySelectorAll("button");
    for (var i = 0; i < bs.length; i++) bs[i].classList.toggle("on", bs[i].getAttribute("data-tab") === id);
  }

  // Fill the layout-only elements from data the agent already has (name, welcome message,
  // suggested prompts). No demo copy is hard-coded.
  function renderLayoutExtras() {
    var layout = state.layout;
    if (!layout || !els.ini) return;
    var cfg = state.config || {};
    var name = String(cfg.name || "Chat");
    els.ini.textContent = (name.replace(/[^A-Za-z0-9À-￿ ]/g, "").trim().charAt(0) || "V").toUpperCase();
    els.ini.hidden = !!logoUrl(cfg.theme || {});
    var prompts = (cfg.suggested_prompts || [])
      .map(function (p) { return typeof p === "string" ? p : p && p.label ? p.label : ""; })
      .filter(Boolean);
    if (els.hero) {
      els.hero.firstChild.textContent = name;
      els.hero.lastChild.textContent = cfg.welcome_message || "";
    }
    if (els.tabs) {
      els.tabs.innerHTML = "";
      var tabs = prompts.length ? [["chat", "Chat"], ["help", "Help"]] : [];
      tabs.forEach(function (t) {
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("data-tab", t[0]);
        b.textContent = t[1];
        b.addEventListener("click", function () { setTab(t[0]); });
        els.tabs.appendChild(b);
      });
      els.pane.innerHTML = "";
      if (prompts.length) {
        var h4 = document.createElement("h4");
        h4.textContent = "Suggested questions";
        els.pane.appendChild(h4);
        prompts.forEach(function (text) {
          var row = document.createElement("button");
          row.type = "button";
          row.className = "bf-artrow";
          row.textContent = text;
          row.addEventListener("click", function () {
            setTab("chat");
            api.sendMessage(text);
          });
          els.pane.appendChild(row);
        });
      }
      setTab("chat");
    }
    updateHome();
  }

  // Concierge shows its home screen until the visitor says something; it never comes back for a
  // conversation that already exists (stored id) or after a proactive message was shown.
  function updateHome() {
    if (!els.panel) return;
    var home =
      state.layout === "concierge" &&
      !!(state.config && (state.config.suggested_prompts || []).length) &&
      !state.hasConversation &&
      !state.conversationId &&
      !state.campaignShown;
    els.panel.classList.toggle("bf-home", home);
  }

  // Keyboard model for layouts: Esc closes from anywhere in the panel; the Command layout also
  // walks the suggestion rows with the arrow keys (the textarea is row 0).
  function onPanelKey(e) {
    if (!state.layout) return;
    if (e.key === "Escape" && state.open) {
      e.stopPropagation();
      api.close();
      return;
    }
    if (state.layout !== "command" || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
    var rows = [els.ta].concat(Array.prototype.slice.call(els.chips.querySelectorAll(".bf-chip")));
    var at = rows.indexOf(root.activeElement);
    if (at === -1) at = 0;
    var next = e.key === "ArrowDown" ? Math.min(rows.length - 1, at + 1) : Math.max(0, at - 1);
    e.preventDefault();
    rows[next].focus();
  }

  // ── Static stylesheet (references CSS vars set by applyTheme) ───────────────
  function styles() {
    return (
      ":host{all:initial}" +
      "*{box-sizing:border-box;font-family:var(--bf-font)}" +
      // launcher (right by default; :host(.bf-left) flips to the left)
      ".bf-launcher{position:fixed;bottom:20px;right:20px;z-index:2147483000;" +
      "display:flex;align-items:center;justify-content:center;gap:10px;min-width:56px;height:56px;" +
      "padding:0;border:none;border-radius:50%;cursor:pointer;color:var(--bf-on-btn);background:var(--bf-btn);" +
      "box-shadow:0 8px 24px rgba(0,0,0,.28);font-size:15px;font-weight:600;transition:transform .15s;overflow:hidden}" +
      ".bf-launcher.bf-pill{border-radius:28px;padding:0 18px 0 16px}" +
      ".bf-launcher.bf-square{border-radius:18px}" +
      ".bf-launcher:hover{transform:translateY(-2px)}" +
      ".bf-launcher svg{width:24px;height:24px}" +
      ".bf-launcher img.bf-logo{width:34px;height:34px;border-radius:50%;object-fit:cover}" +
      ".bf-launcher .bf-lbl{white-space:nowrap}" +
      ".bf-launcher .bf-chip-logo{width:26px;height:26px;border-radius:50%;object-fit:cover}" +
      // pulse-ring animation
      ".bf-launcher.bf-pulse::after{content:'';position:absolute;inset:0;border-radius:50%;" +
      "box-shadow:0 0 0 0 var(--bf-btn);animation:bfpulse 1.8s cubic-bezier(.66,0,0,1) infinite;z-index:-1}" +
      "@keyframes bfpulse{to{box-shadow:0 0 0 16px rgba(0,0,0,0)}}" +
      "@media (prefers-reduced-motion:reduce){.bf-launcher.bf-pulse::after{animation:none}}" +
      // panel
      ".bf-panel{position:fixed;bottom:88px;right:20px;z-index:2147483000;width:380px;" +
      "max-width:calc(100vw - 32px);height:600px;max-height:calc(100vh - 120px);background:var(--bf-bg);" +
      "color:var(--bf-text);border:1px solid var(--bf-border);border-radius:16px;box-shadow:0 24px 60px rgba(0,0,0,.35);" +
      "display:none;flex-direction:column;overflow:hidden}" +
      ".bf-panel.bf-show{display:flex}" +
      ":host(.bf-left) .bf-launcher,:host(.bf-left) .bf-panel{right:auto;left:20px}" +
      // Transparent style = its own independent glass palette (not "theme + blur"): no solid
      // header bar, white-glass bot side with fixed near-black text (guaranteed contrast, not the
      // mode's text var), and a genuinely dark floating input pill. User bubbles keep the accent.
      ":host(.bf-transparent) .bf-panel{background:transparent;border-color:transparent;box-shadow:none}" +
      ":host(.bf-transparent) .bf-msgs{background:transparent}" +
      ":host(.bf-transparent) .bf-head{background:transparent;color:var(--bf-text-on-transparent,#171717)}" +
      ":host(.bf-transparent) .bf-head .bf-x{color:var(--bf-text-on-transparent,#171717)}" +
      ":host(.bf-transparent) .bf-bot .bf-bubble{background:rgba(255,255,255,.7);color:#171717;border-color:rgba(0,0,0,.06);backdrop-filter:blur(10px)}" +
      ":host(.bf-transparent) .bf-chip{background:rgba(255,255,255,.7);color:#171717;border-color:rgba(0,0,0,.06)}" +
      ":host(.bf-transparent) .bf-foot{background:rgba(23,23,23,.8);color:#fff;backdrop-filter:blur(10px);border-radius:14px;margin:8px;border:1px solid rgba(255,255,255,.12)}" +
      ":host(.bf-transparent) .bf-ta{background:rgba(255,255,255,.1);color:#fff;border-color:rgba(255,255,255,.16)}" +
      ":host(.bf-transparent) .bf-iconbtn{background:rgba(255,255,255,.1);color:#fff;border-color:rgba(255,255,255,.16)}" +
      ":host(.bf-transparent) .bf-brand,:host(.bf-transparent) .bf-brand a{color:rgba(255,255,255,.7)}" +
      ".bf-head{display:flex;align-items:center;gap:10px;padding:14px 16px;background:var(--bf-accent);color:var(--bf-on-accent)}" +
      ".bf-head .bf-avatar{width:26px;height:26px;border-radius:50%;object-fit:cover}" +
      ".bf-head .bf-title{font-weight:700;font-size:15px;flex:1}" +
      ".bf-x{background:transparent;border:none;color:var(--bf-on-accent);cursor:pointer;font-size:20px;line-height:1;opacity:.9}" +
      ".bf-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;background:var(--bf-bg)}" +
      ".bf-row{display:flex;gap:8px;max-width:100%}" +
      ".bf-row.bf-user{flex-direction:row-reverse}" +
      ".bf-avatar-sm{width:24px;height:24px;border-radius:50%;object-fit:cover;flex:0 0 auto;align-self:flex-end}" +
      ".bf-bubble{max-width:80%;padding:9px 12px;border-radius:12px;font-size:14px;line-height:1.5;white-space:normal;word-wrap:break-word}" +
      ".bf-bot .bf-bubble{background:var(--bf-bg2);color:var(--bf-text);border:1px solid var(--bf-border)}" +
      ".bf-user .bf-bubble{background:var(--bf-bubble);color:var(--bf-on-bubble)}" +
      ".bf-bubble pre{background:rgba(0,0,0,.25);padding:8px;border-radius:8px;overflow-x:auto;margin:6px 0}" +
      ".bf-cites{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:8px;padding-top:7px;border-top:1px solid var(--bf-border);font-size:11px;line-height:1.4}" +
      ".bf-cites-label{opacity:.6;text-transform:uppercase;letter-spacing:.04em;font-weight:600}" +
      ".bf-cite{padding:2px 7px;border-radius:999px;background:var(--bf-bg);border:1px solid var(--bf-border);opacity:.85;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
      "a.bf-cite{color:inherit;text-decoration:none}" +
      "a.bf-cite:hover{opacity:1;border-color:var(--bf-accent)}" +
      ":host(.bf-transparent) .bf-cites{border-top-color:rgba(0,0,0,.08)}" +
      ":host(.bf-transparent) .bf-cite{background:rgba(255,255,255,.5);border-color:rgba(0,0,0,.08)}" +
      ".bf-bubble code{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}" +
      ".bf-bubble a{color:var(--bf-accent)}" +
      ".bf-typing{display:inline-flex;gap:3px}.bf-typing i{width:6px;height:6px;border-radius:50%;background:var(--bf-muted);animation:bfb 1s infinite}" +
      ".bf-typing i:nth-child(2){animation-delay:.2s}.bf-typing i:nth-child(3){animation-delay:.4s}" +
      "@keyframes bfb{0%,60%,100%{opacity:.3}30%{opacity:1}}" +
      "@media (prefers-reduced-motion:reduce){.bf-typing i{animation:none;opacity:.6}}" +
      ".bf-chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 16px 8px}" +
      ".bf-chip{border:1px solid var(--bf-border);background:var(--bf-bg2);color:var(--bf-text);border-radius:14px;padding:6px 10px;font-size:12.5px;cursor:pointer}" +
      ".bf-chip:hover{border-color:var(--bf-accent)}" +
      ".bf-foot{border-top:1px solid var(--bf-border);padding:10px;display:flex;flex-direction:column;gap:6px;background:var(--bf-bg)}" +
      ".bf-inrow{display:flex;align-items:flex-end;gap:8px;position:relative}" +
      ".bf-iconbtn{flex:0 0 auto;width:36px;height:36px;border-radius:9px;border:1px solid var(--bf-border);background:var(--bf-input);color:var(--bf-muted);cursor:pointer;font-size:17px}" +
      ".bf-emoji-pop{position:absolute;bottom:44px;left:0;display:flex;flex-wrap:wrap;gap:4px;width:220px;padding:8px;border:1px solid var(--bf-border);background:var(--bf-bg2);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.3)}" +
      ".bf-emoji-pop button{border:none;background:transparent;cursor:pointer;font-size:18px;line-height:1;padding:3px;border-radius:6px}" +
      ".bf-emoji-pop button:hover{background:var(--bf-input)}" +
      ".bf-ta{flex:1;resize:none;max-height:120px;min-height:38px;padding:9px 11px;border-radius:9px;border:1px solid var(--bf-border);" +
      "background:var(--bf-input);color:var(--bf-text);font-size:14px;outline:none;font-family:var(--bf-font)}" +
      ".bf-ta:focus{border-color:var(--bf-accent)}" +
      ".bf-send{flex:0 0 auto;width:38px;height:38px;border-radius:9px;border:none;cursor:pointer;background:var(--bf-accent);color:var(--bf-on-accent);font-size:16px}" +
      ".bf-send:disabled{opacity:.5;cursor:default}" +
      ".bf-attach{font-size:12px;color:var(--bf-muted);padding:0 2px}" +
      ".bf-brand{text-align:center;font-size:11px;color:var(--bf-muted);padding:2px}" +
      ".bf-brand a{color:var(--bf-muted);text-decoration:none}" +
      "@media (max-width:480px){.bf-panel,:host(.bf-left) .bf-panel{width:100vw;height:100vh;max-height:100vh;bottom:0;right:0;left:0;border-radius:0}}" +
      // Below 768px an open panel (fullscreen or nearly so) would paint over the equal-z-index
      // launcher and intercept clicks meant for the input's send button — so hide the launcher
      // while open at phone widths and rely on the panel's own header close button (.bf-x). Above
      // 768px nothing changes: the launcher stays visible and doubles as the close control.
      "@media (max-width:767px){:host(.bf-open) .bf-launcher{display:none}}"
    );
  }

  // ── Icons (inline SVG, no icon library) ────────────────────────────────────
  var ICON_CHAT =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  var ICON_MESSAGE =
    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/><rect x="6" y="8" width="12" height="1.8" rx=".9" fill="var(--bf-btn)"/><rect x="6" y="11.4" width="8" height="1.8" rx=".9" fill="var(--bf-btn)"/></svg>';
  var ICON_DOTS =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><circle cx="8" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="10" r="1" fill="currentColor" stroke="none"/></svg>';

  var root, host, els = {};

  // Build the launcher's inner content + shape class for a given design (or the close button).
  function launcherContent(theme, open) {
    var l = els.launcher;
    l.className = "bf-launcher";
    if (open) {
      l.setAttribute("aria-label", "Close chat");
      l.innerHTML = "&times;";
      l.style.fontSize = "26px";
      return;
    }
    l.setAttribute("aria-label", "Open chat");
    l.style.fontSize = "";
    var style = theme.floating_button_style || null; // null → legacy pill
    var logo = logoUrl(theme);
    var logoImg = logo ? '<img class="bf-logo" src="' + logo + '" alt="" />' : "";
    var label = escapeHtml(theme.launcher_text || "Chat with us");
    if (style === "pill-text" || style === null) {
      l.classList.add("bf-pill");
      l.innerHTML = (logo ? '<img class="bf-chip-logo" src="' + logo + '" alt="" />' : ICON_CHAT) +
        '<span class="bf-lbl">' + label + "</span>";
    } else if (style === "rounded-square") {
      l.classList.add("bf-square");
      l.innerHTML = logoImg || ICON_CHAT;
    } else if (style === "circle-message") {
      l.innerHTML = logoImg || ICON_MESSAGE;
    } else if (style === "circle-dots") {
      l.innerHTML = logoImg || ICON_DOTS;
    } else if (style === "pulse-ring") {
      l.classList.add("bf-pulse");
      l.innerHTML = logoImg || ICON_CHAT;
    } else {
      // circle-chat (default design)
      l.innerHTML = logoImg || ICON_CHAT;
    }
  }

  var EMOJIS = ["😀", "😄", "😊", "👍", "🙏", "🎉", "❤️", "🔥", "😎", "🤔", "😅", "🙌", "👏", "✅", "⭐", "💡", "😍", "🚀"];

  function build() {
    var theme = state.config.theme || {};
    // Idempotency: never leave a second widget behind. If a prior host exists (e.g. a React
    // remount of the preview, or a double init), remove it before creating the new one.
    var prior = document.getElementById("vicero-widget");
    if (prior && prior.parentNode) prior.parentNode.removeChild(prior);

    // Build fully DETACHED, theme it, THEN insert — so there's never an unstyled/default frame
    // painted (the white panel + dark launcher flash) before applyTheme sets the CSS vars.
    host = document.createElement("div");
    host.id = "vicero-widget";
    root = host.attachShadow({ mode: "open" });

    var style = document.createElement("style");
    style.textContent = styles();
    root.appendChild(style);

    var launcher = document.createElement("button");
    launcher.className = "bf-launcher";
    root.appendChild(launcher);
    els.launcher = launcher;
    launcher.addEventListener("click", api.toggle);

    // Static structure — built once. Every config-driven detail (colors, logo, input buttons,
    // branding, chips, welcome, launcher design) is filled in by applyConfig() below, so a
    // config change never tears the widget down or changes whether the panel is open.
    var panel = document.createElement("div");
    panel.className = "bf-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Chat window");
    panel.innerHTML =
      '<div class="bf-head"><img class="bf-avatar" alt="" style="display:none" />' +
      '<div class="bf-title"></div><button class="bf-x" aria-label="Close chat">&times;</button></div>' +
      '<div class="bf-msgs"></div>' +
      '<div class="bf-chips"></div>' +
      '<div class="bf-foot"><div class="bf-attach" style="display:none"></div>' +
      '<div class="bf-inrow">' +
      '<button class="bf-iconbtn bf-file" aria-label="Attach file">📎</button>' +
      '<button class="bf-iconbtn bf-emoji" aria-label="Insert emoji">🙂</button>' +
      '<textarea class="bf-ta" rows="1" placeholder="Type a message…" aria-label="Message"></textarea>' +
      '<button class="bf-send" aria-label="Send">➤</button>' +
      "</div>" +
      '<div class="bf-brand">Powered by <a href="https://vicero.dev" target="_blank" rel="noopener">Vicero</a></div>' +
      '<input type="file" style="display:none" /></div>';
    root.appendChild(panel);
    els.panel = panel;
    els.avatar = panel.querySelector(".bf-avatar");
    els.title = panel.querySelector(".bf-title");
    els.msgs = panel.querySelector(".bf-msgs");
    els.chips = panel.querySelector(".bf-chips");
    els.ta = panel.querySelector(".bf-ta");
    els.send = panel.querySelector(".bf-send");
    els.attach = panel.querySelector(".bf-attach");
    els.fileBtn = panel.querySelector(".bf-file");
    els.emojiBtn = panel.querySelector(".bf-emoji");
    els.brand = panel.querySelector(".bf-brand");
    els.inrow = panel.querySelector(".bf-inrow");
    els.fileInput = panel.querySelector('input[type="file"]');
    panel.addEventListener("keydown", onPanelKey);

    panel.querySelector(".bf-x").addEventListener("click", api.close);
    els.send.addEventListener("click", function () {
      submit();
    });
    els.ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submit();
      } else if (e.key === "Escape") {
        api.close();
      }
    });
    els.ta.addEventListener("input", autoGrow);
    els.fileBtn.addEventListener("click", function () {
      els.fileInput.click();
    });
    els.fileInput.addEventListener("change", onFile);
    els.emojiBtn.addEventListener("click", toggleEmoji);

    applyConfig(state.config); // themes + fills content while still detached
    document.body.appendChild(host); // enter the DOM fully styled — no unstyled first paint
    emit("ready", { config: state.config });
  }

  // Apply a (possibly changed) config in place — no teardown, no forced open/close. Preserves
  // whatever state.open currently is. Safe to call repeatedly (used by the builder live preview).
  function applyConfig(config) {
    if (!els.panel) return;
    state.config = config || state.config;
    var theme = state.config.theme || {};
    applyTheme(theme);
    els.title.textContent = state.config.name || "Chat";
    var logo = logoUrl(theme);
    if (logo) {
      els.avatar.src = logo;
      els.avatar.style.display = "";
    } else {
      els.avatar.removeAttribute("src");
      els.avatar.style.display = "none";
    }
    var buttons = theme.input_bar_buttons || ["attachment"];
    els.fileBtn.style.display = buttons.indexOf("attachment") !== -1 ? "" : "none";
    els.emojiBtn.style.display = buttons.indexOf("emoji") !== -1 ? "" : "none";
    els.brand.style.display = theme.branding === false ? "none" : "";
    renderChips();
    refreshWelcome(state.config.welcome_message);
    renderLayoutExtras();
    launcherContent(theme, state.open); // keep the current open/closed state
  }

  // Manage the welcome bubble in place while no real conversation has started (design preview).
  function refreshWelcome(text) {
    if (!els.msgs) return;
    if (state.hasConversation) return; // don't disturb an in-progress chat
    if (state.campaignShown) return; // nor a proactive message the visitor is reading
    els.msgs.innerHTML = "";
    els.welcomeBubble = text ? addMessage("bot", text) : null;
  }

  function toggleEmoji() {
    var existing = els.inrow.querySelector(".bf-emoji-pop");
    if (existing) {
      existing.remove();
      return;
    }
    var pop = document.createElement("div");
    pop.className = "bf-emoji-pop";
    EMOJIS.forEach(function (e) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = e;
      b.addEventListener("click", function () {
        els.ta.value += e;
        els.ta.focus();
        pop.remove();
      });
      pop.appendChild(b);
    });
    els.inrow.appendChild(pop);
  }

  function autoGrow() {
    els.ta.style.height = "auto";
    els.ta.style.height = Math.min(els.ta.scrollHeight, 120) + "px";
  }

  var pendingFile = null;
  function onFile() {
    var f = els.fileInput.files && els.fileInput.files[0];
    if (!f) return;
    pendingFile = f;
    els.attach.style.display = "block";
    els.attach.textContent = "📎 " + f.name + " (sent as context)";
  }

  function renderChips() {
    els.chips.innerHTML = "";
    (state.config.suggested_prompts || []).forEach(function (p) {
      var text = typeof p === "string" ? p : p && p.label ? p.label : "";
      if (!text) return;
      var chip = document.createElement("button");
      chip.className = "bf-chip";
      chip.textContent = text;
      chip.addEventListener("click", function () {
        api.sendMessage(text);
      });
      els.chips.appendChild(chip);
    });
  }

  function addMessage(who, text) {
    var row = document.createElement("div");
    row.className = "bf-row " + (who === "user" ? "bf-user" : "bf-bot");
    if (who === "bot") {
      var logo = logoUrl(state.config.theme || {});
      if (logo) {
        var av = document.createElement("img");
        av.className = "bf-avatar-sm";
        av.src = logo;
        av.alt = "";
        row.appendChild(av);
      }
    }
    var bubble = document.createElement("div");
    bubble.className = "bf-bubble";
    if (who === "bot") bubble.innerHTML = text ? renderMarkdown(text) : "";
    else bubble.textContent = text;
    row.appendChild(bubble);
    els.msgs.appendChild(row);
    els.msgs.scrollTop = els.msgs.scrollHeight;
    return bubble;
  }

  // Grounded answers carry "[1]"-style markers; without a source list they read to a
  // visitor as a glitch. Renders the sources the retrieval step actually returned.
  function citationLabel(c, i) {
    var meta = c.metadata || {};
    return (
      meta.title ||
      meta.filename ||
      meta.heading ||
      c.filename ||
      meta.source_url ||
      c.source_url ||
      "Source " + (i + 1)
    );
  }

  function renderCitations(bubble, cites, answer) {
    if (!cites || !cites.length || !answer) return;
    // Only list sources the answer actually references. Retrieval returns candidates even
    // when the model doesn't use them, and attributing "I don't know" to a document reads
    // as a mistake to a visitor.
    var used = [];
    for (var i = 0; i < cites.length; i++) {
      if (answer.indexOf("[" + (i + 1) + "]") !== -1) used.push({ c: cites[i], n: i + 1 });
    }
    if (!used.length) return;

    var wrap = document.createElement("div");
    wrap.className = "bf-cites";
    var label = document.createElement("span");
    label.className = "bf-cites-label";
    label.textContent = used.length === 1 ? "Source" : "Sources";
    wrap.appendChild(label);

    for (var j = 0; j < used.length; j++) {
      var c = used[j].c;
      var meta = c.metadata || {};
      var url = meta.source_url || c.source_url || "";
      var text = used[j].n + ". " + citationLabel(c, used[j].n - 1);
      var node;
      if (url && /^https?:\/\//i.test(url)) {
        node = document.createElement("a");
        node.href = url;
        node.target = "_blank";
        node.rel = "noopener noreferrer";
      } else {
        node = document.createElement("span");
      }
      node.className = "bf-cite";
      node.textContent = text; // textContent, not innerHTML — snippets are untrusted
      var snippet = (c.content || "").trim();
      if (snippet) node.title = snippet.slice(0, 300);
      wrap.appendChild(node);
    }
    bubble.appendChild(wrap);
    els.msgs.scrollTop = els.msgs.scrollHeight;
  }

  function typingBubble() {
    var row = document.createElement("div");
    row.className = "bf-row bf-bot";
    var bubble = document.createElement("div");
    bubble.className = "bf-bubble";
    bubble.innerHTML = '<span class="bf-typing"><i></i><i></i><i></i></span>';
    row.appendChild(bubble);
    els.msgs.appendChild(row);
    els.msgs.scrollTop = els.msgs.scrollHeight;
    return bubble;
  }

  function submit() {
    var text = els.ta.value.trim();
    if (!text || state.sending) return;
    els.ta.value = "";
    autoGrow();
    api.sendMessage(text);
  }

  async function sendMessage(text) {
    if (!text || state.sending) return;
    state.hasConversation = true;
    updateHome();
    if (PREVIEW) {
      // In preview mode there's no live backend turn — just echo locally so the design shows.
      addMessage("user", text);
      addMessage("bot", "This is a preview. Your real agent will reply here.");
      return;
    }
    if (!state.open) api.open();
    state.sending = true;
    els.send.disabled = true;
    var full = text;
    if (pendingFile) {
      full += "\n\n[Attached file: " + pendingFile.name + "]";
      try {
        if (pendingFile.type.indexOf("text") === 0 || /\.(txt|md|csv|json)$/i.test(pendingFile.name)) {
          full += "\n" + (await pendingFile.text()).slice(0, 4000);
        }
      } catch (e) {
        /* ignore */
      }
      pendingFile = null;
      els.attach.style.display = "none";
    }
    addMessage("user", text);
    var bubble = typingBubble();
    var acc = "";
    var cites = null;
    emit("message", { role: "user", content: text });

    // `id` is always sent so the server can tell this visitor's conversation from anyone
    // else's; anything the host page passed to `setUser()` rides alongside it.
    function chatBody(conversationId) {
      var visitor = { id: visitorId() };
      for (var k in state.visitor) {
        if (Object.prototype.hasOwnProperty.call(state.visitor, k)) visitor[k] = state.visitor[k];
      }
      if (!visitor.id) visitor.id = visitorId();
      return JSON.stringify({
        message: full,
        conversation_id: conversationId,
        stream: true,
        visitor: visitor,
      });
    }

    function post(conversationId) {
      return fetch(API + "/v1/public/agents/" + PUBLIC_KEY + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: chatBody(conversationId),
      });
    }

    try {
      var resp = await post(state.conversationId);
      // The stored conversation is gone, or belongs to someone else on a shared device.
      // Drop it and start a fresh thread rather than dead-ending the visitor on an error —
      // this is also the path that retires conversations created before ownership was
      // enforced, whose anonymous id no browser can present.
      if (resp.status === 404 && state.conversationId) {
        forget(STORE_KEY);
        state.conversationId = null;
        resp = await post(null);
      }
      if (!resp.ok || !resp.body) throw new Error("chat request failed (" + resp.status + ")");
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (var i = 0; i < parts.length; i++) {
          var line = parts[i].trim();
          if (line.indexOf("data:") !== 0) continue;
          var payload = line.slice(5).trim();
          if (!payload) continue;
          var ev;
          try {
            ev = JSON.parse(payload);
          } catch (e) {
            continue;
          }
          if (ev.type === "conversation" && ev.conversation_id) {
            state.conversationId = ev.conversation_id;
            store(STORE_KEY, ev.conversation_id);
            ensureSubscription(ev.conversation_id);
          } else if (ev.type === "citations" && ev.citations) {
            // Emitted once, before the provider stream. Held until the reply finishes so
            // the sources render under the completed answer.
            cites = ev.citations;
          } else if (ev.type === "token" && ev.delta) {
            acc += ev.delta;
            bubble.innerHTML = renderMarkdown(acc);
            els.msgs.scrollTop = els.msgs.scrollHeight;
          } else if (ev.type === "replace") {
            // The output guardrail suppressed or rewrote the reply after we had already
            // painted it (docs/11 §4-L5, ADR-049). Discard everything streamed for this turn
            // and show the replacement — `acc` is reset too, so the emitted `response`/
            // `message` events and any host-page integration see the safe text, not the
            // text that was withdrawn.
            acc = ev.delta || "";
            bubble.innerHTML = renderMarkdown(acc);
            els.msgs.scrollTop = els.msgs.scrollHeight;
          } else if (ev.type === "error") {
            acc += (acc ? "\n\n" : "") + "⚠ " + (ev.error || "something went wrong");
            bubble.innerHTML = renderMarkdown(acc);
          }
        }
      }
      // Nothing came back: the bot chose (or was made) to stay quiet — a conversation handed to
      // a human, or an owner whose plan is out. Take the typing indicator away rather than leave
      // "…" hanging; the visitor's message is already saved for the owner (docs/18 §8).
      if (!acc && bubble.parentNode) bubble.parentNode.removeChild(bubble);
      renderCitations(bubble, cites, acc);
      emit("response", { content: acc, citations: cites || [] });
      emit("message", { role: "assistant", content: acc, citations: cites || [] });
    } catch (err) {
      bubble.textContent = "⚠ " + (err && err.message ? err.message : "connection error");
    } finally {
      state.sending = false;
      els.send.disabled = false;
    }
  }

  // Listen socket: receive operator replies / handback pushes for this conversation.
  function ensureSubscription(cid) {
    if (state.ws || !cid) return;
    try {
      var wsBase = API.replace(/^http/, "ws");
      var ws = new WebSocket(wsBase + "/v1/public/agents/" + PUBLIC_KEY + "/subscribe?conversation_id=" + cid);
      state.ws = ws;
      ws.onmessage = function (e) {
        var ev;
        try {
          ev = JSON.parse(e.data);
        } catch (err) {
          return;
        }
        if (ev.type === "operator_message" && ev.content) {
          addMessage("bot", ev.content);
          emit("message", { role: "assistant", content: ev.content, operator: true });
        } else if (ev.type === "handback" && ev.content) {
          addMessage("bot", ev.content);
        }
      };
      ws.onclose = function () {
        state.ws = null;
      };
    } catch (err) {
      /* WS unavailable — degrade gracefully (operator replies still persist) */
    }
  }

  var api = {
    open: function () {
      if (!els.panel) return;
      state.open = true;
      els.panel.classList.add("bf-show");
      root.host.classList.add("bf-open"); // drives the <768px hide-launcher rule
      els.launcher.setAttribute("aria-expanded", "true");
      launcherContent(state.config.theme || {}, true);
      setTimeout(function () {
        els.ta && els.ta.focus();
      }, 50);
      emit("open");
    },
    close: function () {
      if (!els.panel) return;
      state.open = false;
      els.panel.classList.remove("bf-show");
      root.host.classList.remove("bf-open");
      els.launcher.setAttribute("aria-expanded", "false");
      launcherContent(state.config.theme || {}, false);
      emit("close");
    },
    toggle: function () {
      state.open ? api.close() : api.open();
    },
    sendMessage: function (text) {
      sendMessage(String(text || ""));
    },
    setUser: function (u) {
      state.visitor = u || {};
    },
    on: function (event, cb) {
      (listeners[event] = listeners[event] || []).push(cb);
    },
  };

  // ── Preview mode: apply live config overrides posted by the builder ─────────
  function previewListener(ev) {
    var d = ev && ev.data;
    if (!d || d.type !== "bf-preview-config" || !d.config) return;
    var incoming = d.config;
    var base = state.config || { theme: {} };
    // Optional builder-only backdrop for the preview *page* (never part of the widget/embed).
    if (typeof d.previewBackdrop === "string") {
      document.body.style.background = d.previewBackdrop;
    }
    // Merge posted config over the base (theme is replaced wholesale from the builder's full state).
    var merged = {
      agent_id: base.agent_id,
      name: incoming.name != null ? incoming.name : base.name,
      welcome_message: incoming.welcome_message != null ? incoming.welcome_message : base.welcome_message,
      suggested_prompts: incoming.suggested_prompts || base.suggested_prompts || [],
      theme: incoming.theme || base.theme || {},
    };
    // Apply in place — preserves whether the panel is currently open, and never force-opens it.
    applyConfig(merged);
  }


  // ── Proactive campaigns ────────────────────────────────────────────────────
  // A campaign opens the widget unprompted after a delay, optionally only on pages whose
  // URL contains a pattern. Deliberately conservative: it fires at most once per visitor
  // per campaign (sessionStorage), and never once a real conversation has started — an
  // unprompted message on top of someone's in-progress chat is an interruption, not a
  // greeting.
  function campaignSeen(id) {
    try {
      return sessionStorage.getItem("bf-campaign-" + id) === "1";
    } catch (e) {
      return false; // private mode: better to show it than to crash
    }
  }

  function markCampaignSeen(id) {
    try {
      sessionStorage.setItem("bf-campaign-" + id, "1");
    } catch (e) {}
  }

  function campaignMatchesUrl(pattern) {
    if (!pattern) return true;
    try {
      return location.href.indexOf(pattern) !== -1;
    } catch (e) {
      return false;
    }
  }

  function scheduleCampaigns() {
    var campaigns = (state.config && state.config.campaigns) || [];
    campaigns.forEach(function (c) {
      if (!c || !c.message || campaignSeen(c.id)) return;
      if (!campaignMatchesUrl(c.url_pattern)) return;
      var delay = Math.max(0, (c.delay_seconds || 10) * 1000);
      setTimeout(function () {
        // Re-checked at fire time: the visitor may have started chatting during the wait.
        if (state.hasConversation || state.open) return;
        markCampaignSeen(c.id);
        state.campaignShown = true;
        updateHome();
        addMessage("bot", c.message);
        api.open();
      }, delay);
    });
  }

  async function init() {
    if (PREVIEW) {
      // Start from an empty/default config; the parent posts the real one immediately.
      // Preview starts CLOSED, exactly like a real embed does for a visitor — the panel only
      // opens when the launcher is clicked, never as a side effect of a config change.
      state.config = { agent_id: null, name: "Assistant", welcome_message: "Hi! How can I help you today?", suggested_prompts: [], theme: {} };
      build();
      window.addEventListener("message", previewListener);
      try {
        parent.postMessage({ type: "bf-preview-ready" }, "*");
      } catch (e) {}
      window.Vicero = api;
      return;
    }
    try {
      var r = await fetch(API + "/v1/public/agents/" + PUBLIC_KEY + "/config");
      if (!r.ok) throw new Error("config " + r.status);
      state.config = await r.json();
    } catch (e) {
      console.error("[Vicero] could not load widget config:", e);
      return;
    }
    build();
    scheduleCampaigns();
    window.Vicero = api;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

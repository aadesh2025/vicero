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
    turnBlocks: [],
    restoring: false,
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
    "--bf-aline", "--bf-acfg", "--bf-g1", "--bf-g2", "--bf-g3", "--bf-g4", "--bf-g5",
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
  function lumOf(hex) {
    var c = rgbOf(hex).map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function contrastOf(a, b) {
    var x = lumOf(a), y = lumOf(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
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
      ".bf-lay .bf-chip{border:1px solid var(--bf-aline);background:var(--bf-bg);color:var(--bf-acfg);padding:7px 12px;font-weight:500;font-size:13px;text-align:left;border-radius:min(999px,calc(var(--bf-r)*2 + 2px));transition:background .15s}" +
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
          L + " .bf-chip:hover::after{transform:translateX(3px);color:var(--bf-acfg)}" +
          L + " .bf-foot{padding-top:6px}" +
          L + " .bf-inrow{background:transparent;border:0;border-top:1px solid var(--bf-border);border-radius:0;padding:8px 0 4px;box-shadow:none}" +
          L + " .bf-inrow:focus-within{box-shadow:none}" +
          L + " .bf-send{background:transparent;color:var(--bf-acfg)}"
        );
      case "paper":
        return (
          L + " .bf-head{justify-content:center;text-align:center;padding-block:18px 14px}" +
          L + " .bf-avatar,.bf-l-paper .bf-ini{display:none!important}" +
          L + " .bf-title{flex:none;max-width:100%;font-size:19px;font-weight:400;letter-spacing:-.01em;white-space:normal;overflow:visible;overflow-wrap:anywhere}" +
          L + " .bf-x{position:absolute;right:10px;top:12px}" +
          L + " .bf-msgs{gap:14px}" +
          L + " .bf-bot .bf-bubble{background:transparent;padding:0;font-size:15.5px;line-height:1.6;max-width:100%}" +
          L + " .bf-user .bf-bubble{background:transparent;color:var(--bf-muted);font-style:italic;padding:0;border-radius:0;text-align:right}" +
          L + " .bf-chips{counter-reset:p;flex-direction:column;gap:2px}" +
          L + " .bf-chip{counter-increment:p;display:flex;border:0;background:transparent;color:var(--bf-text);padding:5px 0;border-radius:0;font-size:15px}" +
          L + " .bf-chip::before{content:counter(p,lower-alpha) '.';color:var(--bf-acfg);font-style:italic;min-width:20px}" +
          L + " .bf-chip:hover{background:transparent;color:var(--bf-acfg)}" +
          L + " .bf-inrow{background:transparent;border:0;border-bottom:1px solid var(--bf-text);border-radius:0;padding-left:0;box-shadow:none}" +
          L + " .bf-inrow:focus-within{box-shadow:none}" +
          L + " .bf-send{background:transparent;color:var(--bf-acfg)}"
        );
      case "pebble":
        return (
          L + " .bf-head{flex-direction:column;justify-content:center;text-align:center;gap:6px;padding-block:16px 12px;background:var(--bf-bg2);border-bottom-color:transparent}" +
          L + " .bf-avatar,.bf-l-pebble .bf-ini:not([hidden]){width:46px;height:46px;font-size:16px}" +
          L + " .bf-ini:not([hidden]){display:grid}" +
          L + " .bf-title{flex:none;max-width:100%;white-space:normal;overflow:visible;overflow-wrap:anywhere}" +
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
          L + " .bf-user .bf-bubble{color:var(--bf-acfg)}" +
          L + " .bf-user .bf-bubble::before{content:'\\203A ';color:var(--bf-muted)}" +
          L + " .bf-chips{flex-direction:column;gap:1px;counter-reset:t}" +
          L + " .bf-chip{counter-increment:t;display:flex;border:0;border-radius:0;background:transparent;color:var(--bf-text);padding:3px 6px;margin-left:-6px;font-size:13px}" +
          L + " .bf-chip::before{content:'[' counter(t) ']';color:var(--bf-acfg);margin-right:8px}" +
          L + " .bf-chip:hover{background:var(--bf-soft)}" +
          L + " .bf-inrow{background:transparent;border:0;border-top:1px dashed var(--bf-border);border-radius:0;padding:8px 0 2px;box-shadow:none}" +
          L + " .bf-inrow:focus-within{box-shadow:none}" +
          L + " .bf-inrow::before{content:'\\203A';color:var(--bf-acfg);margin-right:8px;font-weight:600}" +
          L + " .bf-send{background:transparent;color:var(--bf-acfg);border-radius:0}" +
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
          L + ".bf-home .bf-chip::before{content:'\\2192';width:30px;height:30px;border-radius:9px;background:var(--bf-soft);color:var(--bf-acfg);display:grid;place-items:center}" +
          L + ".bf-home .bf-chip:last-child:nth-child(odd){grid-column:span 2}" +
          L + ".bf-home .bf-chip:hover{border-color:var(--bf-acfg);background:var(--bf-bg)}" +
          L + ":not(.bf-home) .bf-chips{display:none}"
        );
      case "command":
        return (
          L + " .bf-head{position:absolute;right:6px;top:6px;padding:0;background:transparent;border:0;z-index:2}" +
          L + " .bf-title,.bf-l-command .bf-avatar,.bf-l-command .bf-ini{display:none!important}" +
          L + " .bf-foot{order:-1;padding:10px 44px 0 10px}" +
          L + " .bf-inrow{background:transparent;border:0;border-bottom:1px solid var(--bf-border);border-radius:0;padding:8px 6px 12px;box-shadow:none}" +
          L + " .bf-inrow:focus-within{box-shadow:none}" +
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
          L + " .bf-tabs button.on{color:var(--bf-text);border-bottom-color:var(--bf-acfg)}" +
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
  function setExtraCss(layout, tuned, blocks) {
    var key = (tuned ? "t" : "") + (blocks ? "b" : "") + ":" + (layout || "");
    if (state.extraKey === key) return;
    state.extraKey = key;
    if (!tuned && !blocks) {
      if (els.extra && els.extra.parentNode) els.extra.parentNode.removeChild(els.extra);
      els.extra = null;
      return;
    }
    if (!els.extra) {
      els.extra = document.createElement("style");
      root.appendChild(els.extra);
    }
    els.extra.textContent =
      (tuned ? tuneCss() : "") +
      (layout ? layoutBaseCss() + layoutCss(layout) : "") +
      (blocks ? blocksCss() : "") +
      (layout ? LAYOUT_MOBILE_CSS : "");
  }

  function applyLayout(theme, p, layout) {
    var radius =
      typeof theme.corner_radius === "number" && theme.corner_radius >= 0 && theme.corner_radius <= 28
        ? Math.round(theme.corner_radius)
        : null;
    var dens = DENSITY[theme.density] || null;
    var tuned = !!(layout || radius !== null || dens);
    var blocks = blocksOn(theme);

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
      v["--bf-g1"] = rgba(p.bg, 0.58);
      v["--bf-g2"] = rgba(p.bg, 0.7);
      v["--bf-g3"] = rgba(p.text, 0.1);
      v["--bf-g4"] = rgba(p.text, 0.85);
      v["--bf-g5"] = rgba(p.bg, 0.6);
    }
    if (layout || blocks) {
      v["--bf-soft"] = mixHex(p.accent, p.bg, 12);
      v["--bf-aline"] = mixHex(p.accent, mixHex(p.text, p.bg, 11), 40);
      // The accent used as TEXT on the panel: a dark accent on a dark panel (or the reverse) is
      // unreadable, so fall back to the text colour when the contrast is below 3:1.
      v["--bf-acfg"] = contrastOf(p.accent, p.bg) >= 3 ? p.accent : p.text;
    }
    LAYOUT_VARS.forEach(function (k) {
      if (v[k] !== undefined) host.style.setProperty(k, v[k]);
      else host.style.removeProperty(k);
    });
    // Screen readers announce new messages in a layout; the legacy DOM is left untouched.
    if (layout) els.msgs.setAttribute("aria-live", "polite");
    else els.msgs.removeAttribute("aria-live");
    setExtraCss(layout, tuned, blocks);
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

  // ── Interactive blocks (docs/27 Phase 2) ──────────────────────────────────
  // Opt-in per agent: nothing here runs, and no CSS is injected, unless the public config carries
  // `theme.interactive_blocks.enabled`. A block is data from the server (already validated against hard
  // caps); the widget only ever writes its strings with textContent, only loads https images (or the
  // widget's own API origin, for signed catalogue photos), and never executes anything from a block.
  // A tap goes back as an ordinary user message plus `block_reply`, so the agent, the guardrails and
  // the rate limits see a normal turn.
  function blocksOn(theme) {
    return !!(theme && theme.interactive_blocks && theme.interactive_blocks.enabled === true);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function btn(cls, text) {
    var b = el("button", cls, text);
    b.type = "button";
    return b;
  }
  function safeImg(url) {
    if (typeof url !== "string") return "";
    if (/^https:\/\//i.test(url)) return url;
    return url.indexOf(API + "/") === 0 ? url : "";
  }
  function svgIcon(path, extra) {
    var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", path);
    s.appendChild(p);
    if (extra) s.setAttribute("class", extra);
    return s;
  }
  var STAR_PATH = "M12 2.8l2.8 5.9 6.4.8-4.7 4.4 1.2 6.3L12 17.1l-5.7 3.1 1.2-6.3L2.8 9.5l6.4-.8z";
  var CHECK_PATH = "M5 12.5l4.5 4.5L19 7";

  function blocksCss() {
    var b = ".bf-blk";
    return (
      ".bf-blockrow{display:block}" +
      b + "{width:100%;max-width:100%;font-size:var(--bf-fs,14px)}" +
      b + " button{cursor:pointer;font-family:inherit}" +
      b + " button:disabled{cursor:default}" +
      b + " :focus-visible{outline:2px solid var(--bf-accent);outline-offset:2px}" +
      ".bf-btn{border:0;background:var(--bf-accent);color:var(--bf-on-accent);border-radius:var(--bf-rb,8px);padding:8px 13px;font-weight:600;font-size:13px;display:inline-flex;align-items:center;justify-content:center;gap:6px}" +
      ".bf-btn:hover:not(:disabled){filter:brightness(1.08)}" +
      ".bf-btn.bf-ghost{background:transparent;color:var(--bf-text);border:1px solid var(--bf-border)}" +
      ".bf-btn.bf-ok{background:#16a34a;color:#fff}" +
      ".bf-btn:disabled:not(.bf-ok):not(.bf-on){opacity:.5}" +
      ".bf-btn svg,.bf-star svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}" +
      // options
      ".bf-prog{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--bf-muted);margin-bottom:6px}" +
      ".bf-prog i{flex:1;height:4px;border-radius:2px;background:var(--bf-border);position:relative;overflow:hidden}" +
      ".bf-prog b{position:absolute;inset:0 auto 0 0;background:var(--bf-accent)}" +
      ".bf-opts{display:flex;flex-direction:column;gap:6px}" +
      ".bf-opt{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:1px solid var(--bf-border);background:var(--bf-bg);color:var(--bf-text);border-radius:var(--bf-rc,10px);padding:10px 12px;font-size:13.5px}" +
      ".bf-opt:hover:not(:disabled){border-color:var(--bf-acfg)}" +
      ".bf-opt small{display:block;color:var(--bf-muted);font-size:12px}" +
      ".bf-opt .bf-rad{width:16px;height:16px;border-radius:50%;border:1.5px solid var(--bf-border);flex:none}" +
      ".bf-opt.bf-on{border-color:var(--bf-acfg);background:var(--bf-soft,rgba(127,127,127,.14))}" +
      ".bf-opt.bf-on .bf-rad{border-color:var(--bf-acfg);background:var(--bf-accent);box-shadow:inset 0 0 0 3px var(--bf-bg)}" +
      ".bf-is-done .bf-opt:not(.bf-on),.bf-is-done .bf-card:not(.bf-on),.bf-is-done .bf-day:not(.bf-on),.bf-is-done .bf-slot:not(.bf-on){opacity:.45}" +
      ".bf-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}" +
      // cards
      ".bf-carnav{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:12px;color:var(--bf-muted)}" +
      ".bf-carnav div{display:flex;gap:4px}" +
      ".bf-carnav button{width:26px;height:26px;border:1px solid var(--bf-border);border-radius:50%;background:transparent;color:inherit;font-size:14px;line-height:1}" +
      ".bf-carousel{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:6px;scrollbar-width:none}" +
      ".bf-carousel::-webkit-scrollbar{display:none}" +
      ".bf-card{flex:0 0 78%;scroll-snap-align:start;border:1px solid var(--bf-border);border-radius:var(--bf-rc,10px);background:var(--bf-bg);overflow:hidden;display:flex;flex-direction:column}" +
      ".bf-card.bf-solo{flex-basis:100%}" +
      ".bf-art{height:96px;position:relative;background:var(--bf-bg2);display:flex;align-items:center;justify-content:center;color:var(--bf-muted);font-size:11px;overflow:hidden}" +
      ".bf-art img{width:100%;height:100%;object-fit:cover;display:block}" +
      ".bf-art span{position:absolute;left:8px;bottom:8px;font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.9);color:#222}" +
      ".bf-cb{padding:10px 12px 12px;display:flex;flex-direction:column;gap:6px;flex:1}" +
      ".bf-ct{font-weight:600;font-size:14px;line-height:1.25}" +
      ".bf-cn{font-size:12px;color:var(--bf-muted);margin-top:2px}" +
      ".bf-price{font-weight:700;font-size:15px}" +
      ".bf-cf{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-top:auto}" +
      // date & time
      ".bf-days{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;scrollbar-width:none}" +
      ".bf-day{flex:0 0 auto;min-width:58px;border:1px solid var(--bf-border);background:var(--bf-bg);color:var(--bf-text);border-radius:var(--bf-rc,10px);padding:8px 6px;font-size:12px;font-weight:500}" +
      ".bf-day:hover:not(:disabled),.bf-slot:hover:not(:disabled){border-color:var(--bf-acfg)}" +
      ".bf-day.bf-on,.bf-slot.bf-on{background:var(--bf-accent);color:var(--bf-on-accent);border-color:var(--bf-acfg)}" +
      ".bf-day:disabled:not(.bf-on){opacity:.4;text-decoration:line-through}" +
      ".bf-slots{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:8px}" +
      ".bf-slot{border:1px solid var(--bf-border);background:var(--bf-bg);color:var(--bf-text);border-radius:var(--bf-rb,8px);padding:9px 6px;font-size:13px;font-weight:500}" +
      ".bf-slot small{display:block;font-size:10.5px;color:inherit;opacity:.7;font-weight:400}" +
      ".bf-slot:disabled:not(.bf-on){opacity:.4}" +
      // counter
      ".bf-counter{display:flex;align-items:center;gap:12px;border:1px solid var(--bf-border);border-radius:var(--bf-rc,10px);padding:8px 8px 8px 14px;background:var(--bf-bg)}" +
      ".bf-counter span{flex:1;font-size:13px}" +
      ".bf-counter b{font-size:18px;min-width:20px;text-align:center}" +
      ".bf-counter button{width:30px;height:30px;border:1px solid var(--bf-border);border-radius:50%;background:transparent;color:inherit;font-size:16px;line-height:1}" +
      // form
      ".bf-form{border:1px solid var(--bf-border);border-radius:var(--bf-rc,10px);padding:12px;background:var(--bf-bg);display:grid;gap:9px}" +
      ".bf-form label{display:grid;gap:4px;font-size:12px;color:var(--bf-muted);font-weight:500}" +
      ".bf-input{width:100%;background:var(--bf-input);color:var(--bf-text);border:1px solid var(--bf-border);border-radius:var(--bf-rb,8px);padding:9px 11px;font-size:13.5px;outline:none}" +
      ".bf-input:focus{border-color:var(--bf-acfg)}" +
      ".bf-input.bf-bad{border-color:#dc2626}" +
      ".bf-err{color:#dc2626;font-size:11.5px}" +
      ".bf-err:empty{display:none}" +
      ".bf-is-done .bf-form{opacity:.75}" +
      // rating / steps / handoff
      ".bf-rate{border:1px solid var(--bf-border);border-radius:var(--bf-rc,10px);padding:12px;background:var(--bf-bg)}" +
      ".bf-rate p{margin:0 0 6px;font-weight:600;font-size:13.5px}" +
      ".bf-stars{display:flex;gap:4px}" +
      ".bf-star{border:0;background:transparent;color:var(--bf-border);padding:2px;width:34px;height:34px;display:grid;place-items:center}" +
      ".bf-star svg{width:26px;height:26px;fill:currentColor;stroke:none}" +
      ".bf-star.bf-lit{color:#f5a524}" +
      ".bf-reasons{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}" +
      ".bf-reasons .bf-chip{padding:5px 10px;font-size:12px}" +
      ".bf-reasons .bf-chip.bf-on{background:var(--bf-accent);color:var(--bf-on-accent);border-color:var(--bf-acfg)}" +
      ".bf-steps{list-style:none;margin:0;padding:12px;border:1px solid var(--bf-border);border-radius:var(--bf-rc,10px);background:var(--bf-bg);display:grid;gap:0}" +
      ".bf-steps li{display:grid;grid-template-columns:18px 1fr auto;gap:10px;align-items:start;position:relative;padding-bottom:12px;font-size:13px}" +
      ".bf-steps li:last-child{padding-bottom:0}" +
      ".bf-steps li::before{content:'';position:absolute;left:8px;top:16px;bottom:-2px;width:2px;background:var(--bf-border)}" +
      ".bf-steps li.bf-done::before{background:var(--bf-accent)}" +
      ".bf-steps li:last-child::before{display:none}" +
      ".bf-steps .bf-pt{width:18px;height:18px;border-radius:50%;border:2px solid var(--bf-border);background:var(--bf-bg);position:relative;z-index:1;display:grid;place-items:center}" +
      ".bf-steps .bf-done .bf-pt{background:var(--bf-accent);border-color:var(--bf-acfg);color:var(--bf-on-accent)}" +
      ".bf-steps .bf-done .bf-pt svg{width:11px;height:11px;fill:none;stroke:currentColor;stroke-width:3}" +
      ".bf-steps .bf-now .bf-pt{border-color:var(--bf-acfg)}" +
      ".bf-steps .bf-now .bf-pt::after{content:'';width:6px;height:6px;border-radius:50%;background:var(--bf-accent)}" +
      ".bf-steps li:not(.bf-done):not(.bf-now) span{color:var(--bf-muted)}" +
      ".bf-steps small{color:var(--bf-muted);font-size:11.5px}" +
      ".bf-eta{display:inline-block;margin-top:8px;font-size:11.5px;background:var(--bf-soft,rgba(127,127,127,.14));padding:2px 8px;border-radius:999px;font-weight:600}" +
      ".bf-ho{display:flex;align-items:center;gap:11px;border:1px solid var(--bf-border);border-radius:var(--bf-rc,10px);padding:11px 12px;background:var(--bf-bg)}" +
      ".bf-ho i{width:36px;height:36px;border-radius:50%;background:var(--bf-accent);color:var(--bf-on-accent);display:grid;place-items:center;font-style:normal;font-weight:700;font-size:13px;flex:none}" +
      ".bf-ho b{display:block;font-size:13.5px}" +
      ".bf-ho small{color:var(--bf-muted);font-size:12px}" +
      // per-layout looks: the block shapes above, re-skinned by the active layout
      ".bf-l-glass .bf-opt,.bf-l-glass .bf-card,.bf-l-glass .bf-form,.bf-l-glass .bf-rate,.bf-l-glass .bf-day,.bf-l-glass .bf-slot,.bf-l-glass .bf-counter,.bf-l-glass .bf-steps,.bf-l-glass .bf-ho{background:var(--bf-g2);border-color:var(--bf-g3)}" +
      ".bf-l-hairline .bf-opt{border:0;border-bottom:1px solid var(--bf-border);border-radius:0;background:transparent;padding:11px 2px}" +
      ".bf-l-hairline .bf-opts{gap:0;border-top:1px solid var(--bf-border)}" +
      ".bf-l-paper .bf-opts{counter-reset:o;gap:2px}" +
      ".bf-l-paper .bf-opt{counter-increment:o;border:0;background:transparent;padding:5px 0;border-radius:0;font-size:15px}" +
      ".bf-l-paper .bf-opt .bf-rad{display:none}" +
      ".bf-l-paper .bf-opt::before{content:counter(o,lower-alpha) '.';color:var(--bf-acfg);font-style:italic;min-width:20px}" +
      ".bf-l-paper .bf-opt.bf-on{background:transparent;color:var(--bf-acfg)}" +
      ".bf-l-pebble .bf-card,.bf-l-pebble .bf-form,.bf-l-pebble .bf-rate,.bf-l-pebble .bf-ho,.bf-l-pebble .bf-steps{border-radius:20px}" +
      ".bf-l-console .bf-opts{counter-reset:o;gap:1px}" +
      ".bf-l-console .bf-opt{counter-increment:o;border:0;border-radius:0;background:transparent;padding:3px 6px;margin-left:-6px;font-size:13px}" +
      ".bf-l-console .bf-opt .bf-rad{display:none}" +
      ".bf-l-console .bf-opt::before{content:'[' counter(o) ']';color:var(--bf-acfg)}" +
      ".bf-l-console .bf-opt.bf-on,.bf-l-console .bf-opt:hover:not(:disabled){background:var(--bf-soft)}" +
      ".bf-l-console .bf-card,.bf-l-console .bf-form,.bf-l-console .bf-rate,.bf-l-console .bf-ho,.bf-l-console .bf-counter,.bf-l-console .bf-steps{border-style:dashed;background:transparent}" +
      ".bf-l-console .bf-btn{border-radius:0;background:transparent;color:var(--bf-acfg);border:1px solid var(--bf-accent)}" +
      ".bf-l-console .bf-btn::before{content:'[ '}.bf-l-console .bf-btn::after{content:' ]'}" +
      ".bf-l-console .bf-btn.bf-ok{background:var(--bf-accent);color:var(--bf-on-accent)}" +
      ".bf-l-console .bf-art{height:44px}" +
      ".bf-l-block .bf-opt,.bf-l-block .bf-card,.bf-l-block .bf-form,.bf-l-block .bf-rate,.bf-l-block .bf-ho,.bf-l-block .bf-day,.bf-l-block .bf-slot,.bf-l-block .bf-counter,.bf-l-block .bf-steps{border:2px solid var(--bf-text);box-shadow:3px 3px 0 var(--bf-text)}" +
      ".bf-l-block .bf-btn{border:2px solid var(--bf-text);box-shadow:3px 3px 0 var(--bf-text);color:var(--bf-on-accent)}" +
      ".bf-l-block .bf-btn.bf-ghost{color:var(--bf-text)}" +
      ".bf-l-block .bf-art{border-bottom:2px solid var(--bf-text)}" +
      "@media (prefers-reduced-motion:reduce){.bf-carousel{scroll-behavior:auto}}"
    );
  }

  // The visitor answered: lock the block and keep what they chose on screen.
  function lock(wrap) {
    wrap.classList.add("bf-is-done");
    var els2 = wrap.querySelectorAll("button,input,select,textarea");
    for (var i = 0; i < els2.length; i++) els2[i].disabled = true;
  }

  // Send a tap as a normal user message (docs/27 §5): the agent reads `text`; `block_reply` only lets the
  // server mark the block answered so it reloads read-only.
  function answer(block, wrap, value, text, mark) {
    lock(wrap);
    if (mark) mark();
    var reply = { block_id: block.id, value: value };
    if (!state.sending) {
      sendMessage(text, reply);
      return;
    }
    // The block event arrives just before the stream ends, so a fast tap can land while the previous turn
    // is still closing. Wait for it (a few frames) instead of dropping the visitor's answer.
    var tries = 0;
    var t = setInterval(function () {
      if (!state.sending || ++tries > 100) {
        clearInterval(t);
        if (!state.sending) sendMessage(text, reply);
      }
    }, 50);
  }

  function arrowNav(container, selector, horizontal) {
    container.addEventListener("keydown", function (e) {
      var back = horizontal ? "ArrowLeft" : "ArrowUp";
      var fwd = horizontal ? "ArrowRight" : "ArrowDown";
      if (e.key !== back && e.key !== fwd) return;
      var items = Array.prototype.slice.call(container.querySelectorAll(selector)).filter(function (n) {
        return !n.disabled;
      });
      var at = items.indexOf(root.activeElement);
      if (at === -1) return;
      e.preventDefault();
      var next = items[Math.max(0, Math.min(items.length - 1, at + (e.key === fwd ? 1 : -1)))];
      if (next) {
        next.focus();
        if (horizontal && next.scrollIntoView) next.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    });
  }

  function bOptions(block, wrap) {
    var picked = block.resolved ? [].concat(block.resolved.value == null ? [] : block.resolved.value) : [];
    if (block.progress) {
      var prog = el("div", "bf-prog");
      prog.appendChild(el("span", null, "Step " + block.progress.step + " of " + block.progress.total));
      var bar = el("i");
      var fill = el("b");
      fill.style.width = Math.round((block.progress.step / block.progress.total) * 100) + "%";
      bar.appendChild(fill);
      prog.appendChild(bar);
      wrap.appendChild(prog);
    }
    var list = el("div", "bf-opts");
    list.setAttribute("role", block.multi ? "group" : "radiogroup");
    var buttons = [];
    block.items.forEach(function (it) {
      var b = btn("bf-opt");
      b.setAttribute("data-id", it.id);
      b.appendChild(el("span", "bf-rad"));
      var t = el("span", null, it.label);
      if (it.sub) t.appendChild(el("small", null, it.sub));
      b.appendChild(t);
      if (picked.indexOf(it.id) !== -1) b.classList.add("bf-on");
      b.addEventListener("click", function () {
        if (block.multi) {
          b.classList.toggle("bf-on");
          b.setAttribute("aria-pressed", String(b.classList.contains("bf-on")));
          return;
        }
        answer(block, wrap, it.id, it.label, function () {
          b.classList.add("bf-on");
        });
      });
      buttons.push(b);
      list.appendChild(b);
    });
    wrap.appendChild(list);
    arrowNav(list, ".bf-opt", false);
    if (block.multi) {
      var send = btn("bf-btn", "Send");
      send.style.marginTop = "8px";
      send.addEventListener("click", function () {
        var chosen = buttons.filter(function (b) {
          return b.classList.contains("bf-on");
        });
        if (!chosen.length) return;
        var ids = chosen.map(function (b) { return b.getAttribute("data-id"); });
        var labels = chosen.map(function (b) { return b.textContent; });
        answer(block, wrap, ids, labels.join(", "));
      });
      wrap.appendChild(send);
    }
  }

  function bCards(block, wrap) {
    var chosen = block.resolved && block.resolved.value && typeof block.resolved.value === "object" ? block.resolved.value : null;
    var solo = block.items.length === 1;
    var track = el("div", "bf-carousel");
    track.setAttribute("role", "group");
    block.items.forEach(function (c) {
      var card = el("div", "bf-card" + (solo ? " bf-solo" : ""));
      var art = el("div", "bf-art");
      var src = safeImg(c.image_url);
      if (src) {
        var img = document.createElement("img");
        img.alt = "";
        img.loading = "lazy";
        img.addEventListener("error", function () {
          if (img.parentNode) img.parentNode.removeChild(img); // keep the box: no layout jump
        });
        img.src = src;
        art.appendChild(img);
      }
      if (c.badge) art.appendChild(el("span", null, c.badge));
      card.appendChild(art);
      var body = el("div", "bf-cb");
      var head = el("div");
      head.appendChild(el("div", "bf-ct", c.title));
      if (c.subtitle) head.appendChild(el("div", "bf-cn", c.subtitle));
      body.appendChild(head);
      var foot = el("div", "bf-cf");
      if (c.price) foot.appendChild(el("span", "bf-price", c.price));
      var acts = el("div", "bf-actions");
      acts.style.marginTop = "0";
      (c.actions || []).forEach(function (a, i) {
        var b = btn("bf-btn" + (i ? " bf-ghost" : ""), a.label);
        if (chosen && chosen.card === c.id && chosen.action === a.id) {
          b.classList.add("bf-on");
          card.classList.add("bf-on");
        }
        b.addEventListener("click", function () {
          answer(block, wrap, { card: c.id, action: a.id }, a.label + ": " + c.title, function () {
            b.classList.add("bf-on");
            b.classList.remove("bf-ghost");
            card.classList.add("bf-on");
          });
        });
        acts.appendChild(b);
      });
      foot.appendChild(acts);
      body.appendChild(foot);
      card.appendChild(body);
      track.appendChild(card);
    });
    if (block.items.length > 1) {
      var nav = el("div", "bf-carnav");
      nav.appendChild(el("span", null, block.items.length + " items"));
      var arrows = el("div");
      [["‹", "Previous", -1], ["›", "Next", 1]].forEach(function (a) {
        var b = btn(null, a[0]);
        b.setAttribute("aria-label", a[1]);
        b.addEventListener("click", function () {
          track.scrollBy({ left: a[2] * track.clientWidth * 0.8, behavior: "smooth" });
        });
        arrows.appendChild(b);
      });
      nav.appendChild(arrows);
      wrap.appendChild(nav);
    }
    wrap.appendChild(track);
    arrowNav(track, ".bf-btn", true);
  }

  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  var PHONE_RE = /^[+\d][\d\s().-]{6,18}$/;
  function bForm(block, wrap) {
    var form = el("form", "bf-form");
    form.noValidate = true;
    var done = block.resolved && block.resolved.value && typeof block.resolved.value === "object" ? block.resolved.value : null;
    var inputs = [];
    block.fields.forEach(function (f) {
      var label = el("label", null, f.label + (f.required ? "" : " (optional)"));
      var input = el("input", "bf-input");
      input.type = f.type === "email" ? "email" : f.type === "tel" ? "tel" : "text";
      input.name = f.id;
      input.autocomplete = f.type === "email" ? "email" : f.type === "tel" ? "tel" : "off";
      input.maxLength = 200;
      if (done && done[f.id] != null) input.value = String(done[f.id]);
      var err = el("span", "bf-err");
      err.setAttribute("role", "alert");
      label.appendChild(input);
      label.appendChild(err);
      form.appendChild(label);
      inputs.push({ f: f, input: input, err: err });
    });
    var submit = btn("bf-btn", block.submit_label || "Send");
    submit.type = "submit";
    if (block.resolved) {
      // Values are not stored with the block (they are in the message), so a reloaded form is just "sent".
      submit.textContent = "Sent";
      submit.classList.add("bf-ok");
    }
    form.appendChild(submit);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var ok = true;
      var values = {};
      inputs.forEach(function (r) {
        var v = r.input.value.trim();
        var bad = "";
        if (!v && r.f.required) bad = "Required";
        else if (v && r.f.type === "email" && !EMAIL_RE.test(v)) bad = "Enter a valid email address.";
        else if (v && r.f.type === "tel" && !PHONE_RE.test(v)) bad = "Enter a valid phone number.";
        r.input.classList.toggle("bf-bad", !!bad);
        r.input.setAttribute("aria-invalid", bad ? "true" : "false");
        r.err.textContent = bad;
        if (bad) ok = false;
        values[r.f.id] = v;
      });
      if (!ok) return;
      var text = inputs
        .filter(function (r) { return values[r.f.id]; })
        .map(function (r) { return r.f.label + ": " + values[r.f.id]; })
        .join("; ");
      answer(block, wrap, values, text, function () {
        submit.textContent = "Sent";
        submit.classList.add("bf-ok");
      });
    });
    wrap.appendChild(form);
    // Never in the builder preview: a focus() inside the iframe would pull the cursor out of whatever
    // control the designer is typing in, on every config change.
    if (!block.resolved && !state.restoring && !PREVIEW) {
      setTimeout(function () {
        if (inputs[0] && inputs[0].input.focus) inputs[0].input.focus({ preventScroll: true });
      }, 60);
    }
  }

  function bDatetime(block, wrap) {
    var done = block.resolved && block.resolved.value && typeof block.resolved.value === "object" ? block.resolved.value : null;
    var days = el("div", "bf-days");
    days.setAttribute("role", "radiogroup");
    var slots = el("div", "bf-slots");
    slots.setAttribute("role", "radiogroup");
    var day = done ? done.day : null;
    block.days.forEach(function (d) {
      var b = btn("bf-day", d.label);
      b.disabled = !!d.disabled;
      if (done && done.day === d.id) b.classList.add("bf-on");
      b.addEventListener("click", function () {
        day = d.id;
        var all = days.querySelectorAll(".bf-day");
        for (var i = 0; i < all.length; i++) all[i].classList.toggle("bf-on", all[i] === b);
      });
      days.appendChild(b);
    });
    block.slots.forEach(function (s) {
      var b = btn("bf-slot", s.label);
      if (s.sub) b.appendChild(el("small", null, s.sub));
      b.disabled = !!s.disabled;
      if (done && done.slot === s.id) b.classList.add("bf-on");
      b.addEventListener("click", function () {
        if (!day) {
          var first = days.querySelector(".bf-day:not(:disabled)");
          if (first) first.focus();
          return;
        }
        var d = block.days.filter(function (x) { return x.id === day; })[0];
        answer(block, wrap, { day: day, slot: s.id }, (d ? d.label : day) + ", " + s.label, function () {
          b.classList.add("bf-on");
        });
      });
      slots.appendChild(b);
    });
    wrap.appendChild(days);
    wrap.appendChild(slots);
    arrowNav(days, ".bf-day", true);
    arrowNav(slots, ".bf-slot", false);
  }

  function bCounter(block, wrap) {
    var n = block.resolved && typeof block.resolved.value === "number" ? block.resolved.value : block.default;
    var c = el("div", "bf-counter");
    c.appendChild(el("span", null, block.label));
    var minus = btn(null, "−");
    minus.setAttribute("aria-label", "Fewer");
    var out = el("b", null, n);
    out.setAttribute("aria-live", "polite");
    var plus = btn(null, "+");
    plus.setAttribute("aria-label", "More");
    minus.addEventListener("click", function () {
      n = Math.max(block.min, n - 1);
      out.textContent = n;
    });
    plus.addEventListener("click", function () {
      n = Math.min(block.max, n + 1);
      out.textContent = n;
    });
    c.appendChild(minus);
    c.appendChild(out);
    c.appendChild(plus);
    var ok = btn("bf-btn", "Confirm");
    ok.style.marginTop = "8px";
    ok.addEventListener("click", function () {
      answer(block, wrap, n, block.label + ": " + n);
    });
    wrap.appendChild(c);
    wrap.appendChild(ok);
  }

  function bRating(block, wrap) {
    var done = block.resolved && block.resolved.value && typeof block.resolved.value === "object" ? block.resolved.value : null;
    var box = el("div", "bf-rate");
    box.appendChild(el("p", null, block.prompt));
    var stars = el("div", "bf-stars");
    stars.setAttribute("role", "radiogroup");
    var starBtns = [];
    var score = done ? done.stars : 0;
    function paint(n) {
      starBtns.forEach(function (s, i) { s.classList.toggle("bf-lit", i < n); });
    }
    var reasonsBox = el("div", "bf-reasons");
    var sendBtn = btn("bf-btn", "Send");
    sendBtn.style.marginTop = "8px";
    sendBtn.style.display = "none";
    function finish(reasons) {
      var text = score + "/5" + (reasons.length ? " - " + reasons.join(", ") : "");
      answer(block, wrap, { stars: score, reasons: reasons }, text, function () { paint(score); });
    }
    function showReasons() {
      reasonsBox.innerHTML = "";
      var list = (score >= 4 ? block.reasons_good : block.reasons_bad) || [];
      if (!list.length) return finish([]);
      list.forEach(function (r) {
        var c = btn("bf-chip", r);
        c.addEventListener("click", function () {
          c.classList.toggle("bf-on");
          c.setAttribute("aria-pressed", String(c.classList.contains("bf-on")));
        });
        reasonsBox.appendChild(c);
      });
      sendBtn.style.display = "";
    }
    for (var i = 1; i <= 5; i++) {
      (function (n) {
        var s = btn("bf-star");
        s.setAttribute("aria-label", n + " star" + (n > 1 ? "s" : ""));
        s.appendChild(svgIcon(STAR_PATH));
        s.addEventListener("mouseenter", function () { if (!score) paint(n); });
        s.addEventListener("click", function () {
          score = n;
          paint(n);
          showReasons();
        });
        starBtns.push(s);
        stars.appendChild(s);
      })(i);
    }
    stars.addEventListener("mouseleave", function () { paint(score); });
    sendBtn.addEventListener("click", function () {
      var chosen = Array.prototype.slice.call(reasonsBox.querySelectorAll(".bf-chip.bf-on")).map(function (c) { return c.textContent; });
      finish(chosen);
    });
    box.appendChild(stars);
    box.appendChild(reasonsBox);
    box.appendChild(sendBtn);
    wrap.appendChild(box);
    arrowNav(stars, ".bf-star", true);
    if (done) {
      paint(score);
      sendBtn.style.display = "none";
      var picked = [].concat(done.reasons || []);
      picked.forEach(function (r) {
        var c = btn("bf-chip bf-on", r);
        reasonsBox.appendChild(c);
      });
    }
  }

  function bSteps(block, wrap) {
    var ol = el("ol", "bf-steps");
    block.steps.forEach(function (s) {
      var li = el("li", s.state === "done" ? "bf-done" : s.state === "now" ? "bf-now" : "");
      var pt = el("span", "bf-pt");
      if (s.state === "done") pt.appendChild(svgIcon(CHECK_PATH));
      li.appendChild(pt);
      li.appendChild(el("span", null, s.label));
      li.appendChild(el("small", null, s.time || ""));
      ol.appendChild(li);
    });
    wrap.appendChild(ol);
    if (block.eta) wrap.appendChild(el("span", "bf-eta", "ETA " + block.eta));
  }

  function bHandoff(block, wrap) {
    var ho = el("div", "bf-ho");
    ho.appendChild(el("i", null, String(block.agent_name || "?").charAt(0).toUpperCase()));
    var t = el("div");
    t.appendChild(el("b", null, block.agent_name));
    t.appendChild(el("small", null, block.state === "joined" ? "Joined the chat" : block.state === "joining" ? "Joining..." : "In the queue"));
    ho.appendChild(t);
    wrap.appendChild(ho);
  }

  var BLOCK_RENDERERS = {
    options: bOptions, cards: bCards, form: bForm, datetime: bDatetime,
    counter: bCounter, rating: bRating, steps: bSteps, handoff: bHandoff,
  };

  // Render one block as a bot row. A block type this build does not know, or one whose data is malformed,
  // degrades to its plain-text version instead of throwing or showing nothing.
  function renderBlock(block) {
    if (!block || typeof block !== "object" || !els.msgs) return null;
    var row = el("div", "bf-row bf-bot bf-blockrow");
    var wrap = el("div", "bf-blk bf-b-" + String(block.type).replace(/[^a-z]/g, ""));
    var fn = BLOCK_RENDERERS[block.type];
    try {
      if (!fn) throw new Error("unknown block");
      fn(block, wrap);
      if (block.resolved) lock(wrap);
    } catch (e) {
      wrap = null;
      var fb = el("div", "bf-bubble");
      fb.textContent = block.fallback_text || "";
      if (!fb.textContent) return null;
      row.appendChild(fb);
    }
    if (wrap) row.appendChild(wrap);
    els.msgs.appendChild(row);
    els.msgs.scrollTop = els.msgs.scrollHeight;
    state.turnBlocks.push(row);
    return row;
  }

  // The turn was withdrawn by the output guard (a `replace` event): its blocks go with it.
  function dropTurnBlocks() {
    state.turnBlocks.forEach(function (r) { if (r.parentNode) r.parentNode.removeChild(r); });
    state.turnBlocks = [];
  }

  // Reload mid-conversation: only agents with blocks on restore history, so the legacy widget — which has
  // never restored a thread — behaves exactly as before.
  async function restoreHistory() {
    if (!blocksOn((state.config || {}).theme) || !state.conversationId || PREVIEW) return;
    try {
      var r = await fetch(
        API + "/v1/public/agents/" + PUBLIC_KEY + "/conversations/" + state.conversationId +
          "/messages?visitor_id=" + encodeURIComponent(visitorId())
      );
      if (r.status === 404) {
        forget(STORE_KEY);
        state.conversationId = null;
        return;
      }
      if (!r.ok) return;
      var msgs = await r.json();
      if (!msgs || !msgs.length || state.hasConversation) return;
      state.restoring = true;
      state.hasConversation = true;
      msgs.forEach(function (m) {
        if (m.text) addMessage(m.role === "user" ? "user" : "bot", m.text);
        (m.blocks || []).forEach(function (b) { renderBlock(b); });
      });
      state.turnBlocks = [];
      state.restoring = false;
      updateHome();
    } catch (e) {
      state.restoring = false; /* history is a nicety; the chat itself still works */
    }
  }

  // Builder preview only: show the enabled block types with sample data so the designer can judge the
  // look of the chosen layout. Never used on a real embed.
  function previewBlocks(flags) {
    var on = function (t) { return flags[t] !== false; };
    var samples = [];
    if (on("options")) samples.push({ id: "p1", type: "options", items: [{ id: "a", label: "First choice" }, { id: "b", label: "Second choice", sub: "With a note" }, { id: "c", label: "Third choice" }], multi: false });
    if (on("cards")) samples.push({ id: "p2", type: "cards", items: [{ id: "x", title: "Sample item", subtitle: "A short description", price: "540", actions: [{ id: "go", label: "Choose" }] }, { id: "y", title: "Another item", price: "480", actions: [{ id: "go", label: "Choose" }] }] });
    if (on("datetime")) samples.push({ id: "p3", type: "datetime", days: [{ id: "2030-01-07", label: "Mon 7" }, { id: "2030-01-08", label: "Tue 8" }, { id: "2030-01-09", label: "Wed 9", disabled: true }], slots: [{ id: "s1", label: "10:00 am" }, { id: "s2", label: "3:00 pm", sub: "2 left" }] });
    if (on("form")) samples.push({ id: "p4", type: "form", fields: [{ id: "n", label: "Name", type: "text", required: true }, { id: "e", label: "Email", type: "email", required: true }], submit_label: "Send" });
    if (on("rating")) samples.push({ id: "p5", type: "rating", prompt: "How did we do?", reasons_good: ["Fast"], reasons_bad: ["Slow"] });
    samples.forEach(function (b) { renderBlock(b); });
    state.turnBlocks = [];
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
    var th = (state.config && state.config.theme) || {};
    if (PREVIEW && blocksOn(th)) previewBlocks(th.interactive_blocks);
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

  async function sendMessage(text, blockReply) {
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
    state.turnBlocks = [];
    emit("message", { role: "user", content: text });

    // `id` is always sent so the server can tell this visitor's conversation from anyone
    // else's; anything the host page passed to `setUser()` rides alongside it.
    function chatBody(conversationId) {
      var visitor = { id: visitorId() };
      for (var k in state.visitor) {
        if (Object.prototype.hasOwnProperty.call(state.visitor, k)) visitor[k] = state.visitor[k];
      }
      if (!visitor.id) visitor.id = visitorId();
      var body = {
        message: full,
        conversation_id: conversationId,
        stream: true,
        visitor: visitor,
      };
      if (blockReply) body.block_reply = blockReply;
      return JSON.stringify(body);
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
            dropTurnBlocks();
          } else if (ev.type === "tool_message" && ev.tool_message && ev.tool_message.text) {
            // What the agent says while a tool runs ("Let me check that for you..."). A normal bot bubble, put
            // above the reply that is still on its way. An older cached widget.js simply ignores the event.
            var noteRow = addMessage("bot", ev.tool_message.text).parentNode;
            if (noteRow && bubble.parentNode) els.msgs.insertBefore(noteRow, bubble.parentNode);
          } else if (ev.type === "block" && ev.block) {
            // Interactive blocks (docs/27). Only ever sent to agents that opted in; an older cached
            // widget.js simply ignores the unknown event.
            renderBlock(ev.block);
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
    await restoreHistory();
    scheduleCampaigns();
    window.Vicero = api;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

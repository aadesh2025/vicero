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
  // legacy widget renders exactly as before. The look of a layout is the approved artifact
  // (designreference/vicero-widget-lab.html): its per-style CSS is ported with selectors renamed
  // only, and injected for the ACTIVE layout alone, keyed on `.bf-l-<name>`.
  var LAYOUTS = ["classic", "glass", "hairline", "paper", "pebble", "console", "concierge", "command", "drawer", "block"];
  var DENSITY = {
    compact: { pad: "12px", gap: "6px", mp: "7px 11px", fs: "13.5px" },
    comfortable: { pad: "16px", gap: "10px", mp: "9px 13px", fs: "14px" },
    roomy: { pad: "20px", gap: "14px", mp: "11px 15px", fs: "14.5px" },
  };
  var LAYOUT_VARS = [
    "--bf-r", "--bf-rm", "--bf-rc", "--bf-rb", "--bf-pad", "--bf-gap", "--bf-mp", "--bf-fs", "--bf-soft",
    "--bf-aline", "--bf-aline2", "--bf-acfg", "--bf-ona20", "--bf-ona78", "--bf-aca55",
    "--bf-g1", "--bf-g2", "--bf-g3", "--bf-g4", "--bf-g5", "--bf-g6", "--bf-aur1", "--bf-aur2", "--bf-aur3",
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

  // Icons: the artifact's own set (stroke icons on a 24 grid), plus a few more for topic tiles.
  var ICONS = {
    chat: '<path d="M21 12a8.5 8.5 0 0 1-12.3 7.6L3 21l1.4-5.4A8.5 8.5 0 1 1 21 12z"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    clip: '<path d="M20 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/>',
    smile: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 9.5h.01M15 9.5h.01"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    bag: '<path d="M5 8h14l-1.2 12H6.2z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18"/>',
    cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    box: '<path d="M3 7.5L12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5l9 4.5 9-4.5M12 12v9"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    arrowR: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    pin: '<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  };
  function iconSvg(name) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || ICONS.chat) + "</svg>";
  }
  // A topic tile / suggestion row gets an icon picked by keyword; anything unmatched gets the chat bubble.
  var ICON_KEYWORDS = [
    ["cal", /book|appointment|schedule|reserve|tasting|visit|demo|meeting|call ?back|slot/i],
    ["box", /track|order|deliver|shipping|parcel|dispatch|status/i],
    ["refresh", /return|refund|exchange|cancel|replace/i],
    ["tag", /\b(price|prices|pricing|cost|plans?|offers?|discounts?|fees?)\b/i],
    ["clock", /\b(hours|open|opening|timings?|when)\b/i],
    ["pin", /\b(where|location|address|directions)\b/i],
    ["mail", /\be-?mail\b|\bnewsletter\b/i],
    ["spark", /\b(find|recommend|quiz|match|suggest|discover|choose)\b/i],
    ["user", /\b(person|human|agent|team|staff|talk|speak|contact|support|someone)\b/i],
    ["bag", /\b(shop|buy|store|products?|purchase|cart|catalog|browse)\b/i],
  ];
  function iconFor(label) {
    for (var i = 0; i < ICON_KEYWORDS.length; i++) if (ICON_KEYWORDS[i][1].test(label)) return ICON_KEYWORDS[i][0];
    return "chat";
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

  // Ported from designreference/vicero-widget-lab.html (selectors renamed only; color-mix() replaced by
  // colours computed in JS). Do not hand-edit: re-port from the artifact.
  var LAYOUT_CSS = {
    "base": "@keyframes bfkIn{from{opacity:0;transform:translateY(8px) scale(.985)}to{opacity:1;transform:none}}.bf-lay svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none}.bf-lay button{cursor:pointer}.bf-lay button:disabled{cursor:default}.bf-lay .bf-head{display:flex;align-items:center;gap:10px;padding:13px var(--bf-pad);border-bottom:1px solid var(--bf-border);flex-wrap:wrap;position:relative;transition:padding .3s}.bf-lay .bf-ava{width:34px;height:34px;border-radius:50%;background:var(--bf-accent);color:var(--bf-on-accent);display:grid;place-items:center;font-weight:600;font-size:13px;flex:none;position:relative}.bf-lay .bf-ava::after{content:\"\";position:absolute;right:-1px;bottom:-1px;width:10px;height:10px;border-radius:50%;background:#22c55e;border:2px solid var(--bf-bg)}.bf-lay .bf-id{min-width:0;flex:1}.bf-lay .bf-title{font-weight:600;font-size:14.5px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bf-lay .bf-sub{font-size:12px;color:var(--bf-muted);display:flex;align-items:center;gap:6px}.bf-lay .bf-dot{display:none;width:7px;height:7px;border-radius:50%;background:#22c55e}.bf-lay .bf-hacts{display:flex;gap:2px;margin-left:auto}.bf-lay .bf-ib{border:0;background:transparent;color:inherit;width:32px;height:32px;border-radius:8px;display:grid;place-items:center;opacity:.75}.bf-lay .bf-ib:hover{opacity:1;background:rgba(127,127,127,.18)}.bf-lay .bf-ib svg{width:17px;height:17px}.bf-lay .bf-tl,.bf-lay .bf-team,.bf-lay .bf-hero,.bf-lay .bf-tabs,.bf-lay .bf-kbd{display:none}.bf-lay .bf-msgs{flex:1;overflow-y:auto;padding:var(--bf-pad);display:flex;flex-direction:column;gap:var(--bf-gap);scrollbar-width:thin}.bf-lay .bf-row{display:flex;gap:8px;align-items:flex-end;animation:bfkMsg .2s ease-out}@keyframes bfkMsg{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}.bf-lay .bf-row.bf-user{justify-content:flex-end}.bf-lay .bf-bubble{max-width:84%;padding:var(--bf-mp);border-radius:var(--bf-rm);word-wrap:break-word}.bf-lay .bf-row.bf-bot .bf-bubble{background:var(--bf-bg2)}.bf-lay .bf-row.bf-user .bf-bubble{background:var(--bf-bubble);color:var(--bf-on-bubble)}.bf-lay .bf-bubble b{font-weight:600}.bf-lay .bf-typing{display:inline-flex;gap:4px;padding:12px 14px;background:var(--bf-bg2);border-radius:var(--bf-rm)}.bf-lay .bf-typing i{width:6px;height:6px;border-radius:50%;background:var(--bf-muted);animation:bfkDot 1s infinite}.bf-lay .bf-typing i:nth-child(2){animation-delay:.15s}.bf-lay .bf-typing i:nth-child(3){animation-delay:.3s}@keyframes bfkDot{0%,60%,100%{opacity:.3;transform:none}30%{opacity:1;transform:translateY(-3px)}}.bf-lay .bf-chips{display:flex;flex-wrap:wrap;gap:6px}.bf-lay .bf-chip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--bf-aline);background:var(--bf-bg);color:var(--bf-accent);padding:7px 12px;border-radius:min(999px,calc(var(--bf-r)*2 + 2px));font-size:13px;font-weight:500;text-align:left;transition:background .15s,transform .15s}.bf-lay .bf-chip:hover{background:var(--bf-soft)}.bf-lay .bf-chip.bf-on{background:var(--bf-accent);color:var(--bf-on-accent);border-color:var(--bf-accent)}.bf-lay .bf-chip .bf-ic,.bf-lay .bf-chip .bf-go{display:none}.bf-lay .bf-chip .bf-ic svg,.bf-lay .bf-chip .bf-go svg{width:16px;height:16px}.bf-lay .bf-chips .bf-btn{margin-left:2px}.bf-lay .bf-foot{padding:10px var(--bf-pad) 6px;position:relative;margin:0}.bf-lay .bf-inrow{display:flex;align-items:center;gap:2px;background:var(--bf-input);border:1px solid var(--bf-border);border-radius:min(var(--bf-r),22px);padding:4px 4px 4px 13px;transition:border-color .15s,box-shadow .15s}.bf-lay .bf-inrow:focus-within{border-color:var(--bf-aline2);box-shadow:0 0 0 3px var(--bf-soft)}.bf-lay .bf-ta{flex:1;min-width:0;border:0;background:transparent;outline:none;padding:7px 0;color:var(--bf-text);font-size:14px;font-family:inherit}.bf-lay .bf-ta::placeholder{color:var(--bf-muted)}.bf-lay .bf-send{border:0;width:34px;height:34px;border-radius:min(var(--bf-r),17px);background:var(--bf-accent);color:var(--bf-on-accent);display:grid;place-items:center;flex:none}.bf-lay .bf-send svg{width:17px;height:17px;stroke-width:2.2}.bf-lay .bf-emoji-pop{position:absolute;bottom:58px;right:var(--bf-pad);background:var(--bf-bg);border:1px solid var(--bf-border);border-radius:12px;padding:6px;display:flex;gap:2px;box-shadow:0 10px 30px -10px rgba(0,0,0,.3);z-index:3}.bf-lay .bf-emoji-pop button{border:0;background:transparent;font-size:18px;width:32px;height:32px;border-radius:8px}.bf-lay .bf-emoji-pop button:hover{background:var(--bf-bg2)}.bf-lay .bf-brand{text-align:center;font-size:11px;color:var(--bf-muted);padding:4px 0 9px}.bf-lay .bf-brand b{font-weight:600;color:var(--bf-text)}.bf-lay .bf-pane{flex:1;overflow:auto;padding:var(--bf-pad)}.bf-lay .bf-pane h4{margin:0 0 10px;font-size:12px;color:var(--bf-muted);font-weight:500}.bf-lay .bf-artrow{display:flex;align-items:center;gap:10px;width:100%;text-align:left;background:transparent;border:0;border-bottom:1px solid var(--bf-border);padding:12px 2px;color:var(--bf-text);font-size:13.5px}.bf-lay .bf-artrow svg{margin-left:auto;color:var(--bf-muted);width:16px;height:16px}.bf-lay .bf-stat{display:flex;align-items:center;gap:10px;padding:11px 0;border-bottom:1px solid var(--bf-border);font-size:13.5px}.bf-lay .bf-stat i{width:8px;height:8px;border-radius:50%;background:#22c55e;flex:none}.bf-lay .bf-stat small{margin-left:auto;color:var(--bf-muted);font-size:12px}",
    "classic": ".bf-lay.bf-l-classic .bf-head{background:var(--bf-accent);color:var(--bf-on-accent);border-bottom:0;padding-block:16px}.bf-lay.bf-l-classic .bf-ava{background:var(--bf-ona20);color:var(--bf-on-accent)}.bf-lay.bf-l-classic .bf-ava::after{border-color:var(--bf-accent)}.bf-lay.bf-l-classic .bf-sub{color:var(--bf-ona78)}",
    "glass": ".bf-lay.bf-l-glass{background:var(--bf-g1);backdrop-filter:blur(24px) saturate(1.5);-webkit-backdrop-filter:blur(24px) saturate(1.5);border-color:var(--bf-g5)}.bf-lay.bf-l-glass .bf-head{border-bottom-color:var(--bf-g6)}.bf-lay.bf-l-glass .bf-row.bf-bot .bf-bubble,.bf-lay.bf-l-glass .bf-card,.bf-lay.bf-l-glass .bf-form,.bf-lay.bf-l-glass .bf-rate,.bf-lay.bf-l-glass .bf-opt,.bf-lay.bf-l-glass .bf-ho,.bf-lay.bf-l-glass .bf-day,.bf-lay.bf-l-glass .bf-slot,.bf-lay.bf-l-glass .bf-counter,.bf-lay.bf-l-glass .bf-chip{background:var(--bf-g2);border-color:var(--bf-g3)}.bf-lay.bf-l-glass .bf-inrow{background:var(--bf-g4);border:0;border-radius:999px}.bf-lay.bf-l-glass .bf-inrow .bf-ta{color:var(--bf-bg)}.bf-lay.bf-l-glass .bf-inrow .bf-ta::placeholder{color:var(--bf-g5)}.bf-lay.bf-l-glass .bf-inrow .bf-ib{color:var(--bf-bg)}.bf-lay.bf-l-glass .bf-send{border-radius:50%}",
    "hairline": ".bf-lay.bf-l-hairline{box-shadow:0 20px 50px -30px rgba(10,14,20,.35)}.bf-lay.bf-l-hairline .bf-head{border-bottom:0;padding-top:18px}.bf-lay.bf-l-hairline .bf-ava{width:26px;height:26px;border-radius:7px;font-size:11px}.bf-lay.bf-l-hairline .bf-ava::after{display:none}.bf-lay.bf-l-hairline .bf-dot{display:inline-block}.bf-lay.bf-l-hairline .bf-row.bf-bot .bf-bubble{background:transparent;padding:0 2px}.bf-lay.bf-l-hairline .bf-row.bf-user .bf-bubble{background:transparent;color:var(--bf-text);border:1px solid var(--bf-border);border-radius:var(--bf-rm)}.bf-lay.bf-l-hairline .bf-chips{flex-direction:column;gap:0;border-top:1px solid var(--bf-border)}.bf-lay.bf-l-hairline .bf-chip{border:0;border-bottom:1px solid var(--bf-border);border-radius:0;background:transparent;color:var(--bf-text);padding:11px 2px;justify-content:space-between;width:100%}.bf-lay.bf-l-hairline .bf-chip .bf-go{display:inline-flex;color:var(--bf-muted);transition:transform .15s}.bf-lay.bf-l-hairline .bf-chip:hover{background:transparent}.bf-lay.bf-l-hairline .bf-chip:hover .bf-go{transform:translateX(3px);color:var(--bf-accent)}.bf-lay.bf-l-hairline .bf-chip.bf-on{background:transparent;color:var(--bf-accent)}.bf-lay.bf-l-hairline .bf-chips .bf-btn{margin:10px 0 0;align-self:flex-start}.bf-lay.bf-l-hairline .bf-foot{padding-top:6px}.bf-lay.bf-l-hairline .bf-inrow{background:transparent;border:0;border-top:1px solid var(--bf-border);border-radius:0;padding:8px 0 4px;box-shadow:none}.bf-lay.bf-l-hairline .bf-send{background:transparent;color:var(--bf-accent)}.bf-lay.bf-l-hairline .bf-typing{background:transparent;padding-left:2px}",
    "paper": ".bf-lay.bf-l-paper .bf-head{justify-content:center;text-align:center;border-bottom:1px solid var(--bf-border);padding-block:18px 14px}.bf-lay.bf-l-paper .bf-ava{display:none}.bf-lay.bf-l-paper .bf-id{flex:none}.bf-lay.bf-l-paper .bf-title{font-size:19px;font-weight:400;letter-spacing:-.01em}.bf-lay.bf-l-paper .bf-sub{justify-content:center;font-style:italic}.bf-lay.bf-l-paper .bf-hacts{position:absolute;right:10px;top:12px}.bf-lay.bf-l-paper .bf-msgs{--bf-gap:14px}.bf-lay.bf-l-paper .bf-row.bf-bot .bf-bubble{background:transparent;padding:0;font-size:15.5px;line-height:1.6;max-width:100%}.bf-lay.bf-l-paper .bf-row.bf-user .bf-bubble{background:transparent;color:var(--bf-muted);font-style:italic;padding:0;border-radius:0;text-align:right}.bf-lay.bf-l-paper .bf-row.bf-user .bf-bubble::after{content:\" \\2014 you\";font-size:12px}.bf-lay.bf-l-paper .bf-chips{counter-reset:p;flex-direction:column;gap:2px}.bf-lay.bf-l-paper .bf-chip{counter-increment:p;border:0;background:transparent;color:var(--bf-text);padding:5px 0;border-radius:0;font-size:15px}.bf-lay.bf-l-paper .bf-chip .bf-lb{text-decoration:underline;text-decoration-color:var(--bf-aca55);text-underline-offset:4px;text-decoration-thickness:1px}.bf-lay.bf-l-paper .bf-chip::before{content:counter(p,lower-alpha) \".\";color:var(--bf-accent);font-style:italic;min-width:16px}.bf-lay.bf-l-paper .bf-chip:hover{background:transparent;color:var(--bf-accent)}.bf-lay.bf-l-paper .bf-chip.bf-on .bf-lb{text-decoration-thickness:2px}.bf-lay.bf-l-paper .bf-chip.bf-on{background:transparent;color:var(--bf-accent)}.bf-lay.bf-l-paper .bf-btn{font-family:inherit;letter-spacing:.02em}.bf-lay.bf-l-paper .bf-inrow{background:transparent;border:0;border-bottom:1px solid var(--bf-text);border-radius:0;padding-left:0;box-shadow:none}.bf-lay.bf-l-paper .bf-send{background:transparent;color:var(--bf-accent)}.bf-lay.bf-l-paper .bf-typing{background:transparent;padding-left:0}",
    "pebble": ".bf-lay.bf-l-pebble .bf-head{flex-direction:column;justify-content:center;text-align:center;gap:6px;padding-block:16px 12px;background:var(--bf-bg2);border-bottom-color:transparent}.bf-lay.bf-l-pebble .bf-ava{width:46px;height:46px;font-size:16px}.bf-lay.bf-l-pebble .bf-id{flex:none}.bf-lay.bf-l-pebble .bf-sub{justify-content:center}.bf-lay.bf-l-pebble .bf-hacts{position:absolute;right:8px;top:8px}.bf-lay.bf-l-pebble .bf-bubble{border-radius:20px;padding:10px 15px}.bf-lay.bf-l-pebble .bf-row.bf-bot .bf-bubble{border-bottom-left-radius:6px}.bf-lay.bf-l-pebble .bf-row.bf-user .bf-bubble{border-bottom-right-radius:6px}.bf-lay.bf-l-pebble .bf-chip{border:0;background:var(--bf-soft)}.bf-lay.bf-l-pebble .bf-chip.bf-on{background:var(--bf-accent)}.bf-lay.bf-l-pebble .bf-card,.bf-lay.bf-l-pebble .bf-rate,.bf-lay.bf-l-pebble .bf-form,.bf-lay.bf-l-pebble .bf-ho{border-radius:20px}.bf-lay.bf-l-pebble .bf-inrow{border-radius:999px;background:var(--bf-bg2);border-color:transparent}.bf-lay.bf-l-pebble .bf-send{border-radius:50%}",
    "console": ".bf-lay.bf-l-console{box-shadow:0 30px 70px -24px rgba(0,0,0,.55)}.bf-lay.bf-l-console .bf-head{padding-block:10px;background:var(--bf-bg2)}.bf-lay.bf-l-console .bf-tl{display:flex;gap:6px}.bf-lay.bf-l-console .bf-tl i{width:10px;height:10px;border-radius:50%;background:var(--bf-border)}.bf-lay.bf-l-console .bf-tl i:first-child{background:var(--bf-accent)}.bf-lay.bf-l-console .bf-ava{display:none}.bf-lay.bf-l-console .bf-id{text-align:center}.bf-lay.bf-l-console .bf-title{font-size:12.5px;font-weight:500}.bf-lay.bf-l-console .bf-title::before{content:\"~/\";color:var(--bf-muted)}.bf-lay.bf-l-console .bf-sub{justify-content:center;font-size:11px}.bf-lay.bf-l-console .bf-msgs{--bf-gap:8px;font-size:13px}.bf-lay.bf-l-console .bf-row{justify-content:flex-start}.bf-lay.bf-l-console .bf-bubble{background:transparent!important;padding:0;border-radius:0;max-width:100%}.bf-lay.bf-l-console .bf-row.bf-bot .bf-bubble::before{content:\"bot \";color:var(--bf-muted)}.bf-lay.bf-l-console .bf-row.bf-user .bf-bubble{color:var(--bf-accent)}.bf-lay.bf-l-console .bf-row.bf-user .bf-bubble::before{content:\"you \\203A \";color:var(--bf-muted)}.bf-lay.bf-l-console .bf-chips{flex-direction:column;gap:1px;counter-reset:t}.bf-lay.bf-l-console .bf-chip{counter-increment:t;border:0;border-radius:0;background:transparent;color:var(--bf-text);padding:3px 6px;margin-left:-6px;font-size:13px}.bf-lay.bf-l-console .bf-chip::before{content:\"[\" counter(t) \"]\";color:var(--bf-accent)}.bf-lay.bf-l-console .bf-chip:hover,.bf-lay.bf-l-console .bf-chip.bf-on{background:var(--bf-soft);color:var(--bf-text)}.bf-lay.bf-l-console .bf-btn{border-radius:0;background:transparent;color:var(--bf-accent);border:1px solid var(--bf-accent)}.bf-lay.bf-l-console .bf-btn::before{content:\"[ \"}.bf-lay.bf-l-console .bf-btn::after{content:\" ]\"}.bf-lay.bf-l-console .bf-btn.bf-ghost{color:var(--bf-muted);border-color:var(--bf-border)}.bf-lay.bf-l-console .bf-btn.bf-ok{background:var(--bf-accent);color:var(--bf-on-accent)}.bf-lay.bf-l-console .bf-card,.bf-lay.bf-l-console .bf-rate,.bf-lay.bf-l-console .bf-form,.bf-lay.bf-l-console .bf-opt,.bf-lay.bf-l-console .bf-ho,.bf-lay.bf-l-console .bf-counter{border-style:dashed;background:transparent}.bf-lay.bf-l-console .bf-art{height:44px}.bf-lay.bf-l-console .bf-inrow{background:transparent;border:0;border-top:1px dashed var(--bf-border);border-radius:0;padding:8px 0 2px;box-shadow:none}.bf-lay.bf-l-console .bf-inrow::before{content:\"\\203A\";color:var(--bf-accent);margin-right:8px;font-weight:600}.bf-lay.bf-l-console .bf-send{background:transparent;color:var(--bf-accent);border-radius:0}.bf-lay.bf-l-console .bf-typing{background:transparent;padding:4px 0}.bf-lay.bf-l-console .bf-typing i{border-radius:0;width:7px;height:12px;background:var(--bf-accent);animation:bfkBlink 1s steps(1) infinite}.bf-lay.bf-l-console .bf-typing i+i{display:none}@keyframes bfkBlink{50%{opacity:0}}",
    "concierge": ".bf-lay.bf-l-concierge .bf-head{background:var(--bf-accent);color:var(--bf-on-accent);border-bottom:0}.bf-lay.bf-l-concierge .bf-ava{display:none}.bf-lay.bf-l-concierge .bf-team{display:flex}.bf-lay.bf-l-concierge .bf-team span{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-size:11px;font-weight:700;border:2px solid var(--bf-accent);margin-left:-8px;background:#fde68a;color:#3b2a06}.bf-lay.bf-l-concierge .bf-team span:first-child{margin-left:0;background:#fbcfe8;color:#4a0d2a}.bf-lay.bf-l-concierge .bf-team span:last-child{background:#bae6fd;color:#08304a}.bf-lay.bf-l-concierge .bf-sub{color:var(--bf-ona78)}.bf-lay.bf-l-concierge .bf-hero{width:100%;order:5}.bf-lay.bf-l-concierge.bf-home .bf-head{padding:20px var(--bf-pad) 26px}.bf-lay.bf-l-concierge.bf-home .bf-id{display:none}.bf-lay.bf-l-concierge.bf-home .bf-hero{display:block;margin-top:14px}.bf-lay .bf-hero-h{font-size:24px;font-weight:700;letter-spacing:-.02em;line-height:1.15}.bf-lay .bf-hero-p{opacity:.85;font-size:13.5px;margin-top:4px}.bf-lay.bf-l-concierge .bf-chip .bf-ic{display:inline-flex}.bf-lay.bf-l-concierge .bf-chips.bf-main{display:grid;grid-template-columns:1fr 1fr;gap:8px}.bf-lay.bf-l-concierge .bf-chips.bf-main .bf-chip{flex-direction:column;align-items:flex-start;gap:10px;padding:13px;border-radius:var(--bf-rc);border:1px solid var(--bf-border);color:var(--bf-text);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.05)}.bf-lay.bf-l-concierge .bf-chips.bf-main .bf-chip .bf-ic{width:30px;height:30px;border-radius:9px;background:var(--bf-soft);color:var(--bf-accent);display:grid;place-items:center}.bf-lay.bf-l-concierge .bf-chips.bf-main .bf-chip:last-child:nth-child(odd){grid-column:span 2;flex-direction:row;align-items:center}.bf-lay.bf-l-concierge .bf-chips.bf-main .bf-chip:hover{border-color:var(--bf-accent);background:var(--bf-bg);transform:translateY(-1px)}",
    "command": ".bf-lay.bf-l-command{width:440px}.bf-lay.bf-l-command .bf-head{display:none}.bf-lay.bf-l-command .bf-foot{order:-1;padding:10px 10px 0}.bf-lay.bf-l-command .bf-inrow{background:transparent;border:0;border-bottom:1px solid var(--bf-border);border-radius:0;padding:8px 6px 12px;box-shadow:none}.bf-lay.bf-l-command .bf-inrow::before{content:\"\";width:20px;height:20px;flex:none;margin-right:10px;background:var(--bf-accent);-webkit-mask:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M20 20l-3.5-3.5'/%3E%3C/svg%3E\") center/contain no-repeat;mask:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M20 20l-3.5-3.5'/%3E%3C/svg%3E\") center/contain no-repeat}.bf-lay.bf-l-command .bf-ta{font-size:16.5px}.bf-lay.bf-l-command .bf-send{border-radius:8px;width:30px;height:30px}.bf-lay.bf-l-command .bf-emoji-pop{bottom:auto;top:58px}.bf-lay.bf-l-command .bf-chips{flex-direction:column;gap:2px}.bf-lay.bf-l-command .bf-chip{border:0;background:transparent;color:var(--bf-text);border-radius:var(--bf-rb);padding:9px 10px;width:100%;font-weight:500}.bf-lay.bf-l-command .bf-chip .bf-ic{display:inline-flex;color:var(--bf-muted)}.bf-lay.bf-l-command .bf-chip .bf-go{display:inline-flex;margin-left:auto;color:var(--bf-muted);opacity:0}.bf-lay.bf-l-command .bf-chip:hover,.bf-lay.bf-l-command .bf-chip:focus-visible{background:var(--bf-soft)}.bf-lay.bf-l-command .bf-chip:hover .bf-go{opacity:1}.bf-lay.bf-l-command .bf-chip.bf-on{background:var(--bf-soft);color:var(--bf-accent)}.bf-lay.bf-l-command .bf-row.bf-bot .bf-bubble{background:transparent;padding:2px 4px;color:var(--bf-muted);font-size:13px}.bf-lay.bf-l-command .bf-row.bf-user .bf-bubble{border-radius:var(--bf-rb)}.bf-lay.bf-l-command .bf-kbd{display:flex;gap:14px;padding:8px 14px;border-top:1px solid var(--bf-border);font-size:11px;color:var(--bf-muted)}.bf-lay.bf-l-command .bf-kbd kbd{font-family:inherit;font-size:10.5px;border:1px solid var(--bf-border);border-radius:4px;padding:0 5px;margin-right:4px;background:var(--bf-bg2)}.bf-lay.bf-l-command .bf-brand{position:absolute;right:14px;bottom:0;padding:8px 0}",
    "drawer": ".bf-lay.bf-l-drawer{width:400px}.bf-lay.bf-l-drawer .bf-head{padding-bottom:0;row-gap:12px}.bf-lay.bf-l-drawer .bf-tabs{display:flex;width:100%;gap:18px;order:6}.bf-lay.bf-l-drawer .bf-tabs button{border:0;background:transparent;color:var(--bf-muted);padding:6px 0 10px;font-size:13px;font-weight:500;border-bottom:2px solid transparent;margin-bottom:-1px}.bf-lay.bf-l-drawer .bf-tabs button.on{color:var(--bf-text);border-bottom-color:var(--bf-accent)}.bf-lay.bf-l-drawer .bf-ava{border-radius:10px}.bf-lay.bf-l-drawer .bf-ava::after{display:none}.bf-lay.bf-l-drawer .bf-dot{display:inline-block}",
    "block": ".bf-lay.bf-l-block{border:2px solid var(--bf-text);box-shadow:7px 7px 0 var(--bf-text);font-weight:500}.bf-lay.bf-l-block .bf-head{background:var(--bf-accent);color:var(--bf-on-accent);border-bottom:2px solid var(--bf-text)}.bf-lay.bf-l-block .bf-title{text-transform:uppercase;letter-spacing:.04em;font-weight:800;font-size:14px}.bf-lay.bf-l-block .bf-sub{color:inherit;opacity:.8}.bf-lay.bf-l-block .bf-ava{border:2px solid var(--bf-text);background:var(--bf-bg);color:var(--bf-text);border-radius:var(--bf-rb)}.bf-lay.bf-l-block .bf-ava::after{display:none}.bf-lay.bf-l-block .bf-bubble{border:2px solid var(--bf-text)}.bf-lay.bf-l-block .bf-row.bf-bot .bf-bubble{background:var(--bf-bg)}.bf-lay.bf-l-block .bf-row.bf-user .bf-bubble{box-shadow:3px 3px 0 var(--bf-text)}.bf-lay.bf-l-block .bf-chip,.bf-lay.bf-l-block .bf-opt,.bf-lay.bf-l-block .bf-card,.bf-lay.bf-l-block .bf-form,.bf-lay.bf-l-block .bf-rate,.bf-lay.bf-l-block .bf-ho,.bf-lay.bf-l-block .bf-day,.bf-lay.bf-l-block .bf-slot,.bf-lay.bf-l-block .bf-counter{border:2px solid var(--bf-text);color:var(--bf-text);box-shadow:3px 3px 0 var(--bf-text)}.bf-lay.bf-l-block .bf-chip:hover,.bf-lay.bf-l-block .bf-opt:hover:not(:disabled),.bf-lay.bf-l-block .bf-slot:hover:not(:disabled){transform:translate(-1px,-1px);box-shadow:4px 4px 0 var(--bf-text);background:var(--bf-bg)}.bf-lay.bf-l-block .bf-chip.bf-on,.bf-lay.bf-l-block .bf-day.bf-on,.bf-lay.bf-l-block .bf-slot.bf-on{background:var(--bf-accent);color:var(--bf-on-accent)}.bf-lay.bf-l-block .bf-btn{border:2px solid var(--bf-text);box-shadow:3px 3px 0 var(--bf-text);color:var(--bf-on-accent)}.bf-lay.bf-l-block .bf-btn.bf-ghost{color:var(--bf-text)}.bf-lay.bf-l-block .bf-inrow{border:2px solid var(--bf-text);box-shadow:none}.bf-lay.bf-l-block .bf-send{border:2px solid var(--bf-text)}.bf-lay.bf-l-block .bf-art{border-bottom:2px solid var(--bf-text)}"
  };

  // What the artifact's stage CSS did around the panel, plus resets for legacy rules the artifact never
  // had to override (it has no legacy widget underneath).
  function layoutSupplementCss() {
    return (
      ".bf-lay{width:372px;max-width:calc(100vw - 32px);height:560px;max-height:calc(100vh - 120px);" +
      "background:var(--bf-bg);color:var(--bf-text);font-size:var(--bf-fs,14px);line-height:1.5;" +
      "border:1px solid var(--bf-border);border-radius:var(--bf-r);" +
      "box-shadow:0 30px 70px -24px rgba(10,14,20,.38),0 4px 14px -4px rgba(10,14,20,.12);animation:bfkIn .22s ease-out}" +
      ".bf-lay :focus-visible{outline:2px solid var(--bf-accent);outline-offset:2px}" +
      ".bf-lay .bf-head{background:transparent;color:inherit}" +
      ".bf-lay .bf-msgs{background:transparent}" +
      ".bf-lay .bf-bubble{border:0}" +
      ".bf-lay .bf-chips{padding:0}" +
      ".bf-lay .bf-chips:empty{display:none}" +
      ".bf-lay.bf-started .bf-chips{display:none}" +
      ".bf-lay .bf-foot{border:0;background:transparent;gap:0}" +
      ".bf-lay .bf-inrow{position:relative}" +
      ".bf-lay .bf-ta{order:1;flex:1;min-width:0;height:auto;min-height:0;max-height:120px;resize:none;border:0;border-radius:0;background:transparent;box-shadow:none;outline:none;padding:7px 0;color:var(--bf-text);font-size:14px}" +
      ".bf-lay .bf-ta:focus-visible{outline:none}" +
      ".bf-lay .bf-ta::placeholder{color:var(--bf-muted)}" +
      ".bf-lay .bf-file{order:2}.bf-lay .bf-emoji{order:3}.bf-lay .bf-send{order:4}" +
      ".bf-lay .bf-ib{font-size:0;padding:0}" +
      ".bf-lay .bf-send{border:0;font-size:0;padding:0}" +
      ".bf-lay .bf-send svg{width:17px;height:17px;stroke-width:2.2}" +
      ".bf-lay .bf-ava{object-fit:cover}" +
      ".bf-lay .bf-ava[hidden]{display:none}" +
      ".bf-lay .bf-sub{line-height:1.4}" +
      ".bf-lay .bf-id{min-width:0;max-width:100%}" +
      ".bf-lay .bf-pane{display:none}" +
      ".bf-lay.bf-tab-help .bf-pane.bf-help,.bf-lay.bf-tab-status .bf-pane.bf-status{display:block}" +
      ".bf-lay.bf-tab-help .bf-msgs,.bf-lay.bf-tab-help .bf-foot,.bf-lay.bf-tab-status .bf-msgs,.bf-lay.bf-tab-status .bf-foot{display:none}" +
      ".bf-lay .bf-stat i{width:8px;height:8px;border-radius:50%;background:#22c55e;flex:none}" +
      ".bf-lay .bf-emoji-pop{bottom:44px;left:auto;right:0;width:220px;flex-wrap:wrap}" +
      ".bf-lay .bf-hero-h{margin:0}" +
      "@media (prefers-reduced-motion:reduce){.bf-lay,.bf-lay .bf-row,.bf-lay .bf-typing i{animation:none}}"
    );
  }

  // Stage positioning the artifact did at host level: Drawer is a full-height side panel, Command is
  // centred near the top. Phone widths keep the full-screen panel for every layout (docs/27 section 2).
  function layoutPlaceCss(name) {
    var L = ".bf-lay.bf-l-" + name;
    var out = "";
    if (name === "command") {
      out +=
        "@media (min-width:481px){" + L + "," + ":host(.bf-left) " + L + "{left:50%;right:auto;bottom:auto;top:12vh;transform:translateX(-50%);animation-name:bfkFade}}" +
        L + " .bf-head{display:none}" + L + " .bf-foot{position:static}" +
        "@media (max-width:767px){" + L + " .bf-head{display:block;position:absolute;right:6px;top:6px;padding:0;border:0;background:transparent;z-index:2}" +
        L + " .bf-head>:not(.bf-hacts){display:none!important}" + L + " .bf-restart{display:none}" +
        L + " .bf-foot{padding-right:44px}}";
    } else if (name === "drawer") {
      out +=
        ":host(.bf-hl-drawer.bf-open) .bf-launcher{display:none}" +
        "@media (min-width:481px){" + L + "," + ":host(.bf-left) " + L + "{top:0;bottom:0;right:0;left:auto;width:400px;height:100vh;max-height:100vh;border-radius:0;border-width:0 0 0 1px;animation-name:bfkFade}" +
        ":host(.bf-left) " + L + "{right:auto;left:0;border-width:0 1px 0 0}}";
    } else if (name === "block") {
      out += ":host(.bf-hl-block) .bf-launcher{border:2px solid #0a0a0a;box-shadow:4px 4px 0 #0a0a0a}" +
        "@media (max-width:480px){" + L + "{box-shadow:none;border-width:0}}";
    } else if (name === "glass") {
      // The artifact shows glass over a colourful page. On a plain page it would be white on white, so the
      // panel carries its own soft aurora under the frosted layer (accent-tinted, never opaque).
      out += L + "{background:radial-gradient(circle at 18% 14%,var(--bf-aur1),transparent 42%),radial-gradient(circle at 88% 62%,var(--bf-aur2),transparent 46%),radial-gradient(circle at 56% 96%,var(--bf-aur3),transparent 40%),var(--bf-g1)}";
    }
    return out;
  }
  var LAYOUT_MOBILE_CSS =
    "@media (max-width:480px){.bf-panel.bf-lay,:host(.bf-left) .bf-panel.bf-lay{width:100vw;max-width:100vw;height:100vh;max-height:100vh;top:auto;bottom:0;right:0;left:0;transform:none;border-radius:0}}";

  function layoutCss(name) {
    return LAYOUT_CSS.base + layoutSupplementCss() + (LAYOUT_CSS[name] || "") + layoutPlaceCss(name);
  }

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
      (layout ? layoutCss(layout) : "") +
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
    // A layout always has spacing (the artifact's default is "comfortable"); legacy only when chosen.
    var spacing = dens || (layout ? DENSITY.comfortable : null);
    if (spacing) {
      v["--bf-pad"] = spacing.pad;
      v["--bf-gap"] = spacing.gap;
      v["--bf-mp"] = spacing.mp;
      v["--bf-fs"] = spacing.fs;
    }
    if (layout) {
      // The legacy palette derives these from the mode alone; a layout derives them from the
      // actual background/text so a custom background still gets matching surfaces.
      host.style.setProperty("--bf-bg2", mixHex(p.text, p.bg, 5));
      host.style.setProperty("--bf-border", mixHex(p.text, p.bg, 11));
      host.style.setProperty("--bf-muted", mixHex(p.text, p.bg, 58));
      if (!theme.typing_area_color) host.style.setProperty("--bf-input", mixHex(p.text, p.bg, 4));
      var bord = mixHex(p.text, p.bg, 11);
      v["--bf-aline"] = mixHex(p.accent, bord, 40);
      v["--bf-aline2"] = mixHex(p.accent, bord, 60);
      v["--bf-ona20"] = rgba(p.onAccent, 0.2);
      v["--bf-ona78"] = rgba(p.onAccent, 0.78);
      v["--bf-aca55"] = rgba(p.accent, 0.55);
      v["--bf-g1"] = rgba(p.bg, 0.58);
      v["--bf-g2"] = rgba(p.bg, 0.7);
      v["--bf-g3"] = rgba(p.text, 0.09);
      v["--bf-g4"] = rgba(p.text, 0.85);
      v["--bf-g5"] = rgba(p.bg, 0.6);
      v["--bf-g6"] = rgba(p.text, 0.08);
      // The artifact's own backdrop colours (warm amber, green, orange), so frosted glass has something to frost.
      v["--bf-aur1"] = "rgba(242,181,107,.55)";
      v["--bf-aur2"] = "rgba(47,109,76,.45)";
      v["--bf-aur3"] = "rgba(230,107,60,.3)";
      // The accent used as TEXT on the panel: a dark accent on a dark panel (or the reverse) is
      // unreadable, so fall back to the text colour when the contrast is below 3:1.
      v["--bf-acfg"] = contrastOf(p.accent, p.bg) >= 3 ? p.accent : p.text;
    }
    if (blocks) v["--bf-soft"] = mixHex(p.accent, p.bg, 12);
    if (layout) v["--bf-soft"] = mixHex(p.accent, p.bg, 12);
    if (blocks && !layout) {
      v["--bf-aline"] = mixHex(p.accent, mixHex(p.text, p.bg, 11), 40);
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

  var PLACEHOLDER = { command: "Ask anything or search…", console: "type a command or question" };

  // Concierge / Drawer / Command / Console add DOM, and every layout swaps the glyph buttons for the
  // artifact's icons; nothing is built for the legacy look, and switching back removes it all again.
  function ensureLayoutDom(layout) {
    if (state.domLayout === layout) return;
    if (state.domLayout === undefined && !layout) {
      state.domLayout = layout; // pristine legacy DOM: nothing to build, nothing to undo
      return;
    }
    teardownLayoutDom();
    state.domLayout = layout;
    if (!layout) return;
    var head = els.panel.querySelector(".bf-head");
    var mk = function (tag, cls, html) {
      var n = document.createElement(tag);
      n.className = cls;
      n.setAttribute("data-bf-lay", "");
      if (html != null) n.innerHTML = html;
      return n;
    };
    var ini = mk("span", "bf-ini bf-ava");
    ini.setAttribute("aria-hidden", "true");
    els.ini = ini;
    els.avatar.classList.add("bf-ava");
    var tl = mk("span", "bf-tl", "<i></i><i></i><i></i>");
    tl.setAttribute("aria-hidden", "true");
    var team = mk(
      "div",
      "bf-team",
      '<span style="background:#fbcfe8;color:#4a0d2a">' + iconSvg("user") + '</span><span style="background:#fde68a;color:#3b2a06">' +
        iconSvg("user") + '</span><span style="background:#bae6fd;color:#08304a">' + iconSvg("user") + "</span>"
    );
    team.setAttribute("aria-hidden", "true");
    var id = mk("div", "bf-id");
    var sub = mk("div", "bf-sub", '<span class="bf-dot"></span><span>Usually replies in seconds</span>');
    id.appendChild(els.title);
    id.appendChild(sub);
    var hero = mk("div", "bf-hero", '<div class="bf-hero-h">Hi there</div><div class="bf-hero-p">Ask us anything, or pick a topic to start.</div>');
    var hacts = mk("div", "bf-hacts");
    var restart = document.createElement("button");
    restart.type = "button";
    restart.className = "bf-ib bf-restart";
    restart.setAttribute("aria-label", "Start over");
    restart.title = "Start over";
    restart.innerHTML = iconSvg("refresh");
    restart.addEventListener("click", restartChat);
    hacts.appendChild(restart);
    var x = head.querySelector(".bf-x");
    x.classList.add("bf-ib");
    x.innerHTML = iconSvg("x");
    hacts.appendChild(x);
    var tabs = null;
    if (layout === "drawer") {
      tabs = mk("nav", "bf-tabs");
      tabs.setAttribute("aria-label", "Sections");
    }
    [ini, tl, els.avatar, team, id, hero, hacts, tabs].forEach(function (n) { if (n) head.appendChild(n); });
    els.tl = tl; els.team = team; els.id = id; els.hero = hero; els.hacts = hacts; els.tabs = tabs; els.restart = restart;

    // Composer: the artifact's icons and arrow instead of the emoji glyphs.
    els.fileBtn.classList.add("bf-ib");
    els.fileBtn.innerHTML = iconSvg("clip");
    els.emojiBtn.classList.add("bf-ib");
    els.emojiBtn.innerHTML = iconSvg("smile");
    els.send.innerHTML = iconSvg("send");
    els.ta.setAttribute("placeholder", PLACEHOLDER[layout] || "Type a message…");

    // Suggested prompts live inside the message list, right under the welcome message.
    els.chips.classList.toggle("bf-main", layout === "concierge");

    if (layout === "drawer") {
      els.helpPane = mk("div", "bf-pane bf-help");
      els.statusPane = mk(
        "div",
        "bf-pane bf-status",
        '<h4>Right now</h4><div class="bf-stat"><i></i>All systems operational</div><div class="bf-stat"><i></i>Usually replies in seconds</div>'
      );
      els.chips.parentNode.insertBefore(els.helpPane, els.chips.nextSibling);
      els.chips.parentNode.insertBefore(els.statusPane, els.helpPane.nextSibling);
    } else if (layout === "command") {
      els.kbd = mk("div", "bf-kbd", "<span><kbd>↵</kbd>select</span><span><kbd>esc</kbd>close</span>");
      els.kbd.setAttribute("aria-hidden", "true");
      els.panel.insertBefore(els.kbd, els.brand.parentNode.nextSibling);
    }
  }

  function teardownLayoutDom() {
    if (!els.hacts) return;
    var head = els.panel.querySelector(".bf-head");
    var x = els.hacts.querySelector(".bf-x");
    head.insertBefore(els.avatar, head.firstChild);
    head.insertBefore(els.title, head.firstChild.nextSibling);
    head.appendChild(x);
    [els.ini, els.tl, els.team, els.id, els.hero, els.hacts, els.tabs, els.helpPane, els.statusPane, els.kbd].forEach(function (n) {
      if (n && n.parentNode) n.parentNode.removeChild(n);
    });
    els.avatar.classList.remove("bf-ava");
    els.avatar.removeAttribute("hidden");
    x.classList.remove("bf-ib");
    x.innerHTML = "&times;";
    els.fileBtn.classList.remove("bf-ib");
    els.fileBtn.textContent = "📎";
    els.emojiBtn.classList.remove("bf-ib");
    els.emojiBtn.textContent = "🙂";
    els.send.textContent = "➤";
    els.ta.setAttribute("placeholder", "Type a message…");
    els.chips.classList.remove("bf-main");
    if (els.chips.parentNode === els.msgs) els.panel.insertBefore(els.chips, els.panel.querySelector(".bf-foot"));
    els.panel.classList.remove("bf-home", "bf-tab-help", "bf-tab-status", "bf-started");
    els.ini = els.tl = els.team = els.id = els.hero = els.hacts = els.tabs = els.helpPane = els.statusPane = els.kbd = els.restart = null;
  }

  // Start over: a fresh conversation, the welcome message and prompts again (layouts only).
  function restartChat() {
    if (state.sending) return;
    try { if (state.ws) state.ws.close(); } catch (e) { /* ignore */ }
    state.ws = null;
    forget(STORE_KEY);
    state.conversationId = null;
    state.hasConversation = false;
    state.campaignShown = false;
    state.turnBlocks = [];
    if (state.config) refreshWelcome(state.config.welcome_message);
    setTab("chat");
    updateHome();
    if (els.ta) els.ta.focus();
  }

  function setTab(id) {
    els.panel.classList.toggle("bf-tab-help", id === "help");
    els.panel.classList.toggle("bf-tab-status", id === "status");
    if (!els.tabs) return;
    var bs = els.tabs.querySelectorAll("button");
    for (var i = 0; i < bs.length; i++) {
      var on = bs[i].getAttribute("data-tab") === id;
      bs[i].classList.toggle("bf-on", on);
      bs[i].setAttribute("aria-pressed", String(on));
    }
  }

  // Fill the layout-only elements from data the agent already has (name, suggested prompts).
  function renderLayoutExtras() {
    var layout = state.layout;
    if (!layout || !els.ini) return;
    var cfg = state.config || {};
    var name = String(cfg.name || "Chat");
    els.ini.textContent = (name.replace(/[^A-Za-z0-9À-￿ ]/g, "").trim().charAt(0) || "V").toUpperCase();
    var logo = !!logoUrl(cfg.theme || {});
    els.ini.hidden = logo;
    els.avatar.hidden = !logo;
    var prompts = promptsOf(cfg);
    if (els.tabs) {
      els.tabs.innerHTML = "";
      var tabs = [["chat", "Chat"]];
      if (prompts.length) tabs.push(["help", "Help"]);
      tabs.push(["status", "Status"]);
      tabs.forEach(function (t) {
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("data-tab", t[0]);
        b.textContent = t[1];
        b.addEventListener("click", function () { setTab(t[0]); });
        els.tabs.appendChild(b);
      });
      els.helpPane.innerHTML = "";
      if (prompts.length) {
        var h4 = document.createElement("h4");
        h4.textContent = "Popular questions";
        els.helpPane.appendChild(h4);
        prompts.forEach(function (text) {
          var row = document.createElement("button");
          row.type = "button";
          row.className = "bf-artrow";
          row.appendChild(document.createTextNode(text));
          row.insertAdjacentHTML("beforeend", iconSvg("arrowR").replace("<svg", '<svg class="bf-go"'));
          row.addEventListener("click", function () {
            setTab("chat");
            api.sendMessage(text);
          });
          els.helpPane.appendChild(row);
        });
      }
      setTab("chat");
    }
    updateHome();
  }

  function promptsOf(cfg) {
    return (cfg.suggested_prompts || [])
      .map(function (p) { return typeof p === "string" ? p : p && p.label ? p.label : ""; })
      .filter(Boolean);
  }

  // Concierge shows its home screen until the visitor says something; it never comes back for a
  // conversation that already exists (stored id) or after a proactive message was shown.
  function updateHome() {
    if (!els.panel) return;
    var home =
      state.layout === "concierge" &&
      !!(state.config && promptsOf(state.config).length) &&
      !state.hasConversation &&
      !state.conversationId &&
      !state.campaignShown;
    els.panel.classList.toggle("bf-home", home);
    els.panel.classList.toggle("bf-started", !!state.layout && state.hasConversation);
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
    if (state.layout) {
      // Layouts: the welcome message (one bubble per line, none invented) with the suggested prompts
      // right under it, inside the message list.
      if (els.chips.parentNode === els.msgs) els.msgs.removeChild(els.chips);
      els.msgs.innerHTML = "";
      els.welcomeBubble = null;
      String(text || "")
        .split(/\n+/)
        .filter(function (l) { return l.trim(); })
        .forEach(function (l) {
          var b = addMessage("bot", l.trim());
          if (!els.welcomeBubble) els.welcomeBubble = b;
        });
      els.msgs.appendChild(els.chips);
    } else {
      els.msgs.innerHTML = "";
      els.welcomeBubble = text ? addMessage("bot", text) : null;
    }
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
      if (state.layout) {
        chip.innerHTML =
          '<span class="bf-ic">' + iconSvg(iconFor(text)) + '</span><span class="bf-lb"></span><span class="bf-go">' + iconSvg("arrowR") + "</span>";
        chip.querySelector(".bf-lb").textContent = text;
      } else {
        chip.textContent = text;
      }
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

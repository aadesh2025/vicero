import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";
import typography from "@tailwindcss/typography";

const rgb = (v: string) => `rgb(var(${v}) / <alpha-value>)`;
/** Prose colours as raw `rgb(...)`. The typography plugin feeds these into CSS variables
 *  rather than class names, so Tailwind's `<alpha-value>` placeholder is never substituted
 *  and would ship literally. */
const solid = (v: string) => `rgb(var(${v}))`;

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
    // Public docs only. `content/internal` is deliberately absent: its class names would be
    // baked into the stylesheet every visitor downloads, and the whole point of that
    // directory is that nothing from it reaches a non-staff browser. Internal pages get
    // their styling from components, which are scanned above.
    "./content/docs/**/*.mdx",
  ],
  theme: {
    extend: {
      colors: {
        bg: rgb("--bg"),
        surface: {
          DEFAULT: rgb("--surface"),
          2: rgb("--surface-2"),
          3: rgb("--surface-3"),
        },
        border: {
          DEFAULT: rgb("--border"),
          strong: rgb("--border-strong"),
        },
        text: rgb("--text"),
        muted: rgb("--muted"),
        faint: rgb("--faint"),
        accent: {
          DEFAULT: rgb("--accent"),
          2: rgb("--accent-2"),
          soft: rgb("--accent-soft"),
          strong: rgb("--accent-strong"),
        },
        // `ember` is an alias for `accent`, not a second palette. The rename to `accent` left
        // 42 `text-ember-soft` / `border-ember/40` usages across the app pointing at a colour
        // Tailwind no longer generated, so those classes silently produced nothing — badges and
        // chips rendered unstyled in *both* themes. Aliasing is safer than a find-and-replace
        // here: it fixes every existing usage at once and cannot miss one.
        ember: {
          DEFAULT: rgb("--accent"),
          2: rgb("--accent-2"),
          soft: rgb("--accent-soft"),
          strong: rgb("--accent-strong"),
        },
        "on-accent": rgb("--on-accent"),
        glow: rgb("--glow"),
        success: rgb("--success"),
        warn: rgb("--warn"),
        error: rgb("--error"),
        info: rgb("--info"),
        ring: rgb("--ring"),
      },
      fontFamily: {
        sans: ["var(--font-body)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: {
        // Shadow colour and weight are themed: pure black at dark-mode strength turns a white
        // card into a smudge, so light tints with the ink and drops the alpha by ~5x.
        card: "0 1px 2px 0 rgb(var(--shadow) / var(--shadow-1)), 0 1px 3px 0 rgb(var(--shadow) / var(--shadow-2))",
        pop: "0 12px 32px -8px rgb(var(--shadow) / calc(var(--shadow-1) * 2)), 0 2px 8px -2px rgb(var(--shadow) / var(--shadow-2))",
        "accent-glow": "0 0 0 1px rgb(var(--accent) / 0.45), 0 8px 24px -8px rgb(var(--accent) / 0.4)",
        "glow-live": "0 0 0 1px rgb(var(--glow) / 0.45), 0 0 18px -4px rgb(var(--glow) / 0.5)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "caret-blink": {
          "0%,70%,100%": { opacity: "1" },
          "20%,50%": { opacity: "0.2" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.4s cubic-bezier(0.22, 1, 0.36, 1) both",
        "caret-blink": "caret-blink 1.1s ease-in-out infinite",
      },
      // `prose-botforge` maps the typography plugin onto the app's own tokens, so docs
      // inherit the palette instead of introducing a second one. Because every colour is a
      // token, light/dark are handled by `.dark` in globals.css — there is no `prose-invert`
      // and adding one would fight the tokens.
      typography: {
        botforge: {
          css: {
            "--tw-prose-body": solid("--muted"),
            "--tw-prose-headings": solid("--text"),
            "--tw-prose-lead": solid("--muted"),
            "--tw-prose-links": solid("--accent-soft"),
            "--tw-prose-bold": solid("--text"),
            "--tw-prose-counters": solid("--faint"),
            "--tw-prose-bullets": solid("--border-strong"),
            "--tw-prose-hr": solid("--border"),
            "--tw-prose-quotes": solid("--text"),
            "--tw-prose-quote-borders": solid("--border-strong"),
            "--tw-prose-captions": solid("--faint"),
            "--tw-prose-code": solid("--text"),
            "--tw-prose-th-borders": solid("--border-strong"),
            "--tw-prose-td-borders": solid("--border"),
            maxWidth: "none",
            // Shiki writes its own colours onto the `pre`, so the plugin's defaults are
            // cleared rather than overridden — otherwise the two fight and the theme loses.
            pre: { backgroundColor: "transparent", color: "inherit", padding: "0" },
            // The plugin quotes inline code with `` characters; the app's own code spans
            // never have, and the docs match the app.
            "code::before": { content: '""' },
            "code::after": { content: '""' },
          },
        },
      },
    },
  },
  plugins: [tailwindcssAnimate, typography],
};
export default config;

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * docs/20-UI-REDESIGN-DUAL-THEME.md §13: no raw hex and no Tailwind palette classes in
 * `src/components` or `src/app` after the redesign. Everything goes through the tokens in
 * `globals.css`, so a rebrand or a rename stays a one-file change.
 *
 * Three kinds of file are allow-listed below, and only for the reason given — not as a general
 * escape hatch:
 *   - a real third-party brand mark (Google/Facebook/n8n) that isn't ours to retint
 *   - the embeddable chat widget's own colour data, which belongs to the client's site, not
 *     this app's theme (docs/20 §10.20)
 *   - test fixtures, which ship to no one
 */
const ROOTS = ["src/components", "src/app"];
const HEX = /#[0-9a-fA-F]{3,8}\b/g;
const PALETTE = /\b(?:bg|text|border|ring|from|via|to|fill|stroke|outline|decoration|divide|caret|accent|shadow)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|grey|zinc|neutral|stone)-(?:\d{2,3})\b/g;

const ALLOW: Record<string, string> = {
  "src/components/auth/oauth-buttons.tsx": "Google/Facebook OAuth brand marks",
  "src/components/builder/tabs/channels-tab.tsx": "the client's own widget colours (docs/20 §10.20), not app UI",
  "src/lib/api/agent-mapping.ts": "default value for the widget's client-owned primaryColor",
  "src/components/auth/auth-showcase.tsx": "the auth chat preview is fixed-colour art, identical in both themes by design (login-v2)",
  "src/components/auth/mini-chat-card.tsx": "the mobile twin of the auth chat preview — same fixed colours",
  "src/components/brand/animated-logo-mark.tsx": "the Vicero logo's own colours, not themeable",
};

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const s = statSync(p);
    if (s.isDirectory()) walk(p, out);
    else if (/\.(tsx?|css)$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}

describe("no hard-coded colour outside the token system (docs/20 §13)", () => {
  const files = ROOTS.flatMap((r) => walk(r));
  expect(files.length).toBeGreaterThan(50); // sanity: the walk actually found the app

  it.each(files)("%s", (path) => {
    const rel = path.replace(/\\/g, "/");
    if (rel.endsWith("src/app/globals.css")) return; // the token definitions themselves
    const reason = ALLOW[rel];
    const text = readFileSync(path, "utf8");
    const hex = text.match(HEX) ?? [];
    const palette = text.match(PALETTE) ?? [];
    const hits = [...hex, ...palette];
    if (reason) {
      expect(hits.length, `${rel} is allow-listed for: ${reason} — but no longer contains any hard-coded colour. Remove it from ALLOW.`).toBeGreaterThan(0);
      return;
    }
    expect(hits, `${rel} has hard-coded colour(s) outside the token system: ${hits.join(", ")}`).toEqual([]);
  });
});

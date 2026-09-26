/** The public docs must not explain or expose API keys.
 *
 * The operator's rule: how API keys work, what they look like and how they are scoped is
 * admin-only, shown at `/vault`. The failure this guards against is gradual — someone writing
 * a helpful new page and pasting a `curl -H 'X-API-Key: …'` example into it — so this scans
 * the source of every public page, not a fixed list, and fails naming the file and line.
 *
 * It reads `content/docs` straight from disk (not through `lib/docs/content`, which is
 * server-only), so it also catches a page the site does not render yet.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PUBLIC_ROOT = join(process.cwd(), "content", "docs");
const PRIVATE_ROOT = join(process.cwd(), "content", "internal");

function mdxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? mdxFiles(path) : path.endsWith(".mdx") ? [path] : [];
  });
}

/** Wording that means "here is how API keys work". `_API_KEY` env names are deliberately not
 *  matched (underscore): the self-hosting guide has to name what an operator must set. */
const FORBIDDEN: [RegExp, string][] = [
  [/\bbf_/i, "a `bf_` key prefix or example"],
  [/x-api-key/i, "the X-API-Key header"],
  [/\bapi[ -]keys?\b/i, "the phrase 'API key'"],
  [/\/v1\/apikeys/i, "the key-management routes"],
  [/\/v1\/credentials/i, "the provider-credential routes"],
];

describe("public docs", () => {
  const files = mdxFiles(PUBLIC_ROOT);

  it("has pages to check", () => {
    expect(files.length).toBeGreaterThan(10);
  });

  for (const file of files) {
    const label = file.slice(PUBLIC_ROOT.length + 1).replace(/\\/g, "/");
    it(`${label} does not explain or expose API keys`, () => {
      const hits: string[] = [];
      readFileSync(file, "utf8")
        .split(/\r?\n/)
        .forEach((line, i) => {
          for (const [pattern, what] of FORBIDDEN) {
            if (pattern.test(line)) hits.push(`line ${i + 1}: ${what} — ${line.trim().slice(0, 80)}`);
          }
        });
      expect(hits).toEqual([]);
    });
  }
});

describe("the explanation still exists, in the admin area", () => {
  it("is in the private collection, so hiding it did not delete it", () => {
    const text = mdxFiles(PRIVATE_ROOT)
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    expect(text).toMatch(/Customer API keys/);
    expect(text).toMatch(/X-API-Key/);
    expect(text).toMatch(/`admin` scope never means `owner`/);
  });
});

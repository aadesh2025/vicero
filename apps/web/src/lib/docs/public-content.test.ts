/** The public docs may explain API-key authentication, but never contain or reveal a key.
 *
 * The line being held: a public page can say *"send your key as a bearer token"* using the
 * placeholder `YOUR_API_KEY`. It cannot contain anything that looks like a real credential,
 * cannot show the internal key format or header, and cannot document the key-management
 * routes. The detail of how keys are scoped, stored and revoked is admin-only (`/vault`).
 *
 * The failure this guards against is gradual — someone writing a helpful example and pasting
 * in a key that happened to be on their clipboard — so this scans the source of every public
 * page, not a fixed list, and fails naming the file and line.
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

/** Things that must not appear on a public page. */
const FORBIDDEN: [RegExp, string][] = [
  [/\bbf_/i, "the internal key prefix (a public example must use YOUR_API_KEY)"],
  [/x-api-key/i, "the internal key header"],
  [/\/v1\/apikeys/i, "the key-management routes"],
  [/\/v1\/credentials/i, "the provider-credential routes"],
  // Anything shaped like a real secret: a well-known prefix followed by a long token.
  // `your_…` is how this site writes a placeholder (e.g. the widget's `pk_your_agent_public_key`).
  [/\b(?:sk|pk|gsk|rk|xox[bp]|ghp|AIza)[-_](?!your)[A-Za-z0-9_-]{16,}/, "a string shaped like a real API secret"],
  // A long unbroken token after "Bearer" that is not one of the allowed placeholders.
  [/Bearer\s+(?!YOUR_API_KEY\b|\$[A-Z_]+|<[a-z_]+>)[A-Za-z0-9._~+/-]{20,}/, "a bearer value that is not a placeholder"],
];

describe("public docs", () => {
  const files = mdxFiles(PUBLIC_ROOT);

  it("has pages to check", () => {
    expect(files.length).toBeGreaterThan(10);
  });

  for (const file of files) {
    const label = file.slice(PUBLIC_ROOT.length + 1).replace(/\\/g, "/");
    it(`${label} contains no real credential and no internal key detail`, () => {
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

describe("the authentication page", () => {
  const text = readFileSync(join(PUBLIC_ROOT, "api", "authentication.mdx"), "utf8");

  it("teaches with the YOUR_API_KEY placeholder", () => {
    expect(text).toContain("Authorization: Bearer YOUR_API_KEY");
    expect(text).toContain('"Bearer YOUR_API_KEY"'); // the JavaScript and Python examples
  });

  it("has all three language examples", () => {
    expect(text).toMatch(/<CodeTabs labels="cURL,JavaScript,Python">/);
    expect(text).toMatch(/```bash[\s\S]*curl [\s\S]*YOUR_API_KEY/);
    expect(text).toMatch(/```javascript[\s\S]*fetch\([\s\S]*YOUR_API_KEY/);
    expect(text).toMatch(/```python[\s\S]*requests\.get\([\s\S]*YOUR_API_KEY/);
  });

  it("carries the required security note", () => {
    expect(text).toContain("Do not commit it to source control, expose it in client-side applications, or include it in");
    expect(text).toContain("secure server-side environment variable or");
  });

  it("links to the existing key-management page rather than duplicating it", () => {
    expect(text).toContain('<DocButton href="/settings/api-keys">');
  });

  it("does not put a key in a URL, even as a placeholder", () => {
    // `?api_key=`, `?key=`, `?token=` in an example would teach exactly the wrong habit.
    expect(text).not.toMatch(/[?&](?:api[_-]?key|key|token|access_token|secret)=/i);
  });
});

describe("the detailed explanation stays admin-only", () => {
  it("exists in the private collection, so keeping it out of public did not delete it", () => {
    const text = mdxFiles(PRIVATE_ROOT)
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    expect(text).toMatch(/Customer API keys/);
    expect(text).toMatch(/X-API-Key/);
    expect(text).toMatch(/`admin` scope never means `owner`/);
  });
});

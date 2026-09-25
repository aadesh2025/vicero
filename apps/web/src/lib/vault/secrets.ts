/** Reading the platform's real configuration values for the vault.
 *
 * Two functions, and the split between them is the whole safety story:
 *
 *   `listSecrets()`   what the *page* renders. Names, descriptions, whether each is set, and
 *                     a masked preview. **No real value ever goes through it**, so a full
 *                     value can never end up in server-rendered HTML, a cached response or a
 *                     screenshot of the page.
 *   `revealSecret()`  what the Reveal button calls, one name at a time, from a route that
 *                     re-checks the session on every request.
 *
 * Which names exist comes from `content/generated/env.json` — the generated snapshot of
 * `.env.example` — never from the caller. `revealSecret("PATH")` or `("VAULT_SESSION_SECRET")`
 * is therefore not a question of filtering bad input: those names are simply not in the set.
 *
 * Where values come from: the web process's own environment, plus an optional file named by
 * `VAULT_ENV_FILE` (e.g. a read-only mount of the root `.env`). In the dev compose the web
 * container is given the whole root `.env`, so everything resolves. The production compose
 * gives it only two variables, so most values report `available: false` until an operator
 * chooses to pass them in — see docs/ENV.md.
 */

import "server-only";

import { readFileSync } from "node:fs";
import env from "../../../content/generated/env.json";

export interface SecretEntry {
  name: string;
  description: string;
  needsHuman: boolean;
  /** Whether the web process can see a non-empty value at all. */
  available: boolean;
  /** A masked preview, or `null` when there is nothing to mask. Never a full value. */
  masked: string | null;
}

export interface SecretSection {
  name: string;
  entries: SecretEntry[];
}

/** The vault's own credentials. They are excluded from the set outright: the page that
 *  reveals secrets must not be able to reveal the key that guards it. */
const RESERVED_PREFIX = "VAULT_";

const KNOWN = new Set(
  env.sections.flatMap((s) => s.vars.map((v) => v.name)).filter((n) => !n.startsWith(RESERVED_PREFIX)),
);

export function isKnownSecret(name: string): boolean {
  return KNOWN.has(name);
}

/** `KEY=value` lines only. No expansion, no export, no multiline: a deliberately dumb
 *  reader, because it is parsing a file that holds real secrets. */
function parseEnvFile(path: string): Record<string, string> {
  const out: Record<string, string> = {};
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return out;
  }
  for (const line of text.split(/\r?\n/)) {
    const m = /^([A-Z][A-Z0-9_]*)=(.*)$/.exec(line);
    if (!m) continue;
    let value = m[2];
    // A trailing ` # comment` belongs to the file, not the value — but only when unquoted.
    if (!/^["']/.test(value)) value = value.replace(/\s+#.*$/, "");
    out[m[1]] = value.trim().replace(/^(["'])(.*)\1$/, "$2");
  }
  return out;
}

function lookup(name: string): string | undefined {
  if (!KNOWN.has(name)) return undefined;
  const fromProcess = process.env[name];
  if (fromProcess) return fromProcess;
  const file = process.env.VAULT_ENV_FILE;
  if (!file) return undefined;
  const value = parseEnvFile(file)[name];
  return value ? value : undefined;
}

/** Enough to recognise a key, not enough to use one. Short values are fully hidden: the
 *  last four characters of an eight-character password are half of it. */
export function maskValue(value: string): string {
  if (value.length < 16) return "••••••••";
  return `••••••••${value.slice(-4)}`;
}

export function listSecrets(): SecretSection[] {
  return env.sections
    .map((section) => ({
      name: section.name,
      entries: section.vars
        .filter((v) => !v.name.startsWith(RESERVED_PREFIX))
        .map((v): SecretEntry => {
          const value = lookup(v.name);
          return {
            name: v.name,
            description: v.description,
            needsHuman: v.needsHuman,
            available: value !== undefined,
            masked: value !== undefined ? maskValue(value) : null,
          };
        }),
    }))
    .filter((section) => section.entries.length > 0);
}

/** The real value for one known name, or `null` (unknown name, or not set). */
export function revealSecret(name: string): string | null {
  return lookup(name) ?? null;
}

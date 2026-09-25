/**
 * Turn `.env.example` into a structured reference for the staff-only docs page.
 *
 * ────────────────────────────────────────────────────────────────────────────────────────
 * HARD RULE: this script reads `.env.example` and nothing else.
 *
 * `.env.example` is the git-tracked placeholder file — every value in it is already public.
 * `.env` holds real credentials and must never be opened here, and no value from any file
 * is ever written to the output: the generated JSON carries variable NAMES, their section,
 * and the surrounding COMMENTS only. This is the one place the docs feature could leak a
 * live secret, so the parser drops the value before it is ever held in a variable.
 * ────────────────────────────────────────────────────────────────────────────────────────
 *
 * Run via `make docs-generate` from the repo root, or directly:
 *   node scripts/generate-env-reference.mjs
 */

import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = join(REPO_ROOT, ".env.example");
const OUT_PATH = join(REPO_ROOT, "apps", "web", "content", "generated", "env.json");

/** `# ── Core ────────────` — the section headers used throughout `.env.example`. */
const SECTION_RE = /^#\s*[─-]{2,}\s*(.+?)\s*[─-]{2,}\s*$/;
/** A variable line. Matches the name and the `=` only — the value is never captured. */
const VAR_RE = /^([A-Z][A-Z0-9_]*)=/;

/**
 * Parse `.env.example` into sections of documented variables.
 *
 * @param {string} source raw contents of `.env.example`
 * @returns {{sections: Array<{name: string, vars: Array<{name: string, needsHuman: boolean, description: string}>}>}}
 */
export function parseEnvExample(source) {
  const sections = [];
  /** Comment lines seen since the last variable — the description of the next one. */
  let pending = [];
  let current = null;

  const ensureSection = () => {
    if (!current) {
      current = { name: "General", vars: [] };
      sections.push(current);
    }
    return current;
  };

  for (const raw of source.split(/\r?\n/)) {
    const line = raw.trimEnd();

    const section = SECTION_RE.exec(line);
    if (section) {
      current = { name: section[1].trim(), vars: [] };
      sections.push(current);
      pending = [];
      continue;
    }

    if (line.startsWith("#")) {
      pending.push(line.replace(/^#\s?/, ""));
      continue;
    }

    if (!line.trim()) {
      // A blank line ends a comment block that was not attached to anything — a file-level
      // note rather than a variable's description.
      pending = [];
      continue;
    }

    const variable = VAR_RE.exec(line);
    if (!variable) {
      pending = [];
      continue;
    }

    const name = variable[1];

    // Split the remainder at the first ` #` and keep ONLY the right-hand side. Half these
    // variables are documented by a trailing note (`ENV=dev  # dev | test | prod`) and
    // dropping it left most of the reference blank. The left-hand side — the value — is
    // never assigned to anything; `.slice` past the marker is the only read of this line.
    const rest = line.slice(variable[0].length);
    const hash = rest.search(/\s#/);
    const inline = hash === -1 ? "" : rest.slice(hash + 2).trim();

    const above = pending.join(" ").trim();
    const needsHuman = above.startsWith("[HUMAN]");
    const description = [above.replace(/^\[HUMAN\]\s*/, "").trim(), inline]
      .filter(Boolean)
      .join(" — ");

    ensureSection().vars.push({ name, needsHuman, description });
    pending = [];
  }

  return { sections: sections.filter((s) => s.vars.length > 0) };
}

function main() {
  const parsed = parseEnvExample(readFileSync(SOURCE, "utf8"));
  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, JSON.stringify(parsed, null, 2) + "\n", "utf8");

  const count = parsed.sections.reduce((n, s) => n + s.vars.length, 0);
  const human = parsed.sections.reduce(
    (n, s) => n + s.vars.filter((v) => v.needsHuman).length,
    0,
  );
  console.log(
    `wrote ${relative(REPO_ROOT, OUT_PATH)} — ${count} variables in ` +
      `${parsed.sections.length} sections (${human} need a human)`,
  );
}

// Only run when invoked directly, so the parser can be unit-tested by importing it.
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main();
}

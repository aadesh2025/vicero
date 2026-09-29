#!/usr/bin/env node
/**
 * One-off: tag the existing n8n inventory so deny-by-default visibility (ADR-040) resolves
 * each workflow to its owning org.
 *
 * Workflow visibility is keyed on n8n tags: a workflow reaches an org only when it carries
 * that org's slug, `shared-template` (reusable starter, every org), or nothing at all — in
 * which case it is hidden from everyone. Workflows that predate the rule are all untagged,
 * so this walks a mapping and applies it.
 *
 *   node scripts/tag-n8n-workflows.mjs            # dry run — prints what it would do
 *   node scripts/tag-n8n-workflows.mjs --apply    # actually writes the tags
 *   node scripts/tag-n8n-workflows.mjs --apply --base-url http://localhost:5678
 *
 * Env: N8N_BASE_URL, N8N_API_KEY (read from the repo-root .env if present).
 * The key needs tag read/create scopes **and** workflow "update tags" — a workflow-only key
 * returns 403 on every tag call, which is what the preflight check reports.
 *
 * Safe to re-run: a workflow that already carries its target tag is skipped, and existing
 * tags are preserved rather than replaced.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");

/** name → tag. Slugs are the real `organizations.slug` values, looked up in Postgres,
 *  not guessed from the workflow name. `internal` hides a workflow from every org. */
const MAPPING = [
  // Vicero's own n8n (:5679)
  { name: "Acme Co — Starter Automation", tag: "acme-co" },
  { name: "Globex Inc — Starter Automation", tag: "globex-inc" },
  { name: "TEMPLATE — Starter Automation", tag: "internal" },

  // The AUROZEN AI instance (:5678). Listed so one run covers whichever instance is
  // pointed at; entries whose workflow isn't present are simply reported as absent.
  { name: "Vicero — Echo (sync)", tag: "internal" },
  { name: "SHARED — Master Router", tag: "internal" },
  { name: "SHARED — Groq AI Caller", tag: "internal" },
  { name: "SHARED — Auto Provisioner", tag: "internal" },
  // "00001 — …" / "00002 — …" and "Website Lead — Contact Form" are deliberately absent:
  // no Vicero organization corresponds to them (see the run report).
];

function loadEnvFile(path) {
  const out = {};
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return out;
  }
  for (const line of text.split(/\r?\n/)) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)$/.exec(line);
    if (!m) continue;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    out[m[1]] = v;
  }
  return out;
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) {
      args[key] = next;
      i++;
    } else {
      args[key] = true;
    }
  }
  return args;
}

const env = { ...loadEnvFile(resolve(ROOT, ".env")), ...process.env };
const args = parseArgs(process.argv.slice(2));
const BASE = String(args["base-url"] ?? env.N8N_BASE_URL ?? "http://localhost:5678").replace(/\/$/, "");
const KEY = env.N8N_API_KEY ?? "";
const APPLY = Boolean(args.apply);

async function n8n(method, path, body) {
  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-N8N-API-KEY": KEY },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const err = new Error(`${method} ${path} -> ${resp.status} ${data?.message ?? text.slice(0, 160)}`);
    err.status = resp.status;
    throw err;
  }
  return data;
}

const tagNames = (wf) =>
  (wf.tags ?? []).map((t) => String(typeof t === "string" ? t : t?.name ?? "").trim().toLowerCase());

async function main() {
  if (!KEY) {
    console.error("N8N_API_KEY is not set (checked .env and the environment).");
    return 1;
  }
  console.log(`n8n: ${BASE}   mode: ${APPLY ? "APPLY" : "dry run"}\n`);

  let list;
  try {
    list = await n8n("GET", "/api/v1/workflows?limit=250");
  } catch (e) {
    console.error(
      e.status === 401
        ? `Unauthorized at ${BASE}. This API key belongs to a different n8n instance.`
        : `Could not list workflows: ${e.message}`
    );
    return 1;
  }
  const workflows = list.data ?? [];
  console.log(`${workflows.length} workflow(s) found.\n`);

  // Preflight: a workflow-scoped key 403s on tags, and finding that out mid-run would
  // leave the inventory half-tagged.
  if (APPLY) {
    try {
      await n8n("GET", "/api/v1/tags?limit=1");
    } catch (e) {
      console.error(
        `Cannot read tags (${e.status ?? "?"}). The API key needs tag read/create scopes ` +
          `plus workflow "update tags".\n` +
          `Mint a new one in n8n -> Settings -> API and re-run.\n  ${e.message}`
      );
      return 1;
    }
  }

  const byName = new Map(workflows.map((w) => [w.name, w]));
  const done = [];
  const skipped = [];
  const absent = [];
  const failed = [];

  for (const { name, tag } of MAPPING) {
    const wf = byName.get(name);
    if (!wf) {
      absent.push(name);
      continue;
    }
    if (tagNames(wf).includes(tag)) {
      skipped.push(`${name} (already "${tag}")`);
      continue;
    }
    if (!APPLY) {
      done.push(`${name} -> "${tag}"`);
      continue;
    }
    try {
      const tags = await n8n("GET", "/api/v1/tags?limit=250");
      let t = (tags.data ?? []).find((x) => String(x.name).trim().toLowerCase() === tag);
      if (!t) t = await n8n("POST", "/api/v1/tags", { name: tag });
      const keep = (wf.tags ?? []).map((x) => (typeof x === "string" ? null : x?.id)).filter(Boolean);
      const ids = [...new Set([...keep, t.id])].map((id) => ({ id }));
      await n8n("PUT", `/api/v1/workflows/${wf.id}/tags`, ids);
      done.push(`${name} -> "${tag}"`);
    } catch (e) {
      failed.push(`${name}: ${e.message}`);
    }
  }

  const report = (label, rows) => {
    if (!rows.length) return;
    console.log(`${label} (${rows.length}):`);
    for (const r of rows) console.log(`  - ${r}`);
    console.log("");
  };
  report(APPLY ? "Tagged" : "Would tag", done);
  report("Already correct", skipped);
  report("Not on this instance", absent);
  report("FAILED", failed);

  // Anything present but unmapped is unowned — the state deny-by-default hides, and the
  // list a human has to resolve.
  const mapped = new Set(MAPPING.map((m) => m.name));
  const untracked = workflows.filter((w) => !mapped.has(w.name) && tagNames(w).length === 0);
  if (untracked.length) {
    console.log(`Untagged and unmapped — hidden from every org until someone claims them (${untracked.length}):`);
    for (const w of untracked) console.log(`  - ${w.name}  (id ${w.id})`);
    console.log("");
  }
  if (!APPLY) console.log("Dry run. Re-run with --apply to write these tags.");
  return failed.length ? 1 : 0;
}

// Set exitCode rather than calling process.exit: an abrupt exit while fetch's keep-alive
// handle is still open trips a libuv assertion on Windows and buries the report.
main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((e) => {
    console.error(e);
    process.exitCode = 1;
  });

export { loadEnvFile, parseArgs, MAPPING };

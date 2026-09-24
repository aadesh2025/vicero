#!/usr/bin/env node
/**
 * import-n8n-workflows.mjs — (re)load the workflow JSON in infra/n8n into a running n8n.
 *
 *   node scripts/import-n8n-workflows.mjs                      # infra/n8n/demo-niches -> org tag "demo"
 *   node scripts/import-n8n-workflows.mjs --org acme --dir infra/n8n
 *
 * For when n8n's database is lost (it has been, once): creates each workflow, or updates it if one
 * with the same name exists, activates it, and tags it with the org slug — an untagged workflow is
 * invisible to every org (deny-by-default, ADR-040). Idempotent. Needs N8N_BASE_URL and N8N_API_KEY
 * (from the root .env); the API key needs workflow create/update/activate and tag scopes.
 *
 * The signature check is part of the JSON, so nothing extra to remember — but the *n8n container*
 * must have N8N_WEBHOOK_SIGNING_SECRET (see infra/n8n/README.md), or every call is rejected (fail
 * closed). No npm dependencies on purpose, like provision-client.mjs.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function loadEnvFile(path) {
  let raw;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    return {};
  }
  const out = {};
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) continue;
    const eq = t.indexOf("=");
    out[t.slice(0, eq).trim()] = t.slice(eq + 1).replace(/\s+#.*$/, "").trim().replace(/^(["'])(.*)\1$/, "$2");
  }
  return out;
}

const fileEnv = loadEnvFile(join(REPO_ROOT, ".env"));
const env = (k, d = "") => process.env[k] || fileEnv[k] || d;
const BASE = env("N8N_BASE_URL", "http://localhost:5678").replace(/\/+$/, "");
const KEY = env("N8N_API_KEY");

const argv = process.argv.slice(2);
const arg = (name, d) => {
  const i = argv.indexOf(`--${name}`);
  return i === -1 ? d : argv[i + 1];
};
const orgSlug = arg("org", "demo").toLowerCase();
const dir = resolve(REPO_ROOT, arg("dir", "infra/n8n/demo-niches"));

async function n8n(method, path, body) {
  let resp;
  try {
    resp = await fetch(`${BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json", "X-N8N-API-KEY": KEY },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    throw new Error(`Cannot reach n8n at ${BASE} (${cause.message}). Is it running? cd infra && docker compose up -d n8n`);
  }
  if (!resp.ok) throw new Error(`n8n ${method} ${path} -> ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  const text = await resp.text();
  return text ? JSON.parse(text) : {};
}

async function main() {
  if (!KEY) throw new Error("N8N_API_KEY is not set (n8n -> Settings -> n8n API).");
  const files = readdirSync(dir).filter((f) => f.endsWith(".json")).sort();
  if (!files.length) throw new Error(`No .json files in ${dir}`);

  const existing = new Map((await n8n("GET", "/api/v1/workflows?limit=250")).data.map((w) => [w.name, w]));
  const tags = (await n8n("GET", "/api/v1/tags?limit=250")).data;
  const tag = tags.find((t) => String(t.name).toLowerCase() === orgSlug) ?? (await n8n("POST", "/api/v1/tags", { name: orgSlug }));

  for (const file of files) {
    const wf = JSON.parse(readFileSync(join(dir, file), "utf8"));
    const body = { name: wf.name, nodes: wf.nodes, connections: wf.connections, settings: wf.settings ?? { executionOrder: "v1" } };
    const found = existing.get(wf.name);
    const saved = found ? await n8n("PUT", `/api/v1/workflows/${found.id}`, body) : await n8n("POST", "/api/v1/workflows", body);
    await n8n("POST", `/api/v1/workflows/${saved.id}/activate`);
    await n8n("PUT", `/api/v1/workflows/${saved.id}/tags`, [{ id: tag.id }]);
    console.log(`${found ? "updated" : "created"}  ${saved.id}  ${wf.name}  (tag "${orgSlug}")`);
  }
}

main().catch((err) => {
  console.error(`import-n8n-workflows: ${err.message}`);
  process.exit(1);
});

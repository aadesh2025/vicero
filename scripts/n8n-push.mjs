#!/usr/bin/env node
/**
 * n8n-push.mjs — the ONLY way a client workflow reaches n8n (docs/25 §12, CLAUDE.md §13).
 *
 *   n8n-push.mjs push <file.json|-> --org <slug> --org-id <uuid> --client "<Client>" \
 *                 --agent "<Agent>" --agent-id <uuid> --job "<Job>" [--apply]
 *   n8n-push.mjs activate "<Client - Agent - Job>" --org <slug>      (a person at a terminal only)
 *
 * push      Dry run by default: prints the plan and changes nothing. With --apply it creates or
 *           updates the workflow INACTIVE, tags it with the org slug (only), adds a sticky note with
 *           the owner ids, writes the manifest and appends one audit line. `-` reads the JSON from stdin.
 * activate  Refuses unless stdin is a real terminal, the workflow is inactive and carries the org tag,
 *           and the person types the workflow name. Nothing else in this repo can activate a client workflow.
 *
 * Runs on the VPS, through ops/n8n-push.sh (inside the web container, which has Node). The API key is read
 * from N8N_API_KEY in the environment only — never from a file, never printed. No npm dependencies.
 *
 * Env: N8N_API_KEY, N8N_BASE_URL (default http://n8n:5678), N8N_PUSH_STATE_DIR (manifests + history.log;
 *      default <repo>/infra/n8n/clients), N8N_PUSH_STATE_DISPLAY (host path shown in messages when the
 *      state dir is a container-internal copy; set by ops/n8n-push.sh).
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export class PushError extends Error {}

// ── pure helpers ──────────────────────────────────────────────────────────────

export const slugify = (s) =>
  String(s)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ORG_SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
const SIGNATURE_NODES = ["Verify Vicero signature", "Signature valid?", "Reject 401"];
const STICKY_NAME = "Vicero ownership";

export function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const t = argv[i];
    if (t === "-" || !t.startsWith("--")) {
      args._.push(t);
      continue;
    }
    const key = t.slice(2);
    const next = argv[i + 1];
    if (next === undefined || (next.startsWith("--") && next !== "-")) args[key] = true;
    else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

/** Everything derived from the flags. Throws if any required flag is missing — ids are never guessed. */
export function identity(args) {
  const missing = ["org", "org-id", "client", "agent", "agent-id", "job"].filter(
    (k) => typeof args[k] !== "string" || !args[k].trim()
  );
  if (missing.length) {
    throw new PushError(
      `Missing ${missing.map((k) => `--${k}`).join(", ")}. Every id comes from the Admin UI; none is guessed.`
    );
  }
  const org = args.org.trim().toLowerCase();
  if (!ORG_SLUG.test(org)) throw new PushError(`--org "${args.org}" is not a valid org slug.`);
  for (const k of ["org-id", "agent-id"]) {
    if (!UUID.test(args[k].trim())) throw new PushError(`--${k} must be a UUID, got "${args[k]}".`);
  }
  const client = args.client.trim();
  const agent = args.agent.trim();
  const job = args.job.trim();
  const agentSlug = slugify(agent);
  const jobSlug = slugify(job);
  if (!agentSlug || !jobSlug) throw new PushError("--agent and --job must contain letters or digits.");
  return {
    org,
    orgId: args["org-id"].trim().toLowerCase(),
    client,
    agent,
    agentId: args["agent-id"].trim().toLowerCase(),
    job,
    name: `${client} - ${agent} - ${job}`,
    path: `${org}-${agentSlug}-${jobSlug}`,
    manifestFile: `${agentSlug}-${jobSlug}.manifest.yaml`,
  };
}

const SECRET_PATTERNS = [
  [/gsk_[A-Za-z0-9]{20,}/, "Groq key"],
  [/\bsk-[A-Za-z0-9_-]{20,}/, "API secret key"],
  [/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./, "JWT"],
  [/Bearer\s+[A-Za-z0-9._~+/-]{20,}/, "bearer token"],
  [/xox[abp]-[A-Za-z0-9-]{10,}/, "Slack token"],
  [/AKIA[0-9A-Z]{16}/, "AWS key id"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "private key"],
];
const SECRET_KEY_NAME = /(password|passwd|secret|api[-_]?key|token|authorization)/i;

/** Walk every string in the workflow. Returns the paths that look like a pasted secret. */
export function findSecrets(node, path = "$", out = []) {
  if (typeof node === "string") {
    for (const [re, label] of SECRET_PATTERNS) if (re.test(node)) out.push(`${path} (${label})`);
  } else if (Array.isArray(node)) {
    node.forEach((v, i) => findSecrets(v, `${path}[${i}]`, out));
  } else if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node)) {
      // A literal value under a secret-looking key. Expressions ("={{ ... }}") and "" are fine.
      if (
        SECRET_KEY_NAME.test(k) &&
        typeof v === "string" &&
        v.length >= 12 &&
        !v.startsWith("=") &&
        !/\s/.test(v)
      ) {
        out.push(`${path}.${k} (literal value under a secret-looking key)`);
      }
      findSecrets(v, `${path}.${k}`, out);
    }
  }
  return out;
}

const targets = (connections, from, output = null) => {
  const outs = connections?.[from]?.main ?? [];
  const lists = output === null ? outs : [outs[output] ?? []];
  return lists.flat().map((c) => c.node);
};

/** Credentials the workflow references, by type + name. Names only — the human enters the secret values. */
export function credentialsNeeded(nodes) {
  const seen = new Map();
  for (const n of nodes) {
    for (const [type, ref] of Object.entries(n.credentials ?? {})) {
      const name = ref?.name ?? "";
      seen.set(`${type}\u0000${name}`, { type, name });
    }
  }
  return [...seen.values()];
}

/**
 * Pure validation of a workflow against an identity. Returns { passed: [...], failed: [...] } — all
 * checks run, so one report lists every problem instead of one per attempt.
 */
export function checkWorkflow(wf, id) {
  const passed = [];
  const failed = [];
  const check = (label, ok, why) => (ok ? passed : failed).push(ok ? label : `${label}: ${why}`);
  const nodes = Array.isArray(wf?.nodes) ? wf.nodes : [];
  const byName = new Map(nodes.map((n) => [n.name, n]));

  check("workflow name is '<Client> - <Agent> - <Job>'", wf?.name === id.name, `JSON has "${wf?.name}", expected "${id.name}"`);

  const webhooks = nodes.filter((n) => String(n.type).endsWith("webhook"));
  check("exactly one webhook node", webhooks.length === 1, `found ${webhooks.length}`);
  const hook = webhooks[0];
  check("webhook path is '<slug>-<agent>-<job>'", hook?.parameters?.path === id.path, `JSON has "${hook?.parameters?.path}", expected "${id.path}"`);
  check("webhookId matches the path", hook?.webhookId === id.path, `JSON has "${hook?.webhookId}"`);
  check("webhook is POST with rawBody on (needed for the signature)", hook?.parameters?.httpMethod === "POST" && hook?.parameters?.options?.rawBody === true, "set httpMethod POST and options.rawBody true");

  const missingSig = SIGNATURE_NODES.filter((n) => !byName.has(n));
  check("the 3 signature nodes are present", missingSig.length === 0, `missing ${missingSig.join(", ")}`);
  if (!missingSig.length && hook) {
    const c = wf.connections ?? {};
    const chain =
      targets(c, hook.name).join() === SIGNATURE_NODES[0] &&
      targets(c, SIGNATURE_NODES[0]).join() === SIGNATURE_NODES[1] &&
      targets(c, SIGNATURE_NODES[1], 1).join() === SIGNATURE_NODES[2];
    check("webhook -> Verify -> Signature valid? -> (false) Reject 401, nothing else before the check", chain, "the first three nodes are not wired as in the template");
    const trueBranch = targets(c, SIGNATURE_NODES[1], 0);
    check("real work only starts on the 'valid' branch", trueBranch.length > 0 && !trueBranch.includes(SIGNATURE_NODES[2]), "the true branch is empty or goes to Reject 401");
    const verify = byName.get(SIGNATURE_NODES[0]);
    const code = String(verify?.parameters?.jsCode ?? "");
    check("the signature check is the template's (HMAC, timing-safe, fails closed)", /x-vicero-signature/.test(code) && /timingSafeEqual/.test(code) && /N8N_WEBHOOK_SIGNING_SECRET/.test(code), "re-copy the node from infra/n8n/template-starter-automation.json");
  }

  const codeNodes = nodes.filter((n) => n.type === "n8n-nodes-base.code" && n.name !== SIGNATURE_NODES[0]);
  check("no Code nodes (only the signature check may be one)", codeNodes.length === 0, `Code node(s): ${codeNodes.map((n) => n.name).join(", ")}`);

  const dupes = nodes.map((n) => n.name).filter((n, i, a) => a.indexOf(n) !== i);
  check("node names are unique", dupes.length === 0, `duplicate: ${[...new Set(dupes)].join(", ")}`);

  const secrets = findSecrets({ nodes, connections: wf?.connections, settings: wf?.settings });
  check("no secret-looking values in the JSON", secrets.length === 0, secrets.slice(0, 5).join("; "));
  check("no pinned data", !wf?.pinData || Object.keys(wf.pinData).length === 0, "remove pinData (it can hold real customer data)");

  return { passed, failed };
}

/** Add (or replace) the ownership sticky note. It never touches the processing nodes. */
export function withStickyNote(wf, id) {
  const content = [
    "## Vicero ownership (written by n8n-push)",
    `- org: ${id.org} (${id.orgId})`,
    `- agent: ${id.agent} (${id.agentId})`,
    `- job: ${id.job}`,
    `- webhook path: ${id.path}`,
    "Do not edit a live workflow in place: push a new copy instead.",
  ].join("\n");
  const note = {
    parameters: { content, height: 180, width: 380 },
    id: "5d0c1a9e-0000-4000-8000-0000000057cc",
    name: STICKY_NAME,
    type: "n8n-nodes-base.stickyNote",
    typeVersion: 1,
    position: [-420, -240],
  };
  return { ...wf, nodes: [...wf.nodes.filter((n) => n.name !== STICKY_NAME), note] };
}

const q = (s) => JSON.stringify(String(s));

export function manifestYaml(id, { workflowId, status, credentials, now }) {
  const creds = credentials.length
    ? ["credentials_needed:", ...credentials.flatMap((c) => [`  - type: ${q(c.type)}`, `    name: ${q(c.name)}`])]
    : ["credentials_needed: []"];
  return (
    [
      `org_slug: ${q(id.org)}`,
      `org_id: ${q(id.orgId)}`,
      `client: ${q(id.client)}`,
      `agent_name: ${q(id.agent)}`,
      `agent_id: ${q(id.agentId)}`,
      `job: ${q(id.job)}`,
      `workflow_name: ${q(id.name)}`,
      `webhook_path: ${q(id.path)}`,
      `tag: ${q(id.org)}`,
      ...creds,
      `n8n_workflow_id: ${workflowId ? q(workflowId) : "null"}`,
      `status: ${q(status)}`,
      `updated_at: ${q(now)}`,
    ].join("\n") + "\n"
  );
}

// ── environment, n8n, audit ───────────────────────────────────────────────────

function settings(env = process.env) {
  return {
    base: (env.N8N_BASE_URL || "http://n8n:5678").replace(/\/+$/, ""),
    key: env.N8N_API_KEY || "",
    stateDir: env.N8N_PUSH_STATE_DIR || join(REPO_ROOT, "infra", "n8n", "clients"),
    stateDisplay: env.N8N_PUSH_STATE_DISPLAY || env.N8N_PUSH_STATE_DIR || join(REPO_ROOT, "infra", "n8n", "clients"),
  };
}

/** Anything printed goes through here so the key can never reach a terminal or log, even inside an error. */
const scrub = (text, key) => (key ? String(text).split(key).join("[redacted]") : String(text));

function makeN8n({ base, key }) {
  return async function n8n(method, path, body) {
    let resp;
    try {
      resp = await fetch(`${base}${path}`, {
        method,
        headers: { "Content-Type": "application/json", "X-N8N-API-KEY": key },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (cause) {
      throw new PushError(scrub(`Cannot reach n8n at ${base} (${cause.message}). Run this on the VPS via ops/n8n-push.sh.`, key));
    }
    const text = await resp.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      /* non-JSON body: keep text for the error */
    }
    if (!resp.ok) {
      const detail = scrub(data?.message ?? text.slice(0, 200), key);
      if (resp.status === 401) throw new PushError(`n8n rejected the API key (401).`);
      if (resp.status === 403) throw new PushError(`n8n refused ${method} ${path} (403): the key lacks a scope. ${detail}`);
      throw new PushError(`n8n ${method} ${path} failed: ${resp.status} — ${detail}`);
    }
    return data;
  };
}

/** One line per action: time, action, workflow name, outcome. No ids of secrets, no payloads. */
export function audit(stateDir, action, name, outcome, now = new Date().toISOString()) {
  mkdirSync(stateDir, { recursive: true });
  const clean = (s) => String(s).replace(/[\t\r\n]+/g, " ");
  appendFileSync(join(stateDir, "history.log"), `${now}\t${clean(action)}\t${clean(name)}\t${clean(outcome)}\n`);
}

const tagNames = (wf) =>
  (wf.tags ?? []).map((t) => String(typeof t === "string" ? t : t?.name ?? "").trim().toLowerCase());

async function listWorkflows(n8n) {
  const out = [];
  let cursor = "";
  do {
    const page = await n8n("GET", `/api/v1/workflows?limit=250${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    out.push(...(page?.data ?? []));
    cursor = page?.nextCursor || "";
  } while (cursor);
  return out;
}

const hookPath = (wf) =>
  (wf.nodes ?? []).find((n) => String(n.type).endsWith("webhook"))?.parameters?.path;

// ── push ──────────────────────────────────────────────────────────────────────

export async function readSource(source, { stdin = process.stdin, readFile = readFileSync } = {}) {
  let raw;
  if (source === "-") {
    const chunks = [];
    for await (const c of stdin) chunks.push(c);
    raw = Buffer.concat(chunks).toString("utf8");
  } else {
    raw = readFile(source, "utf8");
  }
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new PushError(`The workflow JSON does not parse: ${e.message}`);
  }
}

/** Build the plan from the JSON, the identity and what n8n already holds. Pure given its inputs. */
export function planPush(wf, id, existingList) {
  const { passed, failed } = checkWorkflow(wf, id);
  const same = existingList.filter((w) => w.name === id.name);
  let action = "create";
  let existing = null;
  if (same.length > 1) failed.push(`name is unique in n8n: ${same.length} workflows are called "${id.name}"`);
  else if (same.length === 1) {
    existing = same[0];
    action = "update";
    if (existing.active) {
      failed.push(`target is not live: "${id.name}" is ACTIVE — never edit a live workflow in place. Push a new copy under a new job name (e.g. "${id.job} v2"), test it, then switch over by hand`);
    } else passed.push("target is inactive");
  }
  // The list endpoint includes nodes, so the path of every other workflow is visible.
  const clash = existingList.filter((w) => w.name !== id.name && hookPath(w) === id.path);
  if (clash.length) failed.push(`webhook path is unique in n8n: also used by "${clash[0]?.name}"`);
  else passed.push("webhook path is unique in n8n");
  return { action, existing, passed, failed };
}

export async function runPush(args, { env = process.env, readWorkflow = readSource, out = console.log, now = () => new Date() } = {}) {
  const cfg = settings(env);
  if (!cfg.key) throw new PushError("N8N_API_KEY is not set. Run this on the VPS through ops/n8n-push.sh.");
  const id = identity(args);
  const source = args._[1];
  if (!source) throw new PushError("Give the workflow JSON path, or - to read it from stdin.");
  const wf = await readWorkflow(source);
  const n8n = makeN8n(cfg);
  const existing = await listWorkflows(n8n);
  const plan = planPush(wf, id, existing);
  const apply = args.apply === true;

  out(`${apply ? "PUSH" : "PLAN (dry run — nothing is changed; add --apply)"}`);
  out(`  name         ${id.name}`);
  out(`  webhook path ${id.path}`);
  out(`  tag          ${id.org}`);
  out(`  action       ${plan.action}${plan.existing ? ` (id ${plan.existing.id})` : ""}, stays INACTIVE`);
  out(`  checks passed (${plan.passed.length}):`);
  for (const p of plan.passed) out(`    ok   ${p}`);
  if (plan.failed.length) {
    out(`  checks FAILED (${plan.failed.length}):`);
    for (const f of plan.failed) out(`    FAIL ${f}`);
    if (apply) audit(cfg.stateDir, "push", id.name, `refused: ${plan.failed.length} check(s) failed`, now().toISOString());
    throw new PushError("Not pushed: fix the failed checks above.");
  }
  if (!apply) return { applied: false, plan };

  const body = {
    name: id.name,
    nodes: withStickyNote(wf, id).nodes,
    connections: wf.connections,
    settings: wf.settings ?? { executionOrder: "v1" },
  };
  const saved = plan.existing
    ? await n8n("PUT", `/api/v1/workflows/${plan.existing.id}`, body)
    : await n8n("POST", "/api/v1/workflows", body);

  // Tag = the org slug and nothing else. Tags are their own resource: find or create, then attach.
  const tags = (await n8n("GET", "/api/v1/tags?limit=250"))?.data ?? [];
  const tag = tags.find((t) => String(t.name).trim().toLowerCase() === id.org) ?? (await n8n("POST", "/api/v1/tags", { name: id.org }));
  await n8n("PUT", `/api/v1/workflows/${saved.id}/tags`, [{ id: tag.id }]);

  const file = join(cfg.stateDir, id.org, id.manifestFile);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, manifestYaml(id, { workflowId: saved.id, status: "inactive", credentials: credentialsNeeded(wf.nodes), now: now().toISOString() }));
  audit(cfg.stateDir, "push", id.name, `${plan.action}d id=${saved.id} inactive`, now().toISOString());
  out(`\n${plan.action === "create" ? "Created" : "Updated"} workflow ${saved.id}, INACTIVE, tagged "${id.org}".`);
  out(`Manifest: ${join(cfg.stateDisplay, id.org, id.manifestFile)}`);
  out("Next: test it, then a person activates it at a terminal with the 'activate' command.");
  return { applied: true, id: saved.id, manifest: file };
}

// ── activate ──────────────────────────────────────────────────────────────────

/** Ask the person to type the workflow name. Injected in tests; real use is a readline on the terminal. */
export function askOnTerminal(question) {
  return new Promise((resolveAnswer) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    rl.question(question, (answer) => {
      rl.close();
      resolveAnswer(answer);
    });
  });
}

export async function runActivate(args, { env = process.env, isTTY = Boolean(process.stdin.isTTY), ask = askOnTerminal, out = console.log, now = () => new Date() } = {}) {
  const cfg = settings(env);
  const name = args._[1];
  const org = typeof args.org === "string" ? args.org.trim().toLowerCase() : "";
  if (!name || !org) throw new PushError('Usage: activate "<Client - Agent - Job>" --org <slug>');
  const log = (outcome) => audit(cfg.stateDir, "activate", name, outcome, now().toISOString());

  if (!isTTY) {
    log("refused: stdin is not a terminal");
    throw new PushError("Refused: activation needs a person at a real terminal (stdin is not a TTY). Run it yourself over 'ssh -t'.");
  }
  if (env.CLAUDECODE) {
    log("refused: running under Claude Code");
    throw new PushError("Refused: Claude Code never activates workflows.");
  }
  if (!cfg.key) throw new PushError("N8N_API_KEY is not set. Run this on the VPS through ops/n8n-push.sh.");

  const n8n = makeN8n(cfg);
  const matches = (await listWorkflows(n8n)).filter((w) => w.name === name);
  if (matches.length !== 1) {
    log(`refused: ${matches.length} workflow(s) with that name`);
    throw new PushError(`Expected exactly one workflow named "${name}", found ${matches.length}.`);
  }
  const wf = await n8n("GET", `/api/v1/workflows/${matches[0].id}`);
  if (wf.active) {
    log("refused: already active");
    throw new PushError("Refused: the workflow is already active.");
  }
  if (!tagNames(wf).includes(org)) {
    log(`refused: not tagged "${org}"`);
    throw new PushError(`Refused: the workflow is not tagged with the org slug "${org}".`);
  }

  out(`About to ACTIVATE "${name}" (id ${wf.id}) for org "${org}". Live webhook: /webhook/${hookPath(wf)}`);
  const typed = await ask("Type the workflow name to confirm: ");
  if (typed.trim() !== name) {
    log("refused: typed name did not match");
    throw new PushError("Refused: the name you typed does not match. Nothing was changed.");
  }
  await n8n("POST", `/api/v1/workflows/${wf.id}/activate`);
  log(`activated id=${wf.id}`);

  // Webhook path is "<slug>-<agent>-<job>", the manifest is "<agent>-<job>.manifest.yaml".
  const manifest = join(cfg.stateDir, org, `${String(hookPath(wf) ?? "").slice(org.length + 1)}.manifest.yaml`);
  if (existsSync(manifest)) {
    writeFileSync(manifest, readFileSync(manifest, "utf8").replace(/^status: .*$/m, 'status: "active"'));
  }
  out(`Activated workflow ${wf.id}.`);
  return { activated: true, id: wf.id };
}

// ── main ──────────────────────────────────────────────────────────────────────

const USAGE = `Usage:
  n8n-push.mjs push <file.json|-> --org <slug> --org-id <uuid> --client "<Client>" --agent "<Agent>" --agent-id <uuid> --job "<Job>" [--apply]
  n8n-push.mjs activate "<Client - Agent - Job>" --org <slug>        (terminal only)`;

async function main(argv) {
  const args = parseArgs(argv);
  const cmd = args._[0];
  if (cmd === "push") return runPush(args);
  if (cmd === "activate") return runActivate(args);
  console.log(USAGE);
  throw new PushError("Unknown command.");
}

const invokedDirectly = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (invokedDirectly) {
  main(process.argv.slice(2)).catch((err) => {
    const msg = scrub(err instanceof PushError ? err.message : (err?.stack ?? err), process.env.N8N_API_KEY);
    process.stderr.write(`\nn8n-push: ${msg}\n`);
    process.exitCode = 1;
  });
}

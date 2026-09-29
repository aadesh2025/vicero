#!/usr/bin/env node
/**
 * provision-client.mjs — stand up a complete client on Vicero in one command.
 *
 *   node scripts/provision-client.mjs --name "Acme Co" --email owner@acme.com --plan starter
 *
 * Creates the org, builds and publishes an agent, clones the starter automation in n8n and
 * binds it as a tool on that agent, then invites the client as `editor` (the client role:
 * edit + test + connect their own channels, but not publish — see app/core/rbac.py).
 *
 * **Idempotent and resumable**, keyed on --email/--name: every step looks for what it would
 * create before creating it, so a re-run after a failure resumes instead of duplicating. This
 * matters most for the invitation: create_invitation only rejects an already-*active* member,
 * so a blind re-run would mint a second pending invite and email the client twice.
 *
 * Location-agnostic: it only ever talks to VICERO_API_BASE_URL and N8N_BASE_URL, so the same
 * command works on a laptop today and over SSH on a VPS later. Only .env differs.
 *
 * Requires Node 18+ (built-in fetch). No npm dependencies on purpose — nothing to install
 * before provisioning a client.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// ── env ───────────────────────────────────────────────────────────────────────

/** Parse repo-root .env. Values may carry an aligned trailing `# comment` (this repo's style). */
function loadEnvFile(path) {
  let raw;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    return {};
  }
  const out = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    // Strip an inline comment only when it's separated by whitespace, so a value that
    // legitimately contains '#' (a password, say) survives intact.
    let value = trimmed.slice(eq + 1).replace(/\s+#.*$/, "").trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

const fileEnv = loadEnvFile(resolve(REPO_ROOT, ".env"));
/** Real environment wins over .env, so a one-off override needs no file edit. */
const env = (key, fallback = "") => (process.env[key] ?? fileEnv[key] ?? fallback).trim();

const CONFIG = {
  apiBaseUrl: env("VICERO_API_BASE_URL", "http://localhost:8000").replace(/\/+$/, ""),
  staffEmail: env("PROVISION_STAFF_EMAIL"),
  staffPassword: env("PROVISION_STAFF_PASSWORD"),
  n8nBaseUrl: env("N8N_BASE_URL", "http://localhost:5678").replace(/\/+$/, ""),
  n8nApiKey: env("N8N_API_KEY"),
};

const TEMPLATE_WORKFLOW_NAME = "TEMPLATE — Starter Automation";
const CLIENT_ROLE = "editor";

// ── cli ───────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) {
      args[key] = "true";
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

const USAGE = `Usage:
  node scripts/provision-client.mjs --name "Acme Co" --email owner@acme.com [--plan starter]

Options:
  --name    Client / organization name (required)
  --email   Client's email; they're invited as "${CLIENT_ROLE}" (required)
  --plan    Free-text label recorded on the summary (default: starter)
  --skip-n8n  Provision everything except the automation (use when n8n is unavailable)

Env (repo .env or real environment):
  VICERO_API_BASE_URL   default http://localhost:8000
  PROVISION_STAFF_EMAIL   staff login; org creation is gated on is_staff, not an API key
  PROVISION_STAFF_PASSWORD
  N8N_BASE_URL            default http://localhost:5678
  N8N_API_KEY             n8n Settings -> API; needs workflow read/create/activate scopes`;

// ── output ────────────────────────────────────────────────────────────────────

const steps = [];
const log = (msg) => process.stdout.write(`${msg}\n`);
const step = (n, msg) => log(`\n[${n}/8] ${msg}`);
const created = (what) => {
  steps.push(`created  ${what}`);
  log(`  + created ${what}`);
};
const reused = (what) => {
  steps.push(`reused   ${what}`);
  log(`  = exists already, reusing ${what}`);
};

class ProvisionError extends Error {}

// ── http ──────────────────────────────────────────────────────────────────────

let accessToken = null;

async function api(method, path, { body, orgId } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (orgId) headers["X-Org-Id"] = orgId;

  let resp;
  try {
    resp = await fetch(`${CONFIG.apiBaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    throw new ProvisionError(
      `Cannot reach the Vicero API at ${CONFIG.apiBaseUrl} (${cause.message}).\n` +
        `  Is it running? Locally: cd apps/api && uvicorn app.main:app --port 8000`
    );
  }

  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const code = data?.error?.code ?? resp.status;
    let message = data?.error?.message ?? text.slice(0, 200);
    // A bare "Request validation failed" is useless to whoever ran this; the actionable part
    // (which field, and why) lives in details. Surface it.
    const details = data?.error?.details;
    if (Array.isArray(details) && details.length) {
      const parts = details.map((d) => `${(d.loc ?? []).join(".")}: ${d.msg ?? ""}`.trim());
      message += `\n    ${parts.join("\n    ")}`;
    }
    const err = new ProvisionError(`${method} ${path} failed: ${code} — ${message}`);
    err.code = data?.error?.code;
    err.status = resp.status;
    throw err;
  }
  return data;
}

async function n8n(method, path, body) {
  let resp;
  try {
    resp = await fetch(`${CONFIG.n8nBaseUrl}${path}`, {
      method,
      headers: { "Content-Type": "application/json", "X-N8N-API-KEY": CONFIG.n8nApiKey },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    throw new ProvisionError(
      `Cannot reach n8n at ${CONFIG.n8nBaseUrl} (${cause.message}).\n` +
        `  Locally: cd infra && N8N_HOST_PORT=5679 docker compose up -d n8n`
    );
  }
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const detail = data?.message ?? text.slice(0, 200);
    if (resp.status === 401) {
      throw new ProvisionError(
        `n8n rejected the API key (401). Mint one in n8n -> Settings -> API with workflow ` +
          `read/create/activate scopes and set N8N_API_KEY.\n  ${detail}`
      );
    }
    throw new ProvisionError(`n8n ${method} ${path} failed: ${resp.status} — ${detail}`);
  }
  return data;
}

// ── helpers ───────────────────────────────────────────────────────────────────

const slugify = (s) =>
  s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .slice(0, 48) || "client";

function starterPersona(clientName) {
  return {
    displayName: `${clientName} Assistant`,
    character: "helpful, concise, professional",
    role: `Customer support assistant for ${clientName}`,
    tone: "friendly",
    guardrails: [],
  };
}

function starterSystemPrompt(clientName) {
  return [
    `You are the customer support assistant for ${clientName}.`,
    "Answer from the knowledge you are given. If you do not know, say so plainly and offer",
    "to pass the question to a human rather than guessing.",
    "Keep replies short and specific. Use the tools available to you when a request needs an",
    "action taken rather than a question answered.",
  ].join(" ");
}

// ── steps ─────────────────────────────────────────────────────────────────────

async function loginAsStaff() {
  step(1, "Authenticating as staff");
  if (!CONFIG.staffEmail || !CONFIG.staffPassword) {
    throw new ProvisionError(
      "PROVISION_STAFF_EMAIL / PROVISION_STAFF_PASSWORD are not set.\n" +
        "  Creating an organization is gated on the user's is_staff flag server-side, so this\n" +
        "  script signs in as a staff user — an API key cannot do it (there is no such scope)."
    );
  }
  const data = await api("POST", "/v1/auth/login", {
    body: { email: CONFIG.staffEmail, password: CONFIG.staffPassword },
  });
  accessToken = data.access_token;
  const me = await api("GET", "/v1/auth/me");
  if (!me.user?.is_staff) {
    throw new ProvisionError(
      `${CONFIG.staffEmail} is not a staff user, so POST /v1/orgs will return ` +
        `orgs.create_forbidden. Set is_staff on that account (or point at one that has it).`
    );
  }
  log(`  signed in as ${me.user.email} (is_staff)`);
}

async function ensureOrg(name) {
  step(2, `Organization "${name}"`);
  const orgs = await api("GET", "/v1/orgs");
  const existing = orgs.find((o) => o.name === name);
  if (existing) {
    reused(`org ${existing.id}`);
    return existing;
  }
  const org = await api("POST", "/v1/orgs", { body: { name } });
  created(`org ${org.id}`);
  return org;
}

async function ensurePublicContacts(org, email) {
  // The PII-redaction allowlist (docs/11 Phase B, ADR-053/056). An org with an empty list has
  // an agent that redacts EVERY contact detail out of its own replies, including its own
  // support address — which is exactly how the first client shipped, silently, for a week.
  // Seeding the owner's email means a new client is never in that state by default.
  step(2.5, "Public contacts (PII allowlist)");
  const existing = Array.isArray(org.public_contacts) ? org.public_contacts : [];
  if (existing.length > 0) {
    // Never overwrite: by the time this re-runs the client may have curated the list, and
    // replacing it would silently re-break replies they had already fixed.
    reused(`${existing.length} public contact(s) already set`);
    return existing;
  }
  const updated = await api("PATCH", `/v1/orgs/${org.id}`, {
    orgId: org.id,
    body: { public_contacts: [email] },
  });
  created(`public contact ${email}`);
  return updated.public_contacts ?? [email];
}

async function ensureAgent(org, clientName) {
  const agentName = `${clientName} Assistant`;
  step(3, `Agent "${agentName}"`);
  const agents = await api("GET", "/v1/agents", { orgId: org.id });
  let agent = agents.find((a) => a.name === agentName);
  if (agent) {
    reused(`agent ${agent.id}`);
    // Never re-apply the starter config over an agent that already has a prompt. By the time
    // this is re-run the client may well have rewritten their own persona, and provisioning
    // must not quietly replace their work with the defaults. (It would also fork a fresh
    // draft off the published version every run — ADR-023 branch-on-edit — climbing v2, v3,
    // v4… for no reason.) An agent whose prompt is still blank was never configured, so an
    // interrupted first run still gets finished.
    const versions = await api("GET", `/v1/agents/${agent.id}/versions`, { orgId: org.id });
    const latest = versions.reduce((a, b) => (b.version > a.version ? b : a), versions[0]);
    if (latest?.system_prompt?.trim()) {
      log(`  already configured (v${latest.version}) — leaving its persona untouched`);
      return { agent, versionNumber: latest.version, alreadyConfigured: true };
    }
  } else {
    agent = await api("POST", "/v1/agents", {
      orgId: org.id,
      body: { name: agentName, description: `Support agent for ${clientName}` },
    });
    created(`agent ${agent.id}`);
  }

  // Configure the draft. `draft_version` is the editable one; patching a published version
  // transparently forks a new draft (branch-on-edit, ADR-023) and returns its number.
  const version = await api("PATCH", `/v1/agents/${agent.id}/versions/${agent.draft_version}`, {
    orgId: org.id,
    body: {
      system_prompt: starterSystemPrompt(clientName),
      persona: starterPersona(clientName),
      welcome_message: `Hi! I'm the ${clientName} assistant. How can I help?`,
      fallback_message:
        "I'm not sure about that one. Would you like me to pass this to the team?",
      // No model_config on purpose: the agent keeps the platform default (llm/catalog.py
      // DEFAULT_CHAT_MODEL). Pinning a model name here is what went stale when Groq retired it.
      // tools_enabled is what lets the agent actually call the n8n automation bound below.
      features: { tools_enabled: true, memory_enabled: true, handoff_enabled: true },
    },
  });
  log(`  configured draft v${version.version} (platform default model, tools on)`);
  return { agent, versionNumber: version.version, alreadyConfigured: false };
}

async function publishAgent(org, agent, versionNumber, alreadyConfigured) {
  // An agent the client may have edited is republished only if it isn't live at all — that's
  // the interrupted-first-run case. Publishing an agent whose newest draft is the client's
  // own unreviewed work would push it live behind their back, which is the exact thing the
  // AGENTS_WRITE / AGENTS_PUBLISH split exists to prevent.
  if (alreadyConfigured && agent.status === "published") {
    step(4, "Publish — already live, nothing to do");
    reused(`published agent ${agent.id}`);
    return agent;
  }
  step(4, `Publishing agent v${versionNumber}`);
  const published = await api(
    "POST",
    `/v1/agents/${agent.id}/versions/${versionNumber}/publish`,
    { orgId: org.id }
  );
  // Publishing is a straight state set, so re-running is a no-op rather than an error.
  log(`  agent status=${published.status}, live before the client ever logs in`);
  return published;
}

/** Tag a workflow with the owning org's slug, which is what makes it visible to that org.
 *
 * Visibility is deny-by-default (ADR-040): an untagged workflow is hidden from every org,
 * including the client it was just built for, and `POST /v1/tools/n8n/bind` refuses it with
 * `tools.n8n_forbidden`. So this has to succeed *before* the binding step, and a failure is
 * fatal rather than a warning — continuing would hand the client an automation they cannot
 * see and a provisioning run that dies later with a much less obvious error.
 *
 * Tags are their own n8n resource: find-or-create by name, then attach. Existing tags are
 * preserved, so re-running never strips a tag someone added by hand.
 */
async function tagWorkflowForOrg(workflow, orgSlug) {
  const wanted = String(orgSlug).trim().toLowerCase();
  if (!wanted) throw new ProvisionError("Cannot tag the workflow: the org has no slug.");

  const current = (workflow.tags ?? []).map((t) => (typeof t === "string" ? t : t?.name));
  if (current.some((n) => String(n).trim().toLowerCase() === wanted)) {
    reused(`n8n tag "${wanted}" already on workflow ${workflow.id}`);
    return;
  }

  let tags;
  try {
    tags = await n8n("GET", "/api/v1/tags?limit=250");
  } catch (cause) {
    throw tagScopeError(cause);
  }
  let tag = (tags.data ?? []).find((t) => String(t.name).trim().toLowerCase() === wanted);
  if (!tag) {
    try {
      tag = await n8n("POST", "/api/v1/tags", { name: wanted });
    } catch (cause) {
      throw tagScopeError(cause);
    }
  }

  // Keep whatever was already there; PUT replaces the whole set.
  const keep = (workflow.tags ?? [])
    .map((t) => (typeof t === "string" ? null : t?.id))
    .filter(Boolean);
  const ids = [...new Set([...keep, tag.id])].map((id) => ({ id }));
  try {
    await n8n("PUT", `/api/v1/workflows/${workflow.id}/tags`, ids);
  } catch (cause) {
    throw tagScopeError(cause);
  }
  created(`n8n tag "${wanted}" on workflow ${workflow.id}`);
}

function tagScopeError(cause) {
  const detail = cause instanceof Error ? cause.message : String(cause);
  if (!/403|Forbidden/i.test(detail)) return cause;
  return new ProvisionError(
    `n8n refused a tag operation (403). The API key can read and write workflows but not ` +
      `tags.\n` +
      `  Mint a new key in n8n -> Settings -> API that ALSO includes the tag ` +
      `read/create scopes and the workflow "update tags" scope, then set N8N_API_KEY.\n` +
      `  This is not optional: workflow visibility is deny-by-default, so an untagged ` +
      `workflow is invisible to the very client it was provisioned for.\n  ${detail}`
  );
}

async function ensureWorkflow(clientName, orgSlug) {
  step(5, "n8n starter automation");
  const workflowName = `${clientName} — Starter Automation`;
  const list = await n8n("GET", "/api/v1/workflows?limit=250");
  const existing = (list.data ?? []).find((w) => w.name === workflowName);
  if (existing) {
    reused(`n8n workflow ${existing.id}`);
    if (!existing.active) {
      await n8n("POST", `/api/v1/workflows/${existing.id}/activate`);
      log("  activated it (was inactive)");
    }
    // Re-tag on re-run: a workflow created before tagging existed is otherwise invisible.
    await tagWorkflowForOrg(existing, orgSlug);
    return existing;
  }

  const template = (list.data ?? []).find((w) => w.name === TEMPLATE_WORKFLOW_NAME);
  if (!template) {
    throw new ProvisionError(
      `n8n has no workflow named "${TEMPLATE_WORKFLOW_NAME}" to clone.\n` +
        `  Import it once: curl -X POST -H "X-N8N-API-KEY: $N8N_API_KEY" \\\n` +
        `    -H 'Content-Type: application/json' \\\n` +
        `    --data-binary @infra/n8n/template-starter-automation.json \\\n` +
        `    ${CONFIG.n8nBaseUrl}/api/v1/workflows`
    );
  }
  const full = await n8n("GET", `/api/v1/workflows/${template.id}`);

  // Signature verification is not optional. Vicero signs every call to an n8n webhook, but a
  // workflow only benefits if it *checks* the signature; without it anyone who learns the webhook
  // URL can call the automation directly, bypassing the agent, RBAC and budget limits. Every client
  // workflow is a clone of this template, so refuse to clone one that does not verify.
  // (docs/07-INTEGRATIONS.md "Webhook signature verification"; RISK-REGISTER R15.)
  if (!full.nodes.some((node) => node.name === "Verify Vicero signature")) {
    throw new ProvisionError(
      `The template "${TEMPLATE_WORKFLOW_NAME}" has no "Verify Vicero signature" node, so every ` +
        `client cloned from it would get an unauthenticated webhook.
` +
        `  Re-import infra/n8n/template-starter-automation.json (it ships with verification) — see
` +
        `  infra/n8n/README.md.`
    );
  }

  // Each client needs its **own** webhook path. Cloning the template verbatim would point
  // every client's tool at one shared URL, so whichever workflow n8n resolved first would
  // answer everyone — a cross-client data leak, not just a mix-up.
  const slug = slugify(clientName);
  const path = `${slug}-starter-automation`;
  const nodes = full.nodes.map((node) => {
    if (!String(node.type).endsWith("webhook")) return node;
    return { ...node, webhookId: path, parameters: { ...node.parameters, path } };
  });

  const workflow = await n8n("POST", "/api/v1/workflows", {
    name: workflowName,
    nodes,
    connections: full.connections,
    settings: full.settings ?? { executionOrder: "v1" },
  });
  created(`n8n workflow ${workflow.id} (webhook path "${path}")`);
  await n8n("POST", `/api/v1/workflows/${workflow.id}/activate`);
  log("  activated");
  // Scope it to the owning org before anything tries to bind it.
  await tagWorkflowForOrg(workflow, orgSlug);
  return workflow;
}

async function ensureTool(org, agent, workflow) {
  step(6, "Binding the automation as an agent tool");
  const toolName = "starter_automation";
  const tools = await api("GET", `/v1/tools?agent_id=${agent.id}`, { orgId: org.id });
  const existing = tools.find((t) => t.name === toolName);
  if (existing) {
    reused(`tool ${existing.id}`);
    return existing;
  }
  // /v1/tools/n8n/bind resolves the production webhook URL from the workflow itself, so the
  // URL is derived by the same code the runtime uses rather than guessed here.
  const tool = await api("POST", "/v1/tools/n8n/bind", {
    orgId: org.id,
    body: {
      name: toolName,
      workflow_id: String(workflow.id),
      workflow_name: workflow.name,
      agent_id: agent.id,
      mode: "sync",
      description:
        "Runs this client's starter automation in n8n. Use it when the user asks for an " +
        "action to be carried out, and pass the relevant details as args.",
    },
  });
  created(`tool ${tool.id} -> ${tool.config?.webhook_url}`);
  return tool;
}

async function ensureInvitation(org, email) {
  step(7, `Inviting ${email} as ${CLIENT_ROLE}`);
  const pending = await api("GET", `/v1/orgs/${org.id}/invitations`);
  if (pending.some((i) => i.email.toLowerCase() === email.toLowerCase())) {
    reused(`pending invitation for ${email} (not re-sending)`);
    return null;
  }
  try {
    const invitation = await api("POST", `/v1/orgs/${org.id}/invitations`, {
      body: { email, role: CLIENT_ROLE },
    });
    created(`invitation for ${email}`);
    return invitation;
  } catch (err) {
    if (err.code === "org.already_member") {
      reused(`${email} is already an active member`);
      return null;
    }
    throw err;
  }
}

// ── main ──────────────────────────────────────────────────────────────────────

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || args.h) {
    log(USAGE);
    return 0;
  }
  const name = args.name?.trim();
  const email = args.email?.trim();
  const plan = (args.plan ?? "starter").trim();
  if (!name || !email) {
    log(USAGE);
    throw new ProvisionError("--name and --email are both required.");
  }

  log(`Provisioning "${name}" <${email}> [plan: ${plan}]`);
  log(`  api: ${CONFIG.apiBaseUrl}`);
  log(`  n8n: ${args["skip-n8n"] ? "(skipped)" : CONFIG.n8nBaseUrl}`);

  await loginAsStaff();
  const org = await ensureOrg(name);
  await ensurePublicContacts(org, email);
  const { agent, versionNumber, alreadyConfigured } = await ensureAgent(org, name);
  const published = await publishAgent(org, agent, versionNumber, alreadyConfigured);

  let workflow = null;
  let tool = null;
  if (args["skip-n8n"]) {
    step(5, "n8n starter automation — skipped (--skip-n8n)");
    step(6, "Tool binding — skipped (--skip-n8n)");
  } else {
    // The org's real slug, not slugify(name) — the API suffixes it on collision.
    workflow = await ensureWorkflow(name, org.slug);
    tool = await ensureTool(org, agent, workflow);
  }

  const invitation = await ensureInvitation(org, email);

  step(8, "Summary");
  log("");
  log(`  Client            ${name}  (plan: ${plan})`);
  log(`  Organization      ${org.id}`);
  log(`  Agent             ${agent.id}  (${published.status})`);
  log(`  Widget public key ${published.public_key}`);
  if (workflow) log(`  n8n workflow      ${workflow.id}  "${workflow.name}"`);
  if (tool) log(`  Automation tool   ${tool.name} -> ${tool.config?.webhook_url}`);
  log(
    invitation
      ? `  Invite            sent to ${email} as ${CLIENT_ROLE}`
      : `  Invite            already handled for ${email}`
  );
  log("");
  log("  Embed snippet for the client's site:");
  log(
    `    <script src="${CONFIG.apiBaseUrl.replace(/:\d+$/, ":3000")}/widget.js" ` +
      `data-agent="${published.public_key}" defer></script>`
  );
  log("");
  log(`  Actions: ${steps.length}`);
  for (const s of steps) log(`    ${s}`);
  log("\nDone.");
  return 0;
}

// Pure helpers are exported so they can be unit-tested without provisioning anything.
export { loadEnvFile, parseArgs, slugify, starterPersona, starterSystemPrompt, tagScopeError };

// Only provision when run as a command, not when imported by a test.
const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  // Set exitCode and let the loop drain rather than calling process.exit(): a hard exit with
  // fetch's keep-alive sockets still open trips a libuv assertion on Windows, which printed
  // alarming "Assertion failed" noise after an otherwise clean error message.
  main()
    .then((code) => {
      process.exitCode = code;
    })
    .catch((err) => {
      if (err instanceof ProvisionError) {
        process.stderr.write(`\nProvisioning failed.\n  ${err.message}\n\n`);
        process.stderr.write("Nothing is half-created: re-run the same command to resume.\n");
      } else {
        process.stderr.write(`\nUnexpected error: ${err?.stack ?? err}\n`);
      }
      process.exitCode = 1;
    });
}

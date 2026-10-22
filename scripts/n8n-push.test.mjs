/**
 * Tests for n8n-push.mjs. Run: node --test scripts/n8n-push.test.mjs
 *
 * Every n8n call goes to a mock HTTP server started here on a random localhost port. Nothing in this file
 * can reach a real n8n: N8N_BASE_URL is always the mock, and the key is a fake.
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  PushError,
  audit,
  checkWorkflow,
  credentialsNeeded,
  findSecrets,
  identity,
  manifestYaml,
  parseArgs,
  planPush,
  runActivate,
  runPush,
  slugify,
  withStickyNote,
} from "./n8n-push.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const FAKE_KEY = "test-key-0123456789abcdef";
const ORG_ID = "11111111-1111-4111-8111-111111111111";
const AGENT_ID = "22222222-2222-4222-8222-222222222222";

const FLAGS = {
  org: "acme-co",
  "org-id": ORG_ID,
  client: "Acme Co",
  agent: "Support Bot",
  "agent-id": AGENT_ID,
  job: "Book Appointment",
};
const NAME = "Acme Co - Support Bot - Book Appointment";
const PATH = "acme-co-support-bot-book-appointment";

/** The real template, renamed the way a client build would be. */
function goodWorkflow(overrides = {}) {
  const wf = JSON.parse(readFileSync(resolve(HERE, "..", "infra/n8n/template-starter-automation.json"), "utf8"));
  wf.name = NAME;
  const hook = wf.nodes.find((n) => n.type.endsWith("webhook"));
  hook.parameters.path = PATH;
  hook.webhookId = PATH;
  return { ...wf, ...overrides };
}

// ── mock n8n ──────────────────────────────────────────────────────────────────

let server;
let base;
let calls;
let db;

const resetDb = () => {
  calls = [];
  db = { workflows: [], tags: [], nextId: 100 };
};

before(async () => {
  resetDb();
  server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const json = body ? JSON.parse(body) : undefined;
      calls.push({ method: req.method, url: req.url, key: req.headers["x-n8n-api-key"], body: json });
      const send = (status, data) => {
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(data));
      };
      if (req.headers["x-n8n-api-key"] !== FAKE_KEY) return send(401, { message: "unauthorized" });
      const url = new URL(req.url, "http://x");
      const wfMatch = url.pathname.match(/^\/api\/v1\/workflows\/([^/]+)(\/(tags|activate))?$/);
      if (req.method === "GET" && url.pathname === "/api/v1/workflows") return send(200, { data: db.workflows });
      if (req.method === "POST" && url.pathname === "/api/v1/workflows") {
        const wf = { id: String(db.nextId++), active: false, tags: [], ...json };
        db.workflows.push(wf);
        return send(200, wf);
      }
      if (req.method === "GET" && url.pathname === "/api/v1/tags") return send(200, { data: db.tags });
      if (req.method === "POST" && url.pathname === "/api/v1/tags") {
        const t = { id: `t${db.nextId++}`, name: json.name };
        db.tags.push(t);
        return send(200, t);
      }
      if (wfMatch) {
        const wf = db.workflows.find((w) => w.id === wfMatch[1]);
        if (!wf) return send(404, { message: "not found" });
        if (req.method === "GET" && !wfMatch[2]) return send(200, wf);
        if (req.method === "PUT" && !wfMatch[2]) return send(200, Object.assign(wf, json));
        if (req.method === "PUT" && wfMatch[3] === "tags") {
          wf.tags = json.map((x) => db.tags.find((t) => t.id === x.id));
          return send(200, wf.tags);
        }
        if (req.method === "POST" && wfMatch[3] === "activate") {
          wf.active = true;
          return send(200, wf);
        }
      }
      return send(404, { message: `unhandled ${req.method} ${req.url}` });
    });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => server.close());

const freshEnv = () => ({
  N8N_API_KEY: FAKE_KEY,
  N8N_BASE_URL: base,
  N8N_PUSH_STATE_DIR: mkdtempSync(join(tmpdir(), "n8n-push-")),
});
const quiet = () => {
  const lines = [];
  return { out: (m) => lines.push(String(m)), lines };
};
const argsFor = (extra = {}, file = "wf.json") => ({ _: ["push", file], ...FLAGS, ...extra });
const readerOf = (wf) => async () => wf;
const writes = () => calls.filter((c) => c.method !== "GET");

// ── pure helpers ──────────────────────────────────────────────────────────────

test("parseArgs keeps '-' as a positional (stdin) and flags as values", () => {
  const a = parseArgs(["push", "-", "--org", "acme", "--apply"]);
  assert.deepEqual(a._, ["push", "-"]);
  assert.equal(a.org, "acme");
  assert.equal(a.apply, true);
});

test("identity derives the name, path and manifest file", () => {
  const id = identity(FLAGS);
  assert.equal(id.name, NAME);
  assert.equal(id.path, PATH);
  assert.equal(id.manifestFile, "support-bot-book-appointment.manifest.yaml");
});

test("identity refuses when any id or name is missing — ids are never guessed", () => {
  for (const key of Object.keys(FLAGS)) {
    const flags = { ...FLAGS };
    delete flags[key];
    assert.throws(() => identity(flags), (e) => e instanceof PushError && e.message.includes(`--${key}`), key);
  }
  assert.throws(() => identity({ ...FLAGS, "org-id": "  " }), PushError);
  assert.throws(() => identity({ ...FLAGS, "agent-id": "42" }), /UUID/);
  assert.throws(() => identity({ ...FLAGS, org: "Bad Slug!" }), /org slug/);
});

test("slugify matches the webhook-safe style used elsewhere", () => {
  assert.equal(slugify("Book Appointment"), "book-appointment");
  assert.equal(slugify("  A/B & C  "), "ab-c");
});

test("the template, renamed, passes every check", () => {
  const { failed, passed } = checkWorkflow(goodWorkflow(), identity(FLAGS));
  assert.deepEqual(failed, []);
  assert.ok(passed.length >= 10);
});

test("checkWorkflow catches the failures that matter", () => {
  const id = identity(FLAGS);
  const failing = (wf) => checkWorkflow(wf, id).failed.join("\n");

  assert.match(failing(goodWorkflow({ name: "Other - Bot - Job" })), /workflow name/);

  const wrongPath = goodWorkflow();
  wrongPath.nodes.find((n) => n.type.endsWith("webhook")).parameters.path = "template-starter-automation";
  assert.match(failing(wrongPath), /webhook path/);

  const noSig = goodWorkflow();
  noSig.nodes = noSig.nodes.filter((n) => n.name !== "Verify Vicero signature");
  assert.match(failing(noSig), /signature nodes/);

  const bypass = goodWorkflow();
  bypass.connections.Webhook.main[0] = [{ node: "Echo Input", type: "main", index: 0 }];
  assert.match(failing(bypass), /nothing else before the check/);

  const code = goodWorkflow();
  code.nodes.push({ name: "Do stuff", type: "n8n-nodes-base.code", parameters: { jsCode: "return []" }, position: [0, 0] });
  assert.match(failing(code), /no Code nodes/);

  const weakSig = goodWorkflow();
  weakSig.nodes.find((n) => n.name === "Verify Vicero signature").parameters.jsCode = "return [{json:{verified:true}}]";
  assert.match(failing(weakSig), /signature check is the template's/);

  const pinned = goodWorkflow({ pinData: { Webhook: [{ json: { phone: "+91 98765 43210" } }] } });
  assert.match(failing(pinned), /pinned data/);
});

test("findSecrets flags pasted keys and literal secret fields, not expressions", () => {
  assert.ok(findSecrets({ a: "gsk_" + "a".repeat(30) }).length);
  assert.ok(findSecrets({ headers: { Authorization: "Bearer " + "x".repeat(30) } }).length);
  assert.ok(findSecrets({ apiKey: "abcdefghijklmnop1234" }).length);
  assert.deepEqual(findSecrets({ apiKey: "={{ $env.SOMETHING }}", note: "hello world" }), []);
});

test("credentialsNeeded lists names only", () => {
  const nodes = [
    { name: "Sheet", credentials: { googleSheetsOAuth2Api: { id: "9", name: "ORG-1 | Google Sheets" } } },
    { name: "Sheet 2", credentials: { googleSheetsOAuth2Api: { id: "9", name: "ORG-1 | Google Sheets" } } },
    { name: "Plain" },
  ];
  assert.deepEqual(credentialsNeeded(nodes), [{ type: "googleSheetsOAuth2Api", name: "ORG-1 | Google Sheets" }]);
});

test("withStickyNote adds one note with the ids and is idempotent", () => {
  const id = identity(FLAGS);
  const once = withStickyNote(goodWorkflow(), id);
  const twice = withStickyNote(once, id);
  const notes = twice.nodes.filter((n) => n.type === "n8n-nodes-base.stickyNote");
  assert.equal(notes.length, 1);
  for (const needle of ["acme-co", ORG_ID, "Support Bot", AGENT_ID, "Book Appointment"]) {
    assert.ok(notes[0].parameters.content.includes(needle), needle);
  }
  assert.equal(twice.nodes.length, goodWorkflow().nodes.length + 1);
});

test("manifestYaml carries the required fields", () => {
  const y = manifestYaml(identity(FLAGS), { workflowId: "100", status: "inactive", credentials: [{ type: "t", name: "n" }], now: "2026-10-22T00:00:00Z" });
  for (const line of [`org_id: "${ORG_ID}"`, `agent_id: "${AGENT_ID}"`, 'n8n_workflow_id: "100"', 'status: "inactive"', "credentials_needed:", '  - type: "t"']) {
    assert.ok(y.includes(line), line);
  }
  assert.ok(manifestYaml(identity(FLAGS), { workflowId: null, status: "inactive", credentials: [], now: "x" }).includes("credentials_needed: []"));
});

test("planPush refuses a live target and a webhook path used by another workflow", () => {
  const id = identity(FLAGS);
  const live = planPush(goodWorkflow(), id, [{ id: "1", name: NAME, active: true, nodes: [] }]);
  assert.match(live.failed.join(), /ACTIVE/);
  const clash = planPush(goodWorkflow(), id, [{ id: "2", name: "Someone else", active: false, nodes: [{ type: "n8n-nodes-base.webhook", parameters: { path: PATH } }] }]);
  assert.match(clash.failed.join(), /path is unique/);
  const ok = planPush(goodWorkflow(), id, [{ id: "1", name: NAME, active: false, nodes: [] }]);
  assert.equal(ok.action, "update");
  assert.deepEqual(ok.failed, []);
});

// ── push against the mock ─────────────────────────────────────────────────────

test("push is a dry run by default: prints the plan and makes no write call", async () => {
  resetDb();
  const env = freshEnv();
  const { out, lines } = quiet();
  const r = await runPush(argsFor(), { env, readWorkflow: readerOf(goodWorkflow()), out });
  assert.equal(r.applied, false);
  assert.deepEqual(writes(), []);
  const text = lines.join("\n");
  assert.match(text, /PLAN \(dry run/);
  assert.ok(text.includes(NAME) && text.includes(PATH) && text.includes("tag          acme-co"));
  assert.equal(existsSync(join(env.N8N_PUSH_STATE_DIR, "acme-co")), false, "no manifest on a dry run");
  assert.equal(existsSync(join(env.N8N_PUSH_STATE_DIR, "history.log")), false, "no audit line on a dry run");
});

test("push --apply creates an INACTIVE, org-tagged workflow, a manifest and one audit line", async () => {
  resetDb();
  const env = freshEnv();
  const { out } = quiet();
  const r = await runPush(argsFor({ apply: true }), { env, readWorkflow: readerOf(goodWorkflow()), out, now: () => new Date("2026-10-22T10:00:00Z") });
  assert.equal(r.applied, true);

  const wf = db.workflows[0];
  assert.equal(wf.active, false);
  assert.equal(wf.name, NAME);
  assert.deepEqual(wf.tags.map((t) => t.name), ["acme-co"]);
  assert.ok(wf.nodes.some((n) => n.type === "n8n-nodes-base.stickyNote" && n.parameters.content.includes(AGENT_ID)));
  assert.equal(calls.some((c) => c.url.endsWith("/activate")), false, "push must never activate");

  const manifest = readFileSync(join(env.N8N_PUSH_STATE_DIR, "acme-co", "support-bot-book-appointment.manifest.yaml"), "utf8");
  assert.ok(manifest.includes('n8n_workflow_id: "100"') && manifest.includes('status: "inactive"') && manifest.includes(ORG_ID));

  const history = readFileSync(join(env.N8N_PUSH_STATE_DIR, "history.log"), "utf8").trim().split("\n");
  assert.equal(history.length, 1);
  assert.match(history[0], /^2026-10-22T10:00:00.000Z\tpush\tAcme Co - Support Bot - Book Appointment\tcreated id=100 inactive$/);
  assert.ok(!history[0].includes(FAKE_KEY));
});

test("push --apply prints the host manifest path when N8N_PUSH_STATE_DISPLAY is set", async () => {
  resetDb();
  const env = { ...freshEnv(), N8N_PUSH_STATE_DISPLAY: "/opt/vicero/n8n-push-state" };
  const { out, lines } = quiet();
  await runPush(argsFor({ apply: true }), { env, readWorkflow: readerOf(goodWorkflow()), out });
  const line = lines.find((l) => l.startsWith("Manifest:"));
  assert.equal(line.replaceAll("\\", "/"), "Manifest: /opt/vicero/n8n-push-state/acme-co/support-bot-book-appointment.manifest.yaml");
  assert.ok(existsSync(join(env.N8N_PUSH_STATE_DIR, "acme-co", "support-bot-book-appointment.manifest.yaml")), "still written to the real state dir");
});

test("push --apply twice updates in place instead of duplicating", async () => {
  resetDb();
  const env = freshEnv();
  const { out } = quiet();
  await runPush(argsFor({ apply: true }), { env, readWorkflow: readerOf(goodWorkflow()), out });
  await runPush(argsFor({ apply: true }), { env, readWorkflow: readerOf(goodWorkflow()), out });
  assert.equal(db.workflows.length, 1);
  assert.equal(db.workflows[0].nodes.filter((n) => n.type === "n8n-nodes-base.stickyNote").length, 1);
  assert.equal(db.tags.length, 1);
});

test("push refuses to touch a live workflow and writes nothing", async () => {
  resetDb();
  const env = freshEnv();
  db.workflows.push({ id: "7", name: NAME, active: true, tags: [], nodes: [] });
  const { out } = quiet();
  await assert.rejects(runPush(argsFor({ apply: true }), { env, readWorkflow: readerOf(goodWorkflow()), out }), /Not pushed/);
  assert.deepEqual(writes(), []);
  assert.match(readFileSync(join(env.N8N_PUSH_STATE_DIR, "history.log"), "utf8"), /refused/);
});

test("push refuses when a check fails or an id is missing, before any write", async () => {
  resetDb();
  const env = freshEnv();
  const { out } = quiet();
  const bad = goodWorkflow();
  bad.connections.Webhook.main[0] = [{ node: "Echo Input", type: "main", index: 0 }];
  await assert.rejects(runPush(argsFor({ apply: true }), { env, readWorkflow: readerOf(bad), out }), /Not pushed/);
  await assert.rejects(runPush(argsFor({ apply: true, "agent-id": undefined }), { env, readWorkflow: readerOf(goodWorkflow()), out }), /--agent-id/);
  assert.deepEqual(writes(), []);
});

test("push needs the key, and never prints it", async () => {
  resetDb();
  const env = { ...freshEnv(), N8N_API_KEY: "" };
  await assert.rejects(runPush(argsFor(), { env, readWorkflow: readerOf(goodWorkflow()), out: () => {} }), /N8N_API_KEY is not set/);

  const wrong = { ...freshEnv(), N8N_API_KEY: "wrong-key-value-123456" };
  const { out, lines } = quiet();
  await assert.rejects(runPush(argsFor(), { env: wrong, readWorkflow: readerOf(goodWorkflow()), out }), (e) => {
    assert.ok(!e.message.includes("wrong-key-value-123456"));
    return /401/.test(e.message);
  });
  assert.ok(!lines.join("\n").includes("wrong-key-value-123456"));
});

// ── activate ──────────────────────────────────────────────────────────────────

async function pushedWorkflow(env) {
  await runPush(argsFor({ apply: true }), { env, readWorkflow: readerOf(goodWorkflow()), out: () => {} });
  return db.workflows[0];
}

test("activate refuses without a terminal, and changes nothing", async () => {
  resetDb();
  const env = freshEnv();
  const wf = await pushedWorkflow(env);
  const before = calls.length;
  await assert.rejects(runActivate({ _: ["activate", NAME], org: "acme-co" }, { env, isTTY: false, ask: async () => NAME, out: () => {} }), /not a TTY/);
  assert.equal(calls.length, before, "no n8n call at all");
  assert.equal(wf.active, false);
  assert.match(readFileSync(join(env.N8N_PUSH_STATE_DIR, "history.log"), "utf8"), /activate\t.*refused: stdin is not a terminal/);
});

test("activate refuses under Claude Code even with a terminal", async () => {
  resetDb();
  const env = { ...freshEnv(), CLAUDECODE: "1" };
  const wf = await pushedWorkflow(env);
  await assert.rejects(runActivate({ _: ["activate", NAME], org: "acme-co" }, { env, isTTY: true, ask: async () => NAME, out: () => {} }), /Claude Code never activates/);
  assert.equal(wf.active, false);
});

test("activate refuses when the typed name is wrong", async () => {
  resetDb();
  const env = freshEnv();
  const wf = await pushedWorkflow(env);
  await assert.rejects(runActivate({ _: ["activate", NAME], org: "acme-co" }, { env, isTTY: true, ask: async () => "acme", out: () => {} }), /does not match/);
  assert.equal(wf.active, false);
});

test("activate refuses an untagged or wrongly tagged workflow", async () => {
  resetDb();
  const env = freshEnv();
  const wf = await pushedWorkflow(env);
  wf.tags = [];
  await assert.rejects(runActivate({ _: ["activate", NAME], org: "acme-co" }, { env, isTTY: true, ask: async () => NAME, out: () => {} }), /not tagged/);
  wf.tags = [{ id: "t1", name: "globex-inc" }];
  await assert.rejects(runActivate({ _: ["activate", NAME], org: "acme-co" }, { env, isTTY: true, ask: async () => NAME, out: () => {} }), /not tagged/);
  assert.equal(wf.active, false);
});

test("activate refuses an already-active workflow", async () => {
  resetDb();
  const env = freshEnv();
  const wf = await pushedWorkflow(env);
  wf.active = true;
  await assert.rejects(runActivate({ _: ["activate", NAME], org: "acme-co" }, { env, isTTY: true, ask: async () => NAME, out: () => {} }), /already active/);
});

test("activate works for a person: terminal + typed name + inactive + tagged", async () => {
  resetDb();
  const env = freshEnv();
  const wf = await pushedWorkflow(env);
  let prompted = false;
  const r = await runActivate({ _: ["activate", NAME], org: "acme-co" }, { env, isTTY: true, ask: async (q) => ((prompted = q.includes("Type the workflow name")), NAME), out: () => {} });
  assert.equal(r.activated, true);
  assert.ok(prompted);
  assert.equal(wf.active, true);
  const manifest = readFileSync(join(env.N8N_PUSH_STATE_DIR, "acme-co", "support-bot-book-appointment.manifest.yaml"), "utf8");
  assert.ok(manifest.includes('status: "active"'));
  assert.match(readFileSync(join(env.N8N_PUSH_STATE_DIR, "history.log"), "utf8"), /activate\t.*activated id=100/);
});

test("the real CLI refuses to activate when stdin is a pipe", () => {
  const env = { ...process.env, ...freshEnv(), N8N_BASE_URL: "http://127.0.0.1:9" };
  delete env.CLAUDECODE;
  const r = spawnSync(process.execPath, [join(HERE, "n8n-push.mjs"), "activate", NAME, "--org", "acme-co"], { env, input: NAME + "\n", encoding: "utf8" });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /not a TTY/);
  assert.ok(!r.stdout.includes("Activated"));
});

test("the real CLI reads the workflow from stdin, and its dry run changes nothing", () => {
  resetDb();
  const env = { ...process.env, ...freshEnv() };
  const flags = Object.entries(FLAGS).flatMap(([k, v]) => [`--${k}`, v]);
  // spawnSync would block the event loop that serves the mock, so run async via a child and wait.
  return new Promise((done, fail) => {
    import("node:child_process").then(({ spawn }) => {
      const p = spawn(process.execPath, [join(HERE, "n8n-push.mjs"), "push", "-", ...flags], { env });
      let stdout = "";
      p.stdout.on("data", (d) => (stdout += d));
      p.on("close", (code) => {
        try {
          assert.equal(code, 0, stdout);
          assert.match(stdout, /PLAN \(dry run/);
          assert.deepEqual(writes(), []);
          done();
        } catch (e) {
          fail(e);
        }
      });
      p.stdin.end(JSON.stringify(goodWorkflow()));
    });
  });
});

test("audit lines are single-line and tab separated", () => {
  const dir = mkdtempSync(join(tmpdir(), "n8n-audit-"));
  audit(dir, "push", "Name\nwith newline", "ok", "2026-01-01T00:00:00Z");
  const text = readFileSync(join(dir, "history.log"), "utf8");
  assert.equal(text.split("\n").length, 2);
  assert.equal(text.split("\t").length, 4);
});

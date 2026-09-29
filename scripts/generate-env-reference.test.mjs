/**
 * Unit tests for generate-env-reference.mjs's parser. Run: node --test scripts/
 *
 * The important assertion is the negative one: values must never reach the output. The
 * fixture below is shaped like a real `.env`, credentials and all, and the last test checks
 * that not one of them survives. If that goes red, the staff docs page is publishing
 * secrets — treat it as an incident, not a failing test.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { parseEnvExample } from "./generate-env-reference.mjs";

const FIXTURE = `# Vicero environment. Copy to .env and fill in.
# A file-level note that belongs to no variable.

# ── Core ─────────────────────────────────────────────────────────────────────
ENV=dev                                   # dev | test | prod
LOG_LEVEL=info
# [HUMAN] JWT signing + key encryption
SECRET_KEY=change-me-generate-a-long-random-string

# ── LLM providers (free-first) ───────────────────────────────────────────────
# [HUMAN] default provider
GROQ_API_KEY=gsk_liveSecretValue000000000
OLLAMA_BASE_URL=http://localhost:11434    # local models
`;

const parsed = parseEnvExample(FIXTURE);
const byName = Object.fromEntries(parsed.sections.flatMap((s) => s.vars).map((v) => [v.name, v]));

test("groups variables under their section header", () => {
  assert.deepEqual(
    parsed.sections.map((s) => s.name),
    ["Core", "LLM providers (free-first)"],
  );
  assert.deepEqual(
    parsed.sections[0].vars.map((v) => v.name),
    ["ENV", "LOG_LEVEL", "SECRET_KEY"],
  );
});

test("finds every variable", () => {
  assert.deepEqual(Object.keys(byName), [
    "ENV",
    "LOG_LEVEL",
    "SECRET_KEY",
    "GROQ_API_KEY",
    "OLLAMA_BASE_URL",
  ]);
});

test("reads a description from the comment above and the comment beside", () => {
  assert.equal(byName.ENV.description, "dev | test | prod");
  assert.equal(byName.SECRET_KEY.description, "JWT signing + key encryption");
  assert.equal(byName.OLLAMA_BASE_URL.description, "local models");
  assert.equal(byName.LOG_LEVEL.description, "");
});

test("marks the variables only a human can supply, without keeping the marker", () => {
  const human = Object.values(byName)
    .filter((v) => v.needsHuman)
    .map((v) => v.name);
  assert.deepEqual(human, ["SECRET_KEY", "GROQ_API_KEY"]);
  assert.ok(!byName.SECRET_KEY.description.includes("[HUMAN]"));
});

test("finds the human marker on a later line of a multi-line comment", () => {
  const parsed = parseEnvExample(
    [
      "# 127.0.0.1, not localhost: a note that comes first.",
      "# [HUMAN] Password for the dev Redis.",
      "REDIS_PASSWORD=",
      "",
    ].join("\n"),
  );
  const v = parsed.sections[0].vars[0];
  assert.equal(v.needsHuman, true);
  assert.ok(!v.description.includes("[HUMAN]"));
  assert.ok(v.description.includes("Password for the dev Redis"));
});

test("does not attach a file-level comment block to the first variable", () => {
  assert.ok(!byName.ENV.description.includes("Copy to .env"));
});

test("never emits a value", () => {
  const serialized = JSON.stringify(parsed);
  for (const value of [
    "gsk_liveSecretValue000000000",
    "change-me-generate-a-long-random-string",
    "http://localhost:11434",
    "info",
  ]) {
    assert.ok(!serialized.includes(value), `leaked the value of a variable: ${value}`);
  }
});

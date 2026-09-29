/**
 * Unit tests for provision-client.mjs's pure helpers. Run: node --test scripts/
 *
 * The HTTP orchestration is verified by running the script against a live stack (two
 * back-to-back runs prove idempotency); what's worth pinning here is the logic that decides
 * *names and paths*, because a mistake there is a cross-client collision rather than a crash.
 */

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  loadEnvFile,
  parseArgs,
  slugify,
  starterPersona,
  tagScopeError,
} from "./provision-client.mjs";

test("slugify makes a webhook-safe path", () => {
  assert.equal(slugify("Acme Co"), "acme-co");
  assert.equal(slugify("Globex, Inc."), "globex-inc");
  assert.equal(slugify("  Spaced   Out  "), "spaced-out");
  assert.equal(slugify("Ünïcodé Ltd"), "unicode-ltd");
  assert.equal(slugify("A/B & C"), "ab-c");
});

test("slugify never returns an empty path", () => {
  // An empty path would make the cloned webhook answer at the workflow root — every client's
  // tool would then hit whichever workflow n8n resolved first.
  assert.equal(slugify("!!!"), "client");
  assert.equal(slugify(""), "client");
});

test("distinct client names produce distinct slugs", () => {
  const names = ["Acme Co", "Acme Corp", "Globex Inc", "globex inc"];
  const slugs = names.map(slugify);
  // "Globex Inc" and "globex inc" *do* collide, and that's correct — they're the same client
  // by name, so the second run reuses the first's workflow rather than forking a duplicate.
  assert.equal(slugs[0] === slugs[1], false);
  assert.equal(slugs[2], slugs[3]);
});

test("parseArgs reads flags with values and bare flags", () => {
  const args = parseArgs(["--name", "Acme Co", "--email", "a@b.com", "--skip-n8n"]);
  assert.equal(args.name, "Acme Co");
  assert.equal(args.email, "a@b.com");
  assert.equal(args["skip-n8n"], "true");
});

test("parseArgs does not swallow the next flag as a value", () => {
  const args = parseArgs(["--name", "--email", "a@b.com"]);
  assert.equal(args.name, "true");
  assert.equal(args.email, "a@b.com");
});

test("loadEnvFile strips this repo's aligned inline comments", () => {
  const dir = mkdtempSync(join(tmpdir(), "bf-env-"));
  const path = join(dir, ".env");
  writeFileSync(
    path,
    [
      "# a comment line",
      "",
      "EMAIL_BACKEND=console                     # console | smtp",
      "N8N_BASE_URL=http://localhost:5679",
      'SMTP_FROM="Vicero <noreply@x.com>"',
      "PROVISION_STAFF_PASSWORD=pa#ssword",
      "BLANK=",
    ].join("\n")
  );
  const env = loadEnvFile(path);
  assert.equal(env.EMAIL_BACKEND, "console");
  assert.equal(env.N8N_BASE_URL, "http://localhost:5679");
  assert.equal(env.SMTP_FROM, "Vicero <noreply@x.com>");
  // '#' with no leading whitespace is part of the value — a password may legitimately contain it.
  assert.equal(env.PROVISION_STAFF_PASSWORD, "pa#ssword");
  assert.equal(env.BLANK, "");
});

test("loadEnvFile returns empty rather than throwing when there is no .env", () => {
  assert.deepEqual(loadEnvFile(join(tmpdir(), "definitely-not-here", ".env")), {});
});

test("starterPersona names the client", () => {
  const persona = starterPersona("Acme Co");
  assert.equal(persona.displayName, "Acme Co Assistant");
  assert.match(persona.role, /Acme Co/);
  assert.deepEqual(persona.guardrails, []);
});


test("tagScopeError turns a 403 into an actionable message about API-key scopes", () => {
  // The 403 comes from n8n's tag endpoints, which are a separate permission from
  // workflows — the failure mode is otherwise very hard to diagnose.
  const err = tagScopeError(new Error('GET /api/v1/tags -> 403 Forbidden'));
  assert.match(err.message, /tag read\/create scopes/);
  assert.match(err.message, /deny-by-default/);
  assert.match(err.message, /invisible/);
});

test("tagScopeError passes non-permission failures through untouched", () => {
  // A 404 or a network blip must not be mislabelled as a scopes problem.
  const original = new Error("PUT /api/v1/workflows/x/tags -> 404 Not Found");
  assert.equal(tagScopeError(original), original);
});

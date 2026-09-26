/** What the public API reference is allowed to show.
 *
 * The reference is generated from the API's own schema, so the risk is never a typo — it is
 * a route appearing on a public page that nobody decided to publish. These tests pin the
 * decision: the public view is an allow-list, everything not on it is hidden by default, and
 * the endpoints that manage keys, credentials and sessions, and the internal receivers, are
 * never on it.
 */

import { describe, expect, it } from "vitest";

import { PUBLIC_TAG_NAMES, countOperations, operationsByTag } from "./openapi";

const publicOps = operationsByTag("public").flatMap((g) => g.operations);
const allOps = operationsByTag("internal").flatMap((g) => g.operations);

/** Path prefixes that must never be on a public page: key, credential and session
 *  management, membership, platform operation, and the inbound/callback receivers. */
const NEVER_PUBLIC_PREFIXES = [
  "/v1/apikeys",
  "/v1/credentials",
  "/v1/auth",
  "/v1/orgs",
  "/v1/audit",
  "/v1/admin",
  "/v1/mcp",
  "/v1/channels",
  "/v1/tools",
  "/healthz",
  "/readyz",
  "/version",
  "/metrics",
];

describe("public API reference", () => {
  it("lists something — an empty reference would pass every hiding test below", () => {
    expect(publicOps.length).toBeGreaterThan(20);
  });

  it.each(NEVER_PUBLIC_PREFIXES)("never lists anything under %s", (prefix) => {
    const leaked = publicOps.filter((op) => op.path.startsWith(prefix)).map((op) => `${op.method} ${op.path}`);
    expect(leaked).toEqual([]);
  });

  it("does not list the n8n callback or any channel webhook receiver", () => {
    const paths = publicOps.map((op) => op.path);
    expect(paths).not.toContain("/v1/tools/n8n/callback");
    expect(paths.filter((p) => /\/(webhook|events|interactions)$/.test(p) && p.startsWith("/v1/channels"))).toEqual([]);
  });

  it("does not list the OAuth internals", () => {
    expect(publicOps.filter((op) => op.path.includes("/oauth")).map((op) => op.path)).toEqual([]);
  });

  it("shows only tags that are on the allow-list", () => {
    for (const group of operationsByTag("public")) {
      expect(PUBLIC_TAG_NAMES.has(group.tag)).toBe(true);
    }
  });

  it("hides every tag that is not on the allow-list — default deny", () => {
    const shown = new Set(operationsByTag("public").map((g) => g.tag));
    const everyTag = new Set(allOps.map((op) => op.tag));
    for (const tag of everyTag) {
      if (!PUBLIC_TAG_NAMES.has(tag)) expect(shown.has(tag)).toBe(false);
    }
  });

  it("accounts for every operation: shown plus hidden equals the whole API", () => {
    const hidden = allOps.filter((op) => !PUBLIC_TAG_NAMES.has(op.tag));
    expect(publicOps.length + hidden.length).toBe(allOps.length);
    expect(countOperations(operationsByTag("internal"))).toBe(allOps.length);
  });
});

describe("the allow-list itself", () => {
  // The promise in openapi.ts: this fails if someone adds a tag that is not customer-facing.
  it.each(["apikeys", "credentials", "auth", "orgs", "audit", "admin", "mcp", "channels", "tools", "system"])(
    "does not include %s",
    (tag) => {
      expect(PUBLIC_TAG_NAMES.has(tag)).toBe(false);
    },
  );

  it("names only tags that exist in the API, so a rename cannot silently empty it", () => {
    const existing = new Set(allOps.map((op) => op.tag));
    for (const tag of PUBLIC_TAG_NAMES) expect(existing.has(tag)).toBe(true);
  });
});

describe("private reference", () => {
  it("still lists the routes the public one hides, so the private area stays complete", () => {
    const paths = allOps.map((op) => op.path);
    expect(paths).toContain("/v1/apikeys");
    expect(paths).toContain("/v1/credentials");
    expect(paths).toContain("/v1/tools/n8n/callback");
    expect(paths.some((p) => p.startsWith("/v1/admin"))).toBe(true);
  });
});

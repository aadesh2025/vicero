/** The password script and the login route hash in two places. If their formats drift, the
 *  admin gets a correct hash from the script that the server then rejects — and locks
 *  themselves out with nothing to say why. This round-trips one through the other. */

import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { verifyPassword } from "./password";
import { hashPassword as scriptHash } from "../../../../../scripts/vault-hash-password.mjs";

describe("scripts/vault-hash-password.mjs", () => {
  it("produces a hash the server accepts", async () => {
    const hash: string = await scriptHash("a passphrase from the script");
    await expect(verifyPassword("a passphrase from the script", hash)).resolves.toBe(true);
    await expect(verifyPassword("a different passphrase", hash)).resolves.toBe(false);
  });

  it("produces a value that survives being pasted into .env and Docker Compose", async () => {
    const hash: string = await scriptHash("anything");
    expect(hash).not.toMatch(/[$#\s"']/);
  });
});

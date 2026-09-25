import { describe, expect, it } from "vitest";
import { handoffReasonLabel, isPlanLimitReason } from "./inbox-reason";

describe("handoffReasonLabel", () => {
  it("words the plan-limit reason for the owner", () => {
    expect(isPlanLimitReason("plan_limit")).toBe(true);
    expect(handoffReasonLabel("plan_limit")).toBe("agent couldn't reply (plan limit)");
  });

  it("leaves every other reason exactly as the server wrote it", () => {
    for (const r of ["keyword", "distress:crisis", "user asked for a human"]) {
      expect(isPlanLimitReason(r)).toBe(false);
      expect(handoffReasonLabel(r)).toBe(r);
    }
  });

  it("treats a missing reason as not a plan limit", () => {
    expect(isPlanLimitReason(null)).toBe(false);
    expect(isPlanLimitReason(undefined)).toBe(false);
  });
});

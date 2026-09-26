import { describe, expect, it } from "vitest";
import { STATUS_TONE, statusLabel, statusTone, type Tone } from "./status";

describe("STATUS_TONE (docs/20 §8)", () => {
  const cases: [Tone, string[]][] = [
    ["success", ["resolved", "connected", "published", "active", "completed", "success", "verified"]],
    ["info", ["open", "in_progress", "running", "processing", "syncing", "info"]],
    ["warn", ["handoff", "pending", "draft", "trial_ending", "needs_review", "queued"]],
    ["error", ["unanswered", "failed", "error", "disconnected_error", "plan_limit_block", "expired"]],
    ["ai", ["ai_resolved", "ai_suggested", "agent"]],
    ["neutral", ["archived", "disabled", "not_connected", "unknown"]],
  ];

  for (const [tone, statuses] of cases) {
    it(`maps every ${tone} status`, () => {
      for (const s of statuses) expect(STATUS_TONE[s], s).toBe(tone);
    });
  }
});

describe("statusTone", () => {
  it("is neutral for anything unrecognised, empty or missing", () => {
    expect(statusTone("something-new")).toBe("neutral");
    expect(statusTone("")).toBe("neutral");
    expect(statusTone(null)).toBe("neutral");
    expect(statusTone(undefined)).toBe("neutral");
  });

  it("ignores case", () => {
    expect(statusTone("RESOLVED")).toBe("success");
  });
});

describe("statusLabel", () => {
  it("uses the product's own words where they differ from the raw value", () => {
    expect(statusLabel("published")).toBe("Live");
    expect(statusLabel("handoff")).toBe("Needs human");
    expect(statusLabel("plan_limit_block")).toBe("Plan limit");
  });

  it("sentence-cases everything else", () => {
    expect(statusLabel("resolved")).toBe("Resolved");
    expect(statusLabel("some_new_state")).toBe("Some new state");
  });

  it("never returns an empty label", () => {
    expect(statusLabel(null)).toBe("Unknown");
  });
});

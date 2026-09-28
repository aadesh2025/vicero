import { describe, expect, it } from "vitest";
import { computePeak } from "./today-card";

describe("computePeak", () => {
  it("floors at 10 so a quiet org's gauge isn't maxed out by 3 chats", () => {
    expect(computePeak([{ conversations: 1 }, { conversations: 3 }])).toBe(10);
  });

  it("is the busiest day once traffic exceeds the floor", () => {
    expect(computePeak([{ conversations: 4 }, { conversations: 22 }, { conversations: 15 }])).toBe(22);
  });

  it("floors at 10 with no data at all", () => {
    expect(computePeak([])).toBe(10);
    expect(computePeak(undefined)).toBe(10);
  });

  it("is exactly 10 when the busiest day is exactly 10", () => {
    expect(computePeak([{ conversations: 10 }, { conversations: 2 }])).toBe(10);
  });
});

import { describe, expect, it } from "vitest";
import {
  addDays,
  bucketKey,
  defaultRangeFor,
  deltaPct,
  granularityForPeriod,
  isoDate,
  monthStart,
  niceTicks,
  weekStart,
} from "./activity-chart-math";

describe("isoDate", () => {
  it("formats in local fields, not UTC", () => {
    expect(isoDate(new Date(2026, 2, 1))).toBe("2026-03-01");
    expect(isoDate(new Date(2026, 0, 5))).toBe("2026-01-05");
  });
});

describe("weekStart", () => {
  it("returns the Monday of the containing week, including for a Sunday", () => {
    expect(isoDate(weekStart(new Date(2026, 2, 4)))).toBe("2026-03-02"); // Wed -> Mon
    expect(isoDate(weekStart(new Date(2026, 2, 2)))).toBe("2026-03-02"); // Mon -> itself
    expect(isoDate(weekStart(new Date(2026, 2, 8)))).toBe("2026-03-02"); // Sun -> previous Mon
  });
});

describe("monthStart", () => {
  it("returns the 1st of the month", () => {
    expect(isoDate(monthStart(new Date(2026, 4, 17)))).toBe("2026-05-01");
  });
});

describe("bucketKey", () => {
  const d = new Date(2026, 2, 4); // Wed Mar 4 2026
  it("day: the date itself", () => {
    expect(bucketKey(d, "day")).toBe("2026-03-04");
  });
  it("week: the Monday of that week", () => {
    expect(bucketKey(d, "week")).toBe("2026-03-02");
  });
  it("month: the 1st of that month", () => {
    expect(bucketKey(d, "month")).toBe("2026-03-01");
  });
});

describe("granularityForPeriod", () => {
  const from = new Date(2026, 0, 1);
  it("daily/weekly/monthly force their own granularity regardless of span", () => {
    expect(granularityForPeriod("daily", from, addDays(from, 400))).toBe("day");
    expect(granularityForPeriod("weekly", from, addDays(from, 400))).toBe("week");
    expect(granularityForPeriod("monthly", from, addDays(from, 2))).toBe("month");
  });
  it("range picks day <=31 days, week <=182 days, else month (span is inclusive of both ends)", () => {
    expect(granularityForPeriod("range", from, addDays(from, 29))).toBe("day"); // 30-day span
    expect(granularityForPeriod("range", from, addDays(from, 30))).toBe("day"); // 31-day span
    expect(granularityForPeriod("range", from, addDays(from, 31))).toBe("week"); // 32-day span
    expect(granularityForPeriod("range", from, addDays(from, 181))).toBe("week"); // 182-day span
    expect(granularityForPeriod("range", from, addDays(from, 182))).toBe("month"); // 183-day span
  });
});

describe("defaultRangeFor", () => {
  const now = new Date(2026, 5, 15); // Jun 15 2026
  it("daily: 14 days inclusive, ending today", () => {
    const { from, to } = defaultRangeFor("daily", now);
    expect(isoDate(to)).toBe("2026-06-15");
    expect(isoDate(from)).toBe("2026-06-02");
    expect(Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1).toBe(14);
  });
  it("weekly: 12 Monday-start weeks inclusive of this week", () => {
    const { from, to } = defaultRangeFor("weekly", now);
    expect(isoDate(to)).toBe("2026-06-15");
    expect(isoDate(weekStart(from))).toBe(isoDate(from)); // already a Monday
    const weeks = Math.round((weekStart(to).getTime() - from.getTime()) / (7 * 86_400_000)) + 1;
    expect(weeks).toBe(12);
  });
  it("monthly: 12 months inclusive of this month", () => {
    const { from, to } = defaultRangeFor("monthly", now);
    expect(isoDate(to)).toBe("2026-06-15");
    expect(isoDate(from)).toBe("2025-07-01");
  });
});

describe("niceTicks", () => {
  it("returns exactly `count` ticks starting at 0, the top at or above max", () => {
    const ticks = niceTicks(348, 5);
    expect(ticks).toHaveLength(5);
    expect(ticks[0]).toBe(0);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(348);
  });
  it("produces evenly-spaced round numbers", () => {
    expect(niceTicks(1000, 5)).toEqual([0, 250, 500, 750, 1000]);
  });
  it("handles a zero/negative max without crashing", () => {
    expect(niceTicks(0, 5)).toEqual([0, 0, 0, 0, 0]);
    expect(niceTicks(-5, 5)).toEqual([0, 0, 0, 0, 0]);
  });
});

describe("deltaPct", () => {
  it("computes a normal percentage change", () => {
    const d = deltaPct(112, 100);
    expect(d.isNew).toBe(false);
    expect(d.pct).toBeCloseTo(12);
  });
  it("a decrease is negative", () => {
    expect(deltaPct(80, 100).pct).toBeCloseTo(-20);
  });
  it("previous = 0 with a positive current is 'New', not Infinity", () => {
    const d = deltaPct(5, 0);
    expect(d.isNew).toBe(true);
    expect(Number.isFinite(d.pct)).toBe(true);
  });
  it("previous = 0 and current = 0 is not 'New' either (nothing changed)", () => {
    expect(deltaPct(0, 0).isNew).toBe(false);
  });
});

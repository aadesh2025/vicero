import { describe, expect, it } from "vitest";
import { describeProgress, formatBytes, runLimited } from "./ingest-progress";

describe("describeProgress", () => {
  it("counts chunks while embedding", () => {
    expect(describeProgress({ stage: "embedding", done: 128, total: 512 })).toEqual({
      label: "Embedding · 128/512 chunks",
      percent: 25,
    });
  });

  it("counts pages while crawling a website", () => {
    expect(describeProgress({ stage: "crawling", done: 3, total: 25 }).label).toBe(
      "Reading website · 3/25 pages",
    );
  });

  it("says a queued document is waiting rather than looking stuck", () => {
    expect(describeProgress(null)).toEqual({ label: "Waiting for a worker", percent: null });
  });

  it("never reports more than 100%", () => {
    expect(describeProgress({ stage: "embedding", done: 9, total: 4 }).percent).toBe(100);
  });
});

describe("formatBytes", () => {
  it("picks a readable unit", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(48_000)).toBe("47 KB");
    expect(formatBytes(1_875_883)).toBe("1.8 MB");
  });
});

describe("runLimited", () => {
  it("never runs more than the limit at once and finishes every item", async () => {
    let active = 0;
    let peak = 0;
    const done: number[] = [];
    await runLimited([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      active++;
      peak = Math.max(peak, active);
      try {
        await new Promise((r) => setTimeout(r, 5));
        if (n === 4) throw new Error("one upload failing must not stop the rest");
        done.push(n);
      } finally {
        active--;
      }
    });
    expect(peak).toBeLessThanOrEqual(3);
    expect(done.sort()).toEqual([1, 2, 3, 5, 6, 7]);
  });
});

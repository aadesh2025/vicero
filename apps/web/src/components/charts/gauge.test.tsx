import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Gauge } from "./gauge";

/** The fill path is the second <path> (index 1) — the first is the track. */
function fillPath(container: HTMLElement) {
  return container.querySelectorAll("path")[1] ?? null;
}

describe("Gauge", () => {
  it("draws only the track at zero", () => {
    const { container } = render(<Gauge value={0} max={20} ariaLabel="x" />);
    expect(container.querySelectorAll("path")).toHaveLength(1);
  });

  it("draws a partial fill for a value between 0 and max", () => {
    const { container } = render(<Gauge value={10} max={20} ariaLabel="x" />);
    const fill = fillPath(container);
    expect(fill).not.toBeNull();
    // Half the semicircle: the arc's endpoint sits at the bottom of the circle (270deg =
    // 12 o'clock in this component's clockwise-from-3-o'clock convention -> due "north" of
    // centre), i.e. roughly (cx, cy - r), not off to one side.
    const d = fill!.getAttribute("d")!;
    const endMatch = d.match(/A [\d.]+ [\d.]+ 0 0 1 ([\d.]+) ([\d.]+)$/);
    expect(endMatch).not.toBeNull();
    const [, ex, ey] = endMatch!.map(Number) as unknown as [number, number, number];
    expect(ex).toBeCloseTo(110, 0); // centre x — the midpoint of a 180deg sweep is straight up
    expect(ey).toBeLessThan(108); // above the centre line
  });

  it("caps at a full semicircle when value exceeds max", () => {
    const { container } = render(<Gauge value={999} max={20} ariaLabel="x" />);
    const over = fillPath(container)!.getAttribute("d");
    const { container: exact } = render(<Gauge value={20} max={20} ariaLabel="x" />);
    const atMax = fillPath(exact)!.getAttribute("d");
    expect(over).toBe(atMax);
  });

  it("never divides by zero when max is 0", () => {
    const { container } = render(<Gauge value={5} max={0} ariaLabel="x" />);
    expect(container.querySelectorAll("path")).toHaveLength(1); // no fill, no NaN in the path
  });

  it("treats a negative value the same as zero", () => {
    const { container } = render(<Gauge value={-3} max={20} ariaLabel="x" />);
    expect(container.querySelectorAll("path")).toHaveLength(1);
  });
});

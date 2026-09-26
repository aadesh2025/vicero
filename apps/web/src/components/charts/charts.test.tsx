import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Donut } from "./donut";
import { LineAreaChart } from "./line-area-chart";
import { smoothPath } from "./smooth-path";
import { Sparkline } from "./sparkline";

describe("smoothPath", () => {
  it("never leaves the range of the data (no negative counts after a spike)", () => {
    // y grows downward: the flat run is at y=100 (zero) and the spike at y=10.
    const pts = [0, 1, 2, 3, 4, 5].map((i) => ({ x: i * 10, y: i === 3 ? 10 : 100 }));
    const nums = smoothPath(pts).match(/-?\d+(\.\d+)?/g)!.map(Number);
    // Every y control point stays inside [10, 100]; x/y alternate after the leading M.
    const ys = nums.filter((_, i) => i % 2 === 1);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(10);
    expect(Math.max(...ys)).toBeLessThanOrEqual(100);
  });

  it("handles zero, one and two points", () => {
    expect(smoothPath([])).toBe("");
    expect(smoothPath([{ x: 1, y: 2 }])).toBe("M1,2");
    expect(smoothPath([{ x: 0, y: 0 }, { x: 5, y: 5 }])).toBe("M0,0 L5,5");
  });
});

describe("<Sparkline>", () => {
  it("draws a line and an area, hidden from assistive tech", () => {
    const { container } = render(<Sparkline values={[1, 3, 2, 5]} color="success" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg.querySelectorAll("path")).toHaveLength(2);
    expect(svg.querySelector('path[fill="none"]')).toHaveAttribute("stroke", "rgb(var(--success))");
  });

  it("renders an empty placeholder (no SVG) with fewer than two points", () => {
    const { container } = render(<Sparkline values={[4]} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("<LineAreaChart>", () => {
  const labels = ["Sep 1", "Sep 2", "Sep 3", "Sep 4"];

  it("draws one line per series in that series' colour", () => {
    render(
      <LineAreaChart
        ariaLabel="Conversations per day"
        xLabels={labels}
        series={[
          { key: "a", label: "Conversations", color: "accent", values: [1, 4, 2, 6] },
          { key: "b", label: "Resolved by AI", color: "success", values: [0, 2, 1, 3] },
        ]}
      />,
    );
    const chart = screen.getByRole("img", { name: "Conversations per day" });
    const strokes = [...chart.querySelectorAll('path[fill="none"]')].map((p) => p.getAttribute("stroke"));
    expect(strokes).toEqual(["rgb(var(--accent))", "rgb(var(--success))"]);
    expect(chart.querySelector('path[fill="none"]')).toHaveAttribute("stroke-width", "2.5");
  });

  it("shows the latest point in the tooltip until hovered, with every series", () => {
    render(
      <LineAreaChart
        ariaLabel="x"
        xLabels={labels}
        series={[
          { key: "a", label: "Conversations", color: "accent", values: [1, 4, 2, 6] },
          { key: "b", label: "Resolved by AI", color: "success", values: [0, 2, 1, 3] },
        ]}
      />,
    );
    const tip = screen.getByRole("status");
    expect(tip).toHaveTextContent("Sep 4");
    expect(tip).toHaveTextContent("Conversations");
    expect(tip).toHaveTextContent("Resolved by AI");
  });

  it("shows a 'no data' state with a dotted grid instead of a blank box", () => {
    const { container } = render(<LineAreaChart ariaLabel="x" xLabels={[]} series={[]} emptyText="No usage yet." />);
    expect(screen.getByText("No usage yet.")).toBeInTheDocument();
    expect(container.querySelectorAll("line[stroke-dasharray]").length).toBeGreaterThan(0);
  });

  it("draws an all-zero series flat instead of failing", () => {
    render(<LineAreaChart ariaLabel="zeros" xLabels={labels} series={[{ key: "a", label: "A", color: "accent", values: [0, 0, 0, 0] }]} />);
    expect(screen.getByRole("img", { name: "zeros" })).toBeInTheDocument();
  });
});

describe("<Donut>", () => {
  it("draws one arc per non-empty segment and one labelled image", () => {
    const { container } = render(
      <Donut
        ariaLabel="Conversations by channel"
        segments={[
          { key: "w", label: "Web", value: 5, color: "red" },
          { key: "x", label: "Empty", value: 0, color: "blue" },
          { key: "t", label: "Telegram", value: 3, color: "green" },
        ]}
      >
        <span>8</span>
      </Donut>,
    );
    expect(screen.getByRole("img", { name: "Conversations by channel" })).toBeInTheDocument();
    // track + 2 non-empty arcs
    expect(container.querySelectorAll("circle")).toHaveLength(3);
    expect(screen.getByText("8")).toBeInTheDocument();
  });

  it("draws only the track when everything is zero", () => {
    const { container } = render(<Donut ariaLabel="none" segments={[{ key: "a", label: "A", value: 0, color: "red" }]} />);
    expect(container.querySelectorAll("circle")).toHaveLength(1);
  });
});

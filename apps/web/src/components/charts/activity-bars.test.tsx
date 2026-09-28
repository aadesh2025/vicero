import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ActivityBars } from "./activity-bars";

const bars = [
  { key: "2026-03-01", label: "Mar 1", value: 0, ariaLabel: "Mar 1: 0 conversations" },
  { key: "2026-03-02", label: "Mar 2", value: 40, ariaLabel: "Mar 2: 40 conversations" },
  { key: "2026-03-03", label: "Mar 3", value: 100, ariaLabel: "Mar 3: 100 conversations" },
];

describe("<ActivityBars>", () => {
  it("renders one labelled bar per point", () => {
    render(<ActivityBars bars={bars} selectedIndex={2} onSelect={() => {}} format={String} selectedDelta={null} ariaLabel="Activity" />);
    expect(screen.getByLabelText("Mar 1: 0 conversations")).toBeInTheDocument();
    expect(screen.getByLabelText("Mar 2: 40 conversations")).toBeInTheDocument();
    expect(screen.getByLabelText("Mar 3: 100 conversations")).toBeInTheDocument();
  });

  it("gives a zero-value bar a visible 6px stub instead of collapsing to nothing", async () => {
    const { container } = render(
      <ActivityBars bars={bars} selectedIndex={2} onSelect={() => {}} format={String} selectedDelta={null} ariaLabel="Activity" />,
    );
    const rects = () => [...container.querySelectorAll("g[role='img'] > rect:first-child")];
    await waitFor(() => {
      expect(Number(rects()[2].getAttribute("height"))).toBeGreaterThan(6); // grown past the stub
    });
    const heights = rects().map((r) => Number(r.getAttribute("height")));
    expect(heights[0]).toBe(6); // the zero-value bar
    expect(heights[2]).toBeGreaterThan(heights[1]); // 100 taller than 40
  });

  it("fills only the selected bar with the gradient; others use the stripe pattern", () => {
    const { container } = render(
      <ActivityBars bars={bars} selectedIndex={1} onSelect={() => {}} format={String} selectedDelta={null} ariaLabel="Activity" />,
    );
    const rects = [...container.querySelectorAll("g[role='img'] > rect:first-child")];
    expect(rects[1].getAttribute("fill")).toMatch(/-selected\)$/);
    expect(rects[0].getAttribute("fill")).toMatch(/-stripe\)$/);
    expect(rects[2].getAttribute("fill")).toMatch(/-stripe\)$/);
  });

  it("clicking a bar selects it", () => {
    const onSelect = vi.fn();
    render(<ActivityBars bars={bars} selectedIndex={0} onSelect={onSelect} format={String} selectedDelta={null} ariaLabel="Activity" />);
    fireEvent.click(screen.getByLabelText("Mar 3: 100 conversations"));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("arrow keys move the selection, clamped to the ends", () => {
    const onSelect = vi.fn();
    render(<ActivityBars bars={bars} selectedIndex={1} onSelect={onSelect} format={String} selectedDelta={null} ariaLabel="Activity" />);
    const group = screen.getByRole("group", { name: "Activity" });
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith(2);
    fireEvent.keyDown(group, { key: "ArrowLeft" });
    expect(onSelect).toHaveBeenLastCalledWith(0);
  });

  it("does not call onSelect past the first or last bar", () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <ActivityBars bars={bars} selectedIndex={0} onSelect={onSelect} format={String} selectedDelta={null} ariaLabel="Activity" />,
    );
    const group = screen.getByRole("group", { name: "Activity" });
    fireEvent.keyDown(group, { key: "ArrowLeft" });
    expect(onSelect).toHaveBeenLastCalledWith(0);
    rerender(<ActivityBars bars={bars} selectedIndex={2} onSelect={onSelect} format={String} selectedDelta={null} ariaLabel="Activity" />);
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith(2);
  });

  it("shows the selected bar's value and a delta pill", () => {
    render(
      <ActivityBars
        bars={bars}
        selectedIndex={2}
        onSelect={() => {}}
        format={(n) => String(n)}
        selectedDelta={{ label: "▲ +12%", tone: "up" }}
        ariaLabel="Activity"
      />,
    );
    const bar = screen.getByLabelText("Mar 3: 100 conversations");
    expect(within(bar).getByText("100")).toBeInTheDocument();
    expect(screen.getByText("▲ +12%")).toBeInTheDocument();
  });

  it("renders an empty state instead of an empty chart with zero bars", () => {
    render(<ActivityBars bars={[]} selectedIndex={0} onSelect={() => {}} format={String} selectedDelta={null} ariaLabel="Activity" />);
    expect(screen.getByText(/No activity/)).toBeInTheDocument();
  });
});

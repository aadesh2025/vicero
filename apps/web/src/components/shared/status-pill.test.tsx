import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { STATUS_TONE, statusLabel } from "@/lib/status";
import { StatusPill } from "./status-pill";

describe("<StatusPill>", () => {
  it("renders every mapped status with its label and its tone's colours", () => {
    for (const [status, tone] of Object.entries(STATUS_TONE)) {
      const { unmount } = render(<StatusPill status={status} />);
      const pill = screen.getByText(statusLabel(status));
      // Tone classes come from <Badge>; neutral uses surface-3/muted.
      const expected = tone === "neutral" ? "text-muted" : `text-${tone}-text`;
      expect(pill.className, status).toContain(expected);
      unmount();
    }
  });

  it("is neutral (and still labelled) for an unknown status", () => {
    render(<StatusPill status="brand_new_state" />);
    const pill = screen.getByText("Brand new state");
    expect(pill.className).toContain("text-muted");
  });

  it("lets a screen override the tone and the label", () => {
    render(
      <StatusPill status="queued" tone="info">
        Waiting
      </StatusPill>,
    );
    const pill = screen.getByText("Waiting");
    expect(pill.className).toContain("text-info-text");
  });
});

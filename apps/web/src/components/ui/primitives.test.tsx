import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Badge } from "./badge";
import { Button } from "./button";
import { Input } from "./input";

describe("<Button>", () => {
  it("primary is the filled accent button (accent-strong, not accent: AA in dark)", () => {
    render(<Button variant="primary">Save</Button>);
    const cls = screen.getByRole("button", { name: "Save" }).className;
    expect(cls).toContain("bg-accent-strong");
    expect(cls).toContain("text-on-accent");
  });

  it("has secondary, ghost, destructive and ai variants", () => {
    render(
      <>
        <Button variant="secondary">a</Button>
        <Button variant="ghost">b</Button>
        <Button variant="destructive">c</Button>
        <Button variant="ai">d</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "a" }).className).toContain("bg-surface");
    expect(screen.getByRole("button", { name: "b" }).className).toContain("hover:bg-surface-2");
    expect(screen.getByRole("button", { name: "c" }).className).toContain("bg-error");
    expect(screen.getByRole("button", { name: "d" }).className).toContain("bg-ai");
  });

  it("loading disables the button, marks it busy and blocks clicks", () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Publish
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "Publish" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("asChild renders the child element, not a nested button", () => {
    render(
      <Button asChild variant="primary">
        <a href="/x">Go</a>
      </Button>,
    );
    expect(screen.getByRole("link", { name: "Go" })).toHaveAttribute("href", "/x");
  });
});

describe("<Badge>", () => {
  it("soft variants pair the -soft background with the AA -text colour", () => {
    for (const tone of ["ai", "success", "warn", "error", "info"] as const) {
      const { unmount } = render(<Badge variant={tone}>{tone}</Badge>);
      const cls = screen.getByText(tone).className;
      expect(cls).toContain(`bg-${tone}-soft`);
      expect(cls).toContain(`text-${tone}-text`);
      unmount();
    }
  });
});

describe("<Input>", () => {
  it("styles aria-invalid as an error state", () => {
    render(<Input aria-label="Email" aria-invalid="true" />);
    expect(screen.getByLabelText("Email").className).toContain("aria-[invalid=true]:border-error");
  });
});

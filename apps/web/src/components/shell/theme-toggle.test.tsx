import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeToggle, nextTheme } from "./theme-toggle";

const setTheme = vi.fn();
let current: string | undefined = "light";
vi.mock("next-themes", () => ({ useTheme: () => ({ theme: current, setTheme }) }));

beforeEach(() => {
  setTheme.mockReset();
  current = "light";
});

describe("nextTheme", () => {
  it("cycles Light -> Dark -> System -> Light", () => {
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
    expect(nextTheme("system")).toBe("light");
  });

  it("starts over from anything unrecognised", () => {
    expect(nextTheme(undefined)).toBe("light");
    expect(nextTheme("sepia")).toBe("light");
  });
});

describe("<ThemeToggle>", () => {
  it("names the current choice and offers the next", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Theme: Light. Switch to Dark" })).toBeInTheDocument();
    expect(screen.getByText("Light")).toBeInTheDocument();
  });

  it("clicking moves to the next choice", () => {
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button"));
    expect(setTheme).toHaveBeenCalledWith("dark");
  });

  it("walks the whole cycle across clicks", () => {
    const { rerender } = render(<ThemeToggle />);
    for (const [now, expected] of [["light", "dark"], ["dark", "system"], ["system", "light"]] as const) {
      current = now;
      rerender(<ThemeToggle />);
      fireEvent.click(screen.getByRole("button"));
      expect(setTheme).toHaveBeenLastCalledWith(expected);
    }
  });

  it("shows System when that is the stored preference", () => {
    current = "system";
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Theme: System. Switch to Light" })).toBeInTheDocument();
  });
});

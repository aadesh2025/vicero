import { describe, expect, it } from "vitest";
import type { WidgetConfig } from "@/lib/mock/builder";
import { applyLayoutPreset, applyModeChange, BLOCK_TOGGLES, enableBlocks, layoutDef, WIDGET_LAYOUTS } from "./widget-layouts";

function widget(over: Partial<WidgetConfig> = {}): WidgetConfig {
  return {
    primaryColor: "#1F2937",
    position: "bottom-right",
    launcherText: "Chat with us",
    branding: true,
    mode: "light",
    widgetStyle: "solid",
    backgroundColor: null,
    textColor: null,
    bubbleColor: "#123456",
    typingAreaColor: "#abcdef",
    fontFamily: "system",
    logoUrl: null,
    floatingButtonStyle: null,
    floatingButtonColor: "#654321",
    inputBarButtons: ["attachment"],
    widgetLayout: null,
    cornerRadius: null,
    density: null,
    interactiveBlocks: null,
    allowedSites: [],
    ...over,
  };
}

describe("widget layouts", () => {
  it("offers exactly the ten layouts the API accepts", () => {
    expect(WIDGET_LAYOUTS.map((l) => l.id).sort()).toEqual(
      ["block", "classic", "command", "concierge", "console", "drawer", "glass", "hairline", "paper", "pebble"].sort(),
    );
  });

  it("only uses fonts the widget ships (no remote web fonts)", () => {
    const shipped = ["system", "inter", "arial", "georgia", "courier"];
    for (const l of WIDGET_LAYOUTS) expect(shipped).toContain(l.preset.font);
  });

  it("keeps radii inside the API's 0-28 range", () => {
    for (const l of WIDGET_LAYOUTS) {
      expect(l.preset.radius).toBeGreaterThanOrEqual(0);
      expect(l.preset.radius).toBeLessThanOrEqual(28);
    }
  });

  it("picking a layout saves its preset as ordinary config values", () => {
    const w = widget();
    applyLayoutPreset(w, "console");
    expect(w).toMatchObject({
      widgetLayout: "console",
      mode: "dark",
      primaryColor: "#3ddc84",
      fontFamily: "courier",
      cornerRadius: 6,
      floatingButtonStyle: "rounded-square",
      backgroundColor: "#0b0f0d",
      textColor: "#cfe3d5",
    });
    // Per-surface overrides are cleared so the layout's look is what shows.
    expect(w.bubbleColor).toBeNull();
    expect(w.typingAreaColor).toBeNull();
    expect(w.floatingButtonColor).toBeNull();
    // Things a layout has no opinion on are left alone.
    expect(w.launcherText).toBe("Chat with us");
    expect(w.inputBarButtons).toEqual(["attachment"]);
  });

  it("a layout without its own palette lets the widget derive colours", () => {
    const w = widget({ backgroundColor: "#111111", textColor: "#eeeeee" });
    applyLayoutPreset(w, "classic");
    expect(w.backgroundColor).toBeNull();
    expect(w.textColor).toBeNull();
  });

  it("switching mode carries the layout palette along only if the user did not change it", () => {
    const w = widget();
    applyLayoutPreset(w, "paper");
    applyModeChange(w, "dark");
    expect(w.mode).toBe("dark");
    expect(w.backgroundColor).toBe("#191815");

    const custom = widget();
    applyLayoutPreset(custom, "paper");
    custom.backgroundColor = "#ff00ff";
    applyModeChange(custom, "dark");
    expect(custom.backgroundColor).toBe("#ff00ff");
  });

  it("unknown ids resolve to nothing", () => {
    expect(layoutDef(null)).toBeNull();
    expect(layoutDef("nope" as never)).toBeNull();
  });
});

describe("interactive blocks settings", () => {
  it("offers one toggle per tool-backed block type", () => {
    expect(BLOCK_TOGGLES.map((b) => b.key)).toEqual(["options", "cards", "form", "datetime", "rating"]);
  });

  it("turning the master switch on enables every type and keeps earlier choices", () => {
    expect(enableBlocks(null)).toEqual({ options: true, cards: true, form: true, datetime: true, rating: true, enabled: true });
    expect(enableBlocks({ enabled: false, cards: false }).cards).toBe(false);
    expect(enableBlocks({ enabled: false, cards: false }).enabled).toBe(true);
  });
});

import type { FloatingButtonStyle, WidgetConfig, WidgetFont } from "@/lib/mock/builder";

/** The widget's structural designs (docs/27). `null` on the config means the original look. */
export type WidgetLayout =
  | "classic"
  | "glass"
  | "hairline"
  | "paper"
  | "pebble"
  | "console"
  | "concierge"
  | "command"
  | "drawer"
  | "block";

export type WidgetDensity = "compact" | "comfortable" | "roomy";

interface Palette {
  bg: string;
  fg: string;
}

export interface LayoutDef {
  id: WidgetLayout;
  name: string;
  tag: string;
  /** Applied by the designer when the layout is picked, then saved as ordinary config values.
   *  The widget itself carries no preset logic — it only reads what is saved. */
  preset: {
    mode: "light" | "dark";
    accent: string;
    font: WidgetFont;
    radius: number;
    launcher: FloatingButtonStyle;
  };
  /** Panel colours that suit this layout, per mode. Absent = let the widget derive them. */
  pal?: { light: Palette; dark: Palette };
}

// Fonts map onto the keys the widget already ships (no remote web fonts inside customer sites).
export const WIDGET_LAYOUTS: LayoutDef[] = [
  { id: "classic", name: "Classic", tag: "Brand-color header, clean bubbles. The safe default.", preset: { mode: "light", accent: "#2563eb", font: "inter", radius: 16, launcher: "circle-chat" } },
  { id: "glass", name: "Glass", tag: "Frosted panel that lets the site show through.", preset: { mode: "light", accent: "#0f172a", font: "inter", radius: 22, launcher: "circle-message" }, pal: { light: { bg: "#ffffff", fg: "#0f1115" }, dark: { bg: "#101114", fg: "#f3f4f6" } } },
  { id: "hairline", name: "Hairline", tag: "No bubbles, thin rules, lots of air.", preset: { mode: "light", accent: "#111111", font: "inter", radius: 12, launcher: "pill-text" } },
  { id: "paper", name: "Paper", tag: "Reads like a letter. Serif type, no chrome.", preset: { mode: "light", accent: "#1e4fd8", font: "georgia", radius: 4, launcher: "rounded-square" }, pal: { light: { bg: "#f7f7f4", fg: "#1c1b18" }, dark: { bg: "#191815", fg: "#ecebe6" } } },
  { id: "pebble", name: "Pebble", tag: "Soft round bubbles with a messenger feel.", preset: { mode: "light", accent: "#0a84ff", font: "system", radius: 24, launcher: "circle-message" } },
  { id: "console", name: "Console", tag: "Monospace and keyboard first. For dev tools.", preset: { mode: "dark", accent: "#3ddc84", font: "courier", radius: 6, launcher: "rounded-square" }, pal: { light: { bg: "#f4f6f4", fg: "#14211a" }, dark: { bg: "#0b0f0d", fg: "#cfe3d5" } } },
  { id: "concierge", name: "Concierge", tag: "Big welcome screen with topic tiles.", preset: { mode: "light", accent: "#0d7a6c", font: "system", radius: 18, launcher: "pill-text" } },
  { id: "command", name: "Command", tag: "Search bar on top, like a command palette.", preset: { mode: "dark", accent: "#f59e0b", font: "inter", radius: 14, launcher: "circle-dots" }, pal: { light: { bg: "#ffffff", fg: "#111318" }, dark: { bg: "#151517", fg: "#ededef" } } },
  { id: "drawer", name: "Side drawer", tag: "Full-height panel with Chat and Help tabs.", preset: { mode: "light", accent: "#2563eb", font: "inter", radius: 10, launcher: "circle-chat" } },
  { id: "block", name: "Bold block", tag: "Thick outlines and hard shadows.", preset: { mode: "light", accent: "#ffd43b", font: "system", radius: 0, launcher: "rounded-square" }, pal: { light: { bg: "#ffffff", fg: "#0a0a0a" }, dark: { bg: "#0a0a0a", fg: "#fafafa" } } },
];

export const DENSITY_OPTIONS: { value: WidgetDensity; label: string }[] = [
  { value: "compact", label: "Compact" },
  { value: "comfortable", label: "Default" },
  { value: "roomy", label: "Roomy" },
];

export function layoutDef(id: WidgetLayout | null | undefined): LayoutDef | null {
  return WIDGET_LAYOUTS.find((l) => l.id === id) ?? null;
}

/** Colours the swatch in the picker shows for a layout in a given mode. */
export function layoutSwatch(def: LayoutDef, mode: "light" | "dark"): { bg: string; fg: string; accent: string } {
  const pal = def.pal?.[mode];
  return { bg: pal?.bg ?? (mode === "dark" ? "#16181D" : "#FFFFFF"), fg: pal?.fg ?? (mode === "dark" ? "#E7E9EE" : "#14161A"), accent: def.preset.accent };
}

/** Pick a layout: set it and apply its preset. Everything stays editable afterwards.
 *  Also the "Reset to layout defaults" action. Mutates the (immer) draft widget. */
export function applyLayoutPreset(w: WidgetConfig, id: WidgetLayout): void {
  const def = layoutDef(id);
  if (!def) return;
  const p = def.preset;
  const pal = def.pal?.[p.mode];
  w.widgetLayout = id;
  w.mode = p.mode;
  w.primaryColor = p.accent;
  w.fontFamily = p.font;
  w.cornerRadius = p.radius;
  w.floatingButtonStyle = p.launcher;
  w.backgroundColor = pal?.bg ?? null;
  w.textColor = pal?.fg ?? null;
  w.bubbleColor = null;
  w.typingAreaColor = null;
  w.floatingButtonColor = null;
}

/** Switching light/dark: carry the layout's own panel colours along unless the user changed them. */
export function applyModeChange(w: WidgetConfig, next: "light" | "dark"): void {
  const def = layoutDef(w.widgetLayout);
  const prev = w.mode;
  w.mode = next;
  const pal = def?.pal;
  if (!pal) return;
  if (w.backgroundColor === pal[prev].bg && w.textColor === pal[prev].fg) {
    w.backgroundColor = pal[next].bg;
    w.textColor = pal[next].fg;
  }
}

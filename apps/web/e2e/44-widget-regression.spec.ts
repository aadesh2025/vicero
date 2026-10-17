import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

// docs/27 §2: an agent that never touches the new settings (widget_layout / corner_radius /
// density / interactive_blocks all null) must render pixel-for-pixel like it did before the
// layouts existed. This spec dumps the computed styles + class names of every legacy surface and
// compares them to a snapshot that was captured from the PRE-change bundle
// (e2e/fixtures/widget-legacy-styles.json). It needs no API — it drives widget-preview.html.
//
// Re-capture only when the legacy look is changed ON PURPOSE:
//   UPDATE_WIDGET_SNAPSHOT=1 npx playwright test e2e/44-widget-regression.spec.ts

const SNAPSHOT = resolve(__dirname, "fixtures/widget-legacy-styles.json");

const PROPS = [
  "display", "position", "top", "right", "bottom", "left", "z-index", "width", "height", "min-width",
  "max-width", "max-height", "padding", "margin", "gap", "flex", "flex-direction", "align-items",
  "justify-content", "overflow-x", "overflow-y", "box-sizing", "border-top", "border-right",
  "border-bottom", "border-left", "border-radius", "color", "background-color", "background-image",
  "box-shadow", "font-family", "font-size", "font-weight", "font-style", "line-height",
  "letter-spacing", "text-transform", "text-align", "opacity", "transform", "animation-name",
  "backdrop-filter", "order", "cursor", "white-space",
];

const THEMES: Record<string, Record<string, unknown>> = {
  "default-light": { mode: "light" },
  "dark-accent": { mode: "dark", primary_color: "#22CC88", floating_button_style: "rounded-square" },
  "custom-colors": {
    mode: "light",
    primary_color: "#E8590C",
    background_color: "#FFF8F0",
    text_color: "#222222",
    bubble_color: "#0B7285",
    typing_area_color: "#FFFFFF",
    font_family: "georgia",
    floating_button_style: "pulse-ring",
    input_bar_buttons: ["attachment", "emoji"],
  },
  transparent: { mode: "light", widget_style: "transparent" },
  // The new keys present but null, exactly as an untouched agent's public config now returns.
  "explicit-nulls": {
    mode: "light",
    widget_layout: null,
    corner_radius: null,
    density: null,
    interactive_blocks: null,
  },
};

const SELECTORS: Record<string, string> = {
  host: "#vicero-widget",
  launcher: ".bf-launcher",
  panel: ".bf-panel",
  head: ".bf-head",
  title: ".bf-title",
  close: ".bf-x",
  msgs: ".bf-msgs",
  botBubble: ".bf-bot .bf-bubble",
  userBubble: ".bf-user .bf-bubble",
  chips: ".bf-chips",
  chip: ".bf-chip",
  foot: ".bf-foot",
  inrow: ".bf-inrow",
  textarea: ".bf-ta",
  send: ".bf-send",
  brand: ".bf-brand",
};

async function capture(page: Page, viewport: { width: number; height: number }) {
  await page.setViewportSize(viewport);
  await page.goto("/widget-preview.html");
  await expect(page.locator(".bf-launcher")).toBeVisible({ timeout: 10_000 });
  const out: Record<string, unknown> = {};
  for (const [name, theme] of Object.entries(THEMES)) {
    await page.evaluate((t) => {
      window.postMessage(
        {
          type: "bf-preview-config",
          config: {
            name: "Snapshot Bot",
            welcome_message: "Hello there",
            suggested_prompts: ["What are your hours?", "Track my order"],
            theme: t,
          },
        },
        "*",
      );
    }, theme);
    // Closed state, then open state with one preview exchange so both bubble kinds exist.
    const closed = await dump(page);
    const launcher = page.locator(".bf-launcher");
    if (!(await page.locator(".bf-panel.bf-show").count())) await launcher.click();
    if (!(await page.locator(".bf-user").count())) {
      await page.locator(".bf-ta").fill("hi");
      await page.locator(".bf-send").click();
      await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText("preview");
    }
    const open = await dump(page);
    out[name] = { closed, open };
    await page.locator(".bf-x").click(); // close again for the next theme
    await expect(page.locator(".bf-panel")).not.toHaveClass(/bf-show/);
  }
  return out;
}

async function dump(page: Page) {
  // Park the mouse and let the launcher's hover transition (translateY) settle, or the dump
  // captures a mid-transition frame and the comparison flaps.
  await page.mouse.move(2, 2);
  // Focus is a race (open() focuses the composer after 50ms; a click moves it to the send
  // button), and the textarea's focus border is part of the computed style. Pin it: blur.
  await page.evaluate(() => {
    const root = (document.querySelector("#vicero-widget") as HTMLElement).shadowRoot!;
    (root.activeElement as HTMLElement | null)?.blur();
  });
  await page.waitForTimeout(350);
  return page.evaluate(
    ([selectors, props]) => {
      const root = (document.querySelector("#vicero-widget") as HTMLElement).shadowRoot!;
      const result: Record<string, unknown> = {};
      for (const [name, sel] of Object.entries(selectors)) {
        const el =
          name === "host"
            ? (document.querySelector(sel) as HTMLElement | null)
            : (root.querySelector(sel) as HTMLElement | null);
        if (!el) {
          result[name] = null;
          continue;
        }
        const cs = getComputedStyle(el);
        const style: Record<string, string> = {};
        for (const p of props) style[p] = cs.getPropertyValue(p);
        // CSS custom properties set on the host are part of the contract too.
        const vars: Record<string, string> = {};
        if (name === "host") {
          for (let i = 0; i < el.style.length; i++) {
            const k = el.style[i];
            vars[k] = el.style.getPropertyValue(k);
          }
        }
        result[name] = {
          class: el.className,
          hostClass: name === "host" ? "" : undefined,
          children: el.children.length,
          attrs: Array.from(el.attributes)
            .filter((a) => a.name !== "style")
            .map((a) => `${a.name}=${a.value}`)
            .sort(),
          style,
          vars,
        };
      }
      // One stylesheet, untouched: any extra <style> in the shadow root is a legacy change.
      result["styleTags"] = root.querySelectorAll("style").length;
      result["hostClasses"] = (document.querySelector("#vicero-widget") as HTMLElement).className;
      return result;
    },
    [SELECTORS, PROPS] as const,
  );
}

test("untouched agent renders exactly like the pre-layouts widget (desktop + phone)", async ({ page }) => {
  const actual = {
    desktop: await capture(page, { width: 1100, height: 800 }),
    phone: await capture(page, { width: 390, height: 720 }),
  };
  if (process.env.UPDATE_WIDGET_SNAPSHOT === "1" || !existsSync(SNAPSHOT)) {
    mkdirSync(dirname(SNAPSHOT), { recursive: true });
    writeFileSync(SNAPSHOT, JSON.stringify(actual, null, 1));
    test.info().annotations.push({ type: "snapshot", description: "written" });
    return;
  }
  const expected = JSON.parse(readFileSync(SNAPSHOT, "utf8"));
  expect(JSON.parse(JSON.stringify(actual))).toEqual(expected);
});

import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// docs/27 §2: an agent that never touches the new settings (widget_layout / corner_radius /
// density / interactive_blocks all null) must render pixel-for-pixel like it did before the
// layouts existed. This spec loads the widget bundle as it was before docs/27
// (e2e/fixtures/widget-before-docs27.js, the bundle of the commit before 715203c) and the CURRENT
// bundle into the same browser, drives both through the same steps, and compares the computed styles,
// classes, attributes and DOM shape of every legacy surface. Nothing is stored from another machine
// (a stored baseline differs by font metrics between Windows and the Linux CI runner), and it needs no
// API: it drives widget-preview.html.
//
// If the original look is changed on purpose, replace the fixture with the bundle that should now count
// as "original".

const OLD_BUNDLE = readFileSync(resolve(__dirname, "fixtures/widget-before-docs27.js"), "utf8");

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

async function capture(page: Page, viewport: { width: number; height: number }, bundle: "old" | "new") {
  await page.unroute("**/widget.js").catch(() => undefined);
  if (bundle === "old") {
    await page.route("**/widget.js", (route) =>
      route.fulfill({ contentType: "text/javascript", body: OLD_BUNDLE }),
    );
  }
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
  // Focus is a race: open() focuses the composer after 50ms, and the textarea's focus border is part of
  // the computed style. Let that timer fire first, then pin the state by blurring.
  await page.waitForTimeout(150);
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
  // Guard against comparing the bundle with itself: the old one must predate the layouts.
  expect(OLD_BUNDLE).not.toContain("widget_layout");
  const served = await (await page.request.get("/widget.js")).text();
  expect(served).toContain("widget_layout");

  const before = {
    desktop: await capture(page, { width: 1100, height: 800 }, "old"),
    phone: await capture(page, { width: 390, height: 720 }, "old"),
  };
  const after = {
    desktop: await capture(page, { width: 1100, height: 800 }, "new"),
    phone: await capture(page, { width: 390, height: 720 }, "new"),
  };
  expect(after).toEqual(before);
});

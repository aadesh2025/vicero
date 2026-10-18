import { test, expect, type Page } from "@playwright/test";

// The layouts must look like the approved artifact (designreference/vicero-widget-lab.html), in the real
// widget bundle, identically in the builder preview and in an embed. Pixel snapshots differ between
// Windows and the Linux CI runner, so each layout is checked on what defines it: the composer, where the
// suggested prompts sit, and the layout's own signature rules, read from computed styles. A screenshot
// of every layout is attached to the test run for a human to compare with the artifact.

const LAYOUTS = ["classic", "glass", "hairline", "paper", "pebble", "console", "concierge", "command", "drawer", "block"] as const;
const PROMPTS = ["Shop coffee", "Find my roast", "Book a tasting", "Track my order", "Talk to a person"];
const WELCOME = "Hi, I'm Mira. I can help you pick beans, book a tasting, or find your order.";

function config(layout: string, over: Record<string, unknown> = {}) {
  return {
    name: "Mira from Kaapi & Co.",
    welcome_message: WELCOME,
    suggested_prompts: PROMPTS,
    theme: { widget_layout: layout, mode: "light", primary_color: "#2563eb", input_bar_buttons: ["attachment", "emoji"], ...over },
  };
}

async function openPreview(page: Page, cfg: ReturnType<typeof config>) {
  await page.goto("/widget-preview.html");
  await expect(page.locator(".bf-launcher")).toBeVisible();
  await page.evaluate((c) => window.postMessage({ type: "bf-preview-config", config: c }, "*"), cfg);
  await page.locator(".bf-launcher").click();
  await expect(page.locator(".bf-panel.bf-show")).toHaveCount(1);
  await page.mouse.move(2, 2);
  await page.waitForTimeout(450); // opening animation
}

const SIGNATURE_PROPS = ["width", "height", "background-image", "border-radius", "border-top-width", "box-shadow", "padding", "font-family", "font-size", "color", "display", "grid-template-columns"];

async function signature(page: Page) {
  return page.evaluate((props) => {
    const root = (document.querySelector("#vicero-widget") as HTMLElement).shadowRoot!;
    const out: Record<string, Record<string, string>> = {};
    for (const [name, sel] of Object.entries({
      panel: ".bf-panel", head: ".bf-head", title: ".bf-title", bubble: ".bf-bot .bf-bubble", chips: ".bf-chips",
      chip: ".bf-chip", foot: ".bf-foot", inrow: ".bf-inrow", ta: ".bf-ta", send: ".bf-send", brand: ".bf-brand",
    })) {
      const el = root.querySelector(sel) as HTMLElement | null;
      out[name] = {};
      if (!el) continue;
      const cs = getComputedStyle(el);
      for (const p of props) out[name][p] = cs.getPropertyValue(p);
    }
    return out;
  }, SIGNATURE_PROPS);
}

for (const layout of LAYOUTS) {
  test(`${layout}: matches the artifact's composer, prompts and signature`, async ({ page }, info) => {
    await openPreview(page, config(layout));
    const root = page.locator("#vicero-widget");
    await info.attach(`${layout}.png`, { body: await page.locator(".bf-panel").screenshot(), contentType: "image/png" });

    // A. Composer: the artifact's SVG icons and arrow, no emoji glyphs, no blue focus ring.
    for (const sel of [".bf-file", ".bf-emoji", ".bf-send"]) {
      await expect(page.locator(`${sel} svg`)).toHaveCount(1);
      expect((await page.locator(sel).textContent())?.trim()).toBe("");
    }
    expect(await page.locator(".bf-ta").evaluate((e) => getComputedStyle(e).outlineStyle)).toBe("none");
    const inrow = await page.locator(".bf-inrow").evaluate((e) => {
      const c = getComputedStyle(e);
      return { display: c.display, radius: c.borderTopLeftRadius, bg: c.backgroundColor, bt: c.borderTopWidth, bb: c.borderBottomWidth };
    });
    expect(inrow.display).toBe("flex");
    if (layout === "glass") expect(parseFloat(inrow.radius)).toBeGreaterThan(100); // dark pill
    if (layout === "paper") expect(inrow.bb).toBe("1px"); // underline only
    if (layout === "block") expect(inrow.bt).toBe("2px"); // thick box
    if (layout === "hairline" || layout === "console") expect(inrow.bt === "1px" || inrow.bt === "0px").toBeTruthy();
    // icons come after the input, as in the artifact
    const order = await page.evaluate(() => {
      const r = (document.querySelector("#vicero-widget") as HTMLElement).shadowRoot!;
      const x = (s: string) => (r.querySelector(s) as HTMLElement).getBoundingClientRect().left;
      return [x(".bf-ta"), x(".bf-file"), x(".bf-emoji"), x(".bf-send")];
    });
    expect(order).toEqual([...order].sort((a, b) => a - b));

    // C. Suggested prompts sit inside the message list, right under the welcome message.
    const chipsInList = await page.evaluate(() => {
      const r = (document.querySelector("#vicero-widget") as HTMLElement).shadowRoot!;
      const msgs = r.querySelector(".bf-msgs")!;
      const kids = Array.from(msgs.children);
      const welcome = kids.findIndex((k) => k.classList.contains("bf-row"));
      return { inside: !!msgs.querySelector(".bf-chips"), chipsAfterWelcome: kids.findIndex((k) => k.classList.contains("bf-chips")) === welcome + 1 };
    });
    expect(chipsInList).toEqual({ inside: true, chipsAfterWelcome: true });
    await expect(page.locator(".bf-msgs .bf-chip")).toHaveCount(PROMPTS.length);
    // each row has the artifact's label span + an icon + an arrow, built from the agent's prompts only
    await expect(page.locator(".bf-chip .bf-lb").first()).toHaveText(PROMPTS[0]);
    await expect(page.locator(".bf-chip .bf-ic svg").first()).toHaveCount(1);

    // E. Header: refresh + close as icons; the online dot (avatar corner or inline dot); the subtitle.
    await expect(page.locator(".bf-restart svg")).toHaveCount(1);
    await expect(page.locator(".bf-x svg")).toHaveCount(1);
    await expect(page.locator(".bf-sub")).toContainText("Usually replies in seconds");
    // Command hides its header on desktop (Esc closes), as in the artifact.
    if (layout !== "command") await expect(page.locator(".bf-title")).toHaveText("Mira from Kaapi & Co.");

    // Layout signatures
    const pseudo = (sel: string, which: string) =>
      page.locator(sel).first().evaluate((e, w) => getComputedStyle(e, w).content, which);
    if (layout === "glass") {
      expect(await page.locator(".bf-panel").evaluate((e) => getComputedStyle(e).backgroundImage)).toContain("radial-gradient");
      expect(await page.locator(".bf-panel").evaluate((e) => getComputedStyle(e).backdropFilter)).toContain("blur");
      const bub = await page.locator(".bf-bot .bf-bubble").first().evaluate((e) => getComputedStyle(e).backgroundColor);
      expect(bub).toMatch(/rgba\(.*0?\.7\)/); // frosted translucent bubble
    }
    if (layout === "hairline") {
      expect(await page.locator(".bf-chip").first().evaluate((e) => getComputedStyle(e).borderBottomWidth)).toBe("1px");
      expect(await page.locator(".bf-chip .bf-go").first().evaluate((e) => getComputedStyle(e).display)).not.toBe("none"); // → arrow
      expect(await page.locator(".bf-chip").first().evaluate((e) => e.getBoundingClientRect().width)).toBeGreaterThan(300); // full width
      expect(await page.locator(".bf-dot").evaluate((e) => getComputedStyle(e).display)).not.toBe("none");
    }
    if (layout === "paper") expect(await pseudo(".bf-chip", "::before")).toContain("lower-alpha"); // a. b. c.
    if (layout === "console") {
      expect(await pseudo(".bf-chip", "::before")).toContain('"["'); // [1] [2] ...
      await expect(page.locator(".bf-tl i")).toHaveCount(3);
    }
    if (layout === "concierge") {
      await expect(page.locator(".bf-hero-h")).toHaveText("Hi there");
      await expect(page.locator(".bf-team span")).toHaveCount(3);
      expect(await page.locator(".bf-team").evaluate((e) => getComputedStyle(e).display)).toBe("flex");
      expect(await page.locator(".bf-chips").evaluate((e) => getComputedStyle(e).display)).toBe("grid");
      // a distinct icon per tile (shop, find, book, track differ; the default is the chat bubble)
      const paths = await page.locator(".bf-chip .bf-ic svg").evaluateAll((els) => els.map((e) => e.innerHTML));
      expect(new Set(paths.slice(0, 4)).size).toBe(4);
    }
    if (layout === "drawer") {
      await expect(page.locator(".bf-tabs button")).toHaveText(["Chat", "Help", "Status"]);
      await page.locator('.bf-tabs button[data-tab="status"]').click();
      await expect(page.locator(".bf-status")).toContainText("All systems operational");
      await expect(page.locator(".bf-status")).toContainText("Usually replies in seconds");
      await page.locator('.bf-tabs button[data-tab="chat"]').click();
      await expect(page.locator(".bf-msgs")).toBeVisible();
    }
    if (layout === "command") {
      await expect(page.locator(".bf-kbd")).toBeVisible();
      expect(await root.locator(".bf-inrow").evaluate((e) => getComputedStyle(e, "::before").content)).toBe('""'); // search icon mask
    }
    if (layout === "block") expect(await page.locator(".bf-panel").evaluate((e) => getComputedStyle(e).borderTopWidth)).toBe("2px");
  });
}

test("a second welcome line becomes a second bubble; a single line stays one (nothing is invented)", async ({ page }) => {
  await openPreview(page, { ...config("classic"), welcome_message: "First line.\nSecond line." });
  await expect(page.locator(".bf-msgs > .bf-row.bf-bot")).toHaveCount(2);
  await openPreview(page, config("classic"));
  await expect(page.locator(".bf-msgs > .bf-row.bf-bot")).toHaveCount(1);
});

test("Start over returns to the welcome message with the prompts", async ({ page }) => {
  await openPreview(page, config("classic"));
  await page.locator(".bf-ta").fill("hello");
  await page.locator(".bf-send").click();
  await expect(page.locator(".bf-user")).toHaveCount(1);
  await expect(page.locator(".bf-chips")).toBeHidden(); // the menu goes once the chat starts
  await page.locator(".bf-restart").click();
  await expect(page.locator(".bf-user")).toHaveCount(0);
  await expect(page.locator(".bf-msgs .bf-chip")).toHaveCount(PROMPTS.length);
  await expect(page.locator(".bf-chips")).toBeVisible();
});

test("the builder preview and an embed render every layout identically", async ({ page }) => {
  const embed = async (layout: string) => {
    const cfg = config(layout);
    await page.route("**/v1/public/agents/*/config", (route) =>
      route.fulfill({
        json: { agent_id: "00000000-0000-4000-8000-000000000001", ...cfg, campaigns: [] },
        headers: { "access-control-allow-origin": "*" },
      }),
    );
    await page.goto("/login");
    await page.evaluate(() => {
      const s = document.createElement("script");
      s.src = "/widget.js";
      s.setAttribute("data-agent", "bf_pub_visualcheck");
      s.setAttribute("data-api", "http://localhost:8010");
      document.body.appendChild(s);
    });
    await expect(page.locator(".bf-launcher")).toBeVisible({ timeout: 15_000 });
    await page.locator(".bf-launcher").click();
    await page.mouse.move(2, 2);
    await page.waitForTimeout(450);
    return signature(page);
  };
  for (const layout of LAYOUTS) {
    await page.setViewportSize({ width: 1100, height: 800 });
    const fromEmbed = await embed(layout);
    await page.unroute("**/v1/public/agents/*/config");
    await openPreview(page, config(layout));
    expect(await signature(page), layout).toEqual(fromEmbed);
  }
});

test("dark mode: every layout keeps readable prompts and a composer", async ({ page }) => {
  for (const layout of LAYOUTS) {
    await openPreview(page, config(layout, { mode: "dark" }));
    await expect(page.locator(".bf-ta")).toBeVisible();
    const contrast = await page.evaluate(() => {
      const r = (document.querySelector("#vicero-widget") as HTMLElement).shadowRoot!;
      const lum = (rgb: string) => {
        const m = rgb.match(/[\d.]+/g)!.map(Number).slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
        return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2];
      };
      const text = getComputedStyle(r.querySelector(".bf-chip .bf-lb")!).color;
      const panel = getComputedStyle(r.querySelector(".bf-panel")!).backgroundColor;
      const a = lum(text), b = panel.startsWith("rgba(") && /,\s*0\)$/.test(panel) ? 1 : lum(panel);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    });
    expect(contrast, layout).toBeGreaterThan(2.5);
  }
});

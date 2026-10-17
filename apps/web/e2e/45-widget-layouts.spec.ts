import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { API, auth, createAccount, type Account } from "./helpers";

// docs/27 Phase 1: every one of the 10 layouts must embed, open, stream a real reply, render
// citations, close on Esc, and go full-screen on a phone. One agent, re-themed per layout.

const LAYOUTS = [
  "classic", "glass", "hairline", "paper", "pebble", "console", "concierge", "command", "drawer", "block",
] as const;

const SHOTS = "playwright-shots/layouts"; // outside test-results, which Playwright wipes each run

async function agentWithPrompts(request: import("@playwright/test").APIRequestContext, account: Account) {
  const create = await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "Layout Bot" } });
  expect(create.ok()).toBeTruthy();
  const agent = await create.json();
  await request.patch(`${API}/v1/agents/${agent.id}/versions/${agent.draft_version}`, {
    headers: auth(account),
    data: {
      model_config: { provider: "groq", model: "openai/gpt-oss-20b" },
      welcome_message: "Welcome to the layout bot",
      suggested_prompts: ["What are your hours?", "Track my order", "Talk to a person"],
    },
  });
  const pub = await request.post(`${API}/v1/agents/${agent.id}/versions/${agent.draft_version}/publish`, {
    headers: auth(account),
  });
  expect(pub.ok()).toBeTruthy();
  return { id: agent.id as string, publicKey: agent.public_key as string };
}

async function embed(page: Page, publicKey: string) {
  await page.goto("/login");
  await page.evaluate(
    ([key, api]) => {
      const s = document.createElement("script");
      s.src = "/widget.js";
      s.setAttribute("data-agent", key);
      s.setAttribute("data-api", api);
      document.body.appendChild(s);
    },
    [publicKey, API],
  );
  await expect(page.locator(".bf-launcher")).toBeVisible({ timeout: 15_000 });
}

// Canned SSE reply carrying citations, so the sources row is checked on every layout without a KB.
async function mockCitedReply(page: Page) {
  await page.route("**/v1/public/agents/*/chat", async (route) => {
    const ev = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "access-control-allow-origin": "*" },
      body:
        ev({ type: "citations", citations: [{ content: "Open 9-5", metadata: { title: "Hours doc" } }] }) +
        ev({ type: "token", delta: "We are open 9 to 5 [1]" }),
    });
  });
}

test.describe("widget layouts", () => {
  let account: Account;
  let agent: { id: string; publicKey: string };

  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, "Layouts Org");
    agent = await agentWithPrompts(request, account);
    mkdirSync(SHOTS, { recursive: true });
  });

  for (const layout of LAYOUTS) {
    test(`${layout}: open, stream, cite, Esc, phone`, async ({ page, request }) => {
      const r = await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, {
        headers: auth(account),
        data: { widgetLayout: layout, cornerRadius: 14, density: "comfortable" },
      });
      expect(r.ok(), await r.text()).toBeTruthy();

      await embed(page, agent.publicKey);
      const panel = page.locator(".bf-panel");
      await expect(panel).toHaveClass(new RegExp(`bf-l-${layout}`));
      await page.locator(".bf-launcher").click();
      await expect(panel).toHaveClass(/bf-show/);
      if (layout === "concierge") await expect(panel).toHaveClass(/bf-home/);
      await page.screenshot({ path: `${SHOTS}/${layout}-home.png` });

      // 1) A real streamed turn through the API.
      await page.locator(".bf-ta").fill("Hello layout");
      await page.locator(".bf-send").click();
      await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo:\s*Hello layout/i, {
        timeout: 20_000,
      });
      // The concierge home screen is gone after the first message.
      if (layout === "concierge") await expect(panel).not.toHaveClass(/bf-home/);

      // 2) Citations still render under a cited answer.
      await mockCitedReply(page);
      await page.locator(".bf-ta").fill("hours?");
      await page.locator(".bf-send").click();
      await expect(page.locator(".bf-cite").first()).toContainText("Hours doc", { timeout: 10_000 });
      await page.screenshot({ path: `${SHOTS}/${layout}-chat.png` });

      // 3) Esc closes from the composer.
      await page.locator(".bf-ta").focus();
      await page.keyboard.press("Escape");
      await expect(panel).not.toHaveClass(/bf-show/);

      // 4) Phone width: the open panel fills the screen.
      await page.setViewportSize({ width: 390, height: 720 });
      await page.locator(".bf-launcher").click();
      await page.waitForTimeout(450); // let the open animation finish before measuring
      const box = await panel.boundingBox();
      expect(box).toBeTruthy();
      expect(Math.round(box!.width)).toBe(390);
      expect(Math.round(box!.height)).toBe(720);
      expect(Math.round(box!.x)).toBe(0);
      await page.screenshot({ path: `${SHOTS}/${layout}-phone.png` });
    });
  }

  test("command: arrow keys walk the suggestion rows", async ({ page, request }) => {
    await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, {
      headers: auth(account),
      data: { widgetLayout: "command" },
    });
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await page.waitForTimeout(150); // open() focuses the composer after 50ms; don't race it
    await page.locator(".bf-ta").focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.locator(".bf-chip").first()).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.locator(".bf-chip").nth(1)).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");
    await expect(page.locator(".bf-ta")).toBeFocused();
  });

  test("drawer: Help tab lists the suggested prompts and sends one", async ({ page, request }) => {
    await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, {
      headers: auth(account),
      data: { widgetLayout: "drawer" },
    });
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await page.locator('.bf-tabs button[data-tab="help"]').click();
    await expect(page.locator(".bf-artrow")).toHaveCount(3);
    await page.locator(".bf-artrow").first().click();
    await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo:\s*What are your hours/i, {
      timeout: 20_000,
    });
  });

  test("concierge: home screen does not come back for an existing conversation", async ({ page, request }) => {
    await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, {
      headers: auth(account),
      data: { widgetLayout: "concierge" },
    });
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await expect(page.locator(".bf-panel")).toHaveClass(/bf-home/);
    await page.locator(".bf-ta").fill("hi");
    await page.locator(".bf-send").click();
    await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo/i, { timeout: 20_000 });
    await embed(page, agent.publicKey); // reload: the conversation id is stored
    await page.locator(".bf-launcher").click();
    await expect(page.locator(".bf-panel")).not.toHaveClass(/bf-home/);
  });

  test("builder preview renders each layout and never changes open/closed", async ({ page }) => {
    await page.goto("/widget-preview.html");
    await expect(page.locator(".bf-launcher")).toBeVisible();
    for (const layout of LAYOUTS) {
      await page.evaluate((l) => {
        window.postMessage(
          {
            type: "bf-preview-config",
            config: {
              name: "Preview",
              welcome_message: "Hi",
              suggested_prompts: ["One", "Two"],
              theme: { widget_layout: l, corner_radius: 12, density: "roomy" },
            },
          },
          "*",
        );
      }, layout);
      await expect(page.locator(".bf-panel")).toHaveClass(new RegExp(`bf-l-${layout}`));
      await expect(page.locator(".bf-panel")).not.toHaveClass(/bf-show/);
    }
    // Back to no layout: every layout class and extra style is gone.
    await page.evaluate(() =>
      window.postMessage(
        { type: "bf-preview-config", config: { name: "P", welcome_message: "Hi", suggested_prompts: [], theme: {} } },
        "*",
      ),
    );
    await expect(page.locator(".bf-panel")).not.toHaveClass(/bf-lay|bf-l-|bf-tuned/);
  });
});

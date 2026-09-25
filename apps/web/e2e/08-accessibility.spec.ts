import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { API, authenticateBrowser, createAccount, createPublishedAgent } from "./helpers";

// Phase 19.3 a11y gate: no serious/critical WCAG 2.1 A/AA violations on key pages + the widget.
const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const BLOCKING = new Set(["serious", "critical"]);

async function scan(page: import("@playwright/test").Page, selector?: string) {
  let builder = new AxeBuilder({ page }).withTags(WCAG);
  if (selector) builder = builder.include(selector);
  const results = await builder.analyze();
  return results.violations.filter((v) => BLOCKING.has(v.impact ?? ""));
}

test.describe("accessibility (axe, serious+critical)", () => {
  test("public auth pages", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
    expect(await scan(page)).toEqual([]);

    await page.goto("/signup");
    await expect(page.getByRole("button", { name: /create account/i })).toBeVisible();
    expect(await scan(page)).toEqual([]);
  });

  test("public docs", async ({ page }) => {
    // The docs are the product's only fully public surface, so a violation here is one
    // anybody can hit without an account.
    await page.goto("/docs");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await scan(page)).toEqual([]);

    await page.goto("/docs/api/reference");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await scan(page)).toEqual([]);
  });

  test("private area login", async ({ page }) => {
    // Its own sign-in page, reachable by anyone who guesses the URL. Whether or not the
    // server has it configured, the page has to be accessible.
    await page.goto("/vault/login");
    await expect(page.getByRole("heading", { name: "Private area" })).toBeVisible();
    expect(await scan(page)).toEqual([]);
  });

  test("dashboard + agents (authenticated)", async ({ page, context, request }) => {
    const account = await createAccount(request, "A11y Org");
    await request.post(`${API}/v1/agents`, {
      headers: { Authorization: `Bearer ${account.access}`, "X-Org-Id": account.orgId },
      data: { name: "A11y Agent" },
    });
    await authenticateBrowser(context, account);

    await page.goto("/dashboard");
    await expect(page.getByRole("heading").first()).toBeVisible();
    expect(await scan(page)).toEqual([]);

    await page.goto("/agents");
    await expect(page.getByText("A11y Agent")).toBeVisible();
    expect(await scan(page)).toEqual([]);
  });

  test("embedded widget", async ({ page, request }) => {
    const account = await createAccount(request, "A11y Widget Org");
    const { publicKey } = await createPublishedAgent(request, account, { name: "A11y Widget" });

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
    await page.locator(".bf-launcher").click();
    await expect(page.locator(".bf-ta")).toBeVisible();

    // axe descends into the widget's open shadow root on a full-page scan.
    expect(await scan(page)).toEqual([]);
  });
});

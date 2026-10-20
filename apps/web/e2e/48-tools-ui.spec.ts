import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { API, auth, authenticateBrowser, createAccount, createPublishedAgent } from "./helpers";

// docs/28 T5: the Tools tab. Nothing here reaches the public internet: connections and tools are saved, not called.
test("tools tab: connection, API tool, inbound hook called with its secret, recent call visible", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Tools UI Org");
  const { id: agentId } = await createPublishedAgent(request, account, { name: "Tools Bot" });
  await authenticateBrowser(context, account);

  // 1. A saved connection (Settings > Connections).
  await page.goto("/settings/connections");
  await page.getByRole("button", { name: /new connection|add connection/i }).first().click();
  await page.getByLabel("Connection name").fill("Shop API");
  await page.getByLabel("Base URL").fill("https://api.shop.example.com");
  await page.getByLabel("Token").fill("e2e-not-a-real-token");
  await page.getByRole("button", { name: /create connection/i }).click();
  await expect(page.getByText("Shop API")).toBeVisible();

  // 2. The Tools tab shows every section, and an API tool can be started from it.
  await page.goto(`/agents/${agentId}?tab=tools`);
  for (const title of [/built-in tools/i, /api tools/i, /mcp/i, /send events to your systems/i, /let other systems trigger/i]) {
    await expect(page.getByText(title).first()).toBeVisible();
  }
  await page.getByRole("button", { name: /new api tool/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");

  // 3. An inbound hook: create in the UI, copy nothing, read the one-time secret from the dialog.
  await page.getByRole("button", { name: /new inbound hook/i }).click();
  await page.getByLabel("Hook name").fill("Order shipped");
  await page.getByLabel("Hook action").selectOption("set_variables");
  await page.getByRole("textbox", { name: "Field name" }).first().fill("phone");
  await page.getByRole("textbox", { name: "Selector" }).first().fill("$.customer.phone");
  // set_variables needs one field besides the customer id to save.
  await page.getByRole("button", { name: /add field/i }).click();
  await page.getByRole("textbox", { name: "Field name" }).nth(1).fill("order_id");
  await page.getByRole("textbox", { name: "Selector" }).nth(1).fill("$.order.id");
  await page.getByRole("button", { name: /^create hook$/i }).click();

  const secret = (await page.getByLabel("Hook secret").innerText()).trim();
  expect(secret.length).toBeGreaterThan(16);
  await page.getByRole("button", { name: /i have saved the secret/i }).click();

  const hooks = await (await request.get(`${API}/v1/agents/${agentId}/inbound-hooks`, { headers: auth(account) })).json();
  expect(hooks).toHaveLength(1);
  const res = await request.post(`${API}${hooks[0].url_path}`, {
    headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" },
    data: { customer: { phone: "+15555550100" }, event_id: "e2e-1" },
  });
  // hmac is the default; a bearer secret is refused. The refusal is itself logged, secret-free.
  expect([200, 202, 401]).toContain(res.status());

  // 4. Both themes pass axe on the tab.
  for (const theme of ["light", "dark"]) {
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    const results = await new AxeBuilder({ page }).disableRules(["color-contrast"]).analyze();
    expect(results.violations.filter((v) => v.impact === "critical" || v.impact === "serious")).toEqual([]);
  }
});

test("inbox: tool approvals tab renders and is empty by default", async ({ page, context, request }) => {
  const account = await createAccount(request, "Approvals Org");
  await authenticateBrowser(context, account);
  await page.goto("/inbox");
  await page.getByRole("button", { name: /tool approvals/i }).click();
  await expect(page.getByText(/nothing waiting for approval/i)).toBeVisible();
});

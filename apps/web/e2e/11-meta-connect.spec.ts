import { test, expect } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, createPublishedAgent } from "./helpers";

// One-click Meta connect (docs/26): with no Meta app configured on the API (the E2E stack sets no
// META_* values), the three Connect buttons must render but be disabled, and the manual token route
// must still be there. The real Facebook popup cannot be driven from CI.
test("one-click Meta buttons render and are disabled when the app is not configured", async ({ page, context, request }) => {
  const account = await createAccount(request, "Meta Connect Org");
  const { id: agentId } = await createPublishedAgent(request, account, { name: "Meta Bot" });

  const cfg = await request.get(`${API}/v1/channels/meta/config`, { headers: auth(account) });
  expect(cfg.ok(), await cfg.text()).toBeTruthy();
  const config = await cfg.json();
  expect(config.enabled).toBe(false);
  expect(JSON.stringify(config)).not.toMatch(/secret|token/i); // nothing sensitive is exposed

  await authenticateBrowser(context, account);
  await page.goto(`/agents/${agentId}?tab=channels`);

  for (const name of [/connect whatsapp/i, /connect messenger/i, /connect instagram/i]) {
    const button = page.getByRole("button", { name });
    await expect(button).toBeVisible();
    await expect(button).toBeDisabled();
  }
  await expect(page.getByText("Advanced: connect with tokens").first()).toBeVisible();

  // The shared webhook refuses unsigned requests (fail closed), and always answers Meta's handshake with 403 here.
  const unsigned = await request.post(`${API}/api/meta/webhook`, { data: { object: "page", entry: [] } });
  expect([401, 503]).toContain(unsigned.status());
});

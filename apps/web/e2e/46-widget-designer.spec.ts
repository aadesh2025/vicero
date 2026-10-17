import { test, expect } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, createPublishedAgent } from "./helpers";

// docs/27 Phase 1 designer: layout cards, corners, spacing, reset. The live preview is the real
// widget bundle, so a card click must change the iframe's panel class immediately, and the
// autosave must persist the same values to the unversioned widget config.

test("designer: pick a layout, tune corners and spacing, reset, go back to Original", async ({ page, context, request }) => {
  const account = await createAccount(request, "Designer Org");
  const { id } = await createPublishedAgent(request, account, { name: "Designer Bot" });
  await authenticateBrowser(context, account);
  await page.goto(`/agents/${id}?tab=channels`);

  const frame = page.frameLocator('iframe[title="Widget preview"]');
  await expect(frame.locator(".bf-launcher")).toBeVisible({ timeout: 20_000 });
  // Untouched agent: the original look, no layout class.
  await expect(frame.locator(".bf-panel")).not.toHaveClass(/bf-l-/);

  const saved = async () =>
    (await (await request.get(`${API}/v1/agents/${id}/widget-config`, { headers: auth(account) })).json()) as Record<string, unknown>;

  // Pick Glass: preset applied (radius 22, accent #0f172a) and the preview switches at once.
  await page.getByRole("radio", { name: /^Glass/ }).click();
  await expect(frame.locator(".bf-panel")).toHaveClass(/bf-l-glass/);
  await expect.poll(async () => (await saved()).widgetLayout, { timeout: 15_000 }).toBe("glass");
  expect((await saved()).cornerRadius).toBe(22);
  expect((await saved()).primaryColor).toBe("#0f172a");

  // Spacing + corners are independent of the layout and still editable.
  await page.getByRole("button", { name: "Roomy" }).click();
  await expect.poll(async () => (await saved()).density, { timeout: 15_000 }).toBe("roomy");

  const slider = page.getByRole("slider");
  await slider.focus();
  await page.keyboard.press("Home"); // 0
  await expect.poll(async () => (await saved()).cornerRadius, { timeout: 15_000 }).toBe(0);

  // Changing the accent by hand, then "Reset to Glass defaults" restores the preset.
  await page.getByRole("button", { name: "Reset to Glass defaults" }).click();
  await expect.poll(async () => (await saved()).cornerRadius, { timeout: 15_000 }).toBe(22);
  expect((await saved()).density).toBeNull();

  // Existing controls keep working while a layout is active.
  await page.getByRole("radio", { name: /^Console/ }).click();
  await expect(frame.locator(".bf-panel")).toHaveClass(/bf-l-console/);
  await expect.poll(async () => (await saved()).fontFamily, { timeout: 15_000 }).toBe("courier");

  // Back to Original: the layout class is gone from the preview, saved layout is null.
  await page.getByRole("radio", { name: /^Original/ }).click();
  await expect(frame.locator(".bf-panel")).not.toHaveClass(/bf-l-/);
  await expect.poll(async () => (await saved()).widgetLayout, { timeout: 15_000 }).toBeNull();
});

test("designer: interactive blocks master switch and per-type toggles", async ({ page, context, request }) => {
  const account = await createAccount(request, "Designer Blocks Org");
  const { id } = await createPublishedAgent(request, account, { name: "Blocks Designer Bot" });
  await authenticateBrowser(context, account);
  await page.goto(`/agents/${id}?tab=channels`);
  const frame = page.frameLocator('iframe[title="Widget preview"]');
  await expect(frame.locator(".bf-launcher")).toBeVisible({ timeout: 20_000 });

  const saved = async () =>
    (await (await request.get(`${API}/v1/agents/${id}/widget-config`, { headers: auth(account) })).json()) as Record<string, unknown>;

  // Off by default: nothing saved as enabled, no sample blocks in the preview, toggles hidden.
  expect((await saved()).interactiveBlocks ?? null).toBeNull();
  await expect(page.getByLabel("Product cards")).toHaveCount(0);

  await page.getByRole("switch", { name: "Interactive blocks" }).click();
  await expect.poll(async () => ((await saved()).interactiveBlocks as Record<string, boolean> | null)?.enabled, { timeout: 15_000 }).toBe(true);
  await expect(frame.locator(".bf-b-options")).toHaveCount(1); // preview shows a sample
  await expect(frame.locator(".bf-b-cards")).toHaveCount(1);

  await page.getByLabel("Product cards").uncheck();
  await expect.poll(async () => ((await saved()).interactiveBlocks as Record<string, boolean>).cards, { timeout: 15_000 }).toBe(false);
  await expect(frame.locator(".bf-b-cards")).toHaveCount(0);
  await expect(frame.locator(".bf-b-options")).toHaveCount(1);

  // Public config carries the switch only while it is on.
  const agentRow = await (await request.get(`${API}/v1/agents/${id}`, { headers: auth(account) })).json();
  const pub = async () =>
    (await (await request.get(`${API}/v1/public/agents/${agentRow.public_key}/config`)).json()).theme.interactive_blocks;
  expect(await pub()).toMatchObject({ enabled: true, cards: false, options: true });

  await page.getByRole("switch", { name: "Interactive blocks" }).click();
  await expect.poll(pub, { timeout: 15_000 }).toBeNull();
  await expect(frame.locator(".bf-blk")).toHaveCount(0);
});

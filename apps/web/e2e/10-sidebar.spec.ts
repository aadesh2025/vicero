import { test, expect } from "@playwright/test";
import { authenticateBrowser, createAccount } from "./helpers";

// The nav must scroll internally (min-h-0 on the flex child) so the footer — account row +
// Collapse button — stays visible without scrolling, even at a short viewport height. And the new
// header collapse button must toggle the same `collapsed` state as the existing footer one.
test("sidebar footer stays visible at short height; header toggle collapses/expands", async ({ page, context, request }) => {
  const account = await createAccount(request, "Sidebar Org");
  await authenticateBrowser(context, account);

  // Desktop width (sidebar is off-canvas below lg) but deliberately short height.
  await page.setViewportSize({ width: 1280, height: 520 });
  await page.goto("/dashboard");

  // Scoped by aria-label: at this width the AI Builder rail (docs/20 §9.3) is its own
  // labelled <aside> too, so a bare "aside" locator is now ambiguous.
  const aside = page.getByRole("complementary", { name: "Sidebar" });
  await expect(aside).toBeVisible({ timeout: 20_000 });

  // Footer is reachable with no scrolling: account row + Collapse button both in the viewport.
  // (This used to assert a "<tier> plan" label the sidebar had already stopped rendering; the
  // redesign's footer is the account row, and the trial card appears only on trial workspaces.)
  await expect(page.getByRole("button", { name: "Account menu" })).toBeInViewport();
  await expect(page.getByRole("button", { name: "Collapse sidebar" }).last()).toBeInViewport();

  // Header collapse button (first with this accessible name; the footer one is .last()).
  const headerToggle = page.getByRole("button", { name: "Collapse sidebar" }).first();
  const widthOf = () => aside.evaluate((el) => el.getBoundingClientRect().width);

  const expanded = await widthOf();
  expect(expanded).toBeGreaterThan(200); // ~248px

  await headerToggle.click(); // collapse via the header button
  await expect.poll(widthOf).toBeLessThan(100); // ~68px — same collapsed state as the footer control

  // When collapsed, the header expand button is still rendered and expands again.
  const headerExpand = page.getByRole("button", { name: "Expand sidebar" }).first();
  await expect(headerExpand).toBeVisible();
  await headerExpand.click();
  await expect.poll(widthOf).toBeGreaterThan(200);
});

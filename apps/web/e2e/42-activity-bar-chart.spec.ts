import { test, expect } from "@playwright/test";
import { authenticateBrowser, createAccount } from "./helpers";

/**
 * The dashboard "Activity" bar chart (docs/20 §9.3.2, ADR-101) — replaces the old line chart.
 * A brand-new, traffic-free org is enough for these: every bucket in range is zero-filled
 * regardless of real data, so bar *count* per period doesn't depend on seeding conversations.
 */
test.describe("Activity bar chart", () => {
  test("renders in both light and dark", async ({ page, context, request }) => {
    const account = await createAccount(request, "Activity Chart Org");
    await authenticateBrowser(context, account);

    for (const theme of ["light", "dark"] as const) {
      await page.addInitScript((t) => localStorage.setItem("theme", t), theme);
      await page.goto("/dashboard");

      const chart = page.getByTestId("activity-chart");
      await expect(chart.getByRole("heading", { name: "Activity" })).toBeVisible();
      await expect(chart.getByRole("group", { name: /Conversations per/ })).toBeVisible();
    }
  });

  test("Daily/Weekly/Monthly show 14/12/12 bars", async ({ page, context, request }) => {
    const account = await createAccount(request, "Activity Period Org");
    await authenticateBrowser(context, account);
    await page.goto("/dashboard");

    const chart = page.getByTestId("activity-chart");
    const bars = chart.getByRole("group", { name: /Conversations per/ }).locator("g[role='img']");

    await expect(bars).toHaveCount(14); // Daily is the default

    await chart.getByRole("button", { name: "Weekly", exact: true }).click();
    await expect(bars).toHaveCount(12);

    await chart.getByRole("button", { name: "Monthly", exact: true }).click();
    await expect(bars).toHaveCount(12);

    await chart.getByRole("button", { name: "Daily", exact: true }).click();
    await expect(bars).toHaveCount(14);
  });

  test("arrow keys move the selected bar", async ({ page, context, request }) => {
    const account = await createAccount(request, "Activity Keyboard Org");
    await authenticateBrowser(context, account);
    await page.goto("/dashboard");

    const chart = page.getByTestId("activity-chart");
    const group = chart.getByRole("group", { name: /Conversations per/ });
    const selectedRect = group.locator("rect[fill*='-selected']");

    await expect(selectedRect).toHaveCount(1);
    const before = await selectedRect.getAttribute("x");

    await group.focus();
    await group.press("ArrowLeft");
    const afterLeft = await selectedRect.getAttribute("x");
    expect(Number(afterLeft)).toBeLessThan(Number(before));

    await group.press("ArrowRight");
    const afterRight = await selectedRect.getAttribute("x");
    expect(Number(afterRight)).toBeCloseTo(Number(before), 1);
  });
});

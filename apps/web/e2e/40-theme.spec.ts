import { test, expect } from "@playwright/test";
import { authenticateBrowser, createAccount } from "./helpers";

// docs/20 §9.4: Light / Dark / System, remembered across reloads, and no flash of the wrong
// theme on load (next-themes' inline script sets the class before first paint).

const htmlIsDark = (page: import("@playwright/test").Page) =>
  page.evaluate(() => document.documentElement.classList.contains("dark"));

test("the theme toggle cycles Light -> Dark -> System and the choice survives a reload", async ({ page, context, request }) => {
  const account = await createAccount(request, "Theme Org");
  await authenticateBrowser(context, account);
  await page.goto("/dashboard");

  const toggle = page.getByRole("button", { name: /^Theme:/ });
  await expect(toggle).toHaveAccessibleName("Theme: Light. Switch to Dark");
  expect(await htmlIsDark(page)).toBe(false);

  await toggle.click();
  await expect(toggle).toHaveAccessibleName("Theme: Dark. Switch to System");
  expect(await htmlIsDark(page)).toBe(true);

  await page.reload();
  await expect(page.getByRole("button", { name: /^Theme:/ })).toHaveAccessibleName("Theme: Dark. Switch to System");
  expect(await htmlIsDark(page)).toBe(true);

  await page.getByRole("button", { name: /^Theme:/ }).click();
  await expect(page.getByRole("button", { name: /^Theme:/ })).toHaveAccessibleName("Theme: System. Switch to Light");
});

test("System follows the operating system preference", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "dark" });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem("theme", "system"));
  await page.goto("/login");
  expect(await htmlIsDark(page)).toBe(true);
  await context.close();
});

test("a stored dark theme is applied before hydration (no flash)", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "light" });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  // Checked at DOMContentLoaded, i.e. before the React bundle has hydrated.
  expect(await htmlIsDark(page)).toBe(true);
  await context.close();
});

import { test, expect } from "@playwright/test";

/**
 * Login + signup redesign ("Design A"): a form on the left, an always-dark scripted chat preview
 * on the right (a two-bubble card below `lg`). Only the look changed — these pin the structure
 * and the behaviour that must survive it. The wrong-password call is stubbed at the BFF so the
 * spec needs no API.
 */

test("login shows every sign-in method and the showcase", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with Facebook" })).toBeVisible();
  await expect(page.getByLabel("Work email")).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  // The preview is decorative: present on desktop, hidden from assistive tech.
  await expect(page.locator("aside[aria-hidden='true']")).toBeVisible();
  await expect(page.locator("aside[aria-hidden='true'] a, aside[aria-hidden='true'] button")).toHaveCount(0);
});

test("the eye button toggles the password field", async ({ page }) => {
  await page.goto("/login");
  const password = page.getByLabel("Password", { exact: true });
  await expect(password).toHaveAttribute("type", "password");

  await page.getByRole("button", { name: "Show password" }).click();
  await expect(password).toHaveAttribute("type", "text");
  await expect(page.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "Hide password" }).click();
  await expect(password).toHaveAttribute("type", "password");
});

test("login and signup link to each other", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Start your free trial" }).click();
  await expect(page).toHaveURL(/\/signup$/);
  await expect(page.getByRole("heading", { name: "Start your free trial." })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute("minlength", "8");

  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("a rejected password still shows the API's error", async ({ page }) => {
  await page.route("**/api/auth/login", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "auth.invalid_credentials", message: "Incorrect email or password." } }),
    }),
  );
  await page.goto("/login");
  await page.getByLabel("Work email").fill("nobody@example.com");
  await page.getByLabel("Password", { exact: true }).fill("not-the-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Incorrect email or password." })).toBeVisible();
});

test("a provider error redirected to /login is still shown", async ({ page }) => {
  await page.goto("/login?error=auth.oauth_cancelled");
  await expect(page.getByRole("alert").filter({ hasText: "Sign-in was cancelled." })).toBeVisible();
});

test("on a phone the showcase gives way to the mini chat card", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");

  await expect(page.locator("aside[aria-hidden='true']")).toBeHidden();
  await expect(page.getByTestId("mini-chat-card")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("with OS reduced-motion on, the page hydrates cleanly and the demo still plays", async ({ browser }) => {
  // Regression: branching on useReducedMotion() during render broke hydration (a recoverable
  // error in the console) and switched the animation off for anyone with "reduce animations" on.
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error" && /hydrat/i.test(m.text())) problems.push(m.text());
  });
  await page.goto("/login");
  // The first bubble is scheduled at 0.6s; it only ever appears if the animation actually runs.
  await expect(page.getByText("Hi! I'm the assistant on your website")).toBeVisible();
  await expect(page.getByText("Hi! I'm the assistant on your website")).toHaveCSS("opacity", "1", { timeout: 10_000 });
  expect(problems).toEqual([]);
  await context.close();
});

test("the conversation loops: after a full cycle the first bubble plays again", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/login");
  const first = page.getByText("Hi! I'm the assistant on your website");
  await expect(first).toHaveCSS("opacity", "1", { timeout: 10_000 });
  // ~14s per cycle: the run restarts, so the bubble drops back to hidden before fading in again.
  await expect(first).toHaveCSS("opacity", "0", { timeout: 20_000 });
  await expect(first).toHaveCSS("opacity", "1", { timeout: 10_000 });
});

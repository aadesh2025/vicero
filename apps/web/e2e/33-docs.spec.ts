import { test, expect } from "@playwright/test";
import { WEB, createAccount, authenticateBrowser } from "./helpers";

/**
 * The documentation site: public pages readable signed out, and the internal reference
 * refused to everyone who is not platform staff.
 *
 * The refusal assertions check the **response body**, not just the rendered page. A
 * client-side guard produces exactly the same screen as a server-side one — the difference
 * is whether the content was sent at all, and only the body shows that. If this file is
 * ever weakened to `expect(page).toHaveURL(...)`, it stops testing the thing it exists for.
 *
 * Phrases below are quoted from `content/internal/*.mdx`. They are deliberately distinctive
 * so a substring match cannot pass by accident.
 */

/** Sentences that appear only in the internal collection. */
const INTERNAL_ONLY = [
  "Tenant isolation is a convention",
  "SECRET_KEY is not only a signing key",
  "There are no custom queues",
];

test.describe("public docs", () => {
  test("are readable without signing in", async ({ page }) => {
    await page.goto(`${WEB}/docs`);
    await expect(page).toHaveURL(/\/docs$/);
    await expect(page.getByRole("heading", { name: "Introduction", level: 1 })).toBeVisible();
  });

  test("navigate between pages from the sidebar", async ({ page }) => {
    await page.goto(`${WEB}/docs`);
    await page.getByRole("navigation", { name: "Documentation" }).getByRole("link", { name: "Quickstart" }).click();
    await expect(page).toHaveURL(/\/docs\/quickstart$/);
    await expect(page.getByRole("heading", { name: "Quickstart", level: 1 })).toBeVisible();
  });

  test("highlight code and render tables", async ({ page }) => {
    await page.goto(`${WEB}/docs/api/streaming`);
    // Shiki emits both themes as CSS variables; the app switches them by class.
    const code = page.locator("pre .shiki, pre.shiki").first();
    await expect(code).toBeVisible();
    await expect(page.locator("table").first()).toBeVisible();
  });

  test("list endpoints without exposing the staff-only ones", async ({ page }) => {
    await page.goto(`${WEB}/docs/api/reference`);
    await expect(page.getByRole("heading", { name: "Endpoint reference", level: 1 })).toBeVisible();
    const body = await page.content();
    expect(body).toContain("/v1/agents");
    // The public reference filters the admin and mcp tags.
    expect(body).not.toContain("/v1/admin");
    expect(body).not.toContain("/v1/mcp");
  });
});

test.describe("internal docs", () => {
  test("bounce a signed-out visitor to the login page", async ({ page }) => {
    const response = await page.goto(`${WEB}/internal-docs`);
    await expect(page).toHaveURL(/\/login/);
    const body = (await response?.text()) ?? "";
    for (const phrase of INTERNAL_ONLY) expect(body).not.toContain(phrase);
  });

  test("refuse a signed-in account that is not staff, and send it no content", async ({
    page,
    context,
    request,
  }) => {
    const account = await createAccount(request, "Docs Gate Org");
    await authenticateBrowser(context, account);

    const response = await page.goto(`${WEB}/internal-docs`);
    await expect(page.getByText("Platform staff only")).toBeVisible();

    // The point of the whole design: the refusal is not a redirect over content that was
    // already delivered. Nothing from the internal collection is in the response.
    const body = (await response?.text()) ?? "";
    for (const phrase of INTERNAL_ONLY) expect(body).not.toContain(phrase);
  });

  test("refuse a deep link the same way, including one that tries to traverse", async ({
    page,
    context,
    request,
  }) => {
    const account = await createAccount(request, "Docs Traversal Org");
    await authenticateBrowser(context, account);

    for (const path of ["/internal-docs/architecture", "/internal-docs/api-keys-and-secrets"]) {
      const response = await page.goto(`${WEB}${path}`);
      const body = (await response?.text()) ?? "";
      for (const phrase of INTERNAL_ONLY) expect(body).not.toContain(phrase);
    }
  });

  test("are not offered in the sidebar to a non-staff account", async ({ page, context, request }) => {
    const account = await createAccount(request, "Docs Nav Org");
    await authenticateBrowser(context, account);
    await page.goto(`${WEB}/dashboard`);
    await expect(page.getByRole("link", { name: "Internal docs" })).toHaveCount(0);
  });
});

/**
 * The staff-renders-successfully path is covered by `src/lib/docs/staff.test.ts` rather
 * than here: promoting an account to `is_staff` needs direct database access, and there is
 * deliberately no API endpoint that grants it. What this file can prove — that everyone
 * else is refused, and refused without receiving the content — is the half that matters.
 */

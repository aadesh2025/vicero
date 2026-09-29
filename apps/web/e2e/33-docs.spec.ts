import { test, expect } from "@playwright/test";
import { WEB, createAccount, authenticateBrowser } from "./helpers";

/**
 * The documentation site: public pages readable signed out, and the private admin area
 * (`/vault`, its own sign-in) closed to everyone without its own session.
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

  test("list customer-facing endpoints only, never key or credential management", async ({ page }) => {
    await page.goto(`${WEB}/docs/api/reference`);
    await expect(page.getByRole("heading", { name: "Endpoint reference", level: 1 })).toBeVisible();
    const body = await page.content();
    expect(body).toContain("/v1/agents");
    // The public reference is an allow-list: nothing that manages keys, credentials or
    // sessions, no platform routes, and none of the internal receivers.
    for (const hidden of [
      "/v1/admin",
      "/v1/mcp",
      "/v1/apikeys",
      "/v1/credentials",
      "/v1/auth",
      "/v1/orgs",
      "/v1/channels",
      "/v1/tools/n8n/callback",
    ]) {
      expect(body, `public reference leaked ${hidden}`).not.toContain(hidden);
    }
  });
});

test.describe("API authentication page", () => {
  test("explains authentication with placeholders only — never a real key", async ({ page }) => {
    await page.goto(`${WEB}/docs/api/authentication`);
    // `exact`: "How authentication works" would otherwise match by substring too.
    await expect(page.getByRole("heading", { name: "Authentication", level: 2, exact: true })).toBeVisible();
    await expect(page.getByText("Authorization: Bearer YOUR_API_KEY").first()).toBeVisible();

    const html = await page.content();
    expect(html).toContain("YOUR_API_KEY");
    // No internal key format or header, no key-management routes, nothing key-shaped in a URL.
    expect(html).not.toMatch(/bf_[A-Za-z0-9]/);
    expect(html).not.toMatch(/x-api-key/i);
    expect(html).not.toContain("/v1/apikeys");
    expect(page.url()).not.toMatch(/[?&](key|token|api_?key|secret)=/i);
  });

  test("shows cURL, JavaScript and Python, each with the placeholder", async ({ page }) => {
    await page.goto(`${WEB}/docs/api/authentication`);
    const tabs = page.getByRole("tablist", { name: "Language" });
    await expect(tabs.getByRole("tab")).toHaveText(["cURL", "JavaScript", "Python"]);
    for (const [name, needle] of [
      ["cURL", "curl https://YOUR_API_HOST/v1/agents"],
      ["JavaScript", "fetch("],
      ["Python", "requests.get("],
    ] as const) {
      await tabs.getByRole("tab", { name }).click();
      const panel = page.getByRole("tabpanel");
      await expect(panel).toContainText(needle);
      await expect(panel).toContainText("YOUR_API_KEY");
    }
  });

  test("the copy button copies the example as displayed, placeholder included", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(`${WEB}/docs/api/authentication`);

    for (const name of ["cURL", "JavaScript", "Python"]) {
      await page.getByRole("tab", { name }).click();
      const shown = (await page.getByRole("tabpanel").locator("pre").innerText()).trim();
      await page.getByRole("button", { name: `Copy ${name} example` }).click();
      // Windows hands clipboard text back with CRLF line breaks; the content is otherwise identical.
      const copied = (await page.evaluate(() => navigator.clipboard.readText())).replaceAll("\r\n", "\n");
      expect(copied.trim()).toBe(shown);
      expect(copied).toContain("YOUR_API_KEY");
      expect(copied).not.toMatch(/bf_/);
    }
  });

  test("leaves nothing in the reader's browser storage", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(`${WEB}/docs/api/authentication`);
    await page.getByRole("tab", { name: "Python" }).click();
    await page.getByRole("button", { name: "Copy Python example" }).click();

    const stored = await page.evaluate(() => ({
      session: Object.entries(sessionStorage),
      // `theme` is the light/dark preference — the only thing this site ever stores.
      local: Object.entries(localStorage).filter(([k]) => k !== "theme"),
    }));
    expect(stored.session).toEqual([]);
    expect(stored.local).toEqual([]);
  });

  test("links to the existing key-management page and does not duplicate it", async ({ page }) => {
    await page.goto(`${WEB}/docs/api/authentication`);
    const cta = page.getByRole("link", { name: "Manage API keys" }).first();
    await expect(cta).toHaveAttribute("href", "/settings/api-keys");
    // Nothing on this page creates, reveals or rotates a key.
    await expect(page.getByRole("button", { name: /create|reveal|show|rotate|regenerate/i })).toHaveCount(0);
  });

  test("shows the security note", async ({ page }) => {
    await page.goto(`${WEB}/docs/api/authentication`);
    await expect(page.getByText("Keep your API key secret")).toBeVisible();
    await expect(page.getByText(/secure server-side environment variable or\s+secret manager/)).toBeVisible();
  });
});

test.describe("private admin area", () => {
  test("sends a signed-out visitor to its own login page, not Vicero's", async ({ page }) => {
    const response = await page.goto(`${WEB}/vault`);
    await expect(page).toHaveURL(/\/vault\/login$/);
    await expect(page.getByRole("heading", { name: "Private area" })).toBeVisible();
    // Its own door: no Vicero dashboard chrome, and it must not redirect to /login.
    await expect(page).not.toHaveURL(/\/login\?/);
    const body = (await response?.text()) ?? "";
    for (const phrase of INTERNAL_ONLY) expect(body).not.toContain(phrase);
  });

  test("refuses a deep link without a session, and sends no content", async ({ page }) => {
    for (const path of ["/vault/architecture", "/vault/env", "/vault/api-keys-and-secrets"]) {
      const response = await page.goto(`${WEB}${path}`);
      await expect(page).toHaveURL(/\/vault\/login$/);
      const body = (await response?.text()) ?? "";
      for (const phrase of INTERNAL_ONLY) expect(body).not.toContain(phrase);
    }
  });

  test("a signed-in Vicero account gets nothing from it", async ({ page, context, request }) => {
    // The two logins must be unrelated: a real, valid Vicero session — even an owner's —
    // is worth nothing at this door.
    const account = await createAccount(request, "Vault Isolation Org");
    await authenticateBrowser(context, account);

    const response = await page.goto(`${WEB}/vault`);
    await expect(page).toHaveURL(/\/vault\/login$/);
    const body = (await response?.text()) ?? "";
    for (const phrase of INTERNAL_ONLY) expect(body).not.toContain(phrase);
  });

  test("the reveal endpoint refuses a Vicero session outright", async ({ context, request }) => {
    const account = await createAccount(request, "Vault Reveal Org");
    await authenticateBrowser(context, account);
    const res = await context.request.post(`${WEB}/api/vault/reveal`, { data: { name: "SECRET_KEY" } });
    expect(res.status()).toBe(401);
    expect(await res.text()).not.toContain("SECRET");
  });

  test("rejects a wrong password with the same message whoever it is for", async ({ page }) => {
    await page.goto(`${WEB}/vault/login`);
    // Unconfigured servers say so instead of showing a form; both outcomes are safe, and
    // what must never happen is a successful sign-in with made-up credentials.
    const form = page.getByLabel("Admin email");
    if (await form.count()) {
      await form.fill("nobody@example.com");
      await page.getByLabel("Password").fill("definitely-not-the-password");
      await page.getByRole("button", { name: "Sign in" }).click();
      // Not `getByRole("alert")`: Next's route announcer is also role=alert, so that
      // matches two elements and fails strict mode (or, worse, passes on the empty one).
      await expect(page.getByText("Invalid email or password.")).toBeVisible();
      await expect(page).toHaveURL(/\/vault\/login$/);
    } else {
      await expect(page.getByText("Not set up on this server")).toBeVisible();
    }
  });

  test("is not offered anywhere in the Vicero dashboard", async ({ page, context, request }) => {
    const account = await createAccount(request, "Docs Nav Org");
    await authenticateBrowser(context, account);
    await page.goto(`${WEB}/dashboard`);
    await expect(page.getByRole("link", { name: /internal docs|private|vault/i })).toHaveCount(0);
  });
});

/**
 * The signed-in path is covered by `src/app/api/vault/vault-routes.test.ts`, not here: it needs
 * a real password hash in the web server's environment, and this suite deliberately runs
 * without any. What this file proves — the door is locked to everyone without the vault's own
 * session, a Vicero session included, and nothing leaks while it is — is the half a real
 * browser can show.
 */

import { expect, test } from "@playwright/test";
import { API, WEB, uniqueEmail } from "./helpers";

/** Signup and workspace creation, as the UI presents them.
 *
 * Self-serve (docs/18) is on in production: signing up provisions one trial workspace. The
 * E2E API deliberately runs with `SELF_SERVE_ENABLED=false` so every other spec keeps
 * bootstrapping its own tenants through the API, so what these cover is the surface: all four
 * sign-in methods are offered, and an account with no workspace is asked to name one and — on a
 * server that only provisions workspaces for clients — ends on the invitation message rather than
 * an error. The server-side rules (one workspace, the trial clock, 402s) are pinned by
 * `tests/test_self_serve_*.py`.
 */

test("the login page offers every sign-in method and a way to sign up", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with Facebook" })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
  await expect(page.getByRole("button", { name: /email me a sign-in link/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /forgot password/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /start your free trial/i })).toHaveAttribute("href", "/signup");
});

test("the signup page states the trial and offers every method", async ({ page }) => {
  await page.goto("/signup");

  await expect(page.getByRole("heading", { name: /start your free trial/i })).toBeVisible();
  await expect(page.getByText(/10 days, 500 messages/i)).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with Facebook" })).toBeVisible();
  await expect(page.getByRole("button", { name: /create account/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /email link instead/i })).toBeVisible();
});

test("an emailed link with no token says so instead of spinning", async ({ page }) => {
  for (const path of ["/verify-email", "/magic", "/oauth/verify"]) {
    await page.goto(path);
    // (Next also renders a route-announcer with role=alert, so match on the text.)
    await expect(page.getByText(/missing its token/i)).toBeVisible();
  }
});

test("forgot-password answers the same way for any address", async ({ page }) => {
  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill(uniqueEmail("nobody"));
  await page.getByRole("button", { name: /send reset link/i }).click();
  await expect(page.getByRole("status")).toContainText(/if that address has an account/i);
});

test("a signed-up account with no workspace is asked to name one", async ({
  page,
  context,
  request,
}) => {
  // Straight through the API: the point is the state a signed-up, org-less account lands in.
  const email = uniqueEmail("stray");
  const signup = await request.post(`${API}/v1/auth/signup`, {
    data: { email, password: "e2e-Password-123", full_name: "Stray Person" },
  });
  expect(signup.ok(), `signup failed: ${signup.status()}`).toBeTruthy();
  const auth = await signup.json();

  const host = new URL(WEB).hostname;
  await context.addCookies([
    { name: "bf_access", value: auth.access_token, domain: host, path: "/", sameSite: "Lax" },
    { name: "bf_refresh", value: auth.refresh_token, domain: host, path: "/", sameSite: "Lax" },
  ]);

  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: /name your workspace/i })).toBeVisible();

  // This API runs with the test bootstrap (ALLOW_SELF_SERVE_ORGS), so the workspace is created
  // and the app loads. Against a client-provisioned server the same click is refused
  // (`orgs.create_forbidden`) and the screen falls back to naming the address an invitation has
  // to be sent to (`tests/test_org_creation_gate.py`).
  await page.getByRole("button", { name: /create workspace/i }).click();
  await expect(page.getByRole("heading", { name: /name your workspace/i })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /no workspace yet/i })).toHaveCount(0);
});

test("an invited newcomer still gets in — the one legitimate route", async ({ page, context, request }) => {
  // Staff-created host org. The E2E API runs with ALLOW_SELF_SERVE_ORGS so the suite can
  // bootstrap tenants; the production gate itself is covered by the backend suite.
  const ownerEmail = uniqueEmail("host");
  const owner = await request.post(`${API}/v1/auth/signup`, {
    data: { email: ownerEmail, password: "e2e-Password-123", full_name: "Host" },
  });
  const ownerAuth = await owner.json();
  const orgRes = await request.post(`${API}/v1/orgs`, {
    headers: { Authorization: `Bearer ${ownerAuth.access_token}` },
    data: { name: "Invite Host Workspace" },
  });
  expect(orgRes.ok(), `create org failed: ${orgRes.status()} ${await orgRes.text()}`).toBeTruthy();
  const org = await orgRes.json();

  const inviteeEmail = uniqueEmail("newcomer");
  const invite = await request.post(`${API}/v1/orgs/${org.id}/invitations`, {
    headers: { Authorization: `Bearer ${ownerAuth.access_token}`, "X-Org-Id": org.id },
    data: { email: inviteeEmail, role: "editor" },
  });
  expect(invite.ok(), `invite failed: ${invite.status()}`).toBeTruthy();
  const token = (await invite.json()).accept_token as string;
  expect(token, "dev API should expose accept_token").toBeTruthy();

  // Brand-new person, no account yet: signs up from the invite page itself.
  await page.goto(`/invitations/accept?token=${token}`);
  await page.getByLabel(/email/i).fill(inviteeEmail);
  await page.getByLabel(/password/i).fill("e2e-Password-123");
  await page.getByRole("button", { name: /create account & join/i }).click();

  await page.waitForURL("**/dashboard", { timeout: 20_000 });
  // They landed in the org, not on the dead end.
  await expect(page.getByRole("heading", { name: /no workspace yet/i })).toHaveCount(0);

  const cookies = await context.cookies();
  expect(cookies.find((c) => c.name === "bf_org")?.value).toBe(org.id);
});

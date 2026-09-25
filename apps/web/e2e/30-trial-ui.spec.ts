import { expect, test, type Page } from "@playwright/test";
import { authenticateBrowser, createAccount, type Account } from "./helpers";

/** What a trial workspace looks like: the meter, the ended banner, locked features, upgrade page.
 *
 * The plan endpoint is stubbed with `page.route`, because the E2E API runs staff-provisioned
 * (unmetered) workspaces so every other spec keeps working. That is the right seam: this file
 * checks how the dashboard *draws* a plan; whether the server actually enforces one is covered
 * by `tests/test_self_serve_*.py` (HTTP 402s, the counter, the silent stop).
 */

const TRIAL = {
  plan: "trial",
  status: "trial",
  trial_ends_at: "2099-01-01T00:00:00Z",
  days_left: 4,
  expired_reason: null,
  messages_used: 312,
  messages_limit: 500,
  messages_remaining: 188,
  unanswered_messages: 0,
  agents_used: 1,
  max_agents: 1,
  can_create_agent: false,
  features: { workflows: false, n8n: false, tool_calling: false },
};

const ENDED = {
  ...TRIAL,
  status: "trial_expired",
  expired_reason: "time",
  days_left: 0,
  unanswered_messages: 7,
};

async function stubPlan(page: Page, body: object) {
  await page.route("**/v1/orgs/*/plan", (route) => route.fulfill({ json: body }));
}

let account: Account;

test.beforeAll(async ({ request }) => {
  account = await createAccount(request, "Trial UI Org");
});

test.beforeEach(async ({ context }) => {
  await authenticateBrowser(context, account);
});

test("a running trial shows the usage meter and an upgrade button on every page", async ({ page }) => {
  await stubPlan(page, TRIAL);
  await page.goto("/dashboard");
  await expect(page.getByTestId("trial-meter")).toContainText("312 / 500 messages · 4 days left");
  await page.goto("/agents");
  await expect(page.getByTestId("trial-meter")).toBeVisible();
  await page.getByTestId("trial-meter").getByRole("link", { name: "Upgrade" }).click();
  await expect(page).toHaveURL(/\/billing\/upgrade$/);
  await expect(page.getByRole("heading", { name: /plans coming soon/i })).toBeVisible();
});

test("an ended trial shows the persistent banner and how many visitors went unanswered", async ({ page }) => {
  await stubPlan(page, ENDED);
  for (const path of ["/dashboard", "/agents", "/inbox"]) {
    await page.goto(path);
    await expect(page.getByTestId("trial-ended")).toContainText("Your free trial has ended. Upgrade to continue.");
    await expect(page.getByTestId("trial-ended")).toContainText("7 visitor messages were not answered");
  }
  await page.goto("/billing/upgrade");
  await expect(page.getByText(/7 visitor messages are/)).toBeVisible();
});

test("a workspace on an unmetered plan sees no trial UI at all", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByTestId("trial-meter")).toHaveCount(0);
  await expect(page.getByTestId("trial-ended")).toHaveCount(0);
});

test("locked features are shown with an upgrade path, not hidden", async ({ page }) => {
  await stubPlan(page, TRIAL);
  await page.goto("/automations");
  await expect(page.getByText("Automations are part of a paid plan")).toBeVisible();
  await expect(page.getByRole("link", { name: "Upgrade" }).first()).toBeVisible();
});

test("a trial at its agent limit gets a disabled, explained New agent button", async ({ page }) => {
  await stubPlan(page, TRIAL);
  await page.goto("/agents");
  const button = page.getByRole("button", { name: "New agent", exact: true });
  await expect(button).toBeDisabled();
  await page.getByLabel("Your free trial includes one agent.").hover();
  await expect(page.getByRole("tooltip").first()).toContainText("Upgrade");
});

test("the workspace switcher offers no way to add another workspace to a non-staff user", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByRole("button", { name: /trial ui org/i }).first().click();
  await expect(page.getByText(/new organization/i)).toHaveCount(0);
});

test("the admin console is not reachable for a normal user", async ({ page }) => {
  await page.goto("/admin");
  await page.waitForURL("**/dashboard");
  await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
});

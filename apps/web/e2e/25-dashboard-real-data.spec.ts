import { expect, test, type APIRequestContext } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, type Account } from "./helpers";

/**
 * The dashboard, profile page and versions tab render real tenant data — never the mock
 * fixtures they shipped with.
 *
 * The regression this guards: a brand-new org saw four invented agents ("Support
 * Concierge", "Sales Qualifier", "Docs Assistant", "Order Tracker") and five invented
 * conversations, because `dashboard/page.tsx` imported them from `lib/mock/data`. The
 * empty-state case is the important one — an org with nothing is exactly what the
 * fixtures used to paper over.
 *
 * Two tenants only, created once: the suite already brushes up against `AUTH_RATE_LIMIT`
 * on a full back-to-back run (see the roadmap note in docs/PROGRESS.md), so a spec that
 * signed up per test would make that worse.
 */

const FAKE_AGENTS = ["Support Concierge", "Sales Qualifier", "Docs Assistant", "Order Tracker"];

let emptyAccount: Account; // stays pristine: zero agents, zero conversations
let liveAccount: Account; // gets a real agent + a real conversation
let liveAgentId: string;

async function seedLiveOrg(request: APIRequestContext): Promise<void> {
  const created = await request.post(`${API}/v1/agents`, {
    headers: auth(liveAccount),
    data: { name: "Ledger Helper", description: "Handles invoice questions" },
  });
  expect(created.ok(), `create agent failed: ${await created.text()}`).toBeTruthy();
  liveAgentId = (await created.json()).id;

  const chat = await request.post(`${API}/v1/agents/${liveAgentId}/chat`, {
    headers: auth(liveAccount),
    data: { message: "How do I read my invoice?", stream: false },
  });
  expect(chat.ok(), `chat failed: ${await chat.text()}`).toBeTruthy();
}

test.beforeAll(async ({ request }) => {
  emptyAccount = await createAccount(request, "Empty Org");
  liveAccount = await createAccount(request, "Real Data Org");
  await seedLiveOrg(request);
});

test("a brand-new org sees real empty states, not mock agents or conversations", async ({
  page,
  context,
}) => {
  await authenticateBrowser(context, emptyAccount);
  await page.goto("/dashboard");

  // Both panels explain themselves rather than rendering a blank list. "No agents yet" also appears in the
  // analytics "By agent" breakdown, so scope the agent assertions to the "Your agents" panel.
  const agentsPanel = page
    .getByRole("heading", { name: "Your agents" })
    .locator("xpath=ancestor::div[contains(@class,'rounded-card')][1]");
  await expect(agentsPanel.getByText("No agents yet")).toBeVisible();
  await expect(page.getByRole("link", { name: /Create your first agent/ })).toBeVisible();
  await expect(page.getByText("No conversations yet")).toBeVisible();

  // None of the fixture data may appear.
  for (const name of FAKE_AGENTS) {
    await expect(page.getByText(name, { exact: false })).toHaveCount(0);
  }
  await expect(page.getByText(/My order hasn't shipped yet/)).toHaveCount(0);
  await expect(page.getByText(/volume pricing/)).toHaveCount(0);

  // The panel header counts the real total, which is zero.
  await expect(agentsPanel.getByText("0 total")).toBeVisible();
});

test("the dashboard shows the org's own agent and conversation once they exist", async ({
  page,
  context,
}) => {
  await authenticateBrowser(context, liveAccount);
  await page.goto("/dashboard");

  await expect(page.getByText("Ledger Helper").first()).toBeVisible();
  await expect(page.getByText("Handles invoice questions")).toBeVisible();
  await expect(page.getByText("1 total")).toBeVisible();
  // Scoped by href: the agent's name also appears in the conversations panel, as the
  // agent that answered.
  await expect(page.locator(`a[href="/agents/${liveAgentId}"]`)).toBeVisible();

  // The conversation panel picked up the real turn rather than staying empty.
  await expect(page.getByText("No conversations yet")).toHaveCount(0);
  await expect(page.getByText(/\d+ messages/).first()).toBeVisible();
});

test("the profile page shows the signed-in account and its real sessions", async ({
  page,
  context,
}) => {
  await authenticateBrowser(context, liveAccount);
  await page.goto("/settings/profile");

  // The real signed-in identity, not the fixture's hardcoded person.
  await expect(page.locator(`input[value="${liveAccount.email}"]`)).toBeVisible();
  await expect(page.locator('input[value="aadesh@aurozen.ai"]')).toHaveCount(0);
  await expect(page.locator('input[value="Aadesh Sree"]')).toHaveCount(0);

  // Real sessions replace the invented "Chrome · Windows / Coimbatore, IN" devices.
  await expect(page.getByText(/Coimbatore, IN/)).toHaveCount(0);
  await expect(page.getByText("This device")).toBeVisible();
});

test("the versions tab lists the agent's real version history", async ({ page, context }) => {
  await authenticateBrowser(context, liveAccount);
  await page.goto(`/agents/${liveAgentId}?tab=versions`);

  // A freshly created agent has exactly one draft version.
  await expect(page.getByText("v1").first()).toBeVisible();

  // The mock history had four versions with these notes.
  await expect(page.getByText(/Tightened refund policy wording/)).toHaveCount(0);
  await expect(page.getByText(/Added shipping KB/)).toHaveCount(0);
  await expect(page.getByText("v6")).toHaveCount(0);
  await expect(page.getByText("v7")).toHaveCount(0);
});

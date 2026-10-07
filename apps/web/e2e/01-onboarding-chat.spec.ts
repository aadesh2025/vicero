import { test, expect } from "@playwright/test";
import { API, createAccount, uniqueEmail } from "./helpers";

// PRD acceptance criterion 1: a new user gets from nothing to chatting with their own agent,
// driven entirely through the browser UI.
//
// The onboarding leg changed when self-serve signup was closed: organizations are staff-
// provisioned, so a brand-new client no longer creates one. They arrive on an invitation
// instead. The rest of the criterion — create an agent, Groq by default, chat with it — is
// unchanged, and the invite path is now the only way a new person legitimately gets in.
test("criterion 1: invited signup → create agent → chat", async ({ page, request }) => {
  const email = uniqueEmail("onboard");

  // The workspace is provisioned first, the way staff does it for a client.
  const host = await createAccount(request, "E2E Workspace");
  const invite = await request.post(`${API}/v1/orgs/${host.orgId}/invitations`, {
    headers: { Authorization: `Bearer ${host.access}`, "X-Org-Id": host.orgId },
    data: { email, role: "editor" },
  });
  expect(invite.ok(), `invite failed: ${invite.status()} ${await invite.text()}`).toBeTruthy();
  const token = (await invite.json()).accept_token as string;

  // Brand-new person: no account yet, signs up from the invitation link itself.
  await page.goto(`/invitations/accept?token=${token}`);
  await page.getByLabel(/your name/i).fill("E2E Onboarder");
  // The invite pre-fills the invited address and locks it (readonly), so assert it rather than type it.
  await expect(page.getByLabel(/email/i)).toHaveValue(email);
  await page.getByLabel(/password/i).fill("e2e-Password-123");
  await page.getByRole("button", { name: /create account & join/i }).click();

  // Land on the dashboard, inside the org they were invited to.
  await page.waitForURL("**/dashboard", { timeout: 20_000 });

  // Create an agent. Creation now starts with a role picker; this criterion is about the
  // plain onboarding path, so it takes the start-from-scratch option (templates have their
  // own spec).
  await page.goto("/agents");
  await page.getByRole("button", { name: /new agent/i }).first().click();
  await page.getByText("Start from scratch").click();
  await page.getByPlaceholder(/agent name/i).fill("Support Bot");
  await page.getByRole("button", { name: /create & configure/i }).click();

  // Builder loads for the new agent.
  await expect(page).toHaveURL(/\/agents\/[0-9a-f-]+/);

  // The default provider is Groq — the playground footer shows the model label.
  await expect(page.getByText(/Groq/i).first()).toBeVisible();

  // Chat in the playground (deterministic Fake provider echoes the message in CI).
  const composer = page.getByPlaceholder(/message the draft agent/i);
  await composer.fill("Ping E2E");
  await composer.press("Enter");

  await expect(page.getByText(/echo:\s*Ping E2E/i)).toBeVisible({ timeout: 20_000 });
});

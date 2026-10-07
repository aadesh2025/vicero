import { type APIRequestContext, type BrowserContext, type Page, expect } from "@playwright/test";

export const API = process.env.E2E_API_URL ?? "http://localhost:8000";
export const WEB = process.env.E2E_WEB_URL ?? "http://localhost:3001";

let seq = 0;
export function uniqueEmail(prefix = "e2e"): string {
  seq += 1;
  return `${prefix}.${Date.now()}.${seq}@example.com`;
}

export interface Account {
  email: string;
  password: string;
  access: string;
  refresh: string;
  userId: string;
  orgId: string;
}

/** Create a fresh user + organization straight through the API (fast bootstrap). */
export async function createAccount(request: APIRequestContext, orgName = "E2E Org"): Promise<Account> {
  const email = uniqueEmail();
  const password = "e2e-Password-123";
  const signup = await request.post(`${API}/v1/auth/signup`, {
    data: { email, password, full_name: "E2E User" },
  });
  expect(signup.ok(), `signup failed: ${signup.status()} ${await signup.text()}`).toBeTruthy();
  const auth = await signup.json();

  const orgRes = await request.post(`${API}/v1/orgs`, {
    headers: { Authorization: `Bearer ${auth.access_token}` },
    data: { name: orgName },
  });
  expect(orgRes.ok(), `create org failed: ${orgRes.status()} ${await orgRes.text()}`).toBeTruthy();
  const org = await orgRes.json();

  return {
    email,
    password,
    access: auth.access_token,
    refresh: auth.refresh_token,
    userId: auth.user?.id ?? auth.user_id ?? "",
    orgId: org.id,
  };
}

/** Inject the auth cookies the dashboard reads, so the browser is signed in without the UI. */
export async function authenticateBrowser(context: BrowserContext, account: Account): Promise<void> {
  const url = new URL(WEB);
  const base = { domain: url.hostname, path: "/", sameSite: "Lax" as const };
  await context.addCookies([
    { name: "bf_access", value: account.access, ...base },
    { name: "bf_refresh", value: account.refresh, ...base },
    { name: "bf_org", value: account.orgId, ...base },
  ]);
}

export function auth(account: Account): Record<string, string> {
  return { Authorization: `Bearer ${account.access}`, "X-Org-Id": account.orgId };
}

/** Create an agent + publish it, returning its ids and public key (for the widget). */
export async function createPublishedAgent(
  request: APIRequestContext,
  account: Account,
  opts: { name?: string; provider?: string } = {},
): Promise<{ id: string; publicKey: string }> {
  const create = await request.post(`${API}/v1/agents`, {
    headers: auth(account),
    data: { name: opts.name ?? "E2E Agent" },
  });
  expect(create.ok(), `create agent failed: ${create.status()} ${await create.text()}`).toBeTruthy();
  const agent = await create.json();

  // Set the model provider (defaults to groq already; explicit for clarity), then publish.
  await request.patch(`${API}/v1/agents/${agent.id}/versions/${agent.draft_version}`, {
    headers: auth(account),
    data: { model_config: { provider: opts.provider ?? "groq", model: "openai/gpt-oss-20b" } },
  });
  const pub = await request.post(`${API}/v1/agents/${agent.id}/versions/${agent.draft_version}/publish`, {
    headers: auth(account),
  });
  expect(pub.ok(), `publish failed: ${pub.status()} ${await pub.text()}`).toBeTruthy();

  return { id: agent.id, publicKey: agent.public_key };
}

/** Open the first conversation row in the inbox.
 *
 * Matches the ROW (its accessible name starts with the visitor id, "anon-…"), not `/Conversation/i`:
 * the inbox's "Conversations" tab is also a button and comes first, so the old selector clicked the tab,
 * no conversation opened, and the reply box / Take over / Run macro never appeared. */
export async function openFirstConversation(page: Page) {
  await page.getByRole("button", { name: /^(anon|visitor)/i }).first().click();
}

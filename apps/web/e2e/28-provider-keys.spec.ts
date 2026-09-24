import { expect, test, type APIRequestContext } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, type Account } from "./helpers";

/**
 * Adding a provider key in Settings is what makes a model selectable in the builder.
 *
 * Before this, the builder's Model tab read a hardcoded client-side catalogue and offered
 * every provider whether or not the org held a key — so the obvious way to configure an agent
 * was to pick one that could not answer, and it failed at runtime as a dead agent rather than
 * as a validation error.
 *
 * Agents are created through the API rather than the New-agent dialog: that flow has its own
 * spec (26) and re-driving it here would make this file fail for reasons unrelated to keys.
 *
 * One account for the whole file: the suite already brushes up against `AUTH_RATE_LIMIT` on a
 * full back-to-back run (see the roadmap note in docs/PROGRESS.md).
 */

let account: Account;

test.beforeAll(async ({ request }) => {
  account = await createAccount(request, "Provider Keys Org");
});

test.beforeEach(async ({ context }) => {
  await authenticateBrowser(context, account);
});

/** An agent pinned to one provider. `PATCH /versions/{number}` takes the version *number*. */
async function agentOn(
  request: APIRequestContext,
  provider: string,
  model: string,
): Promise<string> {
  const created = await request.post(`${API}/v1/agents`, {
    headers: auth(account),
    data: { name: `${provider} agent` },
  });
  const agent = await created.json();
  const patched = await request.patch(`${API}/v1/agents/${agent.id}/versions/1`, {
    headers: auth(account),
    data: {
      model_config: {
        provider,
        model,
        temperature: 0.7,
        top_p: 1.0,
        max_tokens: 1024,
        presence_penalty: 0.0,
        frequency_penalty: 0.0,
        stop: [],
      },
    },
  });
  expect((await patched.json()).model_config.provider).toBe(provider);
  return agent.id;
}

async function providerInfo(request: APIRequestContext, name: string) {
  const res = await request.get(`${API}/v1/credentials/providers`, { headers: auth(account) });
  return (await res.json()).find((p: { name: string }) => p.name === name);
}

test("a provider with no key is offered in Settings but not in the builder", async ({
  page,
  request,
}) => {
  expect((await providerInfo(request, "anthropic")).configured, "no key yet").toBeFalsy();
  const agentId = await agentOn(request, "groq", "openai/gpt-oss-120b");

  await page.goto("/settings/credentials");
  // Listed as something you *could* connect...
  await expect(page.getByRole("button", { name: "Anthropic provider key" })).toBeVisible();

  // ...but never as something an agent can be pointed at.
  await page.goto(`/agents/${agentId}?tab=model`);
  await expect(page.getByText("Provider & model")).toBeVisible();
  await expect(page.getByText("Anthropic", { exact: true })).toHaveCount(0);
});

test("saving a key makes the provider selectable in the builder", async ({ page, request }) => {
  await page.goto("/settings/credentials");

  await page.getByRole("button", { name: "Anthropic provider key" }).click();
  await page.getByLabel("API key").fill("sk-ant-e2e-key-1234");
  await page.getByRole("button", { name: "Save key" }).click();

  // The card moves into Connected, with only the tail of the key ever shown again.
  const card = page.getByRole("button", { name: "Anthropic provider key" });
  await expect(card.getByText("key saved")).toBeVisible();
  await expect(card.getByText(/1234$/)).toBeVisible();
  expect((await providerInfo(request, "anthropic")).key_source).toBe("org");

  // An agent on that provider now reports a usable key instead of a dead one.
  const agentId = await agentOn(request, "anthropic", "claude-sonnet-5");
  await page.goto(`/agents/${agentId}?tab=model`);
  await expect(page.getByText("Key configured")).toBeVisible();
});

test("removing the key does not silently rewrite an agent already using it", async ({
  page,
  request,
}) => {
  const agentId = await agentOn(request, "anthropic", "claude-sonnet-5");
  await request.delete(`${API}/v1/credentials/providers/anthropic`, { headers: auth(account) });

  await page.goto(`/agents/${agentId}?tab=model`);
  // Still on Anthropic, visibly broken rather than quietly moved to a provider nobody chose.
  await expect(page.getByText(/this agent cannot reply/i)).toBeVisible();

  const versions = await request.get(`${API}/v1/agents/${agentId}/versions`, {
    headers: auth(account),
  });
  expect((await versions.json())[0].model_config.provider).toBe("anthropic");
});

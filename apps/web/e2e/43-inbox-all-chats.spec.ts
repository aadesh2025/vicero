import { test, expect, type APIRequestContext } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, type Account } from "./helpers";

const TG_CHAT = "445566";

async function botChat(request: APIRequestContext, account: Account): Promise<void> {
  // An agent that never hands off: the conversation stays with the bot, so the default
  // "Needs a human" queue must not show it.
  const create = await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "All Chats Bot" } });
  expect(create.ok(), await create.text()).toBeTruthy();
  const agent = await create.json();
  const patch = await request.patch(`${API}/v1/agents/${agent.id}/versions/1`, {
    headers: auth(account),
    data: { features: { tools_enabled: false, memory_enabled: true, handoff_enabled: false } },
  });
  expect(patch.ok(), await patch.text()).toBeTruthy();

  const res = await request.post(`${API}/v1/channels`, {
    headers: auth(account),
    data: { agent_id: agent.id, type: "telegram", config: { bot_token: "123456:E2E" } },
  });
  expect(res.ok(), await res.text()).toBeTruthy();
  const channel = await res.json();
  const en = await request.post(`${API}/v1/channels/${channel.id}/enable`, { headers: auth(account) });
  expect(en.ok(), await en.text()).toBeTruthy();

  const inbound = await request.post(`${API}/v1/channels/telegram/${channel.id}/webhook`, {
    headers: { "x-telegram-bot-api-secret-token": channel.webhook_secret },
    data: {
      message: {
        chat: { id: Number(TG_CHAT) },
        from: { id: Number(TG_CHAT), first_name: "Priya", last_name: "N", username: "priyan" },
        text: "what are your opening hours?",
      },
    },
  });
  expect(inbound.status(), await inbound.text()).toBe(200);
}

test("inbox: 'All chats' shows bot-handled conversations; taking over hands them to a human", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "All Chats Org");
  await botChat(request, account);

  // API: hidden by default, listed with include_bot=true.
  const queue = await (await request.get(`${API}/v1/inbox/conversations`, { headers: auth(account) })).json();
  expect(queue).toHaveLength(0);
  const all = await (
    await request.get(`${API}/v1/inbox/conversations?include_bot=true`, { headers: auth(account) })
  ).json();
  expect(all).toHaveLength(1);
  expect(all[0].contact.display_name).toBe("Priya N");
  expect(all[0].contact.username).toBe("priyan");

  // UI: default is "Needs a human" and the bot chat is not in it.
  await authenticateBrowser(context, account);
  await page.goto("/inbox");
  const needs = page.getByRole("button", { name: "Needs a human", exact: true });
  const everything = page.getByRole("button", { name: "All chats", exact: true });
  await expect(needs).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /Priya N on Telegram/ })).toHaveCount(0);

  // "All chats": the row appears with the contact's name and @handle.
  await everything.click();
  await expect(everything).toHaveAttribute("aria-pressed", "true");
  const row = page.getByRole("button", { name: /Priya N on Telegram/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("@priyan");

  // Read-only until the operator takes over.
  await row.click();
  await page.getByRole("button", { name: /Take over/ }).click();

  // Taking over opened a handoff, so the chat is now in the default queue.
  await expect
    .poll(async () => {
      const items = await (await request.get(`${API}/v1/inbox/conversations`, { headers: auth(account) })).json();
      return items.length;
    })
    .toBe(1);
});

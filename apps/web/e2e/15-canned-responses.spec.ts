import { test, expect, type APIRequestContext } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, type Account, openFirstConversation } from "./helpers";

/** A handed-off widget conversation the operator has taken over, ready to reply in. */
async function takenOverConversation(request: APIRequestContext, account: Account) {
  const agent = await (
    await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "Canned Bot" } })
  ).json();
  await request.patch(`${API}/v1/agents/${agent.id}/versions/1`, {
    headers: auth(account),
    data: {
      fallback_message: "Connecting you to a teammate now.",
      features: { tools_enabled: false, memory_enabled: true, handoff_enabled: true },
    },
  });
  const chat = await request.post(`${API}/v1/public/agents/${agent.public_key}/chat`, {
    data: { message: "I want to talk to a human", stream: false },
  });
  const cid = (await chat.json()).conversation_id as string;
  await request.post(`${API}/v1/inbox/conversations/${cid}/takeover`, { headers: auth(account) });
  return cid;
}

test("canned responses are managed in settings and inserted by shortcut in the inbox", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Canned Org");
  await takenOverConversation(request, account);
  await authenticateBrowser(context, account);

  // ── Create one from the settings page ────────────────────────────────────────
  await page.goto("/settings/canned-responses");
  await expect(page.getByRole("heading", { name: "Canned responses" })).toBeVisible();

  await page.getByLabel("Shortcut").fill("refund");
  await page.getByLabel("Response text").fill("Your refund is on its way — 3-5 business days.");
  await page.getByRole("button", { name: "Add response" }).click();

  await expect(page.getByText("/refund")).toBeVisible();
  await expect(page.getByText("Your refund is on its way — 3-5 business days.")).toBeVisible();

  // A second one, to prove the picker filters rather than listing everything.
  await page.getByLabel("Shortcut").fill("greeting");
  await page.getByLabel("Response text").fill("Hi! Thanks for reaching out.");
  await page.getByRole("button", { name: "Add response" }).click();
  await expect(page.getByText("/greeting")).toBeVisible();

  // ── Use it in the inbox ──────────────────────────────────────────────────────
  await page.goto("/inbox");
  await openFirstConversation(page);

  const reply = page.getByLabel("Reply as an operator");
  await expect(reply).toBeVisible();

  // No picker until the trigger is typed.
  await expect(page.getByRole("listbox", { name: "Canned responses" })).toHaveCount(0);

  await reply.fill("/ref");
  const listbox = page.getByRole("listbox", { name: "Canned responses" });
  await expect(listbox).toBeVisible();
  // Filtered to the match — "greeting" doesn't start with "ref".
  await expect(listbox.getByRole("option")).toHaveCount(1);

  await listbox.getByRole("option").first().click();

  // The trigger text is replaced by the content, not appended to it.
  await expect(reply).toHaveValue("Your refund is on its way — 3-5 business days.");
  await expect(page.getByRole("listbox", { name: "Canned responses" })).toHaveCount(0);

  // And it sends as an ordinary operator reply.
  await page.getByRole("button", { name: "Send reply" }).click();
  await expect(page.getByText("Your refund is on its way — 3-5 business days.").last()).toBeVisible();
});

test("a slash inside a word does not open the picker", async ({ page, context, request }) => {
  const account = await createAccount(request, "Canned Trigger Org");
  await takenOverConversation(request, account);
  await request.post(`${API}/v1/canned-responses`, {
    headers: auth(account),
    data: { shortcut: "refund", content: "Refunded." },
  });

  await authenticateBrowser(context, account);
  await page.goto("/inbox");
  await openFirstConversation(page);

  const reply = page.getByLabel("Reply as an operator");
  // A URL is the everyday case that would misfire a naive "contains /" trigger.
  await reply.fill("see example.com/ref");
  await expect(page.getByRole("listbox", { name: "Canned responses" })).toHaveCount(0);

  // After whitespace it does fire, so mid-sentence use still works.
  await reply.fill("see example.com and /ref");
  await expect(page.getByRole("listbox", { name: "Canned responses" })).toBeVisible();
});

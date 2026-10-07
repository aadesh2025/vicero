import { test, expect, type APIRequestContext } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, type Account, openFirstConversation } from "./helpers";

async function takenOverConversation(request: APIRequestContext, account: Account) {
  const agent = await (
    await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "Macro Bot" } })
  ).json();
  await request.patch(`${API}/v1/agents/${agent.id}/versions/1`, {
    headers: auth(account),
    data: {
      fallback_message: "Connecting you to a teammate now.",
      features: { tools_enabled: false, memory_enabled: true, handoff_enabled: true },
    },
  });
  const chat = await request.post(`${API}/v1/public/agents/${agent.public_key}/chat`, {
    data: { message: "I want a human agent", stream: false },
  });
  const cid = (await chat.json()).conversation_id as string;
  await request.post(`${API}/v1/inbox/conversations/${cid}/takeover`, { headers: auth(account) });
  return cid;
}

test("a macro is built in settings and runs every action from the inbox", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Macro Org");
  const cid = await takenOverConversation(request, account);
  await authenticateBrowser(context, account);

  // ── Build it ─────────────────────────────────────────────────────────────────
  await page.goto("/settings/macros");
  await expect(page.getByRole("heading", { name: "Macros" })).toBeVisible();

  await page.getByLabel("Macro name").fill("Refund and close");
  await page.getByRole("button", { name: "Send a reply" }).click();
  await page.getByLabel("Step 1 reply text").fill("Refunded — sorry about that.");
  await page.getByRole("button", { name: "Add a tag" }).click();
  await page.getByLabel("Step 2 tag").fill("refund");
  await page.getByRole("button", { name: "Resolve & close" }).click();

  await page.getByRole("button", { name: "Create macro" }).click();
  await expect(page.getByText("Refund and close")).toBeVisible();

  // ── Run it ───────────────────────────────────────────────────────────────────
  await page.goto("/inbox");
  await openFirstConversation(page);

  await page.getByRole("button", { name: "Run macro" }).click();
  await page.getByRole("menuitem", { name: "Refund and close" }).click();

  // Every step landed: the reply is in the transcript…
  await expect(page.getByText("Refunded — sorry about that.")).toBeVisible();

  // …and the tag + closed status are on the conversation.
  const detail = await (
    await request.get(`${API}/v1/inbox/conversations/${cid}`, { headers: auth(account) })
  ).json();
  expect(detail.handoff.tags).toEqual(["refund"]);
  expect(detail.status).toBe("closed");
});

test("an incomplete step blocks saving, so a macro can't be half-built", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Macro Validation Org");
  await authenticateBrowser(context, account);
  await page.goto("/settings/macros");

  await page.getByLabel("Macro name").fill("Missing tag");
  await page.getByRole("button", { name: "Add a tag" }).click();

  // The tag input is empty — saving is refused before the round trip.
  const save = page.getByRole("button", { name: "Create macro" });
  await expect(save).toBeDisabled();
  await expect(page.getByText("Every step needs its details filled in.")).toBeVisible();

  await page.getByLabel("Step 1 tag").fill("billing");
  await expect(save).toBeEnabled();
});

test("the Run macro control is hidden until the org has one", async ({ page, context, request }) => {
  const account = await createAccount(request, "No Macro Org");
  await takenOverConversation(request, account);
  await authenticateBrowser(context, account);

  await page.goto("/inbox");
  await openFirstConversation(page);

  await expect(page.getByRole("button", { name: /Take over|Hand back/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run macro" })).toHaveCount(0);
});

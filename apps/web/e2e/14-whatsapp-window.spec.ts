import { test, expect, type APIRequestContext } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, type Account } from "./helpers";

// Outside Meta's 24-hour customer-service window a free-form WhatsApp message is silently
// dropped and the operator never finds out. The composer must refuse it up front and offer
// the only thing that will actually arrive: an approved template.
//
// The closed-window case is reached by rewriting `send_window` on the detail response
// rather than by aging a row: the API deliberately has no endpoint for back-dating a
// conversation, and a test-only mutation route is a backdoor that would ship. The real
// aging behaviour — refusal, nothing persisted, Meta's 131047 — is covered in
// apps/api/tests/test_whatsapp_window.py, which has direct DB access.

async function setup(request: APIRequestContext, account: Account) {
  const agent = await (
    await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "Wa Bot" } })
  ).json();
  await request.patch(`${API}/v1/agents/${agent.id}/versions/1`, {
    headers: auth(account),
    data: {
      fallback_message: "Connecting you to a teammate now.",
      features: { tools_enabled: false, memory_enabled: true, handoff_enabled: true },
    },
  });
  const ch = await (
    await request.post(`${API}/v1/channels`, {
      headers: auth(account),
      data: {
        agent_id: agent.id,
        type: "whatsapp",
        // No app_secret, so the webhook signature check is skipped in this environment.
        // A phone number belongs to one channel (unique per type), so every run needs its own id.
        config: {
          phone_number_id: `PN${Date.now()}${Math.floor(Math.random() * 1e6)}`,
          access_token: "tok",
          templates: "order_update,welcome_back",
        },
      },
    })
  ).json();
  await request.post(`${API}/v1/channels/${ch.id}/enable`, { headers: auth(account) });
  return { agent, channelId: ch.id as string };
}

async function inbound(request: APIRequestContext, channelId: string, text: string) {
  const res = await request.post(`${API}/v1/channels/whatsapp/${channelId}/webhook`, {
    data: {
      entry: [
        {
          changes: [
            {
              value: {
                contacts: [{ profile: { name: "Rohak" } }],
                messages: [{ from: "15551234", text: { body: text } }],
              },
            },
          ],
        },
      ],
    },
  });
  expect(res.status(), await res.text()).toBe(200);
}

/** Bootstrap a handed-off WhatsApp conversation and return its id. */
async function handedOffConversation(request: APIRequestContext, account: Account) {
  const { channelId } = await setup(request, account);
  await inbound(request, channelId, "I want to talk to a human");
  const cid = (
    await (await request.get(`${API}/v1/inbox/conversations`, { headers: auth(account) })).json()
  )[0].id as string;
  await request.post(`${API}/v1/inbox/conversations/${cid}/takeover`, { headers: auth(account) });
  return cid;
}

test("inside the window the operator gets a normal reply box", async ({ page, context, request }) => {
  const account = await createAccount(request, "Wa Window Org");
  await handedOffConversation(request, account);

  await authenticateBrowser(context, account);
  await page.goto("/inbox");
  await page.getByRole("button", { name: /Rohak/ }).first().click();

  // A real inbound just arrived, so the window is genuinely open.
  await expect(page.getByLabel("Reply as an operator")).toBeVisible();
  await expect(page.getByText(/24-hour reply window has closed/)).toHaveCount(0);
});

test("outside the window the reply box is replaced by a warning and a template picker", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Wa Closed Org");
  const cid = await handedOffConversation(request, account);

  await authenticateBrowser(context, account);

  // Serve the real detail payload with only the window flipped shut.
  await page.route(`**/v1/inbox/conversations/${cid}`, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.send_window = {
      open: false,
      closes_at: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString(),
      templates: ["order_update", "welcome_back"],
    };
    await route.fulfill({ response, json: body });
  });

  const templatePosts: Array<Record<string, unknown>> = [];
  await page.route(`**/v1/inbox/conversations/${cid}/template`, async (route) => {
    templatePosts.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, json: { id: "m1", role: "assistant", content: "[template: order_update]" } });
  });

  await page.goto("/inbox");
  await page.getByRole("button", { name: /Rohak/ }).first().click();

  // The free-text box is gone — typing into it would have silently failed.
  await expect(page.getByText(/The 24-hour reply window has closed/)).toBeVisible();
  await expect(page.getByLabel("Reply as an operator")).toHaveCount(0);

  // The channel's approved templates are offered instead.
  const picker = page.getByLabel("Approved template");
  await expect(picker).toBeVisible();
  await expect(picker.locator("option")).toHaveText(["order_update", "welcome_back"]);

  await picker.selectOption("welcome_back");
  await page.getByLabel("Template values, comma-separated").fill("Rohak, A-1");
  await page.getByRole("button", { name: "Send template" }).click();

  // Sends via the template endpoint, with the values split into positional params.
  await expect.poll(() => templatePosts.length).toBe(1);
  expect(templatePosts[0]).toEqual({ template: "welcome_back", params: ["Rohak", "A-1"] });
});

test("a channel with no approved templates says so instead of offering an empty picker", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Wa No Templates Org");
  const cid = await handedOffConversation(request, account);

  await authenticateBrowser(context, account);
  await page.route(`**/v1/inbox/conversations/${cid}`, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.send_window = { open: false, closes_at: null, templates: [] };
    await route.fulfill({ response, json: body });
  });

  await page.goto("/inbox");
  await page.getByRole("button", { name: /Rohak/ }).first().click();

  await expect(page.getByText(/No approved templates on this channel yet/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Send template" })).toHaveCount(0);
});

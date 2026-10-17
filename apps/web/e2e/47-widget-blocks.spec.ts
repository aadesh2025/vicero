import { test, expect, type Page, type Route } from "@playwright/test";
import { API, auth, createAccount, createPublishedAgent, type Account } from "./helpers";

// docs/27 Phase 2: interactive blocks in the real widget bundle. The agent is stubbed at the network
// edge (the chat stream and the public config), so each block type can be driven deterministically
// with no model; the server side of the protocol is covered by apps/api/tests/test_blocks.py.

const FLAGS = { enabled: true, options: true, cards: true, form: true, datetime: true, rating: true, counter: true, steps: true, handoff: true };

type Block = Record<string, unknown> & { id: string; type: string };

const BLOCKS: Record<string, Block> = {
  options: {
    id: "b-options", type: "options", multi: false,
    items: [{ id: "a", label: "Filter coffee", sub: "South Indian style" }, { id: "b", label: "Espresso" }],
    fallback_text: "Options: Filter coffee / Espresso",
  },
  cards: {
    id: "b-cards", type: "cards",
    items: [
      { id: "p1", title: "Attikan Estate", subtitle: "Jaggery, orange peel", price: "540", badge: "New", actions: [{ id: "go", label: "Add to cart" }] },
      { id: "p2", title: "Monsoon Malabar", price: "480", image_url: "https://example.invalid/missing.jpg", actions: [{ id: "go", label: "Add to cart" }] },
    ],
    fallback_text: "Items: Attikan Estate (540); Monsoon Malabar (480)",
  },
  form: {
    id: "b-form", type: "form", submit_label: "Confirm",
    fields: [
      { id: "name", label: "Name", type: "text", required: true },
      { id: "email", label: "Email", type: "email", required: true },
      { id: "phone", label: "Mobile", type: "tel", required: false },
    ],
    fallback_text: "Details requested: Name, Email, Mobile",
  },
  datetime: {
    id: "b-datetime", type: "datetime",
    days: [{ id: "2030-01-07", label: "Mon 7 Jan" }, { id: "2030-01-08", label: "Tue 8 Jan", disabled: true }, { id: "2030-01-09", label: "Wed 9 Jan" }],
    slots: [{ id: "s1", label: "10:00 am", sub: "6 left" }, { id: "s2", label: "11:30 am", disabled: true }],
    fallback_text: "Pick a day and time.",
  },
  counter: { id: "b-counter", type: "counter", label: "Guests", min: 1, max: 4, default: 2, fallback_text: "Guests (1-4)" },
  rating: {
    id: "b-rating", type: "rating", prompt: "How did we do?", reasons_good: ["Fast", "Friendly"], reasons_bad: ["Slow"],
    fallback_text: "How did we do? (1-5)",
  },
  steps: {
    id: "b-steps", type: "steps", eta: "Today 6 pm",
    steps: [{ label: "Packed", state: "done", time: "9:10" }, { label: "On the way", state: "now" }, { label: "Delivered", state: "todo" }],
    fallback_text: "Progress: Packed (done); On the way (in progress)",
  },
  handoff: { id: "b-handoff", type: "handoff", agent_name: "Priya", state: "joined", fallback_text: "Priya has joined the chat." },
};

function sse(events: unknown[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

/** The first chat turn answers with these stream events, every later turn with a plain "Got it."
 *  (a real agent does not repeat its block after being answered). Records what the widget posted. */
async function stubChat(page: Page, events: unknown[], posted: Record<string, unknown>[]) {
  await page.route("**/v1/public/agents/*/chat", async (route: Route) => {
    posted.push(route.request().postDataJSON());
    const turn = posted.length === 1 ? events : [{ type: "token", delta: "Got it." }];
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "access-control-allow-origin": "*" },
      body: sse([{ type: "conversation", conversation_id: "11111111-1111-4111-8111-111111111111" }, ...turn, { type: "done", finish_reason: "stop" }]),
    });
  });
}

/** Turn the feature on for a real agent's public config without touching the server. */
async function stubConfig(page: Page, theme: Record<string, unknown>) {
  await page.route("**/v1/public/agents/*/config", async (route) => {
    const res = await route.fetch();
    const json = await res.json();
    json.theme = { ...json.theme, ...theme };
    await route.fulfill({ response: res, json });
  });
}

async function embed(page: Page, publicKey: string) {
  await page.goto("/login");
  await page.evaluate(
    ([key, api]) => {
      const s = document.createElement("script");
      s.src = "/widget.js";
      s.setAttribute("data-agent", key);
      s.setAttribute("data-api", api);
      document.body.appendChild(s);
    },
    [publicKey, API],
  );
  await expect(page.locator(".bf-launcher")).toBeVisible({ timeout: 15_000 });
}

async function say(page: Page, text: string) {
  await page.locator(".bf-ta").fill(text);
  await page.locator(".bf-send").click();
}

test.describe("interactive blocks", () => {
  let account: Account;
  let agent: { id: string; publicKey: string };

  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, "Blocks Org");
    agent = await createPublishedAgent(request, account, { name: "Blocks Bot" });
  });

  test.beforeEach(async ({ request }) => {
    await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, {
      headers: auth(account),
      data: { widgetLayout: null, cornerRadius: null, density: null, interactiveBlocks: null },
    });
  });

  test("every block type renders from the stream", async ({ page }) => {
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(page, [{ type: "token", delta: "Here you go." }, ...Object.values(BLOCKS).map((b) => ({ type: "block", block: b }))], []);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "show me everything");
    await expect(page.locator(".bf-blk")).toHaveCount(8, { timeout: 10_000 });
    await expect(page.locator(".bf-b-options .bf-opt")).toHaveCount(2);
    await expect(page.locator(".bf-b-cards .bf-card")).toHaveCount(2);
    await expect(page.locator(".bf-b-form .bf-input")).toHaveCount(3);
    await expect(page.locator(".bf-b-datetime .bf-day")).toHaveCount(3);
    await expect(page.locator(".bf-b-datetime .bf-slot")).toHaveCount(2);
    await expect(page.locator(".bf-b-counter .bf-counter b")).toHaveText("2");
    await expect(page.locator(".bf-b-rating .bf-star")).toHaveCount(5);
    await expect(page.locator(".bf-b-steps li")).toHaveCount(3);
    await expect(page.locator(".bf-b-handoff b")).toHaveText("Priya");
    // The missing catalogue photo leaves its box (no layout jump) rather than a broken image.
    await expect(page.locator(".bf-b-cards .bf-art").nth(1)).toBeVisible();
  });

  test("options: a tap is sent as an ordinary user message with block_reply, then the block is read-only", async ({ page }) => {
    const posted: Record<string, unknown>[] = [];
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(page, [{ type: "token", delta: "Which?" }, { type: "block", block: BLOCKS.options }], posted);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "coffee please");
    await page.getByRole("button", { name: /Espresso/ }).click();

    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toMatchObject({ message: "Espresso", block_reply: { block_id: "b-options", value: "b" } });
    await expect(page.locator(".bf-user .bf-bubble").last()).toHaveText("Espresso");
    await expect(page.locator(".bf-opt.bf-on")).toHaveText("Espresso");
    await expect(page.locator(".bf-b-options")).toHaveClass(/bf-is-done/);
    for (const o of await page.locator(".bf-b-options .bf-opt").all()) await expect(o).toBeDisabled();
  });

  test("options are keyboard operable (arrows move, Enter picks)", async ({ page }) => {
    const posted: Record<string, unknown>[] = [];
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(page, [{ type: "block", block: BLOCKS.options }], posted);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "go");
    await page.locator(".bf-opt").first().focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.locator(".bf-opt").nth(1)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toMatchObject({ block_reply: { value: "b" } });
  });

  test("cards: the action button replies with the card and action", async ({ page }) => {
    const posted: Record<string, unknown>[] = [];
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(page, [{ type: "block", block: BLOCKS.cards }], posted);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "beans");
    await page.locator(".bf-card").first().getByRole("button", { name: "Add to cart" }).click();
    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toMatchObject({
      message: "Add to cart: Attikan Estate",
      block_reply: { block_id: "b-cards", value: { card: "p1", action: "go" } },
    });
  });

  test("form: validates inline, never submits invalid input, then locks", async ({ page }) => {
    const posted: Record<string, unknown>[] = [];
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(page, [{ type: "block", block: BLOCKS.form }], posted);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "book");
    const form = page.locator(".bf-b-form");

    await form.getByRole("button", { name: "Confirm" }).click();
    await expect(form.locator(".bf-err").first()).toHaveText("Required");
    expect(posted.length).toBe(1); // nothing was sent

    await form.locator('input[name="name"]').fill("Aadesh");
    await form.locator('input[name="email"]').fill("not-an-email");
    await form.getByRole("button", { name: "Confirm" }).click();
    await expect(form.locator(".bf-err").nth(1)).toHaveText("Enter a valid email address.");
    expect(posted.length).toBe(1);

    await form.locator('input[name="email"]').fill("a@b.co");
    await form.getByRole("button", { name: "Confirm" }).click();
    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toMatchObject({
      block_reply: { block_id: "b-form", value: { name: "Aadesh", email: "a@b.co", phone: "" } },
    });
    await expect(form.locator('input[name="name"]')).toBeDisabled();
  });

  test("datetime: a closed day or full slot cannot be picked; day then slot sends one reply", async ({ page }) => {
    const posted: Record<string, unknown>[] = [];
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(page, [{ type: "block", block: BLOCKS.datetime }], posted);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "when");
    await expect(page.getByRole("button", { name: "Tue 8 Jan" })).toBeDisabled();
    await expect(page.getByRole("button", { name: /11:30 am/ })).toBeDisabled();
    await page.getByRole("button", { name: "Wed 9 Jan" }).click();
    await page.getByRole("button", { name: /10:00 am/ }).click();
    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toMatchObject({
      message: "Wed 9 Jan, 10:00 am",
      block_reply: { block_id: "b-datetime", value: { day: "2030-01-09", slot: "s1" } },
    });
  });

  test("counter and rating reply with their value", async ({ page }) => {
    const posted: Record<string, unknown>[] = [];
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(page, [{ type: "block", block: BLOCKS.counter }, { type: "block", block: BLOCKS.rating }], posted);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "go");

    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("button", { name: "More" }).click(); // clamped at max 4
    await expect(page.locator(".bf-counter b")).toHaveText("4");
    await page.getByRole("button", { name: "Confirm" }).click();
    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toMatchObject({ message: "Guests: 4", block_reply: { block_id: "b-counter", value: 4 } });

    // The rating block, from the same turn, is still waiting.
    await page.getByRole("button", { name: "5 stars" }).click();
    await page.getByRole("button", { name: "Friendly" }).click();
    await page.locator(".bf-rate").getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => posted.length).toBe(3);
    expect(posted[2]).toMatchObject({
      message: "5/5 - Friendly",
      block_reply: { block_id: "b-rating", value: { stars: 5, reasons: ["Friendly"] } },
    });
  });

  test("a withdrawn turn takes its blocks with it, and an unknown type degrades to text", async ({ page }) => {
    await stubConfig(page, { interactive_blocks: FLAGS });
    await stubChat(
      page,
      [
        { type: "token", delta: "Sure, one moment." },
        { type: "block", block: BLOCKS.options },
        { type: "replace", delta: "Let me get a teammate." },
      ],
      [],
    );
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "hi");
    await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText("Let me get a teammate.");
    await expect(page.locator(".bf-blk")).toHaveCount(0);

    await page.unroute("**/v1/public/agents/*/chat");
    await stubChat(
      page,
      [{ type: "block", block: { id: "z", type: "hologram", fallback_text: "Holographic thing: pick A or B" } }],
      [],
    );
    await say(page, "again");
    await expect(page.locator(".bf-bot .bf-bubble").last()).toHaveText("Holographic thing: pick A or B");
    await expect(page.locator(".bf-blk")).toHaveCount(0);
  });

  test("block text is never interpreted as markup", async ({ page }) => {
    await stubConfig(page, { interactive_blocks: FLAGS });
    const evil: Block = {
      id: "b-evil", type: "options", multi: false,
      items: [{ id: "a", label: "<img src=x onerror=window.__pwned=1>", sub: "<b>bold</b>" }],
      fallback_text: "x",
    };
    await stubChat(page, [{ type: "block", block: evil }], []);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "x");
    await expect(page.locator(".bf-opt")).toContainText("<img src=x onerror=window.__pwned=1>");
    expect(await page.locator(".bf-opt img, .bf-opt b").count()).toBe(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  });

  test("a reload restores the thread with answered blocks read-only", async ({ page }) => {
    const history: unknown[] = [];
    let historyCalls = 0;
    await stubConfig(page, { interactive_blocks: FLAGS });
    await page.route("**/conversations/*/messages**", async (route) => {
      historyCalls += 1;
      await route.fulfill({ json: history, headers: { "access-control-allow-origin": "*" } });
    });
    history.push(
      { role: "user", text: "coffee please", blocks: [] },
      {
        role: "assistant",
        text: "Which?",
        blocks: [{ ...BLOCKS.options, resolved: { value: "b", at: "2030-01-01T00:00:00Z" } }],
      },
      { role: "user", text: "Espresso", blocks: [] },
    );
    await page.goto("/login");
    await page.evaluate(
      ([key, api]) => {
        localStorage.setItem(`vicero:conv:${key}`, "22222222-2222-4222-8222-222222222222");
        const s = document.createElement("script");
        s.src = "/widget.js";
        s.setAttribute("data-agent", key);
        s.setAttribute("data-api", api);
        document.body.appendChild(s);
      },
      [agent.publicKey, API],
    );
    await expect(page.locator(".bf-launcher")).toBeVisible({ timeout: 15_000 });
    await page.locator(".bf-launcher").click();
    await expect(page.locator(".bf-user .bf-bubble").first()).toHaveText("coffee please");
    await expect(page.locator(".bf-opt.bf-on")).toHaveText("Espresso");
    await expect(page.locator(".bf-b-options")).toHaveClass(/bf-is-done/);
    await expect(page.locator(".bf-b-options .bf-opt").first()).toBeDisabled();
    expect(historyCalls).toBe(1);
  });

  test("an agent without blocks is untouched: no history call, no block CSS, no extra style", async ({ page }) => {
    let historyCalls = 0;
    await page.route("**/conversations/*/messages**", async (route) => {
      historyCalls += 1;
      await route.continue();
    });
    await page.goto("/login");
    await page.evaluate(
      ([key, api]) => {
        localStorage.setItem(`vicero:conv:${key}`, "33333333-3333-4333-8333-333333333333");
        const s = document.createElement("script");
        s.src = "/widget.js";
        s.setAttribute("data-agent", key);
        s.setAttribute("data-api", api);
        document.body.appendChild(s);
      },
      [agent.publicKey, API],
    );
    await expect(page.locator(".bf-launcher")).toBeVisible({ timeout: 15_000 });
    await page.locator(".bf-launcher").click();
    await say(page, "plain hello");
    await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo:\s*plain hello/i, { timeout: 20_000 });
    expect(historyCalls).toBe(0);
    expect(await page.evaluate(() => document.querySelector("#vicero-widget")!.shadowRoot!.querySelectorAll("style").length)).toBe(1);
    await expect(page.locator(".bf-blk")).toHaveCount(0);
  });

  test("blocks follow the active layout (console draws dashed cards, block layout thick outlines)", async ({ page, request }) => {
    await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, {
      headers: auth(account),
      data: { widgetLayout: "console", interactiveBlocks: FLAGS },
    });
    await stubChat(page, [{ type: "block", block: BLOCKS.cards }], []);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "go");
    await expect(page.locator(".bf-card").first()).toBeVisible();
    expect(await page.locator(".bf-card").first().evaluate((e) => getComputedStyle(e).borderTopStyle)).toBe("dashed");

    await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, { headers: auth(account), data: { widgetLayout: "block" } });
    await page.unroute("**/v1/public/agents/*/chat");
    await stubChat(page, [{ type: "block", block: BLOCKS.cards }], []);
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "go");
    await expect(page.locator(".bf-card").first()).toBeVisible();
    expect(await page.locator(".bf-card").first().evaluate((e) => getComputedStyle(e).borderTopWidth)).toBe("2px");
  });

  test("real API: the saved switch reaches the public config, and a plain turn still streams", async ({ page, request }) => {
    const r = await request.patch(`${API}/v1/agents/${agent.id}/widget-config`, {
      headers: auth(account),
      data: { interactiveBlocks: { enabled: true, cards: false } },
    });
    expect(r.ok(), await r.text()).toBeTruthy();
    const cfg = await (await request.get(`${API}/v1/public/agents/${agent.publicKey}/config`)).json();
    expect(cfg.theme.interactive_blocks).toMatchObject({ enabled: true, options: true, cards: false });

    const posted: unknown[] = [];
    page.on("request", (q) => q.url().endsWith("/chat") && posted.push(q.postDataJSON()));
    await embed(page, agent.publicKey);
    await page.locator(".bf-launcher").click();
    await say(page, "hello blocks");
    await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo:\s*hello blocks/i, { timeout: 20_000 });
    // An ordinary message carries no block_reply.
    expect(posted[0]).not.toHaveProperty("block_reply");
  });

  test("builder preview shows sample blocks for the enabled types only", async ({ page }) => {
    await page.goto("/widget-preview.html");
    await expect(page.locator(".bf-launcher")).toBeVisible();
    await page.evaluate((flags) => {
      window.postMessage(
        { type: "bf-preview-config", config: { name: "P", welcome_message: "Hi", suggested_prompts: [], theme: { interactive_blocks: flags, widget_layout: "pebble" } } },
        "*",
      );
    }, { enabled: true, options: true, cards: false, form: false, datetime: false, rating: false });
    await expect(page.locator(".bf-b-options")).toHaveCount(1);
    await expect(page.locator(".bf-b-cards")).toHaveCount(0);
    await expect(page.locator(".bf-panel")).not.toHaveClass(/bf-show/); // preview never force-opens
    await page.evaluate(() =>
      window.postMessage({ type: "bf-preview-config", config: { name: "P", welcome_message: "Hi", suggested_prompts: [], theme: {} } }, "*"),
    );
    await expect(page.locator(".bf-blk")).toHaveCount(0);
  });
});

test("builder preview never steals focus from the designer when sample blocks render", async ({ page }) => {
  await page.goto("/widget-preview.html");
  await expect(page.locator(".bf-launcher")).toBeVisible();
  await page.evaluate(() => {
    const input = document.createElement("input");
    input.id = "designer-field";
    document.body.appendChild(input);
    input.focus();
    window.postMessage(
      { type: "bf-preview-config", config: { name: "P", welcome_message: "Hi", suggested_prompts: [], theme: { interactive_blocks: { enabled: true } } } },
      "*",
    );
  });
  await page.waitForTimeout(300);
  await expect(page.locator(".bf-blk")).not.toHaveCount(0);
  expect(await page.evaluate(() => document.activeElement?.id)).toBe("designer-field");
});

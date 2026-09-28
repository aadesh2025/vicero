import { test, expect } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, createPublishedAgent } from "./helpers";

/**
 * The dashboard "Today" gauge (docs/20 §9.3.1, ADR-100): total conversations created today,
 * drawn against the 30-day peak. The resolved/handed-off/unanswered breakdown was removed
 * from the card per 2026-09-28 feedback, but `today_snapshot` still returns it and the
 * gauge's accessible name still reports the total.
 */
test("the Today card shows the real conversation count, in both themes", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Today Gauge Org");

  // Resolved by AI: a plain conversation, no handoff at all.
  const { id: plainAgent } = await createPublishedAgent(request, account, { name: "Support Bot" });
  await request.post(`${API}/v1/agents/${plainAgent}/chat`, {
    headers: auth(account),
    data: { message: "hello", stream: false },
  });

  // Handed to human + unanswered: a handoff-enabled agent, one taken over, one still waiting.
  const handoffAgent = await request.post(`${API}/v1/agents`, {
    headers: auth(account),
    data: { name: "Escalation Bot" },
  });
  const { id: haid, public_key: hkey } = await handoffAgent.json();
  await request.patch(`${API}/v1/agents/${haid}/versions/1`, {
    headers: auth(account),
    data: {
      model_config: { provider: "fake", model: "fake-1" },
      features: { tools_enabled: false, memory_enabled: true, handoff_enabled: true },
    },
  });
  const assigned = await request.post(`${API}/v1/public/agents/${hkey}/chat`, {
    data: { message: "I want to talk to a human", stream: false },
  });
  await request.post(`${API}/v1/inbox/conversations/${(await assigned.json()).conversation_id}/takeover`, {
    headers: auth(account),
  });
  await request.post(`${API}/v1/public/agents/${hkey}/chat`, {
    data: { message: "I want to talk to a human", stream: false },
  });

  await authenticateBrowser(context, account);

  for (const theme of ["light", "dark"] as const) {
    await page.addInitScript((t) => localStorage.setItem("theme", t), theme);
    await page.goto("/dashboard");

    const card = page.getByTestId("today-card");
    await expect(card.getByRole("heading", { name: "Today" })).toBeVisible();
    const gauge = card.getByRole("img", { name: /conversations today/i });
    await expect(gauge).toBeVisible();
    await expect(gauge).toHaveAccessibleName(/^3 conversations today/);
  }
});

test("an empty day shows the empty-arc state, not a blank card", async ({ page, context, request }) => {
  const account = await createAccount(request, "Quiet Today Org");
  await authenticateBrowser(context, account);
  await page.goto("/dashboard");

  const card = page.getByTestId("today-card");
  await expect(card.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(card.getByText("No chats yet today.")).toBeVisible();
  const gauge = card.getByRole("img", { name: /conversations today/i });
  await expect(gauge).toHaveAccessibleName(/^0 conversations today, out of a busiest day of 10/);
});

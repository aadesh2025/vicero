import { expect, test } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, createPublishedAgent } from "./helpers";

/** docs/17 Phase 2 gap-closure item 3: the canvas's run overlay for a PAST completed run, not
 * only a live/in-progress one (see ADR-077). */

test("reopening a past completed run shows its step overlay without a live poll", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Run History Org");
  const agent = await createPublishedAgent(request, account, { name: "History Agent" });

  const createWf = await request.post(`${API}/v1/agents/${agent.id}/workflows`, {
    headers: auth(account),
    data: { name: "History flow" },
  });
  const workflow = await createWf.json();

  const graph = {
    nodes: [
      { id: "s1", type: "start", position: { x: 0, y: 0 } },
      { id: "m1", type: "message", config: { content: "hi" }, position: { x: 0, y: 100 } },
      { id: "e1", type: "end", position: { x: 0, y: 200 } },
    ],
    edges: [
      { source: "s1", target: "m1" },
      { source: "m1", target: "e1" },
    ],
  };
  const version = await request.post(`${API}/v1/workflows/${workflow.id}/versions`, {
    headers: auth(account),
    data: { graph },
  });
  const versionBody = await version.json();
  await request.post(`${API}/v1/workflows/${workflow.id}/versions/${versionBody.version}/publish`, {
    headers: auth(account),
  });

  // Run it directly through the API and wait for it to FINISH before the canvas is ever opened,
  // proving the overlay is read from persisted WorkflowStep rows, not a live poll that happened to
  // still be running. The run endpoint answers "running" at once and the worker completes it a moment
  // later (it is only synchronous with CELERY_TASK_ALWAYS_EAGER, which CI does not set), so poll the
  // run list rather than assume the POST's own status.
  const run = await request.post(`${API}/v1/workflows/${workflow.id}/run`, {
    headers: auth(account),
    data: {},
  });
  expect(run.ok(), await run.text()).toBeTruthy();
  await expect
    .poll(
      async () => {
        const runs = await (
          await request.get(`${API}/v1/workflows/${workflow.id}/runs`, { headers: auth(account) })
        ).json();
        return runs[0]?.status;
      },
      { timeout: 30_000, message: "the workflow run should finish" },
    )
    .toBe("completed");

  await authenticateBrowser(context, account);
  await page.goto(`/agents/${agent.id}?tab=workflows`);
  await page.getByText("History flow").click();
  await expect(page.getByRole("heading", { name: "History flow" })).toBeVisible();

  // Select the past run from the history picker — no "Test run" click, no live polling.
  await page.getByText("View a past run…").click();
  await page.getByRole("option", { name: /completed/ }).click();

  await expect(page.getByText("Run: completed")).toBeVisible();
  // The message node picks up the "completed" overlay ring from the persisted steps — the
  // ring class lands on the WorkflowNode component's own root div, a descendant of React
  // Flow's `[data-testid="rf__node-*"]` wrapper, not that wrapper itself.
  await expect(page.locator('[data-testid^="rf__node-m1"] [class*="ring-success"]')).toBeVisible();
});

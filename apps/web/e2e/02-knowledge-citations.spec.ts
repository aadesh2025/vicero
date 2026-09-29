import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { API, auth, authenticateBrowser, createAccount, type Account } from "./helpers";

// PRD acceptance criterion 2: upload a PDF, see it become "ready", and the agent answers
// from it *with citations*. Ingestion runs inline in CI (CELERY_TASK_ALWAYS_EAGER) with the
// deterministic Fake embedding provider (LLM_FORCE_FAKE), so no worker or model pull is needed.
test("criterion 2: upload PDF → ready → grounded answer with citations", async ({ page, context, request }) => {
  const account: Account = await createAccount(request, "KB Org");
  await authenticateBrowser(context, account);

  // Create a knowledge base.
  const kbRes = await request.post(`${API}/v1/knowledge`, {
    headers: auth(account),
    data: { name: "Company Facts", embedding_provider: "fake", embedding_model: "fake" },
  });
  expect(kbRes.ok(), await kbRes.text()).toBeTruthy();
  const kb = await kbRes.json();

  // Upload the PDF fixture (contains the token ORANGE-FALCON-2049).
  const pdf = fs.readFileSync(path.join(__dirname, "fixtures", "vicero-facts.pdf"));
  const up = await request.post(`${API}/v1/knowledge/${kb.id}/documents/upload`, {
    headers: auth(account),
    multipart: {
      file: { name: "vicero-facts.pdf", mimeType: "application/pdf", buffer: pdf },
    },
  });
  expect(up.ok(), await up.text()).toBeTruthy();
  const doc = await up.json();

  // Poll until the document is "ready" (inline ingestion is effectively synchronous).
  await expect
    .poll(
      async () => {
        const r = await request.get(`${API}/v1/knowledge/documents/${doc.id}`, { headers: auth(account) });
        return (await r.json()).status;
      },
      { timeout: 30_000, intervals: [500, 1000, 2000] },
    )
    .toBe("ready");

  // The knowledge page renders the ingested document for the user.
  await page.goto(`/knowledge/${kb.id}`);
  await expect(page.getByText(/vicero-facts\.pdf/i)).toBeVisible({ timeout: 15_000 });

  // Build an agent that retrieves from this KB, then publish it.
  const agent = await (
    await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "Docs Bot" } })
  ).json();
  await request.patch(`${API}/v1/agents/${agent.id}/versions/${agent.draft_version}`, {
    headers: auth(account),
    // threshold 0 → retrieval is deterministic under fake embeddings (returns top_k).
    data: { rag_config: { enabled: true, knowledge_base_ids: [kb.id], top_k: 5, score_threshold: 0, hybrid: true } },
  });
  await request.post(`${API}/v1/agents/${agent.id}/versions/${agent.draft_version}/publish`, {
    headers: auth(account),
  });

  // Ask a grounded question; the answer must carry citations from the uploaded doc.
  const chat = await request.post(`${API}/v1/agents/${agent.id}/chat`, {
    headers: auth(account),
    data: { message: "What is the onboarding passphrase?", stream: false },
  });
  expect(chat.ok(), await chat.text()).toBeTruthy();
  const body = await chat.json();
  expect(Array.isArray(body.citations)).toBeTruthy();
  expect(body.citations.length, "expected at least one citation").toBeGreaterThan(0);
});

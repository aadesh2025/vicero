import { test, expect } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount, type Account } from "./helpers";

// Knowledge uploads (ADR-140) and whole-website import (ADR-139).
// - Several files can be chosen at once; each uploads and ends Ready.
// - The website dialog offers "Whole website" (default, with a page limit) or "This page only".
test("knowledge: multi-file upload and the website dialog", async ({ page, context, request }) => {
  const account: Account = await createAccount(request, "KB Upload Org");
  await authenticateBrowser(context, account);

  const kbRes = await request.post(`${API}/v1/knowledge`, {
    headers: auth(account),
    data: { name: "Uploads", embedding_provider: "fake", embedding_model: "fake" },
  });
  expect(kbRes.ok(), await kbRes.text()).toBeTruthy();
  const kb = await kbRes.json();

  await page.goto(`/knowledge/${kb.id}`);
  await expect(page.getByRole("button", { name: /Upload files/ })).toBeVisible({ timeout: 15_000 });

  // Two files in one pick.
  await page.locator('input[type="file"]').setInputFiles([
    { name: "pricing.txt", mimeType: "text/plain", buffer: Buffer.from("Starter plan costs 9999 rupees.") },
    { name: "hours.txt", mimeType: "text/plain", buffer: Buffer.from("Open Monday to Saturday, 10am to 7pm.") },
  ]);
  const rows = page.locator("tbody tr");
  await expect(rows.filter({ hasText: "pricing.txt" })).toBeVisible({ timeout: 20_000 });
  await expect(rows.filter({ hasText: "hours.txt" })).toBeVisible({ timeout: 20_000 });
  await expect(rows.filter({ hasText: "pricing.txt" }).getByText("Ready")).toBeVisible({ timeout: 30_000 });
  await expect(rows.filter({ hasText: "hours.txt" }).getByText("Ready")).toBeVisible({ timeout: 30_000 });

  // Website dialog: whole site by default, with a page limit; "This page only" hides the limit.
  await page.getByRole("button", { name: /Add a website/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("radio", { name: /Whole website/ })).toHaveAttribute("aria-checked", "true");
  await expect(dialog.getByLabel("Maximum pages")).toHaveValue("25");
  await dialog.getByRole("radio", { name: /This page only/ }).click();
  await expect(dialog.getByLabel("Maximum pages")).toBeHidden();
  await dialog.getByRole("radio", { name: /Whole website/ }).click();
  await dialog.getByLabel("Maximum pages").fill("10");
  await dialog.getByLabel("Website address").fill("https://example.com/");
  await dialog.getByRole("button", { name: /Crawl & ingest/ }).click();
  await expect(dialog).toBeHidden();

  // The import is one document named after the site. It may fail in CI (no outbound network);
  // what matters here is that it was created as a website import with the chosen limit.
  await expect(rows.filter({ hasText: "example.com (website)" })).toBeVisible({ timeout: 15_000 });
  const docs = await (await request.get(`${API}/v1/knowledge/${kb.id}/documents`, { headers: auth(account) })).json();
  const site = docs.find((d: { source_type: string }) => d.source_type === "website");
  expect(site?.crawl?.max_pages).toBe(10);
});

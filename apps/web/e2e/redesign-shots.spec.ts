import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { API, auth, authenticateBrowser, createAccount, createPublishedAgent } from "./helpers";

// docs/20 R0/R8 screenshot + contrast pass. Not part of the normal suite: it only runs when
// REDESIGN_SHOTS is set to the output folder name.
//
//   REDESIGN_SHOTS=before npx playwright test e2e/redesign-shots.spec.ts
//   REDESIGN_SHOTS=after  npx playwright test e2e/redesign-shots.spec.ts
//
// REDESIGN_ONLY=dashboard,inbox limits the pass to those route names (fast iteration).
//
// Writes var/redesign/<name>/<theme>/<route>.png at the repo root (git-ignored) and, for
// "after", fails on any colour-contrast violation in either theme.

const RUN = process.env.REDESIGN_SHOTS;
const OUT = path.resolve(__dirname, "../../../var/redesign", RUN ?? "none");
const THEMES = ["light", "dark"] as const;
const ONLY = process.env.REDESIGN_ONLY?.split(",").map((s) => s.trim()).filter(Boolean);

test.skip(!RUN, "set REDESIGN_SHOTS=before|after to run the redesign screenshot pass");
test.setTimeout(600_000);

async function setTheme(page: Page, theme: string) {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("theme", t);
    } catch {
      /* private mode — falls back to the default theme */
    }
  }, theme);
}

async function shoot(page: Page, theme: string, name: string, url: string) {
  if (ONLY && !ONLY.includes(name)) return;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  await page.waitForTimeout(400);
  const dir = path.join(OUT, theme);
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
  if (RUN === "after") {
    const results = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
    const bad = results.violations.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(" ")}`));
    fs.appendFileSync(
      path.join(OUT, "contrast.txt"),
      `${theme} ${name}: ${bad.length}\n${bad.slice(0, 15).map((b) => `  ${b}`).join("\n")}${bad.length ? "\n" : ""}`,
    );
  }
}

test("screenshot every route in both themes", async ({ browser, request }) => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, "contrast.txt"), "");

  // ---- seed one tenant with enough data that no page is an empty shell ----
  const account = await createAccount(request, "Redesign Org");
  const { id: agentId, publicKey } = await createPublishedAgent(request, account, { name: "Support Bot" });
  await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "Draft Sales Bot" } });
  for (const [vid, name, msg] of [
    ["v1", "Aadesh Kumar", "hello, aadesh@example.com"],
    ["v2", "Rohak Arya", "what does the pro plan cost? rohak@example.com"],
    ["v3", "Priya Nair", "I need a human, my order is late"],
  ] as const) {
    await request.post(`${API}/v1/public/agents/${publicKey}/chat`, {
      data: { message: msg, stream: false, visitor: { id: vid, name } },
    });
  }
  await request.post(`${API}/v1/help-articles`, {
    headers: auth(account),
    data: { agent_id: agentId, title: "Getting started", body_markdown: "# Hello\nWelcome.", published: true },
  });
  const kb = await (
    await request.post(`${API}/v1/knowledge`, {
      headers: auth(account),
      data: { name: "Company Facts", embedding_provider: "fake", embedding_model: "fake" },
    })
  ).json();
  const inbox = await (await request.get(`${API}/v1/inbox/conversations`, { headers: auth(account) })).json();
  const cid: string | undefined = Array.isArray(inbox) ? inbox[0]?.id : undefined;
  const contacts = await (await request.get(`${API}/v1/contacts`, { headers: auth(account) })).json();
  const contactId: string | undefined = (contacts.items ?? contacts)?.[0]?.id;

  const anon: [string, string][] = [
    ["login", "/login"],
    ["signup", "/signup"],
    ["forgot-password", "/forgot-password"],
    ["reset-password", "/reset-password?token=x"],
    ["verify-email", "/verify-email"],
    ["magic", "/magic"],
    ["docs-home", "/docs"],
    ["docs-api-reference", "/docs/api/reference"],
    ["docs-authentication", "/docs/api/authentication"],
    ["help-center-public", `/help/${publicKey}`],
    ["vault-login", "/vault/login"],
    ["invitation-accept", "/invitations/accept?token=x"],
  ];
  const authed: [string, string][] = [
    ["dashboard", "/dashboard"],
    ["agents", "/agents"],
    ["agent-detail", `/agents/${agentId}`],
    ["knowledge", "/knowledge"],
    ["knowledge-detail", `/knowledge/${kb.id}`],
    ["help-center-editor", "/knowledge/help-center"],
    ["automations", "/automations"],
    ["conversations", "/conversations"],
    ["inbox", "/inbox"],
    ...(cid ? ([["inbox-thread", `/inbox/${cid}`]] as [string, string][]) : []),
    ["contacts", "/contacts"],
    ...(contactId ? ([["contact-detail", `/contacts/${contactId}`]] as [string, string][]) : []),
    ["analytics", "/analytics"],
    ["billing-upgrade", "/billing/upgrade"],
    ["onboarding", "/onboarding"],
    ["settings-profile", "/settings/profile"],
    ["settings-org", "/settings/org"],
    ["settings-api-keys", "/settings/api-keys"],
    ["settings-credentials", "/settings/credentials"],
    ["settings-webhooks", "/settings/webhooks"],
    ["settings-canned-responses", "/settings/canned-responses"],
    ["settings-macros", "/settings/macros"],
    ["settings-audit", "/settings/audit"],
    ["admin", "/admin"],
  ];

  for (const theme of THEMES) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await setTheme(page, theme);
    for (const [name, url] of anon) await shoot(page, theme, name, url);
    await authenticateBrowser(ctx, account);
    for (const [name, url] of authed) await shoot(page, theme, name, url);
    await ctx.close();
  }

  expect(fs.readdirSync(path.join(OUT, "light")).length).toBeGreaterThan(ONLY ? 0 : 20);
  if (RUN === "after") {
    const report = fs.readFileSync(path.join(OUT, "contrast.txt"), "utf8");
    expect(report.match(/: [1-9]\d*$/gm) ?? [], report).toEqual([]);
  }
});

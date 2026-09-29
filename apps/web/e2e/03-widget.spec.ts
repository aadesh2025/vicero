import { test, expect } from "@playwright/test";
import { API, createAccount, createPublishedAgent } from "./helpers";

// PRD acceptance criterion 3: embed the widget on a plain page and chat through it.
// We host it on the web origin (so the public API's CORS allows the fetch) and inject the
// widget <script> exactly as a site owner would, using the agent's real public key.
test("criterion 3: embedded widget streams a reply", async ({ page, request }) => {
  const account = await createAccount(request, "Widget Org");
  const { publicKey } = await createPublishedAgent(request, account, { name: "Widget Bot" });

  // A public page on the web origin acts as the "customer site".
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

  // Launcher mounts (Shadow-DOM isolated; Playwright pierces open shadow roots).
  const launcher = page.locator(".bf-launcher");
  await expect(launcher).toBeVisible({ timeout: 15_000 });
  await launcher.click();

  // Send a message through the widget composer.
  const composer = page.locator(".bf-ta");
  await composer.fill("Hello widget");
  await page.locator(".bf-send").click();

  // The bot bubble streams back the deterministic echo.
  await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo:\s*Hello widget/i, {
    timeout: 20_000,
  });
});

// Resuming a conversation now requires owning it (public.conversation_not_found otherwise), so
// the widget sends a visitor id it generates once and keeps in localStorage. Without that the
// API mints a throwaway anonymous id per request and the *second* message of every anonymous
// chat 404s — a break no unit test on the API side can see, because it depends on what the
// browser sends. See apps/api/tests/test_public_chat_hijack.py.
test("a second message continues the same conversation", async ({ page, request }) => {
  const account = await createAccount(request, "Widget Resume Org");
  const { publicKey } = await createPublishedAgent(request, account, { name: "Resume Bot" });

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

  const launcher = page.locator(".bf-launcher");
  await expect(launcher).toBeVisible({ timeout: 15_000 });
  await launcher.click();

  const composer = page.locator(".bf-ta");
  await composer.fill("first message");
  await page.locator(".bf-send").click();
  await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo:\s*first message/i, {
    timeout: 20_000,
  });

  const firstConversation = await page.evaluate(
    (key) => localStorage.getItem(`vicero:conv:${key}`),
    publicKey,
  );
  expect(firstConversation).toBeTruthy();

  await composer.fill("second message");
  await page.locator(".bf-send").click();
  await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(/echo:\s*second message/i, {
    timeout: 20_000,
  });

  // Same thread, not a new one silently started after a rejected resume.
  const secondConversation = await page.evaluate(
    (key) => localStorage.getItem(`vicero:conv:${key}`),
    publicKey,
  );
  expect(secondConversation).toBe(firstConversation);
  expect(
    await page.evaluate((key) => localStorage.getItem(`vicero:vid:${key}`), publicKey),
  ).toBeTruthy();
});

// A conversation id from another device (or one created before ownership was enforced) must
// not dead-end the visitor on "⚠ chat request failed (404)" — the widget drops it and starts a
// fresh thread.
test("a conversation id that isn't ours is discarded, not fatal", async ({ page, request }) => {
  const account = await createAccount(request, "Widget Stale Org");
  const { publicKey } = await createPublishedAgent(request, account, { name: "Stale Bot" });

  await page.goto("/login");
  await page.evaluate(
    ([key, api]) => {
      // Someone else's (well-formed but unowned) conversation, as a shared device would have.
      localStorage.setItem(`vicero:conv:${key}`, "00000000-0000-4000-8000-000000000000");
      const s = document.createElement("script");
      s.src = "/widget.js";
      s.setAttribute("data-agent", key);
      s.setAttribute("data-api", api);
      document.body.appendChild(s);
    },
    [publicKey, API],
  );

  const launcher = page.locator(".bf-launcher");
  await expect(launcher).toBeVisible({ timeout: 15_000 });
  await launcher.click();
  await page.locator(".bf-ta").fill("hello after a stale id");
  await page.locator(".bf-send").click();

  await expect(page.locator(".bf-bot .bf-bubble").last()).toContainText(
    /echo:\s*hello after a stale id/i,
    { timeout: 20_000 },
  );
  expect(
    await page.evaluate((key) => localStorage.getItem(`vicero:conv:${key}`), publicKey),
  ).not.toBe("00000000-0000-4000-8000-000000000000");
});

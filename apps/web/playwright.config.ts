import { defineConfig, devices } from "@playwright/test";

// Vicero E2E — drives the real compose stack (web + api + worker + postgres + redis).
// Services must already be running; CI boots them before invoking `playwright test`.
// The API is pinned to the deterministic Fake LLM provider via LLM_FORCE_FAKE=true so
// the suite needs no paid keys and no local model pulls (docs/10-TESTING.md §E2E).

const WEB_URL = process.env.E2E_WEB_URL ?? "http://localhost:3001";
const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // flows create tenants + poll ingestion; keep them serialized
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: WEB_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Make the API base reachable inside specs via testInfo / env.
    extraHTTPHeaders: {},
  },
  metadata: { apiUrl: API_URL, webUrl: WEB_URL },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

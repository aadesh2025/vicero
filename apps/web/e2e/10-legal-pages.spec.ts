import { test, expect } from "@playwright/test";

// Meta App Review needs these reachable by anyone: logged out, no cookies, rendered on the server.
const PAGES = [
  { path: "/privacy", h1: "Privacy Policy" },
  { path: "/terms", h1: "Terms of Service" },
  { path: "/data-deletion", h1: "Data Deletion Instructions" },
];

test.describe("public legal pages (logged out)", () => {
  for (const { path, h1 } of PAGES) {
    test(`${path} returns 200 and shows its h1`, async ({ page }) => {
      const res = await page.goto(path);
      expect(res?.status()).toBe(200);
      expect(new URL(page.url()).pathname).toBe(path);
      await expect(page.getByRole("heading", { level: 1, name: h1 })).toBeVisible();
    });
  }

  test("a data-deletion code that does not exist says so", async ({ page }) => {
    const res = await page.goto("/data-deletion?code=0000000000000000");
    expect(res?.status()).toBe(200);
    await expect(page.getByRole("status")).toContainText(/no request was found|could not check/i);
  });
});

import { test, expect } from "@playwright/test";
import { API, auth, authenticateBrowser, createAccount } from "./helpers";

// A 1x1 PNG. The server re-encodes every upload, so anything decodable will do.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test("create a product, upload a photo, see the thumbnail, and turn the catalog on for an agent", async ({
  page,
  context,
  request,
}) => {
  const account = await createAccount(request, "Products Org");
  await authenticateBrowser(context, account);

  await page.goto("/products");
  await expect(page.getByRole("heading", { name: "Products" })).toBeVisible();
  await expect(page.getByText("No products yet.")).toBeVisible();

  await page.getByRole("button", { name: "Add your first product" }).click();
  const panel = page.getByRole("complementary", { name: "Product details" });
  await panel.getByLabel("Name").fill("Black Cotton Kurta");
  await panel.getByLabel("SKU").fill("KUR-001");
  await panel.getByLabel("Price", { exact: true }).fill("1299");
  await panel.getByLabel("Details").fill("Regular fit, machine washable.");
  await panel.getByRole("button", { name: "Create product" }).click();

  // After creating, the same panel offers photo upload.
  const upload = panel.getByLabel("Upload photos");
  await expect(upload).toBeAttached();
  await upload.setInputFiles({ name: "kurta.png", mimeType: "image/png", buffer: PNG });

  const thumb = panel.getByRole("img", { name: /photo 1/ });
  await expect(thumb).toBeVisible();
  await expect
    .poll(async () => thumb.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0))
    .toBe(true);

  // The list shows the product with its price and photo count.
  const row = page.getByRole("row", { name: /Black Cotton Kurta/ });
  await expect(row).toContainText("KUR-001");
  await expect(row).toContainText("₹1,299");
  await expect(row).toContainText("1 photo");

  // And the API agrees.
  const listed = await (await request.get(`${API}/v1/products`, { headers: auth(account) })).json();
  expect(listed.total).toBe(1);
  expect(listed.items[0].images).toHaveLength(1);

  // Delete the photo again.
  await panel.getByRole("button", { name: "Delete photo 1" }).click();
  await expect(panel.getByText("No photos yet.")).toBeVisible();
});

test("the agent builder has a 'Use product catalog' switch that persists", async ({ page, context, request }) => {
  const account = await createAccount(request, "Catalog Switch Org");
  const agent = await (
    await request.post(`${API}/v1/agents`, { headers: auth(account), data: { name: "Shop Bot" } })
  ).json();
  await authenticateBrowser(context, account);

  await page.goto(`/agents/${agent.id}`);
  await page.getByRole("tab", { name: "Model" }).click();
  const toggle = page.getByRole("switch", { name: "Use product catalog" });
  await expect(toggle).toBeVisible();
  await expect(toggle).not.toBeChecked();
  await toggle.click();
  await expect(toggle).toBeChecked();

  await expect
    .poll(async () => {
      const versions = await (await request.get(`${API}/v1/agents/${agent.id}/versions`, { headers: auth(account) })).json();
      return versions[0].features.product_catalog_enabled;
    })
    .toBe(true);
});

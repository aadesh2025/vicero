import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProductsView } from "./products-view";
import { useSession } from "@/lib/store/session";
import type { ApiProduct } from "@/lib/api/products";
import { minorToInput, parsePriceToMinor } from "@/lib/api/products";

const listProducts = vi.fn();
const getProduct = vi.fn();
const createProduct = vi.fn();
const uploadProductImage = vi.fn();
vi.mock("@/lib/api/products", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/products")>()),
  listProducts: (...a: unknown[]) => listProducts(...a),
  getProduct: (...a: unknown[]) => getProduct(...a),
  createProduct: (...a: unknown[]) => createProduct(...a),
  uploadProductImage: (...a: unknown[]) => uploadProductImage(...a),
}));
vi.mock("@/lib/rbac", () => ({ useRole: () => "owner", hasPermission: () => true }));

function product(over: Partial<ApiProduct> = {}): ApiProduct {
  return {
    id: "p1",
    name: "Black Kurta",
    sku: "KUR-001",
    price_minor: 129900,
    currency: "INR",
    description: "Soft cotton",
    attributes: { size: "M" },
    in_stock: true,
    url: null,
    is_active: true,
    images: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...over,
  };
}

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProductsView />
    </QueryClientProvider>,
  );
}

const type = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });

beforeEach(() => {
  vi.clearAllMocks();
  useSession.setState({ activeOrgId: "org-1" });
});

describe("price helpers", () => {
  it("converts to and from minor units", () => {
    expect(parsePriceToMinor("1299")).toBe(129900);
    expect(parsePriceToMinor("1,299.50")).toBe(129950);
    expect(parsePriceToMinor("")).toBeNull();
    expect(parsePriceToMinor("12.345")).toBeUndefined();
    expect(parsePriceToMinor("-5")).toBeUndefined();
    expect(parsePriceToMinor("abc")).toBeUndefined();
    expect(minorToInput(129900)).toBe("1299");
    expect(minorToInput(129950)).toBe("1299.50");
    expect(minorToInput(null)).toBe("");
  });
});

describe("ProductsView", () => {
  it("explains the empty state", async () => {
    listProducts.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    renderView();
    expect(await screen.findByText("No products yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add your first product/ })).toBeInTheDocument();
  });

  it("shows a retry when the list fails to load", async () => {
    listProducts.mockRejectedValueOnce(new Error("boom"));
    renderView();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your products");
    listProducts.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No products yet.")).toBeInTheDocument();
  });

  it("lists products with price, SKU, stock and a photo thumbnail", async () => {
    listProducts.mockResolvedValue({
      items: [
        product({
          images: [{ id: "i1", media_id: "m1", url: "https://api/x.jpg", position: 0, alt_text: null }],
        }),
        product({ id: "p2", name: "Blue Shirt", sku: null, price_minor: null, currency: null, in_stock: false }),
      ],
      total: 2,
      limit: 25,
      offset: 0,
    });
    renderView();
    const row = (await screen.findByText("Black Kurta")).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("KUR-001");
    expect(row).toHaveTextContent("₹1,299");
    expect(row).toHaveTextContent("1 photo");
    expect(row.querySelector("img")).toHaveAttribute("src", "https://api/x.jpg");
    const second = screen.getByText("Blue Shirt").closest("tr") as HTMLElement;
    expect(second).toHaveTextContent("Out");
  });

  it("searches server-side", async () => {
    listProducts.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    renderView();
    await screen.findByText("No products yet.");
    type(screen.getByLabelText("Search products"), "kurta");
    await waitFor(() => expect(listProducts).toHaveBeenLastCalledWith(expect.objectContaining({ query: "kurta" })));
    expect(await screen.findByText("No products match that search.")).toBeInTheDocument();
  });

  it("creates a product then offers photo upload", async () => {
    listProducts.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    const created = product({ id: "new1", name: "Red Saree" });
    createProduct.mockResolvedValue(created);
    getProduct.mockResolvedValue(created);
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: /New product/ }));

    const panel = await screen.findByRole("complementary", { name: "Product details" });
    expect(panel).toHaveTextContent("Create the product first, then add photos.");

    type(screen.getByLabelText("Name"), "Red Saree");
    type(screen.getByLabelText("Price"), "2500");
    fireEvent.click(screen.getByRole("button", { name: "Create product" }));

    await waitFor(() =>
      expect(createProduct).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Red Saree", price_minor: 250000, currency: "INR", in_stock: true }),
      ),
    );
    expect(await screen.findByLabelText("Upload photos")).toBeInTheDocument();
  });

  it("rejects a bad price without calling the API", async () => {
    listProducts.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: /New product/ }));
    type(await screen.findByLabelText("Name"), "Thing");
    type(screen.getByLabelText("Price"), "12.345");
    fireEvent.click(screen.getByRole("button", { name: "Create product" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Price must be a number");
    expect(createProduct).not.toHaveBeenCalled();
  });
});

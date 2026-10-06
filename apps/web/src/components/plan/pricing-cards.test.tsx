import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Plan, Pricing } from "@/lib/api/billing";
import { PricingCards } from "./pricing-cards";

const getPlans = vi.fn();
vi.mock("@/lib/api/billing", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/billing")>()),
  getPlans: (c?: string | null) => getPlans(c),
}));
vi.mock("@/components/plan/contact-us", () => ({ ContactUsButtons: () => null }));

const LISTS = {
  USD: { id: [4900, 9900, 19900], pack: [600, 500, 400], note: "excl. taxes" },
  EUR: { id: [4500, 8900, 17900], pack: [500, 450, 350], note: "excl. VAT" },
  INR: { id: [149900, 349900, 699900], pack: [19900, 14900, 9900], note: "excl. GST" },
} as const;

function pricing(currency: keyof typeof LISTS, source: Pricing["currency_source"]): Pricing {
  const l = LISTS[currency];
  const plan = (id: string, i: number): Plan =>
    ({
      id, name: id, price_usd_month: [49, 99, 199][i], price_minor: l.id[i],
      extra_message_pack_size: 500, extra_message_pack_usd: [6, 5, 4][i],
      extra_message_pack_price_minor: l.pack[i],
      limits: { workspaces: 1, agents: 1, messages: 1, knowledge_bases: 1, documents: 1, storage_bytes: 1, workflows: 0, tools: 0, webhooks: 0, team_members: 1 },
      features: { workflows: false, n8n: false, tool_calling: false, webhooks: false, api_write: false, analytics_advanced: false, analytics_export: false, remove_branding: false },
      channels: ["web"], support: "email",
    }) as Plan;
  return {
    plans: ["starter", "pro", "business"].map(plan),
    currency, currency_source: source, available_currencies: ["USD", "EUR", "INR"],
    tax_note: l.note, contact_only: true,
  };
}

function renderCards() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PricingCards />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  getPlans.mockReset();
  getPlans.mockImplementation(async (c?: string | null) =>
    c ? pricing(c as keyof typeof LISTS, "query") : pricing("INR", "geo"),
  );
});
afterEach(() => vi.restoreAllMocks());

describe("<PricingCards> currency", () => {
  it("first load sends no currency, shows the detected one and the INR tax note", async () => {
    renderCards();
    expect(await screen.findByText("₹1,499")).toBeInTheDocument();
    expect(getPlans).toHaveBeenCalledWith(null);
    expect(screen.getByText(/detected from your location/)).toBeInTheDocument();
    expect(screen.getAllByText(/excl\. GST/).length).toBeGreaterThan(0);
  });

  it("switching refetches with ?currency= and remembers the choice", async () => {
    renderCards();
    await screen.findByText("₹1,499");
    fireEvent.click(screen.getByRole("radio", { name: "EUR" }));
    expect(await screen.findByText("€45")).toBeInTheDocument();
    expect(getPlans).toHaveBeenLastCalledWith("EUR");
    expect(screen.getByText(/€4.50 per 500/)).toBeInTheDocument();
    expect(window.localStorage.getItem("vicero.pricing.currency")).toBe("EUR");
  });

  it("uses the remembered choice on the next visit", async () => {
    window.localStorage.setItem("vicero.pricing.currency", "USD");
    renderCards();
    expect(await screen.findByText("$49")).toBeInTheDocument();
    expect(getPlans).toHaveBeenCalledWith("USD");
    expect(getPlans).not.toHaveBeenCalledWith(null);
  });

  it("still works when localStorage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderCards();
    expect(await screen.findByText("₹1,499")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "USD" }));
    await waitFor(() => expect(screen.getByText("$49")).toBeInTheDocument());
  });

  it("shows an error state when pricing cannot load", async () => {
    getPlans.mockRejectedValue(new Error("down"));
    renderCards();
    expect(await screen.findByText(/Couldn.t load pricing/)).toBeInTheDocument();
  });
});

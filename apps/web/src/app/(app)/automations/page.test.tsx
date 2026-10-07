import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { useSession } from "@/lib/store/session";
import AutomationsPage from "./page";

const getAutomationUsage = vi.fn();
const listAutomations = vi.fn();
const listAutomationRequests = vi.fn();
vi.mock("@/lib/api/automations", () => ({
  getAutomationUsage: () => getAutomationUsage(),
  listAutomations: () => listAutomations(),
  listAutomationRequests: () => listAutomationRequests(),
  requestAutomation: vi.fn(),
}));
vi.mock("@/lib/api/agents", () => ({ listAgents: () => Promise.resolve([]) }));

const usage = (over: Record<string, unknown> = {}) => ({
  plan: "pro",
  automations_limit: 5,
  automations_used: 1,
  runs_limit: 2000,
  runs_this_month: 12,
  included: true,
  can_request: true,
  ...over,
});

const card = {
  id: "a1",
  name: "Booking confirmations",
  status: "active",
  agent_id: null,
  agent_name: "Front desk",
  last_run_at: new Date().toISOString(),
  last_status: "success",
  runs_this_month: 12,
  success_rate: 0.75,
};

async function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AutomationsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  listAutomationRequests.mockResolvedValue([]);
  useSession.setState({ activeOrgId: "org-1", user: { is_staff: false } as never });
});

describe("Automations page", () => {
  it("shows a client their automations and never n8n", async () => {
    vi.stubEnv("NEXT_PUBLIC_N8N_URL", "https://n8n.example.test");
    getAutomationUsage.mockResolvedValue(usage());
    listAutomations.mockResolvedValue([card]);
    await renderPage();
    expect(await screen.findByText("Booking confirmations")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Front desk")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open n8n/i })).toBeNull();
    expect(document.body.textContent).not.toMatch(/n8n/i);
  });

  it("gives platform staff the Open n8n link to the protected host", async () => {
    vi.stubEnv("NEXT_PUBLIC_N8N_URL", "https://n8n.example.test/");
    useSession.setState({ user: { is_staff: true } as never });
    getAutomationUsage.mockResolvedValue(usage());
    listAutomations.mockResolvedValue([card]);
    await renderPage();
    const link = await screen.findByRole("link", { name: /open n8n/i });
    expect(link).toHaveAttribute("href", "https://n8n.example.test");
  });

  it("explains an upgrade in plain words when the plan has no automations", async () => {
    getAutomationUsage.mockResolvedValue(
      usage({ plan: "starter", automations_limit: 0, included: false, can_request: false }),
    );
    listAutomations.mockResolvedValue([]);
    await renderPage();
    expect(await screen.findByText("Automations are part of a paid plan")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upgrade" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /request automation/i })).toBeDisabled();
  });

  it("shows a friendly empty state when nothing is set up yet", async () => {
    getAutomationUsage.mockResolvedValue(usage({ automations_used: 0 }));
    listAutomations.mockResolvedValue([]);
    await renderPage();
    expect(await screen.findByText("No automations yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /request automation/i })).toBeEnabled();
  });

  it("shows an error with a retry, not a blank page, when the list cannot load", async () => {
    getAutomationUsage.mockResolvedValue(usage());
    listAutomations.mockRejectedValue(new Error("boom"));
    await renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load/i);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

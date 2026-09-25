import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { TrialBanner } from "./trial-banner";
import { LockedRegion } from "./locked";
import { useSession } from "@/lib/store/session";
import type { PlanStatus } from "@/lib/api/plan";

const getPlan = vi.fn();
vi.mock("@/lib/api/plan", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/plan")>()),
  getPlan: (...a: unknown[]) => getPlan(...a),
}));
vi.mock("@/lib/api/auth", () => ({ resendVerification: vi.fn() }));

function plan(over: Partial<PlanStatus> = {}): PlanStatus {
  return {
    plan: "trial",
    status: "trial",
    trial_ends_at: "2026-10-04T00:00:00Z",
    days_left: 4,
    expired_reason: null,
    messages_used: 312,
    messages_limit: 500,
    messages_remaining: 188,
    unanswered_messages: 0,
    agents_used: 1,
    max_agents: 1,
    can_create_agent: false,
    features: { workflows: false, n8n: false, tool_calling: false },
    ...over,
  };
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  getPlan.mockReset();
  useSession.setState({
    user: { id: "u1", email: "me@example.com", full_name: "Me", avatar_url: null, is_staff: false, email_verified: true, created_at: "" },
    orgs: [],
    activeOrgId: "org-1",
    ready: true,
  });
});

describe("TrialBanner", () => {
  it("shows the usage meter while the trial runs", async () => {
    getPlan.mockResolvedValue(plan());
    wrap(<TrialBanner />);
    expect(await screen.findByText("312 / 500 messages · 4 days left")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /trial messages used/i })).toHaveAttribute("aria-valuenow", "312");
    expect(screen.getByRole("link", { name: "Upgrade" })).toHaveAttribute("href", "/billing/upgrade");
  });

  it("says one day, not 1 days", async () => {
    getPlan.mockResolvedValue(plan({ days_left: 1 }));
    wrap(<TrialBanner />);
    expect(await screen.findByText(/1 day left/)).toBeInTheDocument();
  });

  it("shows the ended banner with the count of unanswered visitors", async () => {
    getPlan.mockResolvedValue(
      plan({ status: "trial_expired", expired_reason: "time", days_left: 0, unanswered_messages: 12 }),
    );
    wrap(<TrialBanner />);
    expect(await screen.findByText("Your free trial has ended. Upgrade to continue.")).toBeInTheDocument();
    expect(screen.getByText(/12 visitor messages were not answered/)).toBeInTheDocument();
    expect(screen.queryByTestId("trial-meter")).toBeNull();
  });

  it("uses the singular for one missed message", async () => {
    getPlan.mockResolvedValue(plan({ status: "trial_expired", unanswered_messages: 1 }));
    wrap(<TrialBanner />);
    expect(await screen.findByText(/1 visitor message was not answered/)).toBeInTheDocument();
  });

  it("stays out of the way on a legacy workspace", async () => {
    getPlan.mockResolvedValue(plan({ plan: "legacy", status: "legacy", messages_limit: null, days_left: null }));
    const { container } = wrap(<TrialBanner />);
    await waitFor(() => expect(getPlan).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("nudges an unverified trial user to verify before publishing", async () => {
    useSession.setState({ user: { ...useSession.getState().user!, email_verified: false } });
    getPlan.mockResolvedValue(plan());
    wrap(<TrialBanner />);
    expect(await screen.findByText(/to publish your agent to live channels/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resend email" })).toBeInTheDocument();
  });
});

describe("LockedRegion", () => {
  it("shows the feature locked, with an upgrade path, when the plan excludes it", async () => {
    getPlan.mockResolvedValue(plan());
    wrap(
      <LockedRegion feature="workflows" label="Workflows">
        <button>Add workflow</button>
      </LockedRegion>,
    );
    expect(await screen.findByText("Workflows are part of a paid plan")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upgrade" })).toHaveAttribute("href", "/billing/upgrade");
    // Still rendered (it is the upsell) but inert, so it cannot be clicked or tabbed to.
    expect(screen.getByText("Add workflow", { selector: "button" }).closest("[inert]")).not.toBeNull();
  });

  it("is transparent when the plan includes the feature", async () => {
    getPlan.mockResolvedValue(plan({ plan: "legacy", status: "legacy", features: { workflows: true, n8n: true, tool_calling: true } }));
    wrap(
      <LockedRegion feature="workflows" label="Workflows">
        <button>Add workflow</button>
      </LockedRegion>,
    );
    await waitFor(() => expect(getPlan).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Add workflow" }).closest("[inert]")).toBeNull();
    expect(screen.queryByText(/paid plan/)).toBeNull();
  });

  it("never locks while the plan is still loading or unreadable", async () => {
    getPlan.mockRejectedValue(new Error("offline"));
    wrap(
      <LockedRegion feature="workflows" label="Workflows">
        <button>Add workflow</button>
      </LockedRegion>,
    );
    expect(screen.getByRole("button", { name: "Add workflow" })).toBeInTheDocument();
    await waitFor(() => expect(getPlan).toHaveBeenCalled());
    expect(screen.queryByText(/paid plan/)).toBeNull();
  });
});

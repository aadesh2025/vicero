import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./auth-gate";
import { ApiError } from "@/lib/api/client";
import { me } from "@/lib/api/auth";
import { listOrgs } from "@/lib/api/orgs";
import { clearAuth, getAccessToken } from "@/lib/api/tokens";
import { useSession } from "@/lib/store/session";

const replace = vi.fn();
// One stable router object: AuthGate's effect depends on `router`, so returning a fresh
// object per render re-runs the effect forever (this test OOM'd until it was hoisted).
const router = { replace };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/auth", () => ({ me: vi.fn() }));
vi.mock("@/lib/api/orgs", () => ({ listOrgs: vi.fn() }));
vi.mock("@/lib/api/tokens", () => ({
  getAccessToken: vi.fn(() => "token"),
  getActiveOrgId: vi.fn(() => null),
  setActiveOrgId: vi.fn(),
  clearAuth: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  useSession.setState({ user: null, orgs: [], activeOrgId: null, ready: false });
  vi.mocked(getAccessToken).mockReturnValue("token");
});

describe("AuthGate session bootstrap", () => {
  it("loads the session and renders the app", async () => {
    vi.mocked(me).mockResolvedValue({
      user: { id: "u1", email: "a@b.c" },
      memberships: [],
    } as never);
    vi.mocked(listOrgs).mockResolvedValue([{ id: "o1", name: "Acme" }] as never);

    render(
      <AuthGate>
        <p>dashboard</p>
      </AuthGate>,
    );

    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    expect(clearAuth).not.toHaveBeenCalled();
  });

  it("signs the user out when the API genuinely rejects the token", async () => {
    vi.mocked(me).mockRejectedValue(new ApiError(401, "auth.invalid_token", "Invalid"));
    vi.mocked(listOrgs).mockResolvedValue([] as never);

    render(
      <AuthGate>
        <p>dashboard</p>
      </AuthGate>,
    );

    await waitFor(() => expect(clearAuth).toHaveBeenCalled());
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("keeps the session when the bootstrap request is aborted by a navigation", async () => {
    // The bug this pins: navigating while /me + /orgs are still in flight aborts them, and the
    // browser rejects an aborted fetch with a TypeError — not a 401. Clearing the tokens there
    // logged people out seconds after signing in, for clicking a link too quickly.
    vi.mocked(me).mockRejectedValue(new TypeError("Failed to fetch"));
    vi.mocked(listOrgs).mockRejectedValue(new TypeError("Failed to fetch"));

    render(
      <AuthGate>
        <p>dashboard</p>
      </AuthGate>,
    );

    await waitFor(() => expect(me).toHaveBeenCalled());
    await Promise.resolve();
    expect(clearAuth).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps the session when the API is briefly down", async () => {
    // A 503 says the server is unwell, not that the caller is unauthenticated.
    vi.mocked(me).mockRejectedValue(new ApiError(503, "unavailable", "Service unavailable"));
    vi.mocked(listOrgs).mockResolvedValue([] as never);

    render(
      <AuthGate>
        <p>dashboard</p>
      </AuthGate>,
    );

    await waitFor(() => expect(me).toHaveBeenCalled());
    await Promise.resolve();
    expect(clearAuth).not.toHaveBeenCalled();
  });

  it("still loads the session when there is no access-token cookie (the refresh cookie covers it)", async () => {
    // The access-token cookie is short-lived (~30m); its absence must not be treated as "never
    // logged in" while the 30-day httpOnly refresh cookie is still valid. `api()` transparently
    // refreshes on a 401 — this only pins that AuthGate no longer bails out before giving it
    // the chance, which previously bounced returning visitors to /login every ~30 minutes.
    vi.mocked(getAccessToken).mockReturnValue(null as never);
    vi.mocked(me).mockResolvedValue({
      user: { id: "u1", email: "a@b.c" },
      memberships: [],
    } as never);
    vi.mocked(listOrgs).mockResolvedValue([{ id: "o1", name: "Acme" }] as never);

    render(
      <AuthGate>
        <p>dashboard</p>
      </AuthGate>,
    );

    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("bounces to /login when there is no access token and the refresh cookie has also expired", async () => {
    vi.mocked(getAccessToken).mockReturnValue(null as never);
    vi.mocked(me).mockRejectedValue(new ApiError(401, "auth.invalid_token", "Invalid"));
    vi.mocked(listOrgs).mockResolvedValue([] as never);

    render(
      <AuthGate>
        <p>dashboard</p>
      </AuthGate>,
    );

    await waitFor(() => expect(clearAuth).toHaveBeenCalled());
    expect(replace).toHaveBeenCalledWith("/login");
  });
});

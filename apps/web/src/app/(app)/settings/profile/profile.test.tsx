import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProfilePage, { describeDevice } from "./page";
import { useSession } from "@/lib/store/session";
import type { ApiSession, ApiUser } from "@/lib/api/types";

const listSessions = vi.fn();
const logout = vi.fn();
const replace = vi.fn();
vi.mock("@/lib/api/auth", () => ({
  listSessions: () => listSessions(),
  revokeSession: vi.fn(),
  logout: (opts?: { all?: boolean }) => logout(opts),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const USER: ApiUser = {
  id: "u1",
  email: "real.person@acme.com",
  full_name: "Real Person",
  avatar_url: null,
  is_staff: false,
  email_verified: true,
  created_at: new Date().toISOString(),
};

function session(over: Partial<ApiSession> & { id: string }): ApiSession {
  return {
    user_agent: "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36",
    ip: "203.0.113.9",
    created_at: new Date().toISOString(),
    expires_at: new Date().toISOString(),
    current: false,
    ...over,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProfilePage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useSession.setState({ user: USER, orgs: [], activeOrgId: "org-1", ready: true });
  listSessions.mockResolvedValue([]);
});

describe("describeDevice", () => {
  it("names the browser and OS from a user agent", () => {
    expect(describeDevice("Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36")).toEqual({
      label: "Chrome · Windows",
      mobile: false,
    });
  });

  it("detects mobile so the row gets a phone glyph", () => {
    const out = describeDevice("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile/15E Safari/604.1");
    expect(out.mobile).toBe(true);
    expect(out.label).toContain("iOS");
  });

  it("stays honestly unknown rather than guessing when there is no UA", () => {
    expect(describeDevice(null)).toEqual({ label: "Unknown device", mobile: false });
  });

  it("prefers Edge over the Chrome token both browsers send", () => {
    expect(describeDevice("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Edg/120").label).toBe("Edge · Windows");
  });
});

describe("ProfilePage", () => {
  it("shows the signed-in user, not a hardcoded identity", async () => {
    renderPage();
    expect(await screen.findByDisplayValue("Real Person")).toBeInTheDocument();
    expect(screen.getByDisplayValue("real.person@acme.com")).toBeInTheDocument();
    // The fake account this page used to render regardless of who was logged in.
    expect(screen.queryByDisplayValue("Aadesh Sree")).toBeNull();
    expect(screen.queryByDisplayValue("aadesh@aurozen.ai")).toBeNull();
  });

  it("renders real sessions from the API", async () => {
    listSessions.mockResolvedValue([
      session({ id: "s1", current: true }),
      session({ id: "s2", user_agent: "Mozilla/5.0 (iPhone) Mobile Safari/604.1", ip: "198.51.100.4" }),
    ]);
    renderPage();
    expect(await screen.findByText("Chrome · Windows")).toBeInTheDocument();
    expect(screen.getByText("This device")).toBeInTheDocument();
    expect(screen.getByText(/198\.51\.100\.4/)).toBeInTheDocument();
    // The invented devices the page used to list.
    expect(screen.queryByText(/Coimbatore, IN/)).toBeNull();
  });

  it("offers Revoke only for other devices", async () => {
    listSessions.mockResolvedValue([session({ id: "s1", current: true }), session({ id: "s2" })]);
    renderPage();
    await screen.findByText("This device");
    expect(screen.getAllByRole("button", { name: /Revoke/ })).toHaveLength(1);
  });

  it("says so when there are no sessions rather than showing an empty list", async () => {
    renderPage();
    expect(await screen.findByText("No active sessions found.")).toBeInTheDocument();
  });

  it("does not offer a Save control while no update endpoint exists", async () => {
    renderPage();
    await screen.findByDisplayValue("Real Person");
    expect(screen.queryByRole("button", { name: /Save changes/ })).toBeNull();
    expect(screen.getByDisplayValue("Real Person")).toHaveAttribute("readonly");
  });

  it("hides \"Log out all devices\" with only one session — nothing else to log out", async () => {
    listSessions.mockResolvedValue([session({ id: "s1", current: true })]);
    renderPage();
    await screen.findByText("This device");
    expect(screen.queryByRole("button", { name: /Log out all devices/i })).toBeNull();
  });

  it("logs out everywhere and sends the user to /login", async () => {
    listSessions.mockResolvedValue([session({ id: "s1", current: true }), session({ id: "s2" })]);
    logout.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("This device");

    fireEvent.click(screen.getByRole("button", { name: /Log out all devices/i }));
    await waitFor(() => expect(logout).toHaveBeenCalledWith({ all: true }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });
});

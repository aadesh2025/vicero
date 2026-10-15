import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GoogleCalendarCard } from "./google-calendar-card";
import type { ApiGoogleCalendarStatus } from "@/lib/api/integrations";

const getStatus = vi.fn();
const getConnectUrl = vi.fn();
const disconnect = vi.fn();
let search = "";

vi.mock("@/lib/api/integrations", () => ({
  getGoogleCalendarStatus: () => getStatus(),
  getGoogleCalendarConnectUrl: (id: string | null) => getConnectUrl(id),
  disconnectGoogleCalendar: () => disconnect(),
}));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search) }));

const REDIRECT = "https://api.example.test/api/google/callback";
function status(over: Partial<ApiGoogleCalendarStatus> = {}): ApiGoogleCalendarStatus {
  return { configured: true, connected: false, needs_reconnect: false, email: null, redirect_uri: REDIRECT, ...over };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GoogleCalendarCard agentId="agent-1" />
    </QueryClientProvider>,
  );
}

describe("GoogleCalendarCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    search = "";
  });

  it("offers Connect when not connected and sends the browser to Google", async () => {
    getStatus.mockResolvedValue(status());
    getConnectUrl.mockResolvedValue({ url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { ...window.location, assign }, writable: true });
    renderCard();
    expect(await screen.findByText(/saved in Vicero only/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /connect google calendar/i }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?x=1"));
    expect(getConnectUrl).toHaveBeenCalledWith("agent-1");
  });

  it("shows the connected account and disconnects after a confirmation click", async () => {
    getStatus.mockResolvedValue(status({ connected: true, email: "owner@example.com" }));
    disconnect.mockResolvedValue(undefined);
    renderCard();
    expect(await screen.findByText("Connected")).toBeTruthy();
    expect(screen.getByText(/owner@example\.com/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /connect google calendar/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(disconnect).not.toHaveBeenCalled(); // one click is not enough
    fireEvent.click(screen.getByRole("button", { name: /confirm disconnect/i }));
    await waitFor(() => expect(disconnect).toHaveBeenCalledTimes(1));
  });

  it("can back out of disconnecting", async () => {
    getStatus.mockResolvedValue(status({ connected: true, email: "owner@example.com" }));
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
    expect(disconnect).not.toHaveBeenCalled();
  });

  it("asks to reconnect when Google ended the grant, and says bookings still work", async () => {
    getStatus.mockResolvedValue(status({ needs_reconnect: true }));
    renderCard();
    expect(await screen.findByRole("button", { name: /reconnect google calendar/i })).toBeTruthy();
    expect(screen.getByText("Needs reconnect")).toBeTruthy();
    expect(screen.getByText(/bookings still work/i)).toBeTruthy();
  });

  it("disables Connect, with the reason, when the server has no Google credentials", async () => {
    getStatus.mockResolvedValue(status({ configured: false }));
    renderCard();
    expect(await screen.findByText(/GOOGLE_CLIENT_ID/)).toBeTruthy();
    const button = screen.getByRole("button", { name: /connect google calendar/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("reports the result Google redirected back with", async () => {
    search = "google=connected";
    getStatus.mockResolvedValue(status({ connected: true, email: "a@b.co" }));
    renderCard();
    expect((await screen.findByRole("status")).textContent).toMatch(/is connected/i);
  });

  it("explains a cancelled or failed connection without leaking details", async () => {
    search = "google=error&reason=google.token_error";
    getStatus.mockResolvedValue(status());
    renderCard();
    const note = await screen.findByRole("status");
    expect(note.textContent).toMatch(/could not connect/i);
    expect(note.textContent).not.toMatch(/token_error/);
  });
});

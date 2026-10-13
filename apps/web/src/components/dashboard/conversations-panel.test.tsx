import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { ConversationsPanel } from "./conversations-panel";
import { useSession } from "@/lib/store/session";
import type { ApiConversation } from "@/lib/api/conversations";

const listConversations = vi.fn();
vi.mock("@/lib/api/conversations", () => ({ listConversations: () => listConversations() }));

const listAgents = vi.fn();
vi.mock("@/lib/api/agents", () => ({ listAgents: () => listAgents() }));

function convo(over: Partial<ApiConversation> & { id: string }): ApiConversation {
  return {
    agent_id: "a1",
    channel: "widget",
    status: "active",
    title: "A question",
    message_count: 3,
    last_message_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...over,
  };
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConversationsPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useSession.setState({ activeOrgId: "org-1" });
  listAgents.mockResolvedValue([{ id: "a1", name: "Billing Bot" }]);
});

describe("ConversationsPanel", () => {
  it("explains the empty state rather than rendering a blank list", async () => {
    listConversations.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText("No conversations yet")).toBeInTheDocument();
    expect(screen.getByText(/once someone messages one of your agents/)).toBeInTheDocument();
  });

  it("never shows the retired mock conversations to an org with none", async () => {
    listConversations.mockResolvedValue([]);
    renderPanel();
    await screen.findByText("No conversations yet");
    expect(screen.queryByText(/My order hasn't shipped yet/)).toBeNull();
    expect(screen.queryByText(/volume pricing/)).toBeNull();
  });

  it("renders real conversations with a resolved agent name and channel label", async () => {
    listConversations.mockResolvedValue([
      convo({ id: "c1", title: "Where is my order?", channel: "telegram", message_count: 5 }),
    ]);
    renderPanel();
    expect(await screen.findByText("Where is my order?")).toBeInTheDocument();
    expect(screen.getByText("Billing Bot")).toBeInTheDocument(); // resolved from agent_id
    expect(screen.getByText("Telegram")).toBeInTheDocument(); // label, not the raw key
    expect(screen.getByText("5 messages")).toBeInTheDocument();
  });

  it("maps the API's `active` status to a label instead of leaking the raw value", async () => {
    listConversations.mockResolvedValue([convo({ id: "c1" })]);
    renderPanel();
    expect(await screen.findByText("Active")).toBeInTheDocument();
  });

  it("routes a handed-off thread to the inbox and everything else to the browser", async () => {
    listConversations.mockResolvedValue([
      convo({ id: "c1", title: "Needs a human", status: "handoff" }),
      convo({ id: "c2", title: "Bot handled it" }),
    ]);
    renderPanel();
    const handoff = await screen.findByRole("link", { name: /Needs a human/ });
    expect(handoff).toHaveAttribute("href", "/inbox/c1");
    // No /conversations/{id} route exists, so a non-handoff row points at the list.
    expect(screen.getByRole("link", { name: /Bot handled it/ })).toHaveAttribute("href", "/conversations");
  });

  it("falls back to a placeholder when a conversation has no title", async () => {
    listConversations.mockResolvedValue([convo({ id: "c1", title: null })]);
    renderPanel();
    expect(await screen.findByText("Untitled conversation")).toBeInTheDocument();
  });

  it("caps the panel at six rows", async () => {
    listConversations.mockResolvedValue(
      Array.from({ length: 10 }, (_, i) => convo({ id: `c${i}`, title: `Thread ${i}` })),
    );
    renderPanel();
    await screen.findByText("Thread 0");
    expect(screen.queryByText("Thread 6")).toBeNull();
  });

  it("shows a skeleton, not an empty state, while loading", () => {
    listConversations.mockReturnValue(new Promise(() => {}));
    const { container } = renderPanel();
    expect(screen.queryByText("No conversations yet")).toBeNull();
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });

  it("does not query until an org is active", async () => {
    useSession.setState({ activeOrgId: null });
    renderPanel();
    await waitFor(() => expect(listConversations).not.toHaveBeenCalled());
  });
});

describe("ConversationsPanel contact identity", () => {
  it("shows the contact's name and @username instead of just a channel badge", async () => {
    listConversations.mockResolvedValue([
      convo({
        id: "c1",
        channel: "instagram",
        channel_user_id: "17841400000000000999",
        contact: { id: "ct", display_name: "Aadesh", avatar_url: null, username: "_.aadesx._" },
      }),
    ]);
    renderPanel();
    expect(await screen.findByText("@_.aadesx._")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Aadesh/ })).toBeInTheDocument();
    expect(screen.queryByText("Instagram")).toBeNull(); // the name replaces the bare channel label
  });

  it("still shows the channel badge for a chat with no contact", async () => {
    listConversations.mockResolvedValue([convo({ id: "c1", channel: "telegram" })]);
    renderPanel();
    expect(await screen.findByText("Telegram")).toBeInTheDocument();
  });
});

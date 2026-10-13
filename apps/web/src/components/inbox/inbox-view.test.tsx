import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { InboxView } from "./inbox-view";
import { useSession } from "@/lib/store/session";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const listChannels = vi.fn();
vi.mock("@/lib/api/channels", () => ({ listChannels: (...a: unknown[]) => listChannels(...a) }));

const listAgents = vi.fn();
vi.mock("@/lib/api/agents", () => ({ listAgents: () => listAgents() }));

const listInbox = vi.fn();
const getInboxDetail = vi.fn();
const takeover = vi.fn();
vi.mock("@/lib/api/inbox", () => ({
  listInbox: (...a: unknown[]) => listInbox(...a),
  getInboxDetail: (...a: unknown[]) => getInboxDetail(...a),
  openInboxSocket: () => null,
  takeover: (...a: unknown[]) => takeover(...a),
  handback: vi.fn(),
  closeConversation: vi.fn(),
  replyInbox: vi.fn(),
}));

function renderInbox() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <InboxView />
    </QueryClientProvider>,
  );
}

const tabs = () => screen.getByRole("tablist", { name: "Channels" });

beforeEach(() => {
  vi.clearAllMocks();
  useSession.setState({ activeOrgId: "org-1" });
  listInbox.mockResolvedValue([]);
  listAgents.mockResolvedValue([{ id: "agent-1", name: "Support Bot" }]);
  listChannels.mockResolvedValue([]);
});

describe("channel tab bar", () => {
  it("renders all 8 channel tabs even when nothing is connected", async () => {
    renderInbox();
    await waitFor(() => expect(within(tabs()).getByRole("tab", { name: /Web Chat/ })).toBeInTheDocument());

    for (const label of ["Web Chat", "Messenger", "Instagram", "WhatsApp", "Telegram", "Slack", "Discord"]) {
      expect(within(tabs()).getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument();
    }
    // Plus the "All messages" tab.
    expect(within(tabs()).getAllByRole("tab")).toHaveLength(9);
  });

  it("renders the same 8 tabs when channels are connected", async () => {
    listChannels.mockResolvedValue([{ type: "whatsapp", enabled: true, created_at: new Date().toISOString() }]);
    renderInbox();
    await waitFor(() => expect(within(tabs()).getAllByRole("tab")).toHaveLength(9));
  });

  it("marks unconnected channels for screen readers, and connected ones not", async () => {
    listChannels.mockResolvedValue([{ type: "whatsapp", enabled: true }]);
    renderInbox();

    await waitFor(() =>
      expect(within(tabs()).getByRole("tab", { name: /^WhatsApp$/ })).toBeInTheDocument(),
    );
    // Instagram has no row at all; Web Chat is inherently on.
    expect(within(tabs()).getByRole("tab", { name: /Instagram \(not connected\)/ })).toBeInTheDocument();
    expect(within(tabs()).getByRole("tab", { name: /^Web Chat$/ })).toBeInTheDocument();
  });

  it("treats a disabled channel as not connected", async () => {
    listChannels.mockResolvedValue([{ type: "telegram", enabled: false }]);
    renderInbox();
    await waitFor(() =>
      expect(within(tabs()).getByRole("tab", { name: /Telegram \(not connected\)/ })).toBeInTheDocument(),
    );
  });
});

describe("not-connected channel state", () => {
  it("shows the connect prompt in both panels for an unconnected channel", async () => {
    renderInbox();
    await waitFor(() => expect(within(tabs()).getAllByRole("tab")).toHaveLength(9));

    fireEvent.click(within(tabs()).getByRole("tab", { name: /Instagram/ }));

    // Both panels: the list column and the detail pane.
    await waitFor(() => expect(screen.getAllByText("Instagram isn’t connected yet.")).toHaveLength(2));
    expect(screen.getAllByText("Connect it to start receiving messages here.")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Connect Instagram/ })).toBeInTheDocument();

    // The generic empty-inbox copy must NOT appear — that's a different situation.
    expect(screen.queryByText("Nothing in the inbox yet.")).toBeNull();
    expect(screen.queryByText("Select a conversation to view it.")).toBeNull();
  });

  it("keeps 'Nothing in the inbox yet.' for a connected channel with no messages", async () => {
    listChannels.mockResolvedValue([{ type: "whatsapp", enabled: true }]);
    renderInbox();
    await waitFor(() => expect(within(tabs()).getAllByRole("tab")).toHaveLength(9));

    fireEvent.click(within(tabs()).getByRole("tab", { name: /WhatsApp/ }));

    // Connected but quiet — the ordinary empty state, no setup prompt anywhere.
    await waitFor(() => expect(screen.getByText("Nothing in the inbox yet.")).toBeInTheDocument());
    expect(screen.queryByText(/isn’t connected yet/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Connect/ })).toBeNull();
  });

  it("never shows the prompt on All messages or Web Chat", async () => {
    renderInbox();
    await waitFor(() => expect(within(tabs()).getAllByRole("tab")).toHaveLength(9));

    // "All messages" is the default selection.
    expect(screen.queryByText(/isn’t connected yet/)).toBeNull();

    fireEvent.click(within(tabs()).getByRole("tab", { name: /Web Chat/ }));
    await waitFor(() => expect(screen.getByText("Nothing in the inbox yet.")).toBeInTheDocument());
    expect(screen.queryByText(/isn’t connected yet/)).toBeNull();
  });
});

describe("connect flow", () => {
  it("deep-links straight to the only agent's Channels tab with the type pre-selected", async () => {
    renderInbox();
    await waitFor(() => expect(within(tabs()).getAllByRole("tab")).toHaveLength(9));

    fireEvent.click(within(tabs()).getByRole("tab", { name: /WhatsApp/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Connect WhatsApp/ }));

    expect(push).toHaveBeenCalledWith("/agents/agent-1?tab=channels&connect=whatsapp");
  });

  it("asks which agent first when the org has several", async () => {
    listAgents.mockResolvedValue([
      { id: "agent-1", name: "Support Bot" },
      { id: "agent-2", name: "Sales Bot" },
    ]);
    renderInbox();
    await waitFor(() => expect(within(tabs()).getAllByRole("tab")).toHaveLength(9));

    fireEvent.click(within(tabs()).getByRole("tab", { name: /WhatsApp/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Connect WhatsApp/ }));

    // No navigation until an agent is chosen — a channel belongs to exactly one.
    expect(push).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Sales Bot" }));
    expect(push).toHaveBeenCalledWith("/agents/agent-2?tab=channels&connect=whatsapp");
  });

  it("points at agent creation when the org has none to attach a channel to", async () => {
    listAgents.mockResolvedValue([]);
    renderInbox();
    await waitFor(() => expect(within(tabs()).getAllByRole("tab")).toHaveLength(9));

    fireEvent.click(within(tabs()).getByRole("tab", { name: /WhatsApp/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Create an agent/ }));

    expect(push).toHaveBeenCalledWith("/agents");
  });
});

describe("plan-limit handoffs", () => {
  const item = (id: string, reason: string | null) => ({
    id,
    agent_id: "agent-1",
    channel: "widget",
    status: "handoff",
    title: `Chat ${id}`,
    channel_user_id: `visitor-${id}`,
    message_count: 1,
    last_message_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    contact: null,
    attention_level: null,
    handoff: {
      id: `h-${id}`,
      status: "open",
      requested_by: reason === "plan_limit" ? "system" : "user",
      reason,
      assigned_to: null,
      notes: [],
      tags: [],
      created_at: new Date().toISOString(),
      resolved_at: null,
    },
  });

  it("tells a plan-limit conversation apart from an ordinary handoff request", async () => {
    listInbox.mockResolvedValue([item("a", "plan_limit"), item("b", "keyword")]);
    renderInbox();

    const planLimit = await screen.findByText("plan limit");
    expect(planLimit).toHaveAttribute("title", "agent couldn't reply (plan limit)");
    // Only the one row carries it; both still read "needs agent" like any handoff.
    expect(screen.getAllByText("plan limit")).toHaveLength(1);
    expect(screen.getAllByText("needs agent")).toHaveLength(2);
  });

  it("does not badge a handoff that has been resolved", async () => {
    const resolved = item("c", "plan_limit");
    resolved.handoff.status = "resolved";
    listInbox.mockResolvedValue([resolved]);
    renderInbox();

    await screen.findByText("Chat c");
    expect(screen.queryByText("plan limit")).toBeNull();
  });
});

describe("contact identity on rows and header", () => {
  const igItem = (over: Record<string, unknown> = {}) => ({
    id: "ig1",
    agent_id: "agent-1",
    channel: "instagram",
    status: "active",
    title: "Hi",
    channel_user_id: "17841400000000000999",
    message_count: 2,
    last_message_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    handoff: null,
    attention_level: null,
    contact: { id: "ct1", display_name: "Aadesh", avatar_url: null, username: "_.aadesx._", crm_contact_id: null },
    ...over,
  });

  it("shows the name, the @username and initials when there is no photo", async () => {
    listInbox.mockResolvedValue([igItem()]);
    renderInbox();
    expect(await screen.findByText("Aadesh")).toBeInTheDocument();
    expect(screen.getByText("@_.aadesx._")).toBeInTheDocument();
    expect(screen.getByText("A")).toBeInTheDocument(); // initials stand in for the missing avatar
  });

  it("shows the photo when the contact has one", async () => {
    listInbox.mockResolvedValue([
      igItem({ contact: { id: "ct1", display_name: "Aadesh", avatar_url: "https://cdn/x.jpg", username: null } }),
    ]);
    const { container } = renderInbox();
    await screen.findByText("Aadesh");
    expect(container.querySelector('img[src="https://cdn/x.jpg"]')).not.toBeNull();
    expect(screen.queryByText(/^@/)).toBeNull(); // no username, no handle line
  });

  it("falls back to the tail of the platform id when there is no name", async () => {
    listInbox.mockResolvedValue([igItem({ contact: null })]);
    renderInbox();
    expect(await screen.findByText("…00000999")).toBeInTheDocument();
  });
});

describe("All chats toggle", () => {
  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn(); // jsdom has no element scrolling; the thread scrolls itself to the bottom
  });

  it("defaults to the handoff queue and asks the API for bot chats only when switched on", async () => {
    listInbox.mockResolvedValue([]);
    renderInbox();
    const group = await screen.findByRole("group", { name: "Which chats" });
    expect(within(group).getByRole("button", { name: "Needs a human" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "All chats" })).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => expect(listInbox).toHaveBeenCalledWith(undefined, undefined, false));

    fireEvent.click(within(group).getByRole("button", { name: "All chats" }));
    await waitFor(() => expect(listInbox).toHaveBeenCalledWith(undefined, undefined, true));
    expect(within(group).getByRole("button", { name: "All chats" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(group).getByRole("button", { name: "Needs a human" }));
    await waitFor(() => expect(listInbox).toHaveBeenLastCalledWith(undefined, undefined, false));
  });

  it("opens a bot-handled chat read-only, with Take over, and takes it over", async () => {
    const bot = {
      id: "b1",
      agent_id: "agent-1",
      channel: "instagram",
      status: "active",
      title: "Hi",
      channel_user_id: "IG42",
      message_count: 2,
      last_message_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      handoff: null,
      attention_level: null,
      contact: { id: "ct1", display_name: "Aadesh", avatar_url: null, username: "acme" },
    };
    listInbox.mockImplementation((_s: unknown, _c: unknown, includeBot: boolean) =>
      Promise.resolve(includeBot ? [bot] : []),
    );
    getInboxDetail.mockResolvedValue({ ...bot, messages: [], send_window: null });
    takeover.mockResolvedValue({ ...bot, status: "handoff" });
    renderInbox();

    fireEvent.click(await screen.findByRole("button", { name: "All chats" }));
    fireEvent.click(await screen.findByText("Aadesh"));

    expect(await screen.findByText("The assistant is handling this conversation. Take over to reply.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull(); // read-only: no reply box
    fireEvent.click(await screen.findByRole("button", { name: /take over/i }));
    await waitFor(() => expect(takeover).toHaveBeenCalledWith("b1"));
  });

  it("offers no take-over on a closed chat", async () => {
    const closed = {
      id: "z1",
      agent_id: "agent-1",
      channel: "instagram",
      status: "closed",
      title: "Old",
      channel_user_id: "IG1",
      message_count: 1,
      last_message_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      handoff: null,
      attention_level: null,
      contact: { id: "c", display_name: "Done Dee", avatar_url: null },
    };
    listInbox.mockResolvedValue([closed]);
    getInboxDetail.mockResolvedValue({ ...closed, messages: [], send_window: null });
    renderInbox();
    fireEvent.click(await screen.findByText("Done Dee"));
    await screen.findByText("The assistant is handling this conversation.");
    expect(screen.queryByRole("button", { name: /take over/i })).toBeNull();
  });
});

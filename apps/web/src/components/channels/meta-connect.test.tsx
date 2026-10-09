import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MetaOverlays, MetaRowActions, MetaRowFooter, useMetaConnect } from "./meta-connect";
import type { ApiChannel } from "@/lib/api/channels";
import { ApiError } from "@/lib/api/client";
import * as metaApi from "@/lib/api/meta-connect";
import { resetFacebookSdkForTests, type FbLoginResponse, type FbSdk } from "@/lib/meta/facebook-sdk";

vi.mock("@/lib/api/meta-connect", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/meta-connect")>()),
  getMetaConfig: vi.fn(),
  connectWhatsApp: vi.fn(),
  listFacebookPages: vi.fn(),
  connectFacebookPages: vi.fn(),
  disconnectMetaChannel: vi.fn(),
}));

const CONFIG = {
  enabled: true,
  whatsapp_enabled: true,
  app_id: "424242",
  config_id: "CFG1",
  graph_version: "v23.0",
  app_live: true,
};

type LoginCallback = (r: FbLoginResponse) => void;
let login: ReturnType<typeof vi.fn>;
let loginOptions: Record<string, unknown> | undefined;
let loginCallback: LoginCallback | undefined;

/** A fake Facebook SDK: the test decides when (and whether) the popup answers. */
function installFakeSdk() {
  loginOptions = undefined;
  loginCallback = undefined;
  login = vi.fn((cb: LoginCallback, options: Record<string, unknown>) => {
    loginCallback = cb;
    loginOptions = options;
  });
  const sdk: FbSdk = { init: vi.fn(), login: login as unknown as FbSdk["login"] };
  window.FB = sdk;
  // `loadFacebookSdk` appends a script; with window.FB present it initialises without one.
}

function channel(over: Partial<ApiChannel>): ApiChannel {
  return {
    id: "c1",
    agent_id: "a1",
    type: "whatsapp",
    name: "Acme Co",
    enabled: true,
    config: {},
    webhook_url: null,
    created_at: "2026-10-01T00:00:00Z",
    connection_source: "meta_oauth",
    status: "active",
    ...over,
  };
}

function Harness({ channels = [] as ApiChannel[], onChanged = () => {} }) {
  const meta = useMetaConnect({ agentId: "a1", onChanged });
  const byType = (t: ApiChannel["type"]) => channels.find((c) => c.type === t);
  return (
    <div>
      {(["whatsapp", "facebook", "instagram"] as const).map((t) => (
        <div key={t} data-testid={t}>
          <MetaRowActions type={t} channel={byType(t)} meta={meta} onToggle={() => {}} />
          <MetaRowFooter type={t} meta={meta} onAdvanced={() => {}} />
        </div>
      ))}
      <MetaOverlays meta={meta} agentId="a1" />
    </div>
  );
}

function renderHarness(props: { channels?: ApiChannel[]; onChanged?: () => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Harness {...props} />
    </QueryClientProvider>,
  );
}

/** Wait until the SDK has initialised, so the Connect buttons are enabled. */
async function ready(name: RegExp | string) {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
}

function facebookMessage(data: unknown, origin = "https://www.facebook.com") {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { origin, data: JSON.stringify(data) }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetFacebookSdkForTests();
  installFakeSdk();
  vi.mocked(metaApi.getMetaConfig).mockResolvedValue(CONFIG);
});

afterEach(() => {
  delete window.FB;
  vi.useRealTimers();
});

describe("when one-click connect is not configured", () => {
  it("disables all three buttons and explains why", async () => {
    vi.mocked(metaApi.getMetaConfig).mockResolvedValue({
      ...CONFIG,
      enabled: false,
      whatsapp_enabled: false,
      app_id: null,
      config_id: null,
    });
    renderHarness();
    for (const name of [/connect whatsapp/i, /connect messenger/i, /connect instagram/i]) {
      const button = await screen.findByRole("button", { name });
      expect(button).toBeDisabled();
      expect(button.closest("span[title]")).toHaveAttribute("title", expect.stringMatching(/isn't set up/i));
    }
    expect(login).not.toHaveBeenCalled();
    // The manual route stays available.
    expect(screen.getAllByText("Advanced: connect with tokens")).toHaveLength(3);
  });

  it("keeps Messenger usable but disables WhatsApp when only the signup config is missing", async () => {
    vi.mocked(metaApi.getMetaConfig).mockResolvedValue({ ...CONFIG, whatsapp_enabled: false, config_id: null });
    renderHarness();
    await ready(/connect messenger/i);
    expect(screen.getByRole("button", { name: /connect whatsapp/i })).toBeDisabled();
  });
});

describe("WhatsApp embedded signup", () => {
  it("logs in with the config id, waits for the number, then calls the backend", async () => {
    const onChanged = vi.fn();
    vi.mocked(metaApi.connectWhatsApp).mockResolvedValue(channel({}));
    renderHarness({ onChanged });
    fireEvent.click(await ready(/connect whatsapp/i));

    expect(login).toHaveBeenCalledTimes(1);
    expect(loginOptions).toMatchObject({
      config_id: "CFG1",
      response_type: "code",
      override_default_response_type: true,
      extras: { setup: {}, featureType: "", sessionInfoVersion: "3" },
    });

    // Order must not matter: the number arrives first, then the login callback with the code.
    facebookMessage({ type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "111", phone_number_id: "222" } });
    act(() => loginCallback?.({ authResponse: { code: "AUTHCODE" } }));

    await waitFor(() =>
      expect(metaApi.connectWhatsApp).toHaveBeenCalledWith({
        agent_id: "a1",
        code: "AUTHCODE",
        waba_id: "111",
        phone_number_id: "222",
      }),
    );
    expect(await screen.findByText(/whatsapp connected/i)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("works when the code arrives before the number", async () => {
    vi.mocked(metaApi.connectWhatsApp).mockResolvedValue(channel({}));
    renderHarness();
    fireEvent.click(await ready(/connect whatsapp/i));
    act(() => loginCallback?.({ authResponse: { code: "C" } }));
    facebookMessage({ type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "1", phone_number_id: "2" } });
    await waitFor(() => expect(metaApi.connectWhatsApp).toHaveBeenCalled());
  });

  it("ignores messages that do not come from Facebook", async () => {
    renderHarness();
    fireEvent.click(await ready(/connect whatsapp/i));
    const finish = { type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "1", phone_number_id: "2" } };
    facebookMessage(finish, "https://evilfacebook.com");
    facebookMessage(finish, "https://facebook.com.evil.example");
    facebookMessage(finish, "http://www.facebook.com");
    act(() => loginCallback?.({ authResponse: { code: "C" } }));
    await act(async () => {});
    expect(metaApi.connectWhatsApp).not.toHaveBeenCalled();
  });

  it("shows Meta's own error text", async () => {
    renderHarness();
    fireEvent.click(await ready(/connect whatsapp/i));
    facebookMessage({
      type: "WA_EMBEDDED_SIGNUP",
      event: "CANCEL",
      data: { error_message: "Phone number is already registered." },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Phone number is already registered.");
  });

  it("reports a cancelled sign-up", async () => {
    renderHarness();
    fireEvent.click(await ready(/connect whatsapp/i));
    facebookMessage({ type: "WA_EMBEDDED_SIGNUP", event: "CANCEL", data: { current_step: "PHONE_NUMBER_SETUP" } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/closed whatsapp sign-up/i);
  });

  it("detects a popup that never opened", async () => {
    renderHarness();
    fireEvent.click(await ready(/connect whatsapp/i));
    act(() => loginCallback?.({ authResponse: null })); // answered instantly, with nothing
    expect(await screen.findByRole("alert")).toHaveTextContent(/allow pop-ups/i);
  });

  it("surfaces a server refusal such as an account connected elsewhere", async () => {
    vi.mocked(metaApi.connectWhatsApp).mockRejectedValue(
      new ApiError(409, "channels.already_connected", "This account is already connected to another Vicero workspace."),
    );
    renderHarness();
    fireEvent.click(await ready(/connect whatsapp/i));
    facebookMessage({ type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "1", phone_number_id: "2" } });
    act(() => loginCallback?.({ authResponse: { code: "C" } }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already connected to another vicero workspace/i);
  });

  it("gives up if Meta never says which number was connected", async () => {
    renderHarness();
    fireEvent.click(await ready(/connect whatsapp/i));
    vi.useFakeTimers();
    act(() => loginCallback?.({ authResponse: { code: "C" } }));
    await act(async () => {
      vi.advanceTimersByTime(16_000);
    });
    vi.useRealTimers();
    expect(await screen.findByRole("alert")).toHaveTextContent(/didn't report which number/i);
    expect(metaApi.connectWhatsApp).not.toHaveBeenCalled();
  });
});

describe("Messenger and Instagram", () => {
  const PAGES = {
    session_id: "sess-1",
    pages: [
      { page_id: "PG1", name: "Acme Shop", picture: null, instagram: { id: "IG1", username: "acme" } },
      { page_id: "PG2", name: "No Insta", picture: null, instagram: null },
    ],
  };

  it("asks for the right permissions, lists Pages, and connects the selection", async () => {
    vi.mocked(metaApi.listFacebookPages).mockResolvedValue(PAGES);
    vi.mocked(metaApi.connectFacebookPages).mockResolvedValue([channel({ type: "facebook" })]);
    const onChanged = vi.fn();
    renderHarness({ onChanged });
    fireEvent.click(await ready(/connect messenger/i));

    const scope = String(loginOptions?.scope).split(",");
    expect(scope).toEqual(
      expect.arrayContaining([
        "pages_show_list",
        "pages_messaging",
        "pages_manage_metadata",
        "instagram_basic",
        "instagram_manage_messages",
      ]),
    );
    expect(scope).not.toContain("business_management");

    act(() => loginCallback?.({ authResponse: { accessToken: "SHORT_TOKEN", grantedScopes: String(loginOptions?.scope) } }));
    const dialog = await screen.findByRole("dialog");
    expect(metaApi.listFacebookPages).toHaveBeenCalledWith("SHORT_TOKEN");

    // Messenger was preselected by the button; Submit is disabled until a Page is ticked.
    const submit = within(dialog).getByRole("button", { name: /connect selected/i });
    expect(submit).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /acme shop/i }));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() =>
      expect(metaApi.connectFacebookPages).toHaveBeenCalledWith({
        session_id: "sess-1",
        agent_id: "a1",
        page_ids: ["PG1"],
        kinds: ["messenger"],
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onChanged).toHaveBeenCalled();
  });

  it("blocks Pages without an Instagram account once Instagram is chosen", async () => {
    vi.mocked(metaApi.listFacebookPages).mockResolvedValue(PAGES);
    renderHarness();
    fireEvent.click(await ready(/connect instagram/i));
    act(() => loginCallback?.({ authResponse: { accessToken: "T" } }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("checkbox", { name: /no insta/i })).toBeDisabled();
    expect(within(dialog).getByRole("checkbox", { name: /acme shop/i })).toBeEnabled();
    expect(within(dialog).getByText(/@acme/)).toBeInTheDocument();
  });

  it("shows a 409 from the server inside the picker and keeps it open", async () => {
    vi.mocked(metaApi.listFacebookPages).mockResolvedValue(PAGES);
    vi.mocked(metaApi.connectFacebookPages).mockRejectedValue(
      new ApiError(409, "channels.already_connected", "This account is already connected to another Vicero workspace."),
    );
    renderHarness();
    fireEvent.click(await ready(/connect messenger/i));
    act(() => loginCallback?.({ authResponse: { accessToken: "T" } }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /acme shop/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: /connect selected/i }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/another vicero workspace/i);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("rejects a login that skipped required permissions", async () => {
    renderHarness();
    fireEvent.click(await ready(/connect messenger/i));
    act(() => loginCallback?.({ authResponse: { accessToken: "T", grantedScopes: "pages_show_list" } }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/missing: .*pages_messaging/i);
    expect(metaApi.listFacebookPages).not.toHaveBeenCalled();
  });
});

describe("connected channels", () => {
  it("shows status badges for every state", async () => {
    renderHarness({
      channels: [
        channel({ type: "whatsapp", status: "active" }),
        channel({ id: "c2", type: "facebook", status: "needs_reconnect", name: "Acme Shop" }),
        channel({ id: "c3", type: "instagram", status: "disconnected", name: "@acme", enabled: false }),
      ],
    });
    expect(await within(await screen.findByTestId("whatsapp")).findByText("Connected")).toBeInTheDocument();
    const fb = screen.getByTestId("facebook");
    expect(within(fb).getByText("Needs reconnect")).toBeInTheDocument();
    expect(within(fb).getByRole("button", { name: /reconnect/i })).toBeInTheDocument();
    const ig = screen.getByTestId("instagram");
    expect(within(ig).getByText("Disconnected")).toBeInTheDocument();
  });

  it("asks for confirmation before disconnecting", async () => {
    vi.mocked(metaApi.disconnectMetaChannel).mockResolvedValue(channel({ status: "disconnected" }));
    const onChanged = vi.fn();
    renderHarness({ channels: [channel({})], onChanged });
    fireEvent.click(await screen.findByRole("button", { name: /disconnect acme co/i }));
    const dialog = await screen.findByRole("dialog");
    expect(metaApi.disconnectMetaChannel).not.toHaveBeenCalled(); // nothing happens on the first click
    fireEvent.click(within(dialog).getByRole("button", { name: /^disconnect$/i }));
    await waitFor(() => expect(metaApi.disconnectMetaChannel).toHaveBeenCalledWith("c1"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("can back out of a disconnect", async () => {
    renderHarness({ channels: [channel({})] });
    fireEvent.click(await screen.findByRole("button", { name: /disconnect acme co/i }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(metaApi.disconnectMetaChannel).not.toHaveBeenCalled();
  });
});

describe("copy", () => {
  it("mentions testers only while the app is not live", async () => {
    vi.mocked(metaApi.getMetaConfig).mockResolvedValue({ ...CONFIG, app_live: false });
    const { unmount } = renderHarness();
    expect(await screen.findAllByText(/only people added as testers/i)).not.toHaveLength(0);
    unmount();

    vi.mocked(metaApi.getMetaConfig).mockResolvedValue({ ...CONFIG, app_live: true });
    renderHarness();
    await ready(/connect whatsapp/i);
    expect(screen.queryByText(/only people added as testers/i)).not.toBeInTheDocument();
    expect(screen.getByText(/in about 1 minute/i)).toBeInTheDocument();
  });
});

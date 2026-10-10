"use client";

import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Unplug } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api/client";
import type { ApiChannel, ChannelType } from "@/lib/api/channels";
import {
  connectFacebookPages,
  connectWhatsApp,
  disconnectMetaChannel,
  getMetaConfig,
  listFacebookPages,
  type MetaConfig,
  type MetaKind,
  type MetaPage,
} from "@/lib/api/meta-connect";
import {
  loadFacebookSdk,
  loginForPages,
  MetaFlowError,
  startWhatsAppSignup,
  type FbSdk,
} from "@/lib/meta/facebook-sdk";

export type MetaChannelType = Extract<ChannelType, "whatsapp" | "facebook" | "instagram">;
export const META_TYPES: readonly ChannelType[] = ["whatsapp", "facebook", "instagram"];

export function isMetaType(type: ChannelType): type is MetaChannelType {
  return META_TYPES.includes(type);
}

const CONNECT_LABEL: Record<MetaChannelType, string> = {
  whatsapp: "Connect WhatsApp",
  facebook: "Connect Messenger",
  instagram: "Connect Instagram",
};

const HELP: Record<MetaChannelType, string> = {
  whatsapp: "Connect your WhatsApp Business number in about 1 minute.",
  facebook: "Log in with Facebook and pick the Page that should chat with customers.",
  instagram: "Log in with Facebook and pick the Page linked to your Instagram professional account.",
};

const NOT_SET_UP =
  "One-click connect isn't set up on this server yet. Use “Advanced: connect with tokens” instead.";
const WHATSAPP_NOT_SET_UP =
  "WhatsApp sign-up isn't set up on this server yet. Use “Advanced: connect with tokens” instead.";
const NOT_LIVE_NOTE = "Until our app is approved by Meta, only people added as testers can connect.";

type Flow =
  | { kind: "idle" }
  | { kind: "working"; message: string }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

interface PickerState {
  sessionId: string;
  pages: MetaPage[];
  kinds: MetaKind[];
  selected: string[];
  submitting: boolean;
  error: string | null;
}

function describeError(e: unknown): string {
  if (e instanceof MetaFlowError || e instanceof ApiError) return e.message;
  return e instanceof Error && e.message ? e.message : "Something went wrong. Please try again.";
}

export interface MetaConnect {
  config: MetaConfig | undefined;
  configLoading: boolean;
  sdkReady: boolean;
  busy: boolean;
  flow: Flow;
  picker: PickerState | null;
  disconnecting: ApiChannel | null;
  disconnectError: string | null;
  startWhatsApp: () => void;
  startPages: (kind: MetaKind) => void;
  setPickerKinds: (kinds: MetaKind[]) => void;
  togglePickerPage: (pageId: string) => void;
  submitPicker: (agentId: string) => Promise<void>;
  closePicker: () => void;
  askDisconnect: (channel: ApiChannel | null) => void;
  confirmDisconnect: () => Promise<void>;
}

/** All state for the one-click Meta flows of one agent's Channels tab. */
export function useMetaConnect({ agentId, onChanged }: { agentId: string; onChanged: () => void }): MetaConnect {
  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ["meta-config"],
    queryFn: getMetaConfig,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const [sdk, setSdk] = useState<FbSdk | null>(null);
  const [flow, setFlow] = useState<Flow>({ kind: "idle" });
  const [picker, setPicker] = useState<PickerState | null>(null);
  const [disconnecting, setDisconnecting] = useState<ApiChannel | null>(null);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);

  const enabled = Boolean(config?.enabled && config.app_id);
  const appId = config?.app_id ?? null;
  const version = config?.graph_version ?? "";

  // Load Meta's script only once this tab shows a usable connect UI, never at app start.
  useEffect(() => {
    if (!enabled || !appId) return;
    let alive = true;
    loadFacebookSdk(appId, version)
      .then((fb) => alive && setSdk(fb))
      .catch((e) => alive && setFlow({ kind: "error", message: describeError(e) }));
    return () => {
      alive = false;
    };
  }, [enabled, appId, version]);

  const busy = flow.kind === "working";
  const closePicker = useCallback(() => setPicker(null), []);

  const failWith = (e: unknown) => setFlow({ kind: "error", message: describeError(e) });

  // `FB.login` must run synchronously inside the click, so neither start* function awaits before it.
  const startWhatsApp = () => {
    if (!config?.whatsapp_enabled || !config.config_id) return;
    if (!sdk) return setFlow({ kind: "error", message: "Facebook sign-in is still loading. Try again in a moment." });
    setFlow({ kind: "working", message: "Waiting for WhatsApp sign-up in the Meta window…" });
    const configId = config.config_id;
    startWhatsAppSignup(sdk, configId)
      .then(async (result) => {
        setFlow({ kind: "working", message: "Connecting your WhatsApp number…" });
        await connectWhatsApp({ agent_id: agentId, ...result });
        setFlow({ kind: "success", message: "WhatsApp connected. Your agent now answers on this number." });
        onChanged();
      })
      .catch(failWith);
  };

  const startPages = (kind: MetaKind) => {
    if (!config?.enabled) return;
    if (!sdk) return setFlow({ kind: "error", message: "Facebook sign-in is still loading. Try again in a moment." });
    setFlow({ kind: "working", message: "Waiting for Facebook login…" });
    loginForPages(sdk)
      .then(async (token) => {
        setFlow({ kind: "working", message: "Loading your Pages…" });
        const result = await listFacebookPages(token);
        if (result.pages.length === 0) {
          throw new MetaFlowError("meta", "This Facebook account doesn't manage any Pages.");
        }
        setFlow({ kind: "idle" });
        setPicker({
          sessionId: result.session_id,
          pages: result.pages,
          kinds: [kind],
          selected: [],
          submitting: false,
          error: null,
        });
      })
      .catch(failWith);
  };

  // Instagram needs a linked account: drop any selected Page that has none when Instagram is chosen.
  const setPickerKinds = (kinds: MetaKind[]) =>
    setPicker((p) => {
      if (!p) return p;
      const needsIg = kinds.includes("instagram");
      const selected = p.selected.filter((id) => !needsIg || p.pages.find((x) => x.page_id === id)?.instagram);
      return { ...p, kinds, selected, error: null };
    });

  const togglePickerPage = (pageId: string) =>
    setPicker((p) =>
      p
        ? { ...p, selected: p.selected.includes(pageId) ? p.selected.filter((x) => x !== pageId) : [...p.selected, pageId] }
        : p,
    );

  const submitPicker = async (forAgentId: string) => {
    if (!picker) return;
    setPicker({ ...picker, submitting: true, error: null });
    try {
      const channels = await connectFacebookPages({
        session_id: picker.sessionId,
        agent_id: forAgentId,
        page_ids: picker.selected,
        kinds: picker.kinds,
      });
      setPicker(null);
      setFlow({
        kind: "success",
        message: channels.length === 1 ? "Connected." : `Connected ${channels.length} channels.`,
      });
      onChanged();
    } catch (e) {
      setPicker((p) => (p ? { ...p, submitting: false, error: describeError(e) } : p));
    }
  };

  const askDisconnect = (channel: ApiChannel | null) => {
    setDisconnectError(null);
    setDisconnecting(channel);
  };

  const confirmDisconnect = async () => {
    if (!disconnecting) return;
    try {
      await disconnectMetaChannel(disconnecting.id);
      setDisconnecting(null);
      setFlow({ kind: "success", message: "Disconnected." });
      onChanged();
    } catch (e) {
      setDisconnectError(describeError(e));
    }
  };

  return {
    config,
    configLoading,
    sdkReady: sdk !== null,
    busy,
    flow,
    picker,
    disconnecting,
    disconnectError,
    startWhatsApp,
    startPages,
    setPickerKinds,
    togglePickerPage,
    submitPicker,
    closePicker,
    askDisconnect,
    confirmDisconnect,
  };
}

/** Badge text for a one-click channel: status is never colour alone. */
function statusBadge(channel: ApiChannel) {
  if (channel.status === "needs_reconnect") return <Badge variant="warn">Needs reconnect</Badge>;
  if (channel.status === "disconnected") return <Badge variant="default">Disconnected</Badge>;
  return <Badge variant={channel.enabled ? "success" : "default"}>{channel.enabled ? "Connected" : "Connected · paused"}</Badge>;
}

/** The right-hand side of a Meta channel's row in the Channels tab. */
export function MetaRowActions({
  type,
  channel,
  meta,
  onToggle,
}: {
  type: MetaChannelType;
  channel: ApiChannel | undefined;
  meta: MetaConnect;
  onToggle: (id: string, on: boolean) => void;
}) {
  const oneClick = channel?.connection_source === "meta_oauth";
  const available = type === "whatsapp" ? Boolean(meta.config?.whatsapp_enabled) : Boolean(meta.config?.enabled);
  const label = CONNECT_LABEL[type];
  const hintId = `meta-hint-${type}`;
  const disabledReason = type === "whatsapp" && meta.config?.enabled ? WHATSAPP_NOT_SET_UP : NOT_SET_UP;

  const connect = () => (type === "whatsapp" ? meta.startWhatsApp() : meta.startPages(type === "facebook" ? "messenger" : "instagram"));
  const connectButton = (text: string, variant: "primary" | "outline" = "primary") => (
    <span title={available ? undefined : disabledReason}>
      <Button
        size="sm"
        variant={variant}
        onClick={connect}
        disabled={!available || meta.busy || (available && !meta.sdkReady)}
        aria-describedby={available ? undefined : hintId}
      >
        {meta.busy && <Loader2 className="size-4 animate-spin" aria-hidden />} {text}
      </Button>
      {!available && (
        <span id={hintId} className="sr-only">
          {disabledReason}
        </span>
      )}
    </span>
  );

  if (oneClick && channel) {
    const live = channel.status === "active";
    return (
      <div className="flex flex-wrap items-center justify-end gap-2">
        {statusBadge(channel)}
        {live ? (
          <Switch
            checked={channel.enabled}
            onCheckedChange={(on) => onToggle(channel.id, on)}
            aria-label={`${channel.enabled ? "Pause" : "Resume"} ${CONNECT_LABEL[type].replace("Connect ", "")}`}
          />
        ) : (
          connectButton("Reconnect")
        )}
        <Button size="sm" variant="ghost" onClick={() => meta.askDisconnect(channel)} aria-label={`Disconnect ${channel.name ?? label}`}>
          <Unplug className="size-4" aria-hidden /> Disconnect
        </Button>
      </div>
    );
  }
  return connectButton(label);
}

/** Help line, the not-yet-approved notice, and the collapsible manual form, shown under a Meta row. */
export function MetaRowFooter({
  type,
  meta,
  onAdvanced,
}: {
  type: MetaChannelType;
  meta: MetaConnect;
  onAdvanced: () => void;
}) {
  const available = type === "whatsapp" ? Boolean(meta.config?.whatsapp_enabled) : Boolean(meta.config?.enabled);
  return (
    <div className="mt-2 space-y-1.5 pl-11 text-xs text-faint">
      {available && <p>{HELP[type]}</p>}
      {available && meta.config && !meta.config.app_live && <p>{NOT_LIVE_NOTE}</p>}
      <details className="group">
        <summary className="cursor-pointer select-none text-muted hover:text-text">Advanced: connect with tokens</summary>
        <div className="mt-2">
          <p className="mb-2">Paste your own access token and webhook details instead of signing in with Meta.</p>
          <Button size="sm" variant="outline" onClick={onAdvanced}>
            Enter tokens
          </Button>
        </div>
      </details>
    </div>
  );
}

/** Live status line, the Page picker, and the disconnect confirmation. Render once per Channels tab. */
export function MetaOverlays({ meta, agentId }: { meta: MetaConnect; agentId: string }) {
  const { flow, picker, disconnecting } = meta;
  return (
    <>
      <div className="min-h-5 text-sm" role={flow.kind === "error" ? "alert" : "status"} aria-live="polite">
        {flow.kind === "working" && (
          <p className="flex items-center gap-2 text-muted">
            <Loader2 className="size-4 animate-spin" aria-hidden /> {flow.message}
          </p>
        )}
        {flow.kind === "success" && <p className="text-success-text">{flow.message}</p>}
        {flow.kind === "error" && <p className="text-error-text">{flow.message}</p>}
      </div>

      <Dialog open={picker !== null} onOpenChange={(open) => !open && meta.closePicker()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Choose what to connect</DialogTitle>
            <DialogDescription>Pick the Facebook Pages your agent should answer on.</DialogDescription>
          </DialogHeader>
          {picker && <PagePicker picker={picker} meta={meta} agentId={agentId} />}
        </DialogContent>
      </Dialog>

      <Dialog open={disconnecting !== null} onOpenChange={(open) => !open && meta.askDisconnect(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Disconnect {disconnecting?.name ?? "this channel"}?</DialogTitle>
            <DialogDescription>
              Vicero stops receiving and sending messages here and removes its saved access. You can connect it again at
              any time.
            </DialogDescription>
          </DialogHeader>
          {meta.disconnectError && (
            <p role="alert" className="mb-3 text-sm text-error-text">
              {meta.disconnectError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => meta.askDisconnect(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void meta.confirmDisconnect()}>
              Disconnect
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

const KIND_LABEL: Record<MetaKind, string> = { messenger: "Messenger", instagram: "Instagram" };

function PagePicker({ picker, meta, agentId }: { picker: PickerState; meta: MetaConnect; agentId: string }) {
  const wantsInstagram = picker.kinds.includes("instagram");
  const toggleKind = (kind: MetaKind) => {
    const next = picker.kinds.includes(kind) ? picker.kinds.filter((k) => k !== kind) : [...picker.kinds, kind];
    meta.setPickerKinds(next);
  };
  const canSubmit = picker.selected.length > 0 && picker.kinds.length > 0 && !picker.submitting;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void meta.submitPicker(agentId);
      }}
      className="space-y-4"
    >
      <fieldset className="space-y-2">
        <legend className="text-xs font-bold text-muted">Connect as</legend>
        <div className="flex gap-4">
          {(Object.keys(KIND_LABEL) as MetaKind[]).map((kind) => (
            <label key={kind} className="flex items-center gap-2 text-sm text-text">
              <input
                type="checkbox"
                checked={picker.kinds.includes(kind)}
                onChange={() => toggleKind(kind)}
                className="size-4 accent-[rgb(var(--accent-strong))]"
              />
              {KIND_LABEL[kind]}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs font-bold text-muted">Pages</legend>
        <ul className="max-h-64 space-y-1.5 overflow-y-auto">
          {picker.pages.map((page) => {
            const blocked = wantsInstagram && !page.instagram;
            return (
              <li key={page.page_id}>
                <label
                  className={`flex items-center gap-3 rounded-md border border-border p-2 text-sm ${blocked ? "opacity-60" : "hover:bg-surface-2"}`}
                >
                  <input
                    type="checkbox"
                    disabled={blocked}
                    checked={picker.selected.includes(page.page_id)}
                    onChange={() => meta.togglePickerPage(page.page_id)}
                    className="size-4 accent-[rgb(var(--accent-strong))]"
                  />
                  {page.picture ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={page.picture} alt="" className="size-8 rounded-full object-cover" />
                  ) : (
                    <span className="size-8 rounded-full bg-surface-3" aria-hidden />
                  )}
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-text">{page.name}</span>
                    <span className="block truncate text-xs text-faint">
                      {page.instagram
                        ? `Instagram: ${page.instagram.username ? `@${page.instagram.username}` : page.instagram.id}`
                        : "No Instagram account linked"}
                    </span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>

      {picker.error && (
        <p role="alert" className="text-sm text-error-text">
          {picker.error}
        </p>
      )}
      <Button type="submit" variant="primary" className="w-full" disabled={!canSubmit}>
        {picker.submitting && <Loader2 className="size-4 animate-spin" aria-hidden />} Connect selected
      </Button>
    </form>
  );
}

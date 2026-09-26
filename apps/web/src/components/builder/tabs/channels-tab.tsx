"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Hash, Loader2, MessageSquare, Send, Trash2, Upload } from "lucide-react";
import { Field, SectionCard } from "@/components/builder/field";
import { CHANNEL_META } from "@/lib/channel-meta";
import { CampaignsSection } from "@/components/builder/campaigns-section";
import { WidgetChatIcon, WidgetDotsIcon, WidgetMessageIcon } from "@/lib/widget-icons";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useBuilder } from "@/lib/store/builder";
import { getAgent, uploadWidgetLogo } from "@/lib/api/agents";
import { API_BASE } from "@/lib/api/config";
import type { AgentDraft, FloatingButtonStyle, InputBarButton, WidgetFont } from "@/lib/mock/builder";
import {
  createChannel,
  deleteChannel,
  disableChannel,
  enableChannel,
  listChannels,
  type ChannelType,
} from "@/lib/api/channels";

type Widget = AgentDraft["widget"];

const FONT_OPTIONS: { value: WidgetFont; label: string }[] = [
  { value: "system", label: "System default" },
  { value: "inter", label: "Inter" },
  { value: "arial", label: "Arial" },
  { value: "georgia", label: "Georgia" },
  { value: "courier", label: "Courier New" },
];

type LauncherOption = {
  value: FloatingButtonStyle | null;
  label: string;
  kind: "chat" | "message" | "dots";
  shape: "circle" | "square" | "pill" | "pulse";
};

const LAUNCHER_OPTIONS: LauncherOption[] = [
  { value: null, label: "Text (default)", kind: "chat", shape: "pill" },
  { value: "circle-chat", label: "Chat circle", kind: "chat", shape: "circle" },
  { value: "circle-message", label: "Message", kind: "message", shape: "circle" },
  { value: "circle-dots", label: "Dots", kind: "dots", shape: "circle" },
  { value: "rounded-square", label: "Square", kind: "chat", shape: "square" },
  { value: "pill-text", label: "Pill + text", kind: "chat", shape: "pill" },
  { value: "pulse-ring", label: "Pulse ring", kind: "chat", shape: "pulse" },
];

/** The real widget icon for a launcher option, rendered white on the button color. */
function LauncherIcon({ kind, cut }: { kind: LauncherOption["kind"]; cut: string }) {
  if (kind === "message") return <WidgetMessageIcon className="size-4" cut={cut} />;
  if (kind === "dots") return <WidgetDotsIcon className="size-4" />;
  return <WidgetChatIcon className="size-4" />;
}

/** Map the builder draft into the snake_case config the widget bundle expects. */
function toPreviewConfig(draft: AgentDraft) {
  const w = draft.widget;
  return {
    name: draft.persona.displayName || draft.name,
    welcome_message: draft.persona.welcomeMessage || "Hi! How can I help you today?",
    suggested_prompts: draft.persona.suggestedPrompts ?? [],
    theme: {
      primary_color: w.primaryColor,
      position: w.position,
      launcher_text: w.launcherText,
      branding: w.branding,
      mode: w.mode,
      widget_style: w.widgetStyle,
      background_color: w.backgroundColor,
      text_color: w.textColor,
      bubble_color: w.bubbleColor,
      typing_area_color: w.typingAreaColor,
      font_family: w.fontFamily,
      logo_url: w.logoUrl,
      floating_button_style: w.floatingButtonStyle,
      floating_button_color: w.floatingButtonColor,
      input_bar_buttons: w.inputBarButtons,
    },
  };
}

export function ChannelsTab() {
  const draft = useBuilder((s) => s.draft);
  const agentId = useBuilder((s) => s.agentId);
  const update = useBuilder((s) => s.update);
  const [copied, setCopied] = useState(false);

  const { data: agent } = useQuery({
    queryKey: ["agent-key", agentId],
    queryFn: () => getAgent(agentId!),
    enabled: Boolean(agentId),
  });

  if (!draft) return null;
  const w = draft.widget;
  const setW = (fn: (widget: Widget) => void) => update((d) => fn(d.widget));
  const publicKey = agent?.public_key ?? "YOUR_PUBLIC_KEY";
  const webOrigin = typeof window !== "undefined" ? window.location.origin : "";
  const snippet = `<script\n  src="${webOrigin}/widget.js"\n  data-agent="${publicKey}"\n  data-api="${API_BASE}"\n  defer></script>`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  const transparent = w.widgetStyle === "transparent";

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          <SectionCard title="Web widget" description="Design an embeddable chat bubble for any website.">
            {/* Style + font */}
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="Widget style">
                <div className="flex rounded-md border border-border bg-surface-2 p-0.5">
                  {(["solid", "transparent"] as const).map((s) => (
                    <button
                      key={s}
                      onClick={() => setW((x) => void (x.widgetStyle = s))}
                      className={`flex-1 rounded px-2 py-1.5 text-xs capitalize transition-colors ${
                        w.widgetStyle === s ? "bg-accent-strong text-on-accent" : "text-muted hover:text-text"
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </Field>
              <Field label="Font">
                <select
                  value={w.fontFamily}
                  onChange={(e) => setW((x) => void (x.fontFamily = e.target.value as WidgetFont))}
                  className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 text-sm text-text"
                >
                  {FONT_OPTIONS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            {/* Colors */}
            <div className="grid gap-5 sm:grid-cols-2">
              <ColorInput label="Accent / launcher bubble" value={w.primaryColor} onChange={(v) => setW((x) => void (x.primaryColor = v))} />
              <NullableColor label="Bubble (user messages)" value={w.bubbleColor} fallback={w.primaryColor} onChange={(v) => setW((x) => void (x.bubbleColor = v))} />
              <NullableColor
                label="Background"
                value={w.backgroundColor}
                fallback={w.mode === "light" ? "#FFFFFF" : "#16181D"}
                onChange={(v) => setW((x) => void (x.backgroundColor = v))}
                disabled={transparent}
                disabledHint="No panel to color in transparent style"
              />
              <NullableColor label="Text" value={w.textColor} fallback={w.mode === "light" ? "#14161A" : "#E7E9EE"} onChange={(v) => setW((x) => void (x.textColor = v))} />
              <NullableColor label="Typing area" value={w.typingAreaColor} fallback={w.mode === "light" ? "#F4F5F7" : "#1E2127"} onChange={(v) => setW((x) => void (x.typingAreaColor = v))} />
              <Field label="Theme mode">
                <select
                  value={w.mode}
                  onChange={(e) => setW((x) => void (x.mode = e.target.value as "dark" | "light"))}
                  className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 text-sm text-text"
                >
                  <option value="dark">Dark</option>
                  <option value="light">Light</option>
                </select>
              </Field>
            </div>

            {/* Launcher design gallery */}
            <Field label="Floating button design">
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-7">
                {LAUNCHER_OPTIONS.map((opt) => {
                  const selected = w.floatingButtonStyle === opt.value;
                  const btnColor = w.floatingButtonColor ?? w.primaryColor;
                  const shapeCls =
                    opt.shape === "square" ? "rounded-lg" : opt.shape === "pill" ? "rounded-full px-2 gap-1" : "rounded-full";
                  return (
                    <button
                      key={String(opt.value)}
                      onClick={() => setW((x) => void (x.floatingButtonStyle = opt.value))}
                      title={opt.label}
                      className={`flex flex-col items-center gap-1.5 rounded-lg border p-2 transition-colors ${
                        selected ? "border-accent bg-accent/[0.06]" : "border-border hover:border-border-strong"
                      }`}
                    >
                      <span
                        className={`grid h-9 min-w-9 place-items-center text-white ${shapeCls} ${
                          opt.shape === "pulse" ? "ring-2 ring-white/30 ring-offset-1 ring-offset-transparent" : ""
                        }`}
                        style={{ background: btnColor }}
                      >
                        <span className="flex items-center gap-1">
                          <LauncherIcon kind={opt.kind} cut={btnColor} />
                          {opt.shape === "pill" && <span className="text-[8px] font-semibold">Chat</span>}
                        </span>
                      </span>
                      <span className="text-[10px] leading-tight text-faint">{opt.label}</span>
                    </button>
                  );
                })}
              </div>
            </Field>

            <div className="grid gap-5 sm:grid-cols-2">
              <NullableColor label="Launcher button color" value={w.floatingButtonColor} fallback={w.primaryColor} onChange={(v) => setW((x) => void (x.floatingButtonColor = v))} />
              <Field label="Launcher text (pill designs)">
                <Input value={w.launcherText} onChange={(e) => setW((x) => void (x.launcherText = e.target.value))} />
              </Field>
              <Field label="Position">
                <select
                  value={w.position}
                  onChange={(e) => setW((x) => void (x.position = e.target.value as "bottom-right" | "bottom-left"))}
                  className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 text-sm text-text"
                >
                  <option value="bottom-right">Bottom right</option>
                  <option value="bottom-left">Bottom left</option>
                </select>
              </Field>
            </div>

            {/* Logo */}
            {agentId && <LogoUpload agentId={agentId} logoUrl={w.logoUrl} onChange={(v) => setW((x) => void (x.logoUrl = v))} />}

            {/* Input-bar buttons */}
            <Field label="Input bar buttons">
              <div className="flex flex-wrap gap-4">
                {(["attachment", "emoji"] as InputBarButton[]).map((b) => {
                  const on = w.inputBarButtons.includes(b);
                  return (
                    <label key={b} className="flex cursor-pointer items-center gap-2 text-sm text-text">
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={(e) =>
                          setW((x) => {
                            const set = new Set(x.inputBarButtons);
                            if (e.target.checked) set.add(b);
                            else set.delete(b);
                            x.inputBarButtons = Array.from(set) as InputBarButton[];
                          })
                        }
                        className="size-4 accent-accent"
                      />
                      <span className="capitalize">{b === "attachment" ? "File attachment" : "Emoji picker"}</span>
                    </label>
                  );
                })}
              </div>
            </Field>

            <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
              <div className="flex-1">
                <div className="text-sm font-medium text-text">“Powered by BotForge” badge</div>
                <div className="text-xs text-muted">Show a small attribution in the widget footer.</div>
              </div>
              <Switch checked={w.branding} onCheckedChange={(v) => setW((x) => void (x.branding = v))} />
            </div>
          </SectionCard>

          <SectionCard title="Embed snippet" description="Paste this before </body> on any page.">
            <div className="relative">
              <pre className="overflow-x-auto rounded-md border border-border bg-surface-2/60 p-3 font-mono text-xs text-muted">
                {snippet}
              </pre>
              <button
                onClick={copy}
                className="absolute right-2 top-2 flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-xs text-muted transition-colors hover:text-text"
              >
                {copied ? <Check className="size-3.5 text-success-text" /> : <Copy className="size-3.5" />}
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <p className="text-xs text-faint">
              The snippet never changes — the widget loads every design choice above from this agent’s live
              config, so saving here updates every embedded widget on the visitor’s next page load.
            </p>
          </SectionCard>
        </div>

        {/* Live preview — runs the real widget bundle in an iframe, updated via postMessage. */}
        <div>
          <div className="sticky top-32 rounded-lg border border-border bg-surface p-4">
            <div className="mb-3 text-xs font-medium uppercase tracking-wide text-faint">Live preview</div>
            <LivePreview draft={draft} />
          </div>
        </div>
      </div>

      {agentId && <CampaignsSection agentId={agentId} />}
      {agentId && <MessagingChannels agentId={agentId} />}
    </div>
  );
}

function ColorInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-12 cursor-pointer rounded-md border border-border bg-surface-2"
        />
        <Input value={value} onChange={(e) => onChange(e.target.value)} className="font-mono" />
      </div>
    </Field>
  );
}

function NullableColor({
  label,
  value,
  fallback,
  onChange,
  disabled,
  disabledHint,
}: {
  label: string;
  value: string | null;
  fallback: string;
  onChange: (v: string | null) => void;
  disabled?: boolean;
  disabledHint?: string;
}) {
  const active = value !== null;
  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <input
          type="color"
          disabled={disabled || !active}
          value={value ?? fallback}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-12 cursor-pointer rounded-md border border-border bg-surface-2 disabled:opacity-40"
        />
        {active ? (
          <>
            <Input value={value} onChange={(e) => onChange(e.target.value)} className="font-mono" disabled={disabled} />
            <button
              type="button"
              onClick={() => onChange(null)}
              title="Reset to theme default"
              className="rounded-md border border-border px-2 py-1.5 text-xs text-muted hover:text-text"
            >
              Reset
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => onChange(fallback)}
            disabled={disabled}
            className="flex-1 rounded-md border border-dashed border-border px-2 py-1.5 text-left text-xs text-faint hover:text-muted disabled:opacity-40"
          >
            {disabled ? disabledHint ?? "Disabled" : "Default (auto) — click to override"}
          </button>
        )}
      </div>
    </Field>
  );
}

function LogoUpload({
  agentId,
  logoUrl,
  onChange,
}: {
  agentId: string;
  logoUrl: string | null;
  onChange: (v: string | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => uploadWidgetLogo(agentId, file),
    onSuccess: (res) => {
      setError(null);
      // Cache-bust so the freshly-uploaded image shows immediately.
      onChange(`${res.logo_url}?t=${Date.now()}`);
    },
    onError: (e) => setError((e as Error).message),
  });
  const src = logoUrl ? (logoUrl.startsWith("/") ? `${API_BASE}${logoUrl}` : logoUrl) : null;

  return (
    <Field label="Logo / avatar">
      <div className="flex items-center gap-3">
        <div className="grid size-12 place-items-center overflow-hidden rounded-full border border-border bg-surface-2">
          {src ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={src} alt="Logo" className="size-full object-cover" />
          ) : (
            <MessageSquare className="size-5 text-faint" />
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = "";
          }}
        />
        <Button type="button" variant="outline" size="sm" onClick={() => inputRef.current?.click()} disabled={upload.isPending}>
          {upload.isPending ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />} Upload
        </Button>
        {logoUrl && (
          <button type="button" onClick={() => onChange(null)} className="text-xs text-muted hover:text-error-text">
            Remove
          </button>
        )}
      </div>
      <p className="mt-1 text-xs text-faint">PNG, JPG, WEBP, or GIF · up to 2 MB. Used on the launcher and as the assistant avatar.</p>
      {error && <p className="mt-1 text-xs text-error-text">{error}</p>}
    </Field>
  );
}

// A soft lavender-blue default, purely so you can eyeball contrast while designing.
const DEFAULT_BACKDROP = "linear-gradient(135deg, #EEF1FF, #DCE6FF)";
const BACKDROP_SWATCHES = ["linear-gradient(135deg, #EEF1FF, #DCE6FF)", "#FFFFFF", "#0B0F19", "#F4F5F7", "#1E2530"];

function LivePreview({ draft }: { draft: AgentDraft }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  // Builder-only: the preview PAGE background. Never persisted, never part of the embed —
  // a real site already has its own background, which a widget can't (and shouldn't) control.
  const [backdrop, setBackdrop] = useState(DEFAULT_BACKDROP);
  const config = useMemo(() => toPreviewConfig(draft), [draft]);
  const src = `/widget-preview.html?api=${encodeURIComponent(API_BASE)}`;

  // Wait for the widget bundle inside the iframe to announce it's ready.
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.data?.type === "bf-preview-ready") setReady(true);
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  // Push the current config (+ the local-only backdrop) into the iframe whenever either changes.
  useEffect(() => {
    if (!ready) return;
    iframeRef.current?.contentWindow?.postMessage({ type: "bf-preview-config", config, previewBackdrop: backdrop }, "*");
  }, [ready, config, backdrop]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        <span className="mr-1 text-[11px] text-faint">Preview page:</span>
        {BACKDROP_SWATCHES.map((bg) => (
          <button
            key={bg}
            type="button"
            title="Preview backdrop (not saved)"
            onClick={() => setBackdrop(bg)}
            className={`size-5 rounded-full border ${backdrop === bg ? "border-accent ring-1 ring-accent" : "border-border"}`}
            style={{ background: bg }}
          />
        ))}
      </div>
      <div className="relative h-[520px] overflow-hidden rounded-lg border border-border bg-surface-2/40">
        <iframe
          ref={iframeRef}
          src={src}
          title="Widget preview"
          className="size-full"
          // Same-origin so we can postMessage; no allow-scripts sandbox restriction needed.
        />
        {!ready && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center text-xs text-faint">
            <Loader2 className="size-4 animate-spin" />
          </div>
        )}
      </div>
    </div>
  );
}

const CHANNEL_SPECS: Record<
  ChannelType,
  { label: string; icon: typeof Send; fields: { key: string; label: string; secret?: boolean }[]; hint: string }
> = {
  telegram: {
    label: "Telegram",
    icon: Send,
    fields: [{ key: "bot_token", label: "Bot token (from @BotFather)", secret: true }],
    hint: "On enable, BotForge auto-registers the webhook with Telegram — nothing else to do.",
  },
  whatsapp: {
    label: "WhatsApp (Meta)",
    icon: CHANNEL_META.whatsapp.Icon,
    fields: [
      { key: "phone_number_id", label: "Phone number ID" },
      { key: "access_token", label: "Access token", secret: true },
      { key: "verify_token", label: "Verify token (you choose)" },
      { key: "app_secret", label: "App secret", secret: true },
      { key: "templates", label: "Approved template names (comma-separated)" },
    ],
    hint: "Set the webhook URL below in Meta → WhatsApp → Configuration, using your verify token. Templates must be approved in Meta Business Manager first — list their names here so operators can reply after the 24-hour window closes.",
  },
  facebook: {
    label: "Facebook Messenger",
    icon: CHANNEL_META.facebook.Icon,
    fields: [
      { key: "page_id", label: "Page ID" },
      { key: "page_access_token", label: "Page access token", secret: true },
      { key: "verify_token", label: "Verify token (you choose)" },
      { key: "app_secret", label: "App secret (same Meta app as WhatsApp)", secret: true },
    ],
    hint: "In Meta → Messenger → Webhooks, set the callback URL below and subscribe to the `messages` field.",
  },
  instagram: {
    label: "Instagram",
    icon: CHANNEL_META.instagram.Icon,
    fields: [
      { key: "ig_user_id", label: "Instagram account ID" },
      { key: "page_access_token", label: "Instagram-scoped page access token", secret: true },
      { key: "verify_token", label: "Verify token (you choose)" },
      { key: "app_secret", label: "App secret (same Meta app as WhatsApp)", secret: true },
    ],
    hint: "Needs the instagram_manage_messages permission. Subscribe to `messages` for the Instagram object using the callback URL below.",
  },
  slack: {
    label: "Slack",
    icon: Hash,
    fields: [
      { key: "bot_token", label: "Bot token (xoxb-…)", secret: true },
      { key: "signing_secret", label: "Signing secret", secret: true },
    ],
    hint: "Set the Event Subscriptions request URL below and subscribe to message / app_mention events.",
  },
  discord: {
    label: "Discord",
    icon: MessageSquare,
    fields: [
      { key: "public_key", label: "Application public key" },
      { key: "bot_token", label: "Bot token (optional)", secret: true },
    ],
    hint: "Set the Interactions Endpoint URL below in the Discord developer portal.",
  },
};

function MessagingChannels({ agentId }: { agentId: string }) {
  const qc = useQueryClient();
  const [connecting, setConnecting] = useState<ChannelType | null>(null);

  // The Inbox links here with `?connect=<type>` when an operator picks an unconnected
  // channel tab, so the credential form they came for is already open on arrival. The
  // param is consumed once and stripped, so a reload or a Back doesn't reopen it.
  useEffect(() => {
    const url = new URL(window.location.href);
    const requested = url.searchParams.get("connect");
    if (!requested || !(requested in CHANNEL_SPECS)) return;
    url.searchParams.delete("connect");
    window.history.replaceState(null, "", url.toString());
    // One-time mount read of the URL (window is unavailable during SSR).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setConnecting(requested as ChannelType);
  }, []);

  const { data: channels } = useQuery({
    queryKey: ["channels", agentId],
    queryFn: () => listChannels(agentId),
    enabled: Boolean(agentId),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["channels", agentId] });
  const toggle = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) => (on ? enableChannel(id) : disableChannel(id)),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: (id: string) => deleteChannel(id), onSuccess: invalidate });

  const byType = new Map((channels ?? []).map((c) => [c.type, c]));

  return (
    <SectionCard title="Messaging channels" description="Connect your agent to chat platforms.">
      <ul className="space-y-2">
        {(Object.keys(CHANNEL_SPECS) as ChannelType[]).map((type) => {
          const spec = CHANNEL_SPECS[type];
          const ch = byType.get(type);
          const Icon = spec.icon;
          return (
            <li key={type} className="rounded-md border border-border bg-surface-2/50 p-3">
              <div className="flex items-center gap-3">
                <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
                  <Icon className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text">{spec.label}</span>
                    {ch && <Badge variant={ch.enabled ? "success" : "default"}>{ch.enabled ? "Live" : "Connected"}</Badge>}
                  </div>
                </div>
                {ch ? (
                  <div className="flex items-center gap-2">
                    <Switch checked={ch.enabled} onCheckedChange={(on) => toggle.mutate({ id: ch.id, on })} />
                    <button
                      onClick={() => remove.mutate(ch.id)}
                      className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
                      title="Disconnect"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                ) : (
                  <Button variant="outline" size="sm" onClick={() => setConnecting(type)}>
                    Connect
                  </Button>
                )}
              </div>
              {ch?.webhook_url && (
                <div className="mt-2 truncate rounded-md border border-border bg-surface px-2 py-1.5 font-mono text-[11px] text-faint">
                  {ch.webhook_url}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <ConnectDialog type={connecting} agentId={agentId} onClose={() => setConnecting(null)} onConnected={invalidate} />
    </SectionCard>
  );
}

function ConnectDialog({
  type,
  agentId,
  onClose,
  onConnected,
}: {
  type: ChannelType | null;
  agentId: string;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const spec = type ? CHANNEL_SPECS[type] : null;

  const create = useMutation({
    mutationFn: () => createChannel({ agent_id: agentId, type: type!, config: values }),
    onSuccess: () => {
      setValues({});
      onClose();
      onConnected();
    },
  });

  return (
    <Dialog open={Boolean(type)} onOpenChange={(o) => !o && (setValues({}), onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Connect {spec?.label}</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
          className="space-y-3"
        >
          {spec?.fields.map((f) => (
            <div key={f.key}>
              <label className="mb-1 block text-xs text-muted">{f.label}</label>
              <Input
                type={f.secret ? "password" : "text"}
                value={values[f.key] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
            </div>
          ))}
          <p className="text-xs text-faint">{spec?.hint}</p>
          {create.isError && <p className="text-sm text-error-text">{(create.error as Error).message}</p>}
          <Button type="submit" variant="primary" className="w-full" disabled={create.isPending}>
            {create.isPending && <Loader2 className="size-4 animate-spin" />} Connect
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

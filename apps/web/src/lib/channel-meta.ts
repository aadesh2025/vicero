/** How each channel is labelled and drawn, shared by the builder and the inbox.
 *
 * Icons come from the same lucide set the builder's channel list already uses (Telegram
 * as a paper plane, WhatsApp as a handset). lucide dropped brand marks, so Instagram and
 * Messenger get generic glyphs too rather than pulling in a second icon dependency for
 * two logos.
 */

import {
  Camera,
  Code2,
  FlaskConical,
  Globe,
  Hash,
  LayoutDashboard,
  Mail,
  MessageCircle,
  MessageSquare,
  Phone,
  Send,
  UserPlus,
} from "lucide-react";
import type { ChannelType } from "./api/channels";

/**
 * Channels a conversation can arrive on — the connectable ones, the built-in widget, and
 * the builder's Playground.
 *
 * Playground is here rather than in `REPORTING_ONLY` because it is the one non-connectable
 * value that produces a transcript a person reads. It has no webhook and nothing to set up,
 * so it behaves like `widget`: always "connected", never "new".
 */
export type InboxChannel = ChannelType | "widget" | "playground";

/** Which `--ch-*` colour set a channel uses. `null` = no brand colour (neutral). */
export type ChannelColor =
  | "widget"
  | "whatsapp"
  | "instagram"
  | "facebook"
  | "telegram"
  | "email"
  | "slack"
  | "discord";

export interface ChannelMeta {
  label: string;
  Icon: typeof Send;
  /** Colour set in globals.css (`--ch-<color>`, `-text`, `-soft`). Channel colours are only
   *  for channel contexts — never general UI colour (docs/20 §5). */
  color: ChannelColor | null;
}

/** Resolved CSS values for one channel, ready for an inline `style`. They are CSS variables, so
 *  they follow the theme without a re-render. */
export interface ChannelTone {
  /** Exact brand colour: dots, logos, chart bars. */
  dot: string;
  /** Brand gradient where the brand is one (Instagram); use as `background-image`. */
  gradient?: string;
  /** Shade that passes AA as text on `surface` and on `soft`. */
  text: string;
  /** Tinted background for chips and avatars. */
  soft: string;
}

export const CHANNEL_META: Record<InboxChannel, ChannelMeta> = {
  widget: { label: "Web Chat", Icon: Globe, color: "widget" },
  facebook: { label: "Messenger", Icon: MessageCircle, color: "facebook" },
  instagram: { label: "Instagram", Icon: Camera, color: "instagram" },
  whatsapp: { label: "WhatsApp", Icon: Phone, color: "whatsapp" },
  telegram: { label: "Telegram", Icon: Send, color: "telegram" },
  slack: { label: "Slack", Icon: Hash, color: "slack" },
  discord: { label: "Discord", Icon: MessageSquare, color: "discord" },
  // The operator testing their own bot: not a customer channel, so no brand colour.
  playground: { label: "Playground", Icon: FlaskConical, color: null },
};

/** Tab order follows the reference: the always-on web chat, then the Meta surfaces.
 *
 * Playground sits last: it is the operator's own testing, so it should never push a channel
 * a real customer is waiting on further from the eye. */
export const INBOX_CHANNEL_ORDER: InboxChannel[] = [
  "widget",
  "facebook",
  "instagram",
  "whatsapp",
  "telegram",
  "slack",
  "discord",
  "playground",
];

/** Channel values a conversation can carry that are never inbox *tabs*.
 *
 * `dashboard` is a conversation held from the app, `api`/`web` are legacy values from before
 * the channel registry, and `manual` is a contact an operator typed into the CRM. None of
 * them can receive a message, so none of them gets a tab — `manual` especially, since it has
 * no webhook and no conversations at all.
 *
 * `playground` used to live here and is now a real tab (see `CHANNEL_META`): unlike these,
 * it produces a transcript someone actually reads, and hiding it made the inbox look like it
 * was dropping conversations that Conversations listed fine.
 */
const REPORTING_ONLY: Record<string, ChannelMeta> = {
  dashboard: { label: "Dashboard", Icon: LayoutDashboard, color: null },
  api: { label: "API", Icon: Code2, color: null },
  // Legacy value for the website widget: same colour as `widget`.
  web: { label: "Web", Icon: Globe, color: "widget" },
  manual: { label: "Added manually", Icon: UserPlus, color: null },
  // Email is reportable (contacts and analytics can carry it) but has no inbox tab yet.
  email: { label: "Email", Icon: Mail, color: "email" },
};

const FALLBACK: ChannelMeta = { label: "Other", Icon: MessageSquare, color: null };

/** Meta for any channel string, including reporting-only values like `dashboard`. */
export function channelMeta(type: string): ChannelMeta {
  return CHANNEL_META[type as InboxChannel] ?? REPORTING_ONLY[type] ?? FALLBACK;
}

const NEUTRAL_TONE: ChannelTone = {
  dot: "rgb(var(--faint))",
  text: "rgb(var(--muted))",
  soft: "rgb(var(--surface-2))",
};

/** Colours for any channel string. Unknown and non-brand channels get neutral greys. */
export function channelTone(type: string): ChannelTone {
  const color = channelMeta(type).color;
  if (!color) return NEUTRAL_TONE;
  const tone: ChannelTone = {
    dot: `rgb(var(--ch-${color}))`,
    text: `rgb(var(--ch-${color}-text))`,
    soft: `rgb(var(--ch-${color}-soft))`,
  };
  if (color === "instagram") tone.gradient = "var(--ch-instagram-gradient)";
  return tone;
}

/** A channel counts as connected once a row exists for it *and* it's switched on. */
export type ConnectedChannel = { type: string; enabled: boolean; created_at?: string };

/**
 * The inbox's channel tabs: every channel, always.
 *
 * These used to be filtered down to connected channels only, which hid the platforms a
 * user hasn't set up yet — precisely the ones they need to discover. Showing all of them
 * turns the tab bar into the prompt to connect, instead of requiring someone to already
 * know the builder's Channels tab exists. Connection status drives *styling*, not
 * presence — see `isChannelConnected`.
 */
export function inboxChannelTabs(): InboxChannel[] {
  return INBOX_CHANNEL_ORDER;
}

/** Whether messages can actually arrive on this channel right now.
 *
 * `widget` and `playground` are inherent to every agent — there is no row to enable and no
 * credential to paste — so neither can ever be "not connected". Returning false for
 * playground would render the connect prompt for something with nothing to connect. */
export function isChannelConnected(
  channels: ConnectedChannel[] | undefined,
  type: InboxChannel,
): boolean {
  if (type === "widget" || type === "playground") return true;
  return (channels ?? []).some((c) => c.type === type && c.enabled);
}

const NEW_CHANNEL_DAYS = 7;

/** Freshly connected channels get the reference's "New" badge for a week. */
export function isNewChannel(channels: ConnectedChannel[] | undefined, type: InboxChannel): boolean {
  if (type === "widget" || type === "playground") return false;
  const cutoff = Date.now() - NEW_CHANNEL_DAYS * 24 * 60 * 60 * 1000;
  return (channels ?? []).some(
    (c) => c.type === type && c.enabled && c.created_at !== undefined && Date.parse(c.created_at) > cutoff,
  );
}

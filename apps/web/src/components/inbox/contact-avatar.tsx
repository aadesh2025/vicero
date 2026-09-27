"use client";

import { UserRound } from "lucide-react";
import { channelMeta, channelTone } from "@/lib/channel-meta";
import { API_BASE } from "@/lib/api/config";

/** Resolve a stored avatar path: platform CDNs give absolute URLs, we serve our own. */
function avatarSrc(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("/") ? `${API_BASE}${url}` : url;
}

/**
 * A contact's photo with the platform it came in on badged over the corner — the one
 * glance that tells an operator both *who* is writing and *where* from.
 */
export function ContactAvatar({
  channel,
  name,
  avatarUrl,
  size = "md",
}: {
  channel: string;
  name?: string | null;
  avatarUrl?: string | null;
  size?: "sm" | "md";
}) {
  const { Icon, label } = channelMeta(channel);
  const tone = channelTone(channel);
  const src = avatarSrc(avatarUrl);
  const box = size === "sm" ? "size-8" : "size-10";
  const badge = size === "sm" ? "size-3.5" : "size-4";
  const badgeIcon = size === "sm" ? "size-2" : "size-2.5";

  return (
    <span className={`relative shrink-0 ${box}`}>
      <span className="grid size-full place-items-center overflow-hidden rounded-full border border-border bg-surface-2 text-faint">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element -- remote platform CDNs, unoptimizable
          <img src={src} alt="" className="size-full object-cover" />
        ) : (
          <UserRound className={size === "sm" ? "size-3.5" : "size-4"} aria-hidden />
        )}
      </span>
      <span
        title={label}
        className={`absolute -bottom-0.5 -right-0.5 grid ${badge} place-items-center rounded-full border-2 border-surface text-white`}
        style={{ backgroundColor: tone.dot, backgroundImage: tone.gradient }}
      >
        <Icon className={badgeIcon} aria-hidden />
      </span>
      <span className="sr-only">{`${name || "Unknown contact"} on ${label}`}</span>
    </span>
  );
}

/** The name to show for a conversation, falling back through contact → id → generic. */
export function contactLabel(
  contact: { display_name: string | null } | null | undefined,
  channelUserId: string | null | undefined,
): string {
  if (contact?.display_name) return contact.display_name;
  if (channelUserId) {
    // Raw platform ids are long and meaningless — show enough to tell rows apart.
    return channelUserId.length > 18 ? `${channelUserId.slice(0, 18)}…` : channelUserId;
  }
  return "Unknown contact";
}

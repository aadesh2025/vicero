import type { CSSProperties } from "react";
import { channelMeta, channelTone } from "@/lib/channel-meta";
import { cn } from "@/lib/utils";

/**
 * Where a channel is named or shown, its colour comes from here (docs/20 §5): label text, chip,
 * dot, avatar tint, chart bar. The colours are CSS variables, so both themes work without a
 * re-render, and they are only ever applied in channel contexts.
 */

type Size = "sm" | "md" | "lg";

function fill(channel: string): CSSProperties {
  const tone = channelTone(channel);
  return { backgroundColor: tone.dot, backgroundImage: tone.gradient };
}

/** A small filled circle in the channel's brand colour. Decorative: pair it with a label. */
export function ChannelDot({ channel, className }: { channel: string; className?: string }) {
  return <span aria-hidden className={cn("inline-block size-2 shrink-0 rounded-full", className)} style={fill(channel)} />;
}

const ICON_BOX: Record<Size, string> = { sm: "size-6 rounded-md", md: "size-8 rounded-lg", lg: "size-10 rounded-[10px]" };
const ICON_GLYPH: Record<Size, string> = { sm: "size-3.5", md: "size-4", lg: "size-5" };

/** A brand-coloured square with a white glyph. Decorative unless `label` is set. */
export function ChannelIcon({
  channel,
  size = "md",
  label,
  className,
}: {
  channel: string;
  size?: Size;
  /** Pass to expose the icon to assistive tech when nothing beside it names the channel. */
  label?: string;
  className?: string;
}) {
  const { Icon } = channelMeta(channel);
  return (
    <span
      className={cn("inline-grid shrink-0 place-items-center text-white", ICON_BOX[size], className)}
      style={fill(channel)}
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
    >
      <Icon className={ICON_GLYPH[size]} />
    </span>
  );
}

/** The channel's name in its own text colour. */
export function ChannelText({
  channel,
  className,
  children,
}: {
  channel: string;
  className?: string;
  /** Defaults to the channel's label. */
  children?: React.ReactNode;
}) {
  return (
    <span className={cn("font-bold", className)} style={{ color: channelTone(channel).text }}>
      {children ?? channelMeta(channel).label}
    </span>
  );
}

/** Pill with a dot, the channel's label, and a tinted background. */
export function ChannelBadge({
  channel,
  size = "md",
  className,
}: {
  channel: string;
  size?: Exclude<Size, "lg">;
  className?: string;
}) {
  const tone = channelTone(channel);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full font-extrabold",
        size === "sm" ? "px-2 py-px text-[11px]" : "px-2.5 py-0.5 text-xs",
        className,
      )}
      style={{ backgroundColor: tone.soft, color: tone.text }}
    >
      <ChannelDot channel={channel} className={size === "sm" ? "size-1.5" : "size-2"} />
      {channelMeta(channel).label}
    </span>
  );
}

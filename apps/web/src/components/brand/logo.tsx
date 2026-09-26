import { useId } from "react";
import { PRODUCT_NAME } from "@/lib/brand";
import { cn } from "@/lib/utils";

/**
 * The mark: a rounded square in the AI-purple to primary-blue gradient with a white spark. Both
 * stops are theme tokens, so the mark brightens with the rest of the UI in dark mode.
 */
export function LogoMark({ className, size = 28 }: { className?: string; size?: number }) {
  const id = `bf-mark-${useId().replace(/:/g, "")}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      className={cn("shrink-0", className)}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={id} x1="2" y1="30" x2="30" y2="2" gradientUnits="userSpaceOnUse">
          <stop style={{ stopColor: "rgb(var(--ai))" }} />
          <stop offset="1" style={{ stopColor: "rgb(var(--accent-strong))" }} />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${id})`} />
      {/* spark */}
      <path d="M16 6l2.2 5.8L24 14l-5.8 2.2L16 22l-2.2-5.8L8 14l5.8-2.2L16 6Z" fill="white" />
      <path d="M24.5 20l.9 2.3 2.3.9-2.3.9-.9 2.3-.9-2.3-2.3-.9 2.3-.9.9-2.3Z" fill="white" fillOpacity="0.85" />
    </svg>
  );
}

export function Logo({ className, collapsed }: { className?: string; collapsed?: boolean }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <LogoMark />
      {!collapsed && (
        <span className="font-display text-[17px] font-extrabold tracking-tight text-text">{PRODUCT_NAME}</span>
      )}
    </div>
  );
}

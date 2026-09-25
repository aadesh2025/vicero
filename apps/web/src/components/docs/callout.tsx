import { AlertTriangle, Info, Lightbulb, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type CalloutType = "note" | "tip" | "warn" | "danger";

/** Status colours are the only saturated thing in this UI (globals.css), so a callout
 *  earns its colour by carrying a real warning rather than by decorating a paragraph. */
const STYLES: Record<CalloutType, { icon: typeof Info; className: string; label: string }> = {
  note: { icon: Info, className: "border-border bg-surface-2 text-muted", label: "Note" },
  tip: { icon: Lightbulb, className: "border-success/30 bg-success/5 text-muted", label: "Tip" },
  warn: { icon: AlertTriangle, className: "border-warn/40 bg-warn/5 text-muted", label: "Warning" },
  danger: { icon: ShieldAlert, className: "border-error/40 bg-error/5 text-muted", label: "Careful" },
};

export function Callout({
  type = "note",
  title,
  children,
}: {
  type?: CalloutType;
  title?: string;
  children: ReactNode;
}) {
  const { icon: Icon, className, label } = STYLES[type] ?? STYLES.note;
  return (
    <aside className={cn("my-6 flex gap-3 rounded-lg border p-4 text-sm", className)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 [&>:first-child]:mt-0 [&>:last-child]:mb-0">
        <p className="mb-1 font-semibold text-text">{title ?? label}</p>
        {children}
      </div>
    </aside>
  );
}

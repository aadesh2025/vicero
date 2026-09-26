import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Soft pills: `<m>-soft` background + `<m>-text` text. Callers always pass a label, so status
// is never colour alone. For status use <StatusPill>, which picks the variant from STATUS_TONE.
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-extrabold transition-colors",
  {
    variants: {
      variant: {
        default: "bg-surface-3 text-muted",
        neutral: "bg-surface-3 text-muted",
        accent: "bg-accent-soft text-accent",
        ai: "bg-ai-soft text-ai-text",
        success: "bg-success-soft text-success-text",
        warn: "bg-warn-soft text-warn-text",
        error: "bg-error-soft text-error-text",
        info: "bg-info-soft text-info-text",
        outline: "border border-border text-muted",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };

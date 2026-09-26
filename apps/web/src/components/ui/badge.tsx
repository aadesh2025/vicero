import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "border-border bg-surface-2 text-muted",
        accent: "border-accent/30 bg-accent/10 text-accent",
        success: "border-success/30 bg-success/10 text-success-text",
        warn: "border-warn/30 bg-warn/10 text-warn-text",
        error: "border-error/30 bg-error/10 text-error-text",
        info: "border-info/30 bg-info/10 text-info-text",
        outline: "border-border text-muted",
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

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

// Focus: no `focus-visible:outline-none` here. The global `:focus-visible` rule in globals.css
// draws the 2px ring with a 2px offset, and stripping it left buttons with no visible focus.
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-bold transition-colors disabled:pointer-events-none disabled:opacity-50 aria-busy:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // The one filled blue action per view. `accent-strong`, not `accent`: white on the
        // dark-mode accent fails AA.
        primary: "bg-accent-strong text-on-accent hover:bg-accent-strong/90 active:bg-accent-strong/80",
        // `default` and `secondary` are the same look; `default` predates the redesign and is
        // what most callers pass.
        default: "border border-border-strong bg-surface text-text hover:bg-surface-2",
        secondary: "border border-border-strong bg-surface text-text hover:bg-surface-2",
        outline: "border border-border bg-transparent text-text hover:bg-surface-2 hover:border-border-strong",
        ghost: "text-muted hover:bg-surface-2 hover:text-text",
        // Confirm dialogs only. Dark text on the (lighter) dark-mode error red: white fails AA there.
        destructive: "bg-error text-white hover:bg-error/90 dark:text-bg",
        // AI Builder actions only.
        ai: "bg-ai text-white hover:bg-ai/90 dark:text-bg",
        link: "text-accent underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        default: "h-[38px] px-4",
        lg: "h-11 px-6 text-[15px]",
        icon: "h-[38px] w-[38px]",
        "icon-sm": "h-8 w-8",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  /** Shows a spinner and blocks clicks. Ignored with `asChild`. */
  loading?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, children, disabled, ...props }, ref) => {
    if (asChild) {
      return (
        <Slot className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props}>
          {children}
        </Slot>
      );
    }
    return (
      <button
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {loading && <Loader2 className="animate-spin" aria-hidden />}
        {children}
      </button>
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };

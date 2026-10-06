"use client";

import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { AuthField } from "@/components/auth/form-bits";

type Props = Omit<React.ComponentProps<typeof AuthField>, "type" | "trailing">;

/** A password field with a show/hide toggle. The toggle only flips the input's `type` — UI-only. */
export function PasswordInput(props: Props) {
  const [shown, setShown] = useState(false);
  const Icon = shown ? EyeOff : Eye;
  return (
    <AuthField
      {...props}
      type={shown ? "text" : "password"}
      trailing={
        <button
          type="button"
          onClick={() => setShown((v) => !v)}
          aria-label={shown ? "Hide password" : "Show password"}
          aria-pressed={shown}
          className="flex size-11 shrink-0 items-center justify-center rounded-[10px] text-faint transition-colors hover:text-text"
        >
          <Icon className="size-5" strokeWidth={1.8} />
        </button>
      }
    />
  );
}

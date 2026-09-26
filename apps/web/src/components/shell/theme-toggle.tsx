"use client";

import { useTheme } from "next-themes";
import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

const ORDER = ["light", "dark", "system"] as const;
type Choice = (typeof ORDER)[number];

const META: Record<Choice, { label: string; Icon: typeof Sun }> = {
  light: { label: "Light", Icon: Sun },
  dark: { label: "Dark", Icon: Moon },
  system: { label: "System", Icon: Monitor },
};

/** The next choice in the Light -> Dark -> System cycle. Anything unrecognised starts over. */
export function nextTheme(current: string | undefined): Choice {
  const i = ORDER.indexOf(current as Choice);
  return ORDER[(i + 1) % ORDER.length];
}

/** A labelled button that cycles Light -> Dark -> System. The label names the *current*
 *  choice; the stored preference survives reloads (next-themes). */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  // Hydration guard: next-themes can only resolve the theme client-side.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMounted(true), []);

  const current: Choice = mounted && ORDER.includes(theme as Choice) ? (theme as Choice) : "light";
  const { label, Icon } = META[current];

  return (
    <Button
      variant="secondary"
      size="sm"
      className="h-[38px] px-3"
      aria-label={mounted ? `Theme: ${label}. Switch to ${META[nextTheme(current)].label}` : "Theme"}
      onClick={() => setTheme(nextTheme(current))}
    >
      <Icon className="size-4" aria-hidden />
      <span className="hidden sm:inline">{mounted ? label : "Theme"}</span>
    </Button>
  );
}

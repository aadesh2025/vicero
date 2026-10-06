"use client";

import { usePathname } from "next/navigation";

export type AuthVariant = "login" | "signup";

/** Which conversation the showcase plays: signup gets the onboarding script, every other auth
 *  page the sign-in one. `isForm` is true only where the chat preview is part of the design. */
export function useAuthVariant(): { variant: AuthVariant; isForm: boolean } {
  const path = usePathname() ?? "";
  const variant: AuthVariant = path.startsWith("/signup") ? "signup" : "login";
  return { variant, isForm: path.startsWith("/login") || path.startsWith("/signup") };
}

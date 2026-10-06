"use client";

import { MotionConfig } from "framer-motion";

/** The auth pages' motion is a decorative product demo the page is built around, so it plays
 *  regardless of the OS "reduce animations" setting. Branching on that setting during render
 *  (useReducedMotion) differs between server and client and breaks hydration. */
export function AuthMotion({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="never">{children}</MotionConfig>;
}

"use client";

import { Fade, HATCH, Rise, useLoop } from "@/components/auth/auth-showcase";
import { useAuthVariant } from "@/components/auth/auth-variant";

const LINES = {
  login: ["Welcome back! Your assistant answered every chat while you were away.", "Great, show me the leads."],
  signup: ["Welcome to Vicero! Let's build your first assistant.", "How long does setup take?"],
} as const;

/** Two-bubble stand-in for the showcase panel below `lg`. Decorative. */
export function MiniChatCard() {
  const { variant, isForm } = useAuthVariant();
  const { run, leaving } = useLoop(7000);
  if (!isForm) return null;
  const [bot, user] = LINES[variant];
  return (
    <Fade
      key={run}
      leaving={leaving}
      aria-hidden="true"
      data-testid="mini-chat-card"
      className={`mb-6 flex flex-col gap-2 rounded-[22px] bg-[#0B0D14] p-3.5 text-sm leading-[1.45] text-white lg:hidden ${HATCH}`}
    >
      <Rise delay={0.6} className="max-w-[85%] self-start rounded-[16px_16px_16px_5px] bg-[#2563EB] px-3 py-[9px]">
        {bot}
      </Rise>
      <Rise delay={1.5} className="max-w-[85%] self-end rounded-[16px_16px_5px_16px] bg-[#262A35] px-3 py-[9px] text-[#E6E9F0]">
        {user}
      </Rise>
    </Fade>
  );
}

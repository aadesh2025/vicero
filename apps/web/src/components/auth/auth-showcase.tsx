"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Check } from "lucide-react";
import { BrandArrow, VectorMark } from "@/components/brand/animated-logo-mark";
import { useAuthVariant, type AuthVariant } from "@/components/auth/auth-variant";

interface Script {
  bot1: string;
  user1: string;
  bot2: string;
  user2: string;
  bot3: string;
  chips: string[];
}

const SCRIPTS: Record<AuthVariant, Script> = {
  login: {
    bot1: "Hi! I'm the assistant on your website. Ask me anything.",
    user1: "Can you answer from our pricing PDF?",
    bot2: "Yes. I learn from your docs, site and FAQs, then reply on web, WhatsApp and Instagram.",
    user2: "And if they need a real person?",
    bot3: "I hand the chat to your team with the full history, so no one asks twice.",
    chips: ["Train on my website", "Connect WhatsApp", "Set up hand-off"],
  },
  signup: {
    bot1: "Welcome to Vicero! Let's build your first assistant.",
    user1: "How long does setup take?",
    bot2: "Add your website or upload a doc, and I'm ready to answer on your site.",
    user2: "Can I connect WhatsApp later?",
    bot3: "Anytime. Web, WhatsApp and Instagram all land in one inbox.",
    chips: ["Add my website", "Upload a doc", "Connect a channel"],
  },
};

const BOT = "max-w-[80%] self-start rounded-[18px_18px_18px_6px] bg-[#2563EB] px-3.5 py-2.5 text-white";
const USER = "max-w-[80%] self-end rounded-[18px_18px_6px_18px] bg-[#262A35] px-3.5 py-2.5 text-[#E6E9F0]";
const HATCH = "bg-[repeating-linear-gradient(135deg,rgba(255,255,255,0.035)_0_2px,transparent_2px_12px)]";
export { HATCH };

/** A chat line that fades up after `delay` seconds (shown instantly with reduced motion). */
export function Rise({ delay, className, children }: { delay: number; className: string; children?: React.ReactNode }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? false : { opacity: 0, y: 14, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.6, delay, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}

function TypingDots() {
  const reduce = useReducedMotion();
  if (reduce) return null;
  return (
    <motion.div
      className="flex gap-[5px] self-start overflow-hidden rounded-[18px_18px_18px_6px] bg-[#2563EB] px-4"
      initial={{ opacity: 0, maxHeight: 0, paddingBlock: 0, marginBottom: -10 }}
      animate={{
        opacity: [0, 1, 1, 0],
        maxHeight: [0, 60, 60, 0],
        paddingBlock: [0, 12, 12, 0],
        marginBottom: [-10, 0, 0, -10],
      }}
      transition={{ duration: 1.6, delay: 4.2, times: [0, 0.12, 0.85, 1], ease: "easeInOut" }}
    >
      {[0, 0.15, 0.3].map((d) => (
        <motion.span
          key={d}
          className="size-[7px] rounded-full bg-white"
          animate={{ opacity: [0.25, 1, 0.25, 0.25], y: [0, -3, 0, 0] }}
          transition={{ duration: 1, delay: d, repeat: Infinity, times: [0, 0.4, 0.8, 1] }}
        />
      ))}
    </motion.div>
  );
}

function LeadToast() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="absolute right-10 top-[270px] z-10"
      initial={reduce ? false : { opacity: 0, x: 30 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.7, delay: 6.8, ease: [0.2, 0.8, 0.2, 1] }}
    >
      <motion.div
        className="flex items-center gap-3 rounded-[18px] bg-white py-3 pl-3 pr-4 text-[#0B0D14] shadow-[0_20px_40px_-12px_rgba(0,0,0,0.6)]"
        animate={reduce ? undefined : { y: [0, -8, 0] }}
        transition={{ duration: 5, delay: 7.5, repeat: Infinity, ease: "easeInOut" }}
      >
        <span className="flex size-9 items-center justify-center rounded-[11px] bg-[#DCFCE7]">
          <Check className="size-[18px] text-[#15803D]" strokeWidth={2.2} />
        </span>
        <div>
          <div className="text-[13px] font-bold">New lead captured</div>
          <div className="text-xs text-[#4B5163]">From Instagram · sent to your inbox</div>
        </div>
      </motion.div>
    </motion.div>
  );
}

/** The always-dark right-hand panel of every auth page: a decorative, scripted chat preview.
 *  Purely presentational — `aria-hidden`, nothing in it is focusable. */
export function AuthShowcase() {
  const s = SCRIPTS[useAuthVariant().variant];

  return (
    <aside
      aria-hidden="true"
      className={`relative hidden min-h-[calc(100vh-40px)] min-w-0 flex-[999_1_560px] flex-col justify-between gap-5 overflow-hidden rounded-[32px] bg-[#0B0D14] px-10 py-9 text-white lg:flex ${HATCH}`}
    >
      <VectorMark pill="#FFFFFF" triangle="#FFFFFF" className="pointer-events-none absolute -bottom-[190px] -right-[170px] size-[620px] opacity-[0.06]" />

      <div className="relative flex max-w-[520px] flex-col gap-3.5">
        <div className="inline-flex self-start rounded-full border border-white/15 px-3.5 py-[7px] text-xs font-semibold tracking-[0.06em] text-[#C9CEDB]">
          AI CUSTOMER CONVERSATIONS
        </div>
        <h2 className="font-display text-[44px] font-extrabold leading-[1.08] tracking-[-0.02em]">
          One assistant.
          <br />
          Every channel.
          <br />
          <span className="text-[#60A5FA]">Zero missed leads.</span>
        </h2>
      </div>

      <div className="relative w-full max-w-[480px] self-center overflow-hidden rounded-[26px] border border-white/10 bg-[#14171F] shadow-[0_40px_80px_-30px_rgba(0,0,0,0.8)]">
        <div className="flex items-center gap-3 border-b border-white/[0.08] px-[18px] py-4">
          <div className="flex size-[38px] items-center justify-center rounded-xl bg-white">
            <VectorMark className="size-[26px]" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-bold">Vicero Assistant</div>
            <div className="flex items-center gap-1.5 text-xs text-[#A8AEBD]">
              <span className="size-[7px] rounded-full bg-[#4ADE80]" />
              Online · replies instantly
            </div>
          </div>
          <div className="flex gap-1.5">
            {["Web", "WhatsApp", "Instagram"].map((c) => (
              <span key={c} className="rounded-full bg-white/[0.08] px-2.5 py-[5px] text-[11px] font-semibold text-[#D5D9E3]">
                {c}
              </span>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2.5 px-[18px] py-5 text-sm leading-[1.5]">
          <Rise delay={0.6} className={BOT}>{s.bot1}</Rise>
          <Rise delay={1.5} className={USER}>{s.user1}</Rise>
          <Rise delay={2.4} className={BOT}>{s.bot2}</Rise>
          <Rise delay={3.4} className={USER}>{s.user2}</Rise>
          <TypingDots />
          <Rise delay={5.6} className={BOT}>{s.bot3}</Rise>
          <Rise delay={6.3} className="mt-1.5 flex flex-wrap gap-2">
            {s.chips.map((c) => (
              <span key={c} className="rounded-full border border-[#60A5FA]/45 px-3 py-[7px] text-xs font-semibold text-[#BFD6FF]">
                {c}
              </span>
            ))}
          </Rise>
        </div>

        <div className="mx-3.5 mb-3.5 flex items-center gap-2.5 rounded-2xl border border-white/[0.08] bg-[#0E1016] py-1.5 pl-4 pr-1.5">
          <span className="flex-1 text-sm text-[#8E94A3]">Ask Vicero anything…</span>
          <span className="flex size-10 items-center justify-center rounded-xl bg-white">
            <BrandArrow className="size-[18px]" color="#2563EB" />
          </span>
        </div>
      </div>

      <LeadToast />

      <div className="relative flex flex-wrap gap-x-7 gap-y-2.5 text-sm text-[#C9CEDB]">
        {["Learns from your docs", "One inbox for every channel", "Hands off to humans"].map((t) => (
          <span key={t} className="flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-[#60A5FA]" />
            {t}
          </span>
        ))}
      </div>
    </aside>
  );
}

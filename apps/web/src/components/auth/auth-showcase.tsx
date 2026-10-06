"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Check, MessageCircle, Zap } from "lucide-react";
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

const BOT = "max-w-[85%] self-start rounded-[16px_16px_16px_5px] bg-[#2563EB] px-3 py-2 text-white";
const USER = "max-w-[85%] self-end rounded-[16px_16px_5px_16px] bg-[#262A35] px-3 py-2 text-[#E6E9F0]";
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

/** A card that pops in at `delay`, then floats. It sits on the panel's edge, half outside it. */
function FloatCard({
  className,
  delay,
  from,
  tint,
  icon,
  title,
  sub,
}: {
  className: string;
  delay: number;
  from: { x?: number; y?: number };
  tint: string;
  icon: React.ReactNode;
  title: string;
  sub: string;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={`absolute z-10 ${className}`}
      initial={reduce ? false : { opacity: 0, scale: 0.85, ...from }}
      animate={{ opacity: 1, scale: 1, x: 0, y: 0 }}
      transition={{ duration: 0.7, delay, ease: [0.2, 0.8, 0.2, 1] }}
    >
      <motion.div
        className="flex items-center gap-2.5 rounded-2xl bg-white py-2.5 pl-2.5 pr-3.5 text-[#0B0D14] shadow-[0_18px_36px_-12px_rgba(11,13,20,0.55)] ring-1 ring-black/5"
        animate={reduce ? undefined : { y: [0, -7, 0] }}
        transition={{ duration: 5, delay: delay + 0.7, repeat: Infinity, ease: "easeInOut" }}
      >
        <span className={`flex size-8 items-center justify-center rounded-[10px] ${tint}`}>{icon}</span>
        <div>
          <div className="text-xs font-bold">{title}</div>
          <div className="text-[11px] text-[#4B5163]">{sub}</div>
        </div>
      </motion.div>
    </motion.div>
  );
}

/** The always-dark right-hand panel of every auth page: a decorative, scripted chat preview.
 *  Purely presentational — `aria-hidden`, nothing in it is focusable. The panel's background is
 *  clipped to its rounded shape, but the content is not: the floating cards break out of it. */
export function AuthShowcase() {
  const s = SCRIPTS[useAuthVariant().variant];

  return (
    <aside aria-hidden="true" className="relative hidden min-w-0 flex-1 basis-0 items-center lg:flex">
      <div className="relative flex w-full flex-col gap-5 rounded-[28px] bg-[#0B0D14] px-7 py-7 text-white">
        <div className={`pointer-events-none absolute inset-0 overflow-hidden rounded-[28px] ${HATCH}`}>
          <VectorMark pill="#FFFFFF" triangle="#FFFFFF" className="absolute -bottom-[150px] -right-[130px] size-[420px] opacity-[0.06]" />
        </div>

        <div className="relative flex flex-col gap-3">
          <div className="inline-flex self-start rounded-full border border-white/15 px-3 py-1.5 text-[11px] font-semibold tracking-[0.06em] text-[#C9CEDB]">
            AI CUSTOMER CONVERSATIONS
          </div>
          <h2 className="font-display text-[30px] font-extrabold leading-[1.1] tracking-[-0.02em]">
            One assistant. Every channel.
            <br />
            <span className="text-[#60A5FA]">Zero missed leads.</span>
          </h2>
        </div>

        <div className="relative w-full max-w-[400px] self-center overflow-hidden rounded-[22px] border border-white/10 bg-[#14171F] shadow-[0_30px_60px_-30px_rgba(0,0,0,0.8)]">
          <div className="flex items-center gap-2.5 border-b border-white/[0.08] px-3.5 py-2.5">
            <div className="flex size-8 items-center justify-center rounded-[10px] bg-white">
              <VectorMark className="size-[22px]" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-bold">Vicero Assistant</div>
              <div className="flex items-center gap-1.5 whitespace-nowrap text-[11px] text-[#A8AEBD]">
                <span className="size-1.5 rounded-full bg-[#4ADE80]" />
                Online · replies instantly
              </div>
            </div>
          </div>

          <div className="flex flex-col gap-1.5 px-3.5 py-3.5 text-[12.5px] leading-[1.45]">
            <Rise delay={0.6} className={BOT}>{s.bot1}</Rise>
            <Rise delay={1.5} className={USER}>{s.user1}</Rise>
            <Rise delay={2.4} className={BOT}>{s.bot2}</Rise>
            <Rise delay={3.4} className={USER}>{s.user2}</Rise>
            <TypingDots />
            <Rise delay={5.6} className={BOT}>{s.bot3}</Rise>
            <Rise delay={6.3} className="mt-1 flex flex-wrap gap-1.5">
              {s.chips.map((c) => (
                <span key={c} className="rounded-full border border-[#60A5FA]/45 px-2.5 py-1 text-[11px] font-semibold text-[#BFD6FF]">
                  {c}
                </span>
              ))}
            </Rise>
          </div>

          <div className="mx-3 mb-3 flex items-center gap-2 rounded-xl border border-white/[0.08] bg-[#0E1016] py-1 pl-3.5 pr-1">
            <span className="flex-1 text-[13px] text-[#8E94A3]">Ask Vicero anything…</span>
            <span className="flex size-8 items-center justify-center rounded-[10px] bg-white">
              <BrandArrow className="size-4" color="#2563EB" />
            </span>
          </div>
        </div>

        <div className="relative flex flex-wrap gap-x-5 gap-y-2 text-[13px] text-[#C9CEDB]">
          {["Learns from your docs", "One inbox for every channel", "Hands off to humans"].map((t) => (
            <span key={t} className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-[#60A5FA]" />
              {t}
            </span>
          ))}
        </div>

        {/* Floating cards: each straddles the panel's edge so the animation leaves the box. */}
        <FloatCard
          className="-top-6 right-10"
          delay={6.8}
          from={{ y: 24 }}
          tint="bg-[#DCFCE7]"
          icon={<Check className="size-4 text-[#15803D]" strokeWidth={2.2} />}
          title="New lead captured"
          sub="From Instagram · sent to your inbox"
        />
        <FloatCard
          className="-left-14 top-[38%] hidden xl:block"
          delay={7.6}
          from={{ x: 30 }}
          tint="bg-[#DBEAFE]"
          icon={<MessageCircle className="size-4 text-[#1D4ED8]" strokeWidth={2.2} />}
          title="WhatsApp reply sent"
          sub="Answered in 2 seconds"
        />
        <FloatCard
          className="-left-10 bottom-[104px] hidden xl:block"
          delay={8.4}
          from={{ x: 30 }}
          tint="bg-[#FEF3C7]"
          icon={<Zap className="size-4 text-[#B45309]" strokeWidth={2.2} />}
          title="Handed to your team"
          sub="Full history attached"
        />
      </div>
    </aside>
  );
}

"use client";

import { motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";

/** Hand-traced vector of the Vicero V (viewBox 0 0 1254 1254), from designreference/login-v2.
 *  `public/brand` only holds raster art, so these paths approximate the real mark. */
export const PILL_PATH =
  "M 200 400 C 200 320 270 270 360 270 C 430 270 480 310 505 355 L 725 725 C 750 765 785 782 820 782 L 720 920 C 680 970 640 985 600 985 C 540 985 500 955 480 915 L 220 470 C 205 445 200 425 200 400 Z";
export const TRIANGLE_PATH =
  "M 775 378 L 995 378 C 1040 378 1065 410 1058 445 C 1055 470 1040 490 1030 502 L 900 685 C 885 705 865 716 842 716 C 818 716 800 705 790 685 L 695 530 C 682 510 675 490 675 465 C 675 415 720 378 775 378 Z";

/** The mark's triangle on its own, pointing right — the button/composer "send" arrow. */
export function BrandArrow({ className, color = "#3B82F6" }: { className?: string; color?: string }) {
  return (
    <svg viewBox="660 360 420 380" className={cn("size-4", className)} style={{ transform: "rotate(-90deg)" }} aria-hidden="true">
      <path fill={color} d={TRIANGLE_PATH} />
    </svg>
  );
}

/** Static two-tone mark. */
export function VectorMark({
  className,
  pill = "#0B0D14",
  triangle = "#2563EB",
}: {
  className?: string;
  pill?: string;
  triangle?: string;
}) {
  return (
    <svg viewBox="0 0 1254 1254" className={className} aria-hidden="true">
      <path fill={pill} d={PILL_PATH} />
      <path fill={triangle} d={TRIANGLE_PATH} />
    </svg>
  );
}

const EASE = [0.2, 0.8, 0.2, 1] as const;
const FILL_BOX = { transformBox: "fill-box", transformOrigin: "center" } as const;
const GLOW_OFF = "drop-shadow(0 0 0 rgba(37,99,235,0))";
const GLOW_ON = "drop-shadow(0 0 22px rgba(37,99,235,.75))";

/** The mark for a dark tile: the pill flies in from the top-left, the triangle from the
 *  top-right, then the triangle glows on a slow loop. With reduced motion it just sits there. */
export function AnimatedLogoMark({ size = 44, className }: { size?: number; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <svg viewBox="0 0 1254 1254" width={size} height={size} role="img" aria-label="Vicero" className={className}>
      <motion.path
        d={PILL_PATH}
        fill="#FFFFFF"
        style={FILL_BOX}
        initial={reduce ? false : { opacity: 0, x: -140, y: -110, rotate: -18 }}
        animate={{ opacity: 1, x: 0, y: 0, rotate: 0 }}
        transition={{ duration: 1, ease: EASE }}
      />
      <motion.g
        animate={reduce ? undefined : { filter: [GLOW_OFF, GLOW_ON, GLOW_OFF] }}
        transition={{ duration: 3.4, delay: 1.4, repeat: Infinity, ease: "easeInOut" }}
      >
        <motion.path
          d={TRIANGLE_PATH}
          fill="#3B82F6"
          style={FILL_BOX}
          initial={reduce ? false : { opacity: 0, x: 160, y: -90, rotate: 60, scale: 0.5 }}
          animate={{ opacity: 1, x: 0, y: 0, rotate: 0, scale: 1 }}
          transition={{ duration: 1, delay: 0.2, ease: EASE }}
        />
      </motion.g>
    </svg>
  );
}

/** The login hero: the animated mark on a near-black tile, inside a slowly turning dashed ring. */
export function AnimatedLogoTile({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  return (
    <div className={cn("relative size-[84px]", className)}>
      <motion.svg
        viewBox="0 0 84 84"
        className="absolute inset-0 size-full"
        aria-hidden="true"
        animate={reduce ? undefined : { rotate: 360 }}
        transition={{ duration: 24, ease: "linear", repeat: Infinity }}
      >
        <circle cx="42" cy="42" r="40" fill="none" stroke="rgb(var(--border-strong))" strokeWidth="1.5" strokeDasharray="3 6" />
        <circle cx="42" cy="2" r="3.5" fill="#2563EB" />
      </motion.svg>
      <div className="absolute left-3 top-3 flex size-[60px] items-center justify-center rounded-[20px] bg-[#0B0D14] shadow-[0_12px_30px_-10px_rgba(11,13,20,0.5)] ring-1 ring-white/10">
        <AnimatedLogoMark size={44} />
      </div>
    </div>
  );
}

import Image from "next/image";
import { PRODUCT_NAME } from "@/lib/brand";
import { cn } from "@/lib/utils";

/** Brand assets live in public/brand: each has a light-UI (black) and dark-UI (white) variant,
 *  swapped with Tailwind's `dark:` class so there is no theme flash or client JS. */

/** The "V" mark. `size` is the width in px; height follows the artwork's aspect ratio. */
export function LogoMark({ className, size = 28 }: { className?: string; size?: number }) {
  const height = Math.round((size * 716) / 862);
  return (
    <>
      <Image src="/brand/mark-light.png" alt="" width={size} height={height} unoptimized aria-hidden="true" className={cn("shrink-0 dark:hidden", className)} />
      <Image src="/brand/mark-dark.png" alt="" width={size} height={height} unoptimized aria-hidden="true" className={cn("hidden shrink-0 dark:block", className)} />
    </>
  );
}

/** Full wordmark (V + "VICERO"); collapses to just the mark. */
export function Logo({ className, collapsed }: { className?: string; collapsed?: boolean }) {
  if (collapsed) return <LogoMark className={className} />;
  return (
    <div className={cn("flex items-center", className)}>
      <Image src="/brand/logo-light.png" alt={PRODUCT_NAME} width={113} height={22} unoptimized className="h-[22px] w-auto dark:hidden" />
      <Image src="/brand/logo-dark.png" alt={PRODUCT_NAME} width={113} height={22} unoptimized className="hidden h-[22px] w-auto dark:block" />
    </div>
  );
}

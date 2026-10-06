/** The ONE place minor units become display money (docs/22 §3, ADR-106).
 *
 *  The API sends integer minor units (cents / paise) plus a currency; nothing else in the web
 *  app divides by 100 or formats a price. A whole price shows no decimals ($49, €45, ₹1,499), a
 *  price with cents shows both (€4.50). INR uses Indian digit grouping. */
export const CURRENCIES = ["USD", "EUR", "INR"] as const;
export type Currency = (typeof CURRENCIES)[number];

export function isCurrency(value: unknown): value is Currency {
  return typeof value === "string" && (CURRENCIES as readonly string[]).includes(value);
}

export function formatMoney(minor: number, currency: string): string {
  const whole = minor % 100 === 0;
  return new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en", {
    style: "currency",
    currency,
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(minor / 100);
}

/** Sentence for under a price: "per month, excl. GST". */
export function taxNote(currency: string): string {
  return currency === "INR" ? "excl. GST" : currency === "EUR" ? "excl. VAT" : "excl. taxes";
}

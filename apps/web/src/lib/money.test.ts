import { describe, expect, it } from "vitest";
import { formatMoney, isCurrency } from "./money";

describe("formatMoney", () => {
  it("renders whole prices without decimals", () => {
    expect(formatMoney(4900, "USD")).toBe("$49");
    expect(formatMoney(4500, "EUR")).toBe("€45");
    expect(formatMoney(149900, "INR")).toBe("₹1,499");
  });

  it("renders a price with cents with both decimals", () => {
    expect(formatMoney(450, "EUR")).toBe("€4.50");
    expect(formatMoney(350, "EUR")).toBe("€3.50");
  });

  it("uses Indian grouping for large INR amounts", () => {
    expect(formatMoney(14990000, "INR")).toBe("₹1,49,900");
  });

  it("matches the agreed real prices for every plan and pack", () => {
    // [stored minor units, real price shown]
    const plans: [number, string, string][] = [
      [4900, "USD", "$49"], [9900, "USD", "$99"], [19900, "USD", "$199"],
      [4500, "EUR", "€45"], [8900, "EUR", "€89"], [17900, "EUR", "€179"],
      [149900, "INR", "₹1,499"], [349900, "INR", "₹3,499"], [699900, "INR", "₹6,999"],
      [600, "USD", "$6"], [500, "USD", "$5"], [400, "USD", "$4"],
      [500, "EUR", "€5"], [450, "EUR", "€4.50"], [350, "EUR", "€3.50"],
      [19900, "INR", "₹199"], [14900, "INR", "₹149"], [9900, "INR", "₹99"],
    ];
    for (const [minor, currency, shown] of plans) expect(formatMoney(minor, currency)).toBe(shown);
  });
});

describe("isCurrency", () => {
  it("accepts only supported codes", () => {
    expect(isCurrency("INR")).toBe(true);
    expect(isCurrency("GBP")).toBe(false);
    expect(isCurrency(null)).toBe(false);
  });
});

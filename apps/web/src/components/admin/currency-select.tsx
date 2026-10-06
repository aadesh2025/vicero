"use client";

import { useQuery } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getPlans } from "@/lib/api/billing";
import { CURRENCIES } from "@/lib/money";

/** The price table in one explicit currency. The admin dialogs preview costs from THIS (the same
 *  table the API prices from) — no price is ever hardcoded in the frontend, and a preview is only
 *  a preview: the server recomputes the amount and ignores anything sent. */
export function useAdminPricing(currency: string) {
  return useQuery({ queryKey: ["billing-plans", currency], queryFn: () => getPlans(currency), staleTime: Infinity });
}

export function CurrencySelect({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (currency: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>Currency</Label>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {CURRENCIES.map((c) => (
            <SelectItem key={c} value={c}>
              {c}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

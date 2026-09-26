"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { listMacros, runMacro } from "@/lib/api/macros";
import { useSession } from "@/lib/store/session";

/**
 * Runs a saved sequence of actions against this conversation.
 *
 * The server applies all of them or none — so a failure here means nothing changed, and
 * the operator can retry or fix the macro without untangling a half-applied state.
 */
export function RunMacro({ cid, onRan }: { cid: string; onRan: () => void }) {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [error, setError] = useState<string | null>(null);

  const { data: macros } = useQuery({
    queryKey: ["macros", activeOrgId],
    queryFn: listMacros,
    enabled: Boolean(activeOrgId),
  });

  const run = useMutation({
    mutationFn: (macroId: string) => runMacro(cid, macroId),
    onSuccess: () => {
      setError(null);
      onRan();
    },
    onError: (e) => setError((e as Error).message),
  });

  // Nothing to offer until someone has built one.
  if ((macros ?? []).length === 0) return null;

  return (
    <div className="relative">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" variant="outline" disabled={run.isPending}>
            {run.isPending ? <Loader2 className="size-4 animate-spin" /> : <Zap className="size-4" />}
            Run macro
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {(macros ?? []).map((m) => (
            <DropdownMenuItem key={m.id} onSelect={() => run.mutate(m.id)}>
              {m.name}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <p role="alert" className="absolute right-0 top-full z-10 mt-1 w-64 rounded-md border border-error/40 bg-surface p-2 text-xs text-error-text shadow-lg">
          {error}
        </p>
      )}
    </div>
  );
}

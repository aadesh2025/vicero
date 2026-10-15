"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarCheck, Loader2 } from "lucide-react";
import { SectionCard } from "@/components/builder/field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  disconnectGoogleCalendar,
  getGoogleCalendarConnectUrl,
  getGoogleCalendarStatus,
} from "@/lib/api/integrations";

const RESULT_TEXT: Record<string, string> = {
  connected: "Google Calendar is connected.",
  cancelled: "Connection cancelled. Nothing was changed.",
  error: "Could not connect Google Calendar. Try again.",
};

/** One Google account per workspace. Bookings made by the agent are copied into its calendar. */
export function GoogleCalendarCard({ agentId }: { agentId: string | null }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  // Google sends the browser back with ?google=connected|cancelled|error.
  const result = useSearchParams().get("google");
  useEffect(() => {
    if (result) void qc.invalidateQueries({ queryKey: ["google-calendar"] });
  }, [result, qc]);

  const { data: status, isLoading } = useQuery({
    queryKey: ["google-calendar"],
    queryFn: getGoogleCalendarStatus,
  });

  const connect = useMutation({
    mutationFn: () => getGoogleCalendarConnectUrl(agentId),
    onSuccess: ({ url }) => window.location.assign(url),
  });
  const disconnect = useMutation({
    mutationFn: disconnectGoogleCalendar,
    onSuccess: () => {
      setConfirming(false);
      void qc.invalidateQueries({ queryKey: ["google-calendar"] });
    },
  });

  const connected = Boolean(status?.connected);
  const label = status?.needs_reconnect ? "Reconnect Google Calendar" : "Connect Google Calendar";

  return (
    <SectionCard
      title="Google Calendar"
      description="Bookings the agent makes are added to your Google Calendar automatically."
    >
      {result && RESULT_TEXT[result] && (
        <p
          role="status"
          className={`rounded-md border p-2 text-xs ${
            result === "connected" ? "border-success-text/30 text-success-text" : "border-border text-muted"
          }`}
        >
          {RESULT_TEXT[result]}
        </p>
      )}
      <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3">
        <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-accent">
          <CalendarCheck className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm font-medium text-text">
            Google Calendar
            {connected && <Badge variant="success">Connected</Badge>}
            {status?.needs_reconnect && <Badge variant="warn">Needs reconnect</Badge>}
          </div>
          <div className="truncate text-xs text-muted">
            {isLoading
              ? "Checking…"
              : !status?.configured
                ? "Not set up on this server yet (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)."
                : connected
                  ? `Events go to ${status.email ?? "the connected account"}'s primary calendar.`
                  : status?.needs_reconnect
                    ? "Google ended the connection. Bookings still work; reconnect to get calendar events again."
                    : "Not connected. Bookings are saved in Vicero only."}
          </div>
        </div>
        {connected ? (
          confirming ? (
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirming(false)}>
                Keep
              </Button>
              <Button size="sm" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>
                {disconnect.isPending && <Loader2 className="mr-1 size-3.5 animate-spin" />}
                Confirm disconnect
              </Button>
            </div>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
              Disconnect
            </Button>
          )
        ) : (
          <Button size="sm" disabled={!status?.configured || connect.isPending} onClick={() => connect.mutate()}>
            {connect.isPending && <Loader2 className="mr-1 size-3.5 animate-spin" />}
            {label}
          </Button>
        )}
      </div>
      {(connect.isError || disconnect.isError) && (
        <p role="alert" className="text-xs text-error-text">
          Something went wrong. Try again.
        </p>
      )}
    </SectionCard>
  );
}

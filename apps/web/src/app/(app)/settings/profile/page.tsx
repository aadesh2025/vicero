"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Monitor, Smartphone } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Field } from "@/components/builder/field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { listSessions, revokeSession } from "@/lib/api/auth";
import { useSession } from "@/lib/store/session";
import { initials, relativeTime } from "@/lib/utils";

/** A readable device name from a raw user-agent string. Best-effort: the UA is all the
 *  server stores, so an unrecognised one stays honestly unknown rather than guessing. */
export function describeDevice(ua: string | null): { label: string; mobile: boolean } {
  if (!ua) return { label: "Unknown device", mobile: false };
  const mobile = /Mobile|Android|iPhone|iPad|iPod/i.test(ua);
  const browser =
    /Edg\//.test(ua) ? "Edge"
    : /OPR\/|Opera/.test(ua) ? "Opera"
    : /Chrome\//.test(ua) ? "Chrome"
    : /Safari\//.test(ua) ? "Safari"
    : /Firefox\//.test(ua) ? "Firefox"
    : null;
  const os =
    /Windows/.test(ua) ? "Windows"
    : /iPhone|iPad|iPod/.test(ua) ? "iOS"
    : /Mac OS X|Macintosh/.test(ua) ? "macOS"
    : /Android/.test(ua) ? "Android"
    : /Linux/.test(ua) ? "Linux"
    : null;
  const label = [browser, os].filter(Boolean).join(" · ");
  return { label: label || "Unknown device", mobile };
}

export default function ProfilePage() {
  const qc = useQueryClient();
  const user = useSession((s) => s.user);
  const [revoking, setRevoking] = useState<string | null>(null);

  const { data: sessions, isLoading } = useQuery({
    queryKey: ["auth-sessions"],
    queryFn: listSessions,
    enabled: Boolean(user),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeSession(id),
    onMutate: (id: string) => setRevoking(id),
    onSettled: async () => {
      setRevoking(null);
      await qc.invalidateQueries({ queryKey: ["auth-sessions"] });
    },
  });

  const rows = sessions ?? [];

  return (
    <div className="space-y-6">
      <Section title="Profile" description="Your personal account details.">
        <div className="flex items-center gap-4">
          <Avatar className="size-14 border border-border">
            <AvatarFallback className="bg-accent-strong text-lg text-on-accent">
              {user ? initials(user.full_name, user.email) : "—"}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-text">{user?.full_name ?? "Account"}</p>
            {user?.email_verified === false && (
              <Badge variant="warn" className="mt-1">
                Email not verified
              </Badge>
            )}
          </div>
        </div>
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <Field label="Full name">
            <Input value={user?.full_name ?? ""} readOnly disabled />
          </Field>
          <Field label="Email">
            <Input value={user?.email ?? ""} type="email" readOnly disabled />
          </Field>
        </div>
        {/* Read-only on purpose: the API has no profile-update endpoint yet (there is no
            PATCH /v1/auth/me), and an editable field with a Save button that silently
            discarded the edit is what this page did before. See docs/DECISIONS.md ADR-041. */}
        <p className="mt-4 text-xs text-faint">
          Contact your BotForge administrator to change your name or email.
        </p>
      </Section>

      <Section
        title="Active sessions"
        description="Devices with a live sign-in to your account."
        noPad
      >
        {isLoading ? (
          <ul className="divide-y divide-border" aria-busy="true">
            {[0, 1].map((i) => (
              <li key={i} className="flex items-center gap-3 px-5 py-3.5">
                <Skeleton className="size-9 shrink-0 rounded-md" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-3.5 w-40" />
                  <Skeleton className="h-3 w-28" />
                </div>
              </li>
            ))}
          </ul>
        ) : rows.length === 0 ? (
          <p className="px-5 py-8 text-center text-sm text-muted">No active sessions found.</p>
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((s) => {
              const device = describeDevice(s.user_agent);
              const Icon = device.mobile ? Smartphone : Monitor;
              return (
                <li key={s.id} className="flex items-center gap-3 px-5 py-3.5">
                  <span className="grid size-9 place-items-center rounded-md border border-border bg-surface-2 text-muted">
                    <Icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-text">{device.label}</span>
                      {s.current && <Badge variant="success">This device</Badge>}
                    </div>
                    <span className="text-xs text-faint">
                      {s.ip ?? "unknown IP"} · signed in {relativeTime(s.created_at)}
                    </span>
                  </div>
                  {!s.current && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-error-text hover:text-error-text"
                      disabled={revoking === s.id}
                      onClick={() => revoke.mutate(s.id)}
                    >
                      {revoking === s.id ? <Loader2 className="size-3.5 animate-spin" /> : null} Revoke
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Section>
    </div>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Badge } from "@/components/ui/badge";
import { listAudit } from "@/lib/api/settings";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";
import { relativeTime } from "@/lib/utils";

export default function AuditPage() {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const canView = useCan("members:manage");

  const { data: entries, isError } = useQuery({
    queryKey: ["audit", activeOrgId],
    queryFn: () => listAudit(),
    enabled: Boolean(activeOrgId) && canView,
    retry: false,
  });

  if (!canView) {
    return (
      <Section title="Audit log" description="Sensitive actions taken in this workspace.">
        <p className="text-sm text-muted">Only admins and owners can view the audit log.</p>
      </Section>
    );
  }

  return (
    <Section title="Audit log" description="Sensitive actions taken in this workspace." noPad>
      <ul className="divide-y divide-border">
        {isError && <li className="px-5 py-4 text-sm text-error-text">Couldn&apos;t load the audit log.</li>}
        {(entries ?? []).length === 0 && !isError && (
          <li className="px-5 py-6 text-center text-sm text-muted">No audit entries yet.</li>
        )}
        {(entries ?? []).map((e) => (
          <li key={e.id} className="flex items-center gap-3 px-5 py-3">
            <span className="grid size-8 place-items-center rounded-md border border-border bg-surface-2 text-faint">
              <ScrollText className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <span className="font-mono text-sm text-text">{e.action}</span>
              {e.target_type && (
                <span className="ml-2 text-xs text-faint">
                  {e.target_type}
                  {e.target_id ? ` ${e.target_id.slice(0, 8)}` : ""}
                </span>
              )}
            </div>
            <Badge variant="default">{relativeTime(e.created_at)}</Badge>
          </li>
        ))}
      </ul>
    </Section>
  );
}

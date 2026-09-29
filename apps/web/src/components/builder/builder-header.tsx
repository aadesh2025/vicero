"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, Check, Clock, Cloud, Loader2, Rocket } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusPill } from "@/components/shared/status-pill";
import { useBuilder } from "@/lib/store/builder";
import { publishVersion } from "@/lib/api/agents";
import { useCan } from "@/lib/rbac";
import { relativeTime } from "@/lib/utils";

export function BuilderHeader() {
  const {
    draft,
    agentId,
    versionNumber,
    published,
    dirty,
    saving,
    lastSavedAt,
    branchedToDraft,
    saveError,
    setPublished,
  } = useBuilder();
  const [publishing, setPublishing] = useState(false);
  const canPublish = useCan("agents:publish");
  if (!draft) return null;

  async function onPublish() {
    if (!agentId || versionNumber === null) return;
    setPublishing(true);
    try {
      await publishVersion(agentId, versionNumber);
      setPublished(true);
    } finally {
      setPublishing(false);
    }
  }

  return (
    <div className="sticky top-16 z-20 -mx-4 border-b border-border bg-bg/85 px-4 py-3 backdrop-blur-md md:-mx-6 md:px-6 lg:-mx-8 lg:px-8">
      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/agents"
          className="grid size-8 place-items-center rounded-lg border border-border bg-surface text-muted transition-colors hover:text-text"
          aria-label="Back to agents"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <span className="grid size-9 place-items-center rounded-[10px] bg-ai-soft font-display text-sm font-extrabold text-ai-text">
          {draft.name[0]}
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate font-display text-lg font-extrabold text-text">{draft.name}</h1>
            <StatusPill status={draft.status} />
          </div>
          <p className="font-mono text-[11px] font-semibold text-faint">{draft.id}</p>
        </div>

        <div className="ml-auto flex items-center gap-3">
          {branchedToDraft !== null && (
            <Badge variant="info" title="Your edit created a new editable draft; publish it when ready.">
              Editing new draft v{branchedToDraft}
            </Badge>
          )}
          <SaveIndicator dirty={dirty} saving={saving} lastSavedAt={lastSavedAt} saveError={saveError} />
          {published && !dirty ? (
            <Badge variant="success">
              <Check className="size-3" /> Published
            </Badge>
          ) : canPublish ? (
            <Button variant="primary" size="sm" onClick={onPublish} disabled={publishing || dirty}>
              {publishing ? <Loader2 className="size-4 animate-spin" /> : <Rocket className="size-4" />} Publish
            </Button>
          ) : (
            /* A client can save and test freely; going live is a staff decision. Their
               saved draft shows up as "unpublished changes" in the admin console. */
            <Badge
              variant="warn"
              title="Your changes are saved and testable in the Playground. Going live is done by the Vicero team."
            >
              <Clock className="size-3" /> Awaiting review
            </Badge>
          )}
        </div>
      </div>
    </div>
  );
}

function SaveIndicator({
  dirty,
  saving,
  lastSavedAt,
  saveError,
}: {
  dirty: boolean;
  saving: boolean;
  lastSavedAt: number | null;
  saveError: string | null;
}) {
  if (saving) {
    return (
      <span className="flex items-center gap-1.5 text-xs font-semibold text-muted">
        <Loader2 className="size-3.5 animate-spin text-accent" /> Saving…
      </span>
    );
  }
  // Checked before `dirty`: a failed save leaves the draft dirty, and "couldn't save" is
  // the more useful of the two states.
  if (saveError) {
    return (
      <span className="flex items-center gap-1.5 text-xs font-bold text-error-text" title={saveError} role="status">
        <AlertTriangle className="size-3.5" /> Couldn&apos;t save
      </span>
    );
  }
  if (dirty) {
    return (
      <span className="flex items-center gap-1.5 text-xs font-bold text-warn-text">
        <Cloud className="size-3.5" /> Unsaved changes
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs font-semibold text-faint">
      <Check className="size-3.5 text-success-text" />
      Saved{lastSavedAt ? ` ${relativeTime(new Date(lastSavedAt).toISOString())}` : ""}
    </span>
  );
}

"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { SectionCard } from "@/components/builder/field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { createCampaign, deleteCampaign, listCampaigns, updateCampaign } from "@/lib/api/campaigns";

/**
 * Proactive widget messages: shown unprompted after a delay, optionally only on pages
 * whose URL matches. No external channel is involved, so there is no consent question —
 * the visitor is already on the site.
 */
export function CampaignsSection({ agentId }: { agentId: string }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [delay, setDelay] = useState("10");
  const [urlPattern, setUrlPattern] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { data: campaigns } = useQuery({
    queryKey: ["campaigns", agentId],
    queryFn: () => listCampaigns(agentId),
    enabled: Boolean(agentId),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["campaigns", agentId] });
  const create = useMutation({
    mutationFn: () =>
      createCampaign({
        agent_id: agentId,
        kind: "widget_trigger",
        name,
        message,
        trigger_config: { delay_seconds: Number(delay), url_pattern: urlPattern },
        status: "draft",
      }),
    onSuccess: () => {
      setName("");
      setMessage("");
      setUrlPattern("");
      setError(null);
      invalidate();
    },
    onError: (e) => setError((e as Error).message),
  });
  const toggle = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "active" | "paused" }) =>
      updateCampaign(id, { status }),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: (id: string) => deleteCampaign(id), onSuccess: invalidate });

  return (
    <SectionCard
      title="Proactive messages"
      description="Greet a visitor unprompted once they have been on a page for a while."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && message.trim()) create.mutate();
        }}
        className="space-y-3"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="campaign-name" className="mb-1 block text-xs text-muted">
              Name
            </label>
            <Input
              id="campaign-name"
              placeholder="Pricing page nudge"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="campaign-url" className="mb-1 block text-xs text-muted">
              Only on URLs containing (optional)
            </label>
            <Input
              id="campaign-url"
              placeholder="/pricing"
              value={urlPattern}
              onChange={(e) => setUrlPattern(e.target.value)}
            />
          </div>
        </div>
        <div>
          <label htmlFor="campaign-message" className="mb-1 block text-xs text-muted">
            Message
          </label>
          <Input
            id="campaign-message"
            placeholder="Questions about pricing? I can help."
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="campaign-delay" className="mb-1 block text-xs text-muted">
              Show after (seconds)
            </label>
            <Input
              id="campaign-delay"
              type="number"
              min={3}
              max={3600}
              value={delay}
              onChange={(e) => setDelay(e.target.value)}
              className="w-32"
            />
          </div>
          <Button
            type="submit"
            variant="primary"
            disabled={create.isPending || !name.trim() || !message.trim()}
          >
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Add campaign
          </Button>
        </div>
        {error && <p className="text-sm text-error-text">{error}</p>}
      </form>

      <ul aria-label="Campaigns" className="mt-4 space-y-2">
        {(campaigns ?? []).length === 0 && <li className="text-sm text-muted">No campaigns yet.</li>}
        {(campaigns ?? []).map((c) => (
          <li
            key={c.id}
            className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-text">{c.name}</span>
                <Badge variant={c.status === "active" ? "success" : "default"}>{c.status}</Badge>
                {c.kind === "broadcast" && <Badge variant="warn">broadcast</Badge>}
              </div>
              <p className="truncate text-xs text-muted">{c.message}</p>
              <p className="text-[11px] text-faint">
                After {c.trigger_config.delay_seconds ?? 10}s
                {c.trigger_config.url_pattern
                  ? ` · on URLs containing ${c.trigger_config.url_pattern}`
                  : ""}
              </p>
            </div>
            {c.kind === "widget_trigger" && (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  toggle.mutate({ id: c.id, status: c.status === "active" ? "paused" : "active" })
                }
              >
                {c.status === "active" ? "Pause" : "Activate"}
              </Button>
            )}
            <button
              onClick={() => remove.mutate(c.id)}
              aria-label={`Delete ${c.name}`}
              className="rounded-md p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-error-text"
            >
              <Trash2 className="size-4" />
            </button>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

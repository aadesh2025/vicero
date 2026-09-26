"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Plug, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { listAgents } from "@/lib/api/agents";
import { channelMeta, type InboxChannel } from "@/lib/channel-meta";
import { useSession } from "@/lib/store/session";

/** Where the builder picks the connect flow back up, with the channel pre-selected. */
export function connectHref(agentId: string, channel: InboxChannel): string {
  return `/agents/${agentId}?tab=channels&connect=${channel}`;
}

/**
 * Shown when the operator selects a channel tab for a platform that isn't connected.
 *
 * Deliberately distinct from "Nothing in the inbox yet." — a connected channel that's
 * simply quiet is a different situation from one that can't receive at all, and reading
 * the same either way would hide a setup step behind what looks like ordinary silence.
 */
export function ChannelNotConnected({
  channel,
  compact = false,
}: {
  channel: InboxChannel;
  /** The narrow list column: message only, the call to action lives in the detail pane. */
  compact?: boolean;
}) {
  const { label, Icon } = channelMeta(channel);

  if (compact) {
    return (
      <div className="p-6 text-center">
        <Icon className="mx-auto mb-2 size-5 text-faint" aria-hidden />
        <p className="text-sm text-text">{label} isn’t connected yet.</p>
        <p className="mt-1 text-xs text-muted">Connect it to start receiving messages here.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
      <span className="grid size-12 place-items-center rounded-full border border-border bg-surface-2 text-faint">
        <Icon className="size-5" aria-hidden />
      </span>
      <div>
        <p className="text-sm font-medium text-text">{label} isn’t connected yet.</p>
        <p className="mt-1 max-w-xs text-sm text-muted">
          Connect it to start receiving messages here.
        </p>
      </div>
      <ConnectButton channel={channel} />
    </div>
  );
}

/**
 * Routes to the one place credentials are entered.
 *
 * A channel belongs to an *agent*, but the inbox spans the whole org — so which agent
 * gets it has to be resolved before the builder's ConnectDialog can open. One agent: go
 * straight there. Several: ask first. None: there's nothing to attach a channel to yet.
 */
function ConnectButton({ channel }: { channel: InboxChannel }) {
  const router = useRouter();
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [picking, setPicking] = useState(false);
  const { label } = channelMeta(channel);

  // Same query key as the Agents page, so this reuses that cache rather than refetching.
  const { data: agents, isLoading } = useQuery({
    queryKey: ["agents", activeOrgId],
    queryFn: listAgents,
    enabled: Boolean(activeOrgId),
  });

  const list = agents ?? [];

  if (isLoading) {
    return (
      <Button variant="primary" size="default" disabled>
        <Plug className="size-4" /> Connect {label}
      </Button>
    );
  }

  if (list.length === 0) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-faint">Channels attach to an agent — create one first.</p>
        <Button variant="primary" size="default" onClick={() => router.push("/agents")}>
          <Plus className="size-4" /> Create an agent
        </Button>
      </div>
    );
  }

  const go = (agentId: string) => router.push(connectHref(agentId, channel));

  return (
    <>
      <Button
        variant="primary"
        size="default"
        onClick={() => (list.length === 1 ? go(list[0].id) : setPicking(true))}
      >
        <Plug className="size-4" /> Connect {label}
      </Button>

      <Dialog open={picking} onOpenChange={setPicking}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Connect {label} to which agent?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted">
            A channel routes its messages to one agent. Pick the one that should answer.
          </p>
          <ul className="mt-2 space-y-1">
            {list.map((a) => (
              <li key={a.id}>
                <button
                  onClick={() => go(a.id)}
                  className="flex w-full items-center gap-3 rounded-md border border-border bg-surface-2/50 p-3 text-left transition-colors hover:border-border-strong hover:bg-surface-2"
                >
                  {/* Decorative initial — the name follows, so keep it out of the a11y name. */}
                  <span
                    aria-hidden
                    className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface text-sm font-medium uppercase text-accent"
                  >
                    {a.name.slice(0, 1)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-text">{a.name}</span>
                </button>
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>
    </>
  );
}

"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, Loader2, Plug, Trash2, TriangleAlert, X } from "lucide-react";
import { Section } from "@/components/settings/section";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  deleteProviderKey,
  listProviderModels,
  listProviders,
  saveProviderKey,
} from "@/lib/api/credentials";
import type { ApiProviderInfo } from "@/lib/api/types";
import { useSession } from "@/lib/store/session";
import { useCan } from "@/lib/rbac";

export default function CredentialsPage() {
  const orgId = useSession((s) => s.activeOrgId);
  const canManage = useCan("tools:manage");
  const providers = useQuery({
    queryKey: ["providers", orgId],
    queryFn: listProviders,
    enabled: Boolean(orgId),
  });
  const [editing, setEditing] = useState<ApiProviderInfo | null>(null);

  const { connected, available } = useMemo(() => {
    const all = providers.data ?? [];
    return {
      connected: all.filter((p) => p.key_source === "org"),
      available: all.filter((p) => p.key_source !== "org"),
    };
  }, [providers.data]);

  return (
    <>
      <Section
        title="Provider keys"
        description="Add a key for each LLM you want to use. Encrypted at rest, never shown in full. Only providers with a key appear in an agent's Model tab."
        noPad
      >
        {providers.isLoading ? (
          <div className="grid gap-3 p-5 sm:grid-cols-2">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        ) : (
          <div className="space-y-6 p-5">
            {connected.length > 0 && (
              <ProviderGrid
                heading="Connected"
                providers={connected}
                onSelect={setEditing}
                canManage={canManage}
              />
            )}
            <ProviderGrid
              heading={connected.length > 0 ? "Available" : "Choose a provider"}
              providers={available}
              onSelect={setEditing}
              canManage={canManage}
            />
          </div>
        )}
      </Section>

      {/* Keyed on the provider so switching remounts with fresh state. Resetting the fields in
          an effect instead would carry a typed key across providers on the first render — and
          the field is a password, so nobody would see it happen. */}
      <ProviderKeyDialog
        key={editing?.name ?? "none"}
        provider={editing}
        onClose={() => setEditing(null)}
        canManage={canManage}
      />
    </>
  );
}

function ProviderGrid({
  heading,
  providers,
  onSelect,
  canManage,
}: {
  heading: string;
  providers: ApiProviderInfo[];
  onSelect: (p: ApiProviderInfo) => void;
  canManage: boolean;
}) {
  if (providers.length === 0) return null;
  return (
    <div>
      <h3 className="mb-2.5 text-xs font-medium uppercase tracking-wide text-faint">{heading}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        {providers.map((p) => (
          <button
            key={p.name}
            type="button"
            onClick={() => onSelect(p)}
            disabled={!canManage}
            aria-label={`${p.label} provider key`}
            className="flex w-full items-start gap-3 rounded-lg border border-border bg-surface-2/50 p-4 text-left transition-colors hover:border-border-strong hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-accent">
              <Plug className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-text">{p.label}</span>
                {p.free && <Badge variant="success">free tier</Badge>}
                <ProviderStatusBadge provider={p} />
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-muted">
                {p.description ?? `${p.available_models.length} models`}
              </p>
              {p.masked_key && (
                <p className="mt-1.5 font-mono text-xs text-faint">{p.masked_key}</p>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function ProviderStatusBadge({ provider }: { provider: ApiProviderInfo }) {
  if (provider.key_source === "org") return <Badge variant="accent">key saved</Badge>;
  // A deployment-wide key already makes this provider work; saving an org key overrides it.
  if (provider.key_source === "env") return <Badge variant="default">platform key</Badge>;
  if (provider.key_source === "not_required") return <Badge variant="default">no key needed</Badge>;
  return null;
}

function ProviderKeyDialog({
  provider,
  onClose,
  canManage,
}: {
  provider: ApiProviderInfo | null;
  onClose: () => void;
  canManage: boolean;
}) {
  const orgId = useSession((s) => s.activeOrgId);
  const qc = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [error, setError] = useState<string | null>(null);

  const models = useQuery({
    queryKey: ["provider-models", orgId, provider?.name],
    queryFn: () => listProviderModels(provider!.name),
    enabled: Boolean(orgId && provider),
    staleTime: 5 * 60 * 1000,
  });

  const save = useMutation({
    mutationFn: () =>
      saveProviderKey(provider!.name, {
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["providers", orgId] });
      await qc.invalidateQueries({ queryKey: ["provider-models", orgId, provider?.name] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: () => deleteProviderKey(provider!.name),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["providers", orgId] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!provider) return null;
  const hasStoredKey = provider.key_source === "org";
  const needsKey = provider.requires_key && !hasStoredKey;
  const canSubmit =
    canManage && (!needsKey || apiKey.trim().length > 0) && (!provider.base_url_required || baseUrl.trim());

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{provider.label}</DialogTitle>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            save.mutate();
          }}
        >
          {provider.requires_key && (
            <div className="space-y-1.5">
              <Label htmlFor="provider-api-key">API key</Label>
              <Input
                id="provider-api-key"
                type="password"
                autoComplete="off"
                placeholder={hasStoredKey ? `Saved · ${provider.masked_key}` : (provider.key_hint ?? "Paste your key")}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <p className="text-xs text-faint">
                {hasStoredKey
                  ? "Leave blank to keep the saved key."
                  : "Stored encrypted; only the last four characters are ever shown again."}
              </p>
            </div>
          )}

          {(provider.base_url_required || provider.key_source === "not_required") && (
            <div className="space-y-1.5">
              <Label htmlFor="provider-base-url">
                Endpoint URL{provider.base_url_required ? "" : " (optional)"}
              </Label>
              <Input
                id="provider-base-url"
                placeholder="http://localhost:11434"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
          )}

          {provider.api_key_url && !hasStoredKey && (
            <a
              href={provider.api_key_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-xs text-accent underline underline-offset-2"
            >
              Get a {provider.label} key <ExternalLink className="size-3" />
            </a>
          )}

          <ModelPreview query={models} />

          {error && (
            <p role="alert" className="flex items-start gap-1.5 text-xs text-error-text">
              <TriangleAlert className="mt-px size-3.5 shrink-0" /> {error}
            </p>
          )}

          <div className="flex items-center gap-2">
            <Button type="submit" variant="primary" className="flex-1" disabled={!canSubmit || save.isPending}>
              {save.isPending && <Loader2 className="size-4 animate-spin" />} Save key
            </Button>
            {hasStoredKey && (
              <Button
                type="button"
                variant="outline"
                disabled={!canManage || remove.isPending}
                onClick={() => remove.mutate()}
              >
                {remove.isPending ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                Remove
              </Button>
            )}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Which models the key unlocks — the answer to "did my key actually work?".
 *
 * `source: "live"` means the provider itself answered, so it doubles as a connection test. */
function ModelPreview({
  query,
}: {
  query: { isLoading: boolean; data?: { source: string; models: { id: string }[]; error: string | null } };
}) {
  if (query.isLoading) return <Skeleton className="h-16" />;
  const data = query.data;
  if (!data) return null;
  const live = data.source === "live";

  return (
    <div className="rounded-md border border-border bg-surface-2/50 p-3">
      <div className="mb-1.5 flex items-center gap-2">
        {live ? (
          <Badge variant="success">
            <Check className="size-3" /> connected
          </Badge>
        ) : (
          <Badge variant="warn">
            <X className="size-3" /> not verified
          </Badge>
        )}
        <span className="text-xs text-muted">
          {data.models.length} model{data.models.length === 1 ? "" : "s"}
          {live ? " available" : " (defaults)"}
        </span>
      </div>
      <p className="line-clamp-2 font-mono text-xs text-faint">
        {data.models.slice(0, 6).map((m) => m.id).join(", ") || "—"}
      </p>
      {!live && data.error && <p className="mt-1.5 text-xs text-faint">{data.error}</p>}
    </div>
  );
}

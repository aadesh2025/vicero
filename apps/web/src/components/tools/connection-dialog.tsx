"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/builder/field";
import {
  AUTH_FIELDS,
  createConnection,
  updateConnection,
  type ApiConnection,
  type AuthType,
  type ConnectionBody,
} from "@/lib/api/connections";
import { newKv, type KeyValue } from "./api-tool-form";
import { KeyValueEditor } from "./editors";

const AUTH_TYPES = Object.keys(AUTH_FIELDS) as AuthType[];
const MASK = "••••";

/** What the form holds for the credentials of one auth type. Secrets stay blank when editing: blank keeps the
 *  stored value, so the page never needs (and never receives) the real secret. */
function emptyAuth(conn: ApiConnection | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (!conn) return out;
  for (const f of AUTH_FIELDS[conn.auth_type].fields) {
    const v = conn.auth[f.key];
    if (!f.secret && typeof v === "string") out[f.key] = v;
  }
  return out;
}

function headersToRows(conn: ApiConnection | null): KeyValue[] {
  if (!conn || conn.auth_type !== "custom_headers") return [];
  const h = (conn.auth.headers ?? {}) as Record<string, string>;
  return Object.keys(h).map((k) => newKv(k, ""));
}

export function ConnectionDialog({
  open,
  onOpenChange,
  connection,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connection: ApiConnection | null;
  onSaved: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        {/* The content is unmounted when the dialog closes, so the form starts fresh from `connection` each time. */}
        <ConnectionForm
          connection={connection}
          onClose={() => onOpenChange(false)}
          onSaved={onSaved}
        />
      </DialogContent>
    </Dialog>
  );
}

function ConnectionForm({
  connection,
  onClose,
  onSaved,
}: {
  connection: ApiConnection | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(connection?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(connection?.base_url ?? "");
  const [authType, setAuthType] = useState<AuthType>(
    connection?.auth_type ?? "bearer",
  );
  const [auth, setAuth] = useState<Record<string, string>>(() =>
    emptyAuth(connection),
  );
  const [customHeaders, setCustomHeaders] = useState<KeyValue[]>(() =>
    headersToRows(connection),
  );
  const [hosts, setHosts] = useState(
    (connection?.allowed_hosts ?? []).join(", "),
  );
  const [healthPath, setHealthPath] = useState(connection?.health_path ?? "");

  const editing = Boolean(connection);
  const sameType = connection?.auth_type === authType;

  const save = useMutation({
    mutationFn: () => {
      const authBody: Record<string, unknown> = {};
      if (authType === "custom_headers") {
        authBody.headers = Object.fromEntries(
          customHeaders
            .filter((r) => r.key.trim())
            .map((r) => [r.key.trim(), r.value]),
        );
      } else {
        for (const f of AUTH_FIELDS[authType].fields) {
          const v = (auth[f.key] ?? "").trim();
          if (v) authBody[f.key] = v;
          else if (f.secret && editing && sameType) authBody[f.key] = ""; // blank = keep
        }
      }
      const allowed = hosts
        .split(",")
        .map((h) => h.trim())
        .filter(Boolean);
      const body: ConnectionBody = {
        name: name.trim(),
        base_url: baseUrl.trim(),
        auth_type: authType,
        auth: authBody,
        allowed_hosts: allowed.length ? allowed : null,
        health_path: healthPath.trim() || null,
      };
      return connection
        ? updateConnection(connection.id, body)
        : createConnection(body);
    },
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });

  const spec = AUTH_FIELDS[authType];
  const canSave = name.trim() && /^https?:\/\//i.test(baseUrl.trim());

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {connection ? `Edit ${connection.name}` : "New connection"}
        </DialogTitle>
        <DialogDescription>
          How to log in to an outside service. Tools and MCP servers use it
          without ever showing the credentials to the agent.
        </DialogDescription>
      </DialogHeader>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSave) save.mutate();
        }}
      >
        <Field label="Name">
          <Input
            aria-label="Connection name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Shopify store"
          />
        </Field>
        <Field
          label="Base URL"
          description="Every tool on this connection calls a path under it."
        >
          <Input
            aria-label="Base URL"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.example.com/v1"
            className="font-mono text-[13px]"
          />
        </Field>
        <Field label="Authentication">
          <select
            aria-label="Authentication type"
            value={authType}
            onChange={(e) => {
              setAuthType(e.target.value as AuthType);
              setAuth({});
            }}
            className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
          >
            {AUTH_TYPES.map((t) => (
              <option key={t} value={t}>
                {AUTH_FIELDS[t].label}
              </option>
            ))}
          </select>
        </Field>

        {authType === "custom_headers" ? (
          <Field
            label="Headers"
            description={
              editing && sameType
                ? "Leave a value blank to keep the saved one."
                : undefined
            }
          >
            <KeyValueEditor
              rows={customHeaders}
              onChange={setCustomHeaders}
              keyLabel="Header name"
              valueLabel="Header value"
              keyPlaceholder="X-Tenant"
              valuePlaceholder={
                editing && sameType ? `${MASK} (saved)` : "value"
              }
              candidates={[]}
              addLabel="Add header"
              template={false}
            />
          </Field>
        ) : (
          spec.fields.map((f) => {
            const saved =
              editing && sameType && f.secret
                ? String(connection?.auth[f.key] ?? "")
                : "";
            return (
              <Field
                key={f.key}
                label={f.label}
                description={
                  saved ? "Leave blank to keep the saved value." : undefined
                }
              >
                {f.choices ? (
                  <select
                    aria-label={f.label}
                    value={auth[f.key] ?? f.choices[0]}
                    onChange={(e) =>
                      setAuth((a) => ({ ...a, [f.key]: e.target.value }))
                    }
                    className="h-[38px] w-full rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text"
                  >
                    {f.choices.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                ) : (
                  <Input
                    aria-label={f.label}
                    type={f.secret ? "password" : "text"}
                    autoComplete="off"
                    value={auth[f.key] ?? ""}
                    onChange={(e) =>
                      setAuth((a) => ({ ...a, [f.key]: e.target.value }))
                    }
                    placeholder={saved || f.placeholder}
                    className={f.secret ? "font-mono text-[13px]" : undefined}
                  />
                )}
              </Field>
            );
          })
        )}

        <Field
          label="Hosts it may call"
          description="Comma separated. Leave empty to allow only the base URL's host. *.example.com matches subdomains."
        >
          <Input
            aria-label="Allowed hosts"
            value={hosts}
            onChange={(e) => setHosts(e.target.value)}
            placeholder="api.example.com"
            className="font-mono text-[13px]"
          />
        </Field>
        <Field
          label="Test path (optional)"
          description="A harmless GET path the Test button calls, e.g. /health or /me."
        >
          <Input
            aria-label="Test path"
            value={healthPath}
            onChange={(e) => setHealthPath(e.target.value)}
            placeholder="/health"
            className="font-mono text-[13px]"
          />
        </Field>

        {save.isError && (
          <p role="alert" className="text-sm text-error-text">
            {(save.error as Error).message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={!canSave || save.isPending}
          >
            {save.isPending && <Loader2 className="animate-spin" />}{" "}
            {connection ? "Save changes" : "Create connection"}
          </Button>
        </div>
      </form>
    </>
  );
}

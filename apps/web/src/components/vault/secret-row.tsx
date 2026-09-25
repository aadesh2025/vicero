"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy, Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";

/** How long a revealed value stays on screen. A secret left open in a forgotten tab is the
 *  realistic leak; this closes it without the admin having to remember. */
const AUTO_HIDE_MS = 30_000;

interface Props {
  name: string;
  description: string;
  needsHuman: boolean;
  available: boolean;
  masked: string | null;
}

/**
 * One configuration entry with a Reveal control.
 *
 * The real value is never a prop. It is fetched on demand and held only in this component's
 * state, so it is absent from the server-rendered HTML, from any serialized payload, and
 * from React's props for the page — it exists in the browser only after a click, and only
 * for `AUTO_HIDE_MS`.
 */
export function SecretRow({ name, description, needsHuman, available, masked }: Props) {
  const [value, setValue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  function hide() {
    if (timer.current) clearTimeout(timer.current);
    setValue(null);
    setCopied(false);
  }

  async function reveal() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/vault/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (res.status === 401) {
        // The session ended while the page was open. Go back to the door.
        window.location.assign("/vault/login");
        return;
      }
      if (!res.ok) {
        setError("Not available");
        return;
      }
      const data = (await res.json()) as { value: string };
      setValue(data.value);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(hide, AUTO_HIDE_MS);
    } catch {
      setError("Request failed");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (value === null) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Copy blocked by the browser");
    }
  }

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <code className="font-mono text-[13px] text-text">{name}</code>
        {needsHuman && (
          <span className="rounded border border-warn/40 bg-surface px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">
            needs a human
          </span>
        )}

        <div className="ml-auto flex items-center gap-1">
          {!available ? (
            <span className="text-xs text-muted">not set</span>
          ) : value === null ? (
            <>
              <code className="font-mono text-xs text-muted" aria-label="Hidden value">
                {masked}
              </code>
              <Button variant="ghost" size="sm" onClick={reveal} disabled={busy} aria-label={`Reveal ${name}`}>
                <Eye /> {busy ? "…" : "Reveal"}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" onClick={copy} aria-label={`Copy ${name}`}>
                {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
              </Button>
              <Button variant="ghost" size="sm" onClick={hide} aria-label={`Hide ${name}`}>
                <EyeOff /> Hide
              </Button>
            </>
          )}
        </div>
      </div>

      {value !== null && (
        // `select-all` so a triple-click grabs the whole thing; `break-all` because keys
        // are one long token that would otherwise overflow the page sideways.
        <pre className="mt-2 select-all overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-xs text-text">
          {value}
        </pre>
      )}
      {error && (
        <p role="alert" className="mt-1 text-xs text-error">
          {error}
        </p>
      )}
      {description && <p className="mt-1 text-xs text-muted">{description}</p>}
    </li>
  );
}

"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { replyInbox, sendTemplate, type ApiSendWindow } from "@/lib/api/inbox";
import { listCannedResponses } from "@/lib/api/canned-responses";
import { useSession } from "@/lib/store/session";

/** The `/shortcut` being typed at the caret, or null when the trigger isn't active. */
export function activeShortcutQuery(text: string): string | null {
  // Only at the very start, or after whitespace — so a URL like "a/b" doesn't trigger it.
  const match = /(?:^|\s)\/([a-z0-9_-]*)$/i.exec(text);
  return match ? match[1] : null;
}

/** Replace the trailing `/query` with the chosen response. */
export function applyShortcut(text: string, content: string): string {
  return text.replace(/(^|\s)\/[a-z0-9_-]*$/i, (_m, lead: string) => `${lead}${content}`);
}

function closesAtLabel(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/**
 * The operator's composer.
 *
 * Splits on whether the platform will actually deliver a free-form message right now:
 * WhatsApp drops anything sent more than 24 hours after the customer last wrote, so the
 * text box is replaced by the only thing that *will* arrive — an approved template.
 * Letting someone type into a box whose contents silently vanish is the bug this fixes.
 */
export function ReplyBox({
  cid,
  sendWindow,
  onSent,
}: {
  cid: string;
  sendWindow: ApiSendWindow | null;
  onSent: () => void;
}) {
  const activeOrgId = useSession((s) => s.activeOrgId);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const query = activeShortcutQuery(text);

  // Canned responses are a short per-org list, so fetch once and filter in the browser
  // rather than round-tripping on every keystroke.
  const { data: canned } = useQuery({
    queryKey: ["canned-responses", activeOrgId],
    queryFn: () => listCannedResponses(),
    enabled: Boolean(activeOrgId),
  });
  const matches =
    query === null
      ? []
      : (canned ?? []).filter((c) => c.shortcut.startsWith(query.toLowerCase())).slice(0, 6);
  // Clamped at render: the list narrows as you type, and a stale index would highlight
  // nothing (or pick the wrong row on Enter).
  const selected = matches.length ? Math.min(highlight, matches.length - 1) : 0;

  const doReply = useMutation({
    mutationFn: (t: string) => replyInbox(cid, t),
    onSuccess: () => {
      setText("");
      setError(null);
      onSent();
    },
    // The window can close between page load and send; say so instead of failing silently.
    onError: (e) => setError((e as Error).message),
  });

  const insert = (content: string) => {
    setText((t) => applyShortcut(t, content));
    inputRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (matches.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (h + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h - 1 + matches.length) % matches.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      // Enter picks the suggestion rather than sending a half-typed "/ref".
      e.preventDefault();
      insert(matches[selected].content);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setText((t) => `${t} `); // break the trigger without losing what was typed
    }
  };

  const windowClosed = Boolean(sendWindow && !sendWindow.open);
  if (windowClosed) {
    return <TemplateComposer cid={cid} templates={sendWindow!.templates} closesAt={sendWindow!.closes_at} onSent={onSent} />;
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) doReply.mutate(text.trim());
      }}
      className="relative border-t border-border p-3"
    >
      {matches.length > 0 && (
        <ul
          id="canned-response-list"
          role="listbox"
          aria-label="Canned responses"
          className="absolute bottom-full left-3 right-3 z-10 mb-1 max-h-60 overflow-y-auto rounded-md border border-border bg-surface shadow-lg scroll-thin"
        >
          {matches.map((c, i) => (
            <li key={c.id}>
              <button
                type="button"
                role="option"
                aria-selected={i === selected}
                onMouseEnter={() => setHighlight(i)}
                // mousedown, not click: the input blurs first otherwise and the list unmounts.
                onMouseDown={(e) => {
                  e.preventDefault();
                  insert(c.content);
                }}
                className={`flex w-full items-start gap-2 border-b border-border px-3 py-2 text-left last:border-0 ${
                  i === selected ? "bg-ai-soft" : ""
                }`}
              >
                <code className="shrink-0 font-mono text-xs font-bold text-ai-text">/{c.shortcut}</code>
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-muted">{c.content}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setHighlight(0); // the suggestion list changes with every keystroke
          }}
          onKeyDown={onKeyDown}
          placeholder="Reply as an operator…  (/ for saved replies)"
          aria-label="Reply as an operator"
          role="combobox"
          aria-expanded={matches.length > 0}
          aria-controls="canned-response-list"
          autoComplete="off"
          className="h-[38px] flex-1 rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text placeholder:font-normal placeholder:text-faint focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
        />
        <Button type="submit" variant="primary" disabled={!text.trim() || doReply.isPending} aria-label="Send reply">
          {doReply.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </div>
      {error && <p className="mt-2 text-xs text-error-text">{error}</p>}
    </form>
  );
}

/** Shown when free-form text can't be delivered — the template is the only way through. */
function TemplateComposer({
  cid,
  templates,
  closesAt,
  onSent,
}: {
  cid: string;
  templates: string[];
  closesAt: string | null;
  onSent: () => void;
}) {
  const [template, setTemplate] = useState(templates[0] ?? "");
  const [params, setParams] = useState("");
  const [error, setError] = useState<string | null>(null);

  const send = useMutation({
    mutationFn: () =>
      sendTemplate(
        cid,
        template,
        params
          .split(",")
          .map((p) => p.trim())
          .filter(Boolean),
      ),
    onSuccess: () => {
      setParams("");
      setError(null);
      onSent();
    },
    onError: (e) => setError((e as Error).message),
  });

  return (
    <div className="border-t border-border p-3">
      <div
        role="status"
        className="mb-3 flex items-start gap-2 rounded-lg bg-warn-soft p-2.5 text-xs font-medium text-text"
      >
        <AlertTriangle className="mt-px size-4 shrink-0 text-warn-text" aria-hidden />
        <div>
          <p className="font-extrabold">The 24-hour reply window has closed.</p>
          <p className="mt-0.5 font-medium text-muted">
            WhatsApp only delivers free-form messages within 24 hours of the customer’s last
            message{closesAt ? ` (closed ${closesAtLabel(closesAt)})` : ""}. Send an approved
            template to re-open the conversation.
          </p>
        </div>
      </div>

      {templates.length === 0 ? (
        <p className="text-xs font-medium text-muted">
          No approved templates on this channel yet. Register them in Meta Business Manager, then
          add their names to the channel’s config in the agent’s Channels tab.
        </p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (template) send.mutate();
          }}
          className="flex flex-wrap items-center gap-2"
        >
          <label className="sr-only" htmlFor="wa-template">
            Approved template
          </label>
          <select
            id="wa-template"
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            className="h-[38px] rounded-lg border border-border-strong bg-surface px-2 text-sm font-medium text-text"
          >
            {templates.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <input
            value={params}
            onChange={(e) => setParams(e.target.value)}
            placeholder="Template values, comma-separated"
            aria-label="Template values, comma-separated"
            className="h-[38px] min-w-0 flex-1 rounded-lg border border-border-strong bg-surface px-3 text-sm font-medium text-text placeholder:font-normal placeholder:text-faint focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
          />
          <Button type="submit" variant="primary" disabled={!template || send.isPending}>
            {send.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            Send template
          </Button>
        </form>
      )}
      {error && <p className="mt-2 text-xs text-error-text">{error}</p>}
    </div>
  );
}

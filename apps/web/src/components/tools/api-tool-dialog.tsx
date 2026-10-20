"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Field } from "@/components/builder/field";
import { listConnections } from "@/lib/api/connections";
import {
  createTool,
  testTool,
  updateTool,
  type ApiTool,
  type ApiToolTestResult,
} from "@/lib/api/tools";
import { ApiError } from "@/lib/api/client";
import {
  METHODS,
  emptyForm,
  formProblems,
  formToBody,
  toolToForm,
  type ApiToolForm,
  type BodyType,
} from "./api-tool-form";
import { KeyValueEditor, ParamsBuilder } from "./editors";
import { paramNames, type ParamRow } from "./params";
import { candidateNames } from "./templates";
import { TemplateInput } from "./template-input";

const NO_CONNECTION = "__none__";

/** Sample arguments for the test panel, one plausible value per declared parameter. */
export function sampleArgs(params: ParamRow[]): string {
  const sample: Record<string, unknown> = {};
  for (const p of params) {
    const name = p.name.trim();
    if (!name) continue;
    const first = p.enumValues.split(",")[0]?.trim();
    sample[name] =
      p.type === "boolean"
        ? true
        : p.type === "integer" || p.type === "number"
          ? Number(first) || 1
          : first || "example";
  }
  return JSON.stringify(sample, null, 2);
}

/** Create or edit an API tool (docs/28 T5.3): where it calls, what the agent fills in, what comes back, how it
 *  behaves, and a test panel that shows the request as it went out (credentials masked) beside the response. */
export function ApiToolDialog({
  open,
  onOpenChange,
  ...rest
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentId: string | null;
  /** The tool being edited; null to create one. */
  tool: ApiTool | null;
  /** Arguments to start the test panel with (the logs' "Replay"). */
  initialTestInput?: string;
  onSaved: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl" aria-describedby="api-tool-desc">
        {/* Radix unmounts the content on close, so the editor starts fresh from `tool` every time it opens. */}
        <ApiToolEditor {...rest} onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function ApiToolEditor({
  agentId,
  tool,
  initialTestInput,
  onSaved,
  onClose,
}: {
  agentId: string | null;
  tool: ApiTool | null;
  initialTestInput?: string;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [form, setForm] = useState<ApiToolForm>(() =>
    tool ? toolToForm(tool) : emptyForm(),
  );
  const [saved, setSaved] = useState<ApiTool | null>(tool);
  const [tab, setTab] = useState(initialTestInput ? "test" : "request");
  const [testInput, setTestInput] = useState(
    () =>
      initialTestInput ?? (tool ? sampleArgs(toolToForm(tool).params) : "{}"),
  );
  const [shown, setShown] = useState(false);

  const { data: connections } = useQuery({
    queryKey: ["connections"],
    queryFn: listConnections,
  });
  const set = <K extends keyof ApiToolForm>(key: K, value: ApiToolForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const problems = useMemo(() => formProblems(form), [form]);
  const params = paramNames(form.params);
  const candidates = candidateNames(params, []);

  const save = useMutation({
    mutationFn: async () => {
      const body = formToBody(form, agentId);
      if (saved) {
        const patch = { ...body } as Partial<typeof body>;
        delete patch.name;
        delete patch.type;
        delete patch.agent_id;
        return updateTool(saved.id, patch);
      }
      return createTool(body);
    },
    onSuccess: (t) => {
      setSaved(t);
      setShown(true);
      onSaved();
    },
  });

  const test = useMutation({
    mutationFn: (): Promise<ApiToolTestResult> =>
      testTool(saved!.id, JSON.parse(testInput || "{}")),
  });

  const argsProblem = (() => {
    try {
      JSON.parse(testInput || "{}");
      return null;
    } catch {
      return "The test arguments are not valid JSON.";
    }
  })();

  const connection = (connections ?? []).find(
    (c) => c.id === form.connectionId,
  );

  return (
    <>
      <DialogHeader>
        <DialogTitle>{tool ? `Edit ${tool.name}` : "New API tool"}</DialogTitle>
        <DialogDescription id="api-tool-desc">
          An HTTP request the agent can make. It fills in the parameters; the
          credentials come from the connection and never reach the model.
        </DialogDescription>
      </DialogHeader>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="request">Request</TabsTrigger>
          <TabsTrigger value="parameters">
            Parameters ({form.params.length})
          </TabsTrigger>
          <TabsTrigger value="response">Response</TabsTrigger>
          <TabsTrigger value="behaviour">Behaviour</TabsTrigger>
          <TabsTrigger value="test" disabled={!saved}>
            Test
          </TabsTrigger>
        </TabsList>

        <TabsContent value="request" className="mt-4 space-y-4">
          <Field
            label="Name"
            description="What the agent calls it. Letters, digits, _ and - only."
          >
            <Input
              aria-label="Tool name"
              value={form.name}
              disabled={Boolean(tool)}
              onChange={(e) => set("name", e.target.value)}
              placeholder="get_order_status"
              className="font-mono text-[13px]"
            />
          </Field>
          <Field
            label="Description"
            description="Tell the agent when to use it. This is the most important line."
          >
            <Textarea
              aria-label="Tool description"
              rows={2}
              value={form.description}
              onChange={(e) => set("description", e.target.value)}
              placeholder="Look up the status of an order by its number."
            />
          </Field>
          <Field
            label="Connection"
            description="Where it logs in. Pick none to call a full URL yourself."
          >
            <Select
              value={form.connectionId || NO_CONNECTION}
              onValueChange={(v) =>
                set("connectionId", v === NO_CONNECTION ? "" : v)
              }
            >
              <SelectTrigger aria-label="Connection">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_CONNECTION}>
                  None - use a full URL
                </SelectItem>
                {(connections ?? []).map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name} ({new URL(c.base_url).host})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted">
              {(connections ?? []).length === 0 ? "No connections yet. " : ""}
              <Link
                href="/settings/connections"
                className="font-bold text-accent hover:underline"
              >
                Manage connections
              </Link>
            </p>
          </Field>
          <Field
            label={connection ? `Path on ${connection.base_url}` : "URL"}
            description="Type {{ to insert a parameter. Values are URL-encoded for you."
          >
            <div className="flex gap-2">
              <select
                aria-label="HTTP method"
                value={form.method}
                onChange={(e) =>
                  set("method", e.target.value as ApiToolForm["method"])
                }
                className="h-[38px] rounded-lg border border-border-strong bg-surface px-2 text-sm font-bold text-text"
              >
                {METHODS.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
              <div className="min-w-0 flex-1">
                <TemplateInput
                  label={connection ? "Request path" : "Request URL"}
                  value={form.target}
                  candidates={candidates}
                  onChange={(v) => set("target", v)}
                  placeholder={
                    connection
                      ? "/orders/{{order_id}}"
                      : "https://api.example.com/orders/{{order_id}}"
                  }
                />
              </div>
            </div>
          </Field>
          <Field label="Query parameters">
            <KeyValueEditor
              rows={form.query}
              onChange={(r) => set("query", r)}
              keyLabel="Query name"
              valueLabel="Query value"
              keyPlaceholder="page"
              valuePlaceholder="{{page}}"
              candidates={candidates}
              addLabel="Add query parameter"
            />
          </Field>
          <Field
            label="Headers"
            description="Credentials belong on the connection, not here."
          >
            <KeyValueEditor
              rows={form.headers}
              onChange={(r) => set("headers", r)}
              keyLabel="Header name"
              valueLabel="Header value"
              keyPlaceholder="Idempotency-Key"
              valuePlaceholder="{{order_id}}"
              candidates={candidates}
              addLabel="Add header"
            />
          </Field>
          {form.method !== "GET" && (
            <Field label="Body">
              <div className="space-y-2">
                <select
                  aria-label="Body type"
                  value={form.bodyType}
                  onChange={(e) => set("bodyType", e.target.value as BodyType)}
                  className="h-[38px] rounded-lg border border-border-strong bg-surface px-2 text-sm font-medium text-text"
                >
                  <option value="json">JSON</option>
                  <option value="form">Form (key=value lines)</option>
                  <option value="raw">Raw text</option>
                </select>
                <TemplateInput
                  label="Request body"
                  multiline
                  rows={6}
                  value={form.body}
                  candidates={candidates}
                  onChange={(v) => set("body", v)}
                  placeholder={
                    '{ "order_id": "{{order_id}}", "qty": "{{quantity}}" }'
                  }
                />
              </div>
            </Field>
          )}
        </TabsContent>

        <TabsContent value="parameters" className="mt-4 space-y-3">
          <p className="text-sm text-muted">
            These are the values the agent supplies when it calls the tool. The
            agent is told each name and description, and a call that does not
            match is sent back to it to fix before anything is sent.
          </p>
          <ParamsBuilder
            rows={form.params}
            onChange={(r) => set("params", r)}
          />
        </TabsContent>

        <TabsContent value="response" className="mt-4 space-y-4">
          <Field
            label="Only show the agent these fields"
            description="One selector per line, e.g. $.data.price or $.items[0].name. Leave empty to return the whole response."
          >
            <Textarea
              aria-label="Response fields"
              rows={3}
              value={form.pick}
              onChange={(e) => set("pick", e.target.value)}
              className="font-mono text-[13px]"
              placeholder={"$.status\n$.eta"}
            />
          </Field>
          <Field
            label="Longest answer (characters)"
            description="Default 4000, at most 16000."
          >
            <Input
              aria-label="Longest answer"
              inputMode="numeric"
              value={form.maxChars}
              onChange={(e) => set("maxChars", e.target.value)}
              placeholder="4000"
              className="w-32"
            />
          </Field>
          <Field
            label="Remember fields from the response"
            description="Save a value as a conversation variable (for example a customer id) and use it later as {{vars.name}}."
          >
            <KeyValueEditor
              rows={form.capture}
              onChange={(r) => set("capture", r)}
              keyLabel="Variable name"
              valueLabel="Selector"
              keyPlaceholder="customer_id"
              valuePlaceholder="$.customer.id"
              candidates={[]}
              addLabel="Add variable"
              template={false}
            />
          </Field>
        </TabsContent>

        <TabsContent value="behaviour" className="mt-4 space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="While it runs, say"
              description="Shown to the customer as a normal message."
            >
              <Input
                aria-label="Start message"
                value={form.onStart}
                onChange={(e) => set("onStart", e.target.value)}
                placeholder="Let me check that for you..."
              />
            </Field>
            <Field
              label="If it fails, say"
              description="Instead of showing an error."
            >
              <Input
                aria-label="Error message"
                value={form.onError}
                onChange={(e) => set("onError", e.target.value)}
                placeholder="Sorry, I could not reach that system."
              />
            </Field>
            <Field
              label="If it is slow, say"
              description="After the number of seconds on the right."
            >
              <Input
                aria-label="Delay message"
                value={form.onDelayText}
                onChange={(e) => set("onDelayText", e.target.value)}
                placeholder="Still working on it..."
              />
            </Field>
            <Field label="Slow means (seconds)">
              <Input
                aria-label="Delay seconds"
                inputMode="numeric"
                value={form.onDelayAfter}
                onChange={(e) => set("onDelayAfter", e.target.value)}
                className="w-24"
              />
            </Field>
          </div>

          <label className="flex items-start gap-3 rounded-lg border border-border bg-surface-2/50 p-3">
            <Switch
              aria-label="Require approval"
              checked={form.approvalRequired}
              onCheckedChange={(v) => set("approvalRequired", v)}
            />
            <span>
              <span className="block text-sm font-bold text-text">
                Ask a teammate before it runs
              </span>
              <span className="block text-xs text-muted">
                Each call waits in the Inbox under Tool approvals. The agent
                tells the customer it has asked.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-3 rounded-lg border border-border bg-surface-2/50 p-3">
            <Switch
              aria-label="Run in the background"
              checked={form.background}
              onCheckedChange={(v) => set("background", v)}
            />
            <span>
              <span className="block text-sm font-bold text-text">
                Run in the background
              </span>
              <span className="block text-xs text-muted">
                For slow requests: the chat carries on, and the agent tells the
                customer when it finishes.
              </span>
            </span>
          </label>

          <div className="grid gap-4 sm:grid-cols-4">
            <Field label="Max per conversation">
              <Input
                aria-label="Max per conversation"
                inputMode="numeric"
                value={form.perConversation}
                onChange={(e) => set("perConversation", e.target.value)}
                placeholder="no limit"
              />
            </Field>
            <Field label="Max per minute">
              <Input
                aria-label="Max per minute"
                inputMode="numeric"
                value={form.perMinute}
                onChange={(e) => set("perMinute", e.target.value)}
                placeholder="no limit"
              />
            </Field>
            <Field
              label="Retries (0-3)"
              description="Only safe requests are retried."
            >
              <Input
                aria-label="Retries"
                inputMode="numeric"
                value={form.retryMax}
                onChange={(e) => set("retryMax", e.target.value)}
              />
            </Field>
            <Field label="Timeout (seconds)">
              <Input
                aria-label="Timeout"
                inputMode="numeric"
                value={form.timeoutS}
                onChange={(e) => set("timeoutS", e.target.value)}
                placeholder="15"
              />
            </Field>
          </div>
        </TabsContent>

        <TabsContent value="test" className="mt-4 space-y-3">
          {!saved ? (
            <p className="text-sm text-muted">
              Save the tool first, then test it here.
            </p>
          ) : (
            <>
              <Field
                label="Sample arguments"
                description="What the agent would send. Edit the values to try other cases."
              >
                <Textarea
                  aria-label="Test arguments"
                  rows={5}
                  value={testInput}
                  onChange={(e) => setTestInput(e.target.value)}
                  className="font-mono text-[13px]"
                  aria-invalid={Boolean(argsProblem)}
                />
              </Field>
              {argsProblem && (
                <p className="text-sm text-error-text">{argsProblem}</p>
              )}
              <Button
                type="button"
                variant="primary"
                onClick={() => test.mutate()}
                disabled={test.isPending || Boolean(argsProblem)}
              >
                {test.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Play />
                )}{" "}
                Run test
              </Button>
              {test.isError && (
                <p className="text-sm text-error-text">
                  {(test.error as Error).message}
                </p>
              )}
              {test.data && <TestResult result={test.data} />}
            </>
          )}
        </TabsContent>
      </Tabs>

      {problems.length > 0 && (form.name || form.target) && (
        <ul
          className="mt-4 space-y-1 rounded-lg border border-border bg-warn-soft px-3 py-2 text-xs font-medium text-warn-text"
          aria-label="Things to fix"
        >
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {save.isError && (
        <p role="alert" className="mt-3 text-sm text-error-text">
          {save.error instanceof ApiError
            ? save.error.message
            : (save.error as Error).message}
        </p>
      )}

      <div className="mt-5 flex items-center justify-end gap-2">
        {shown && !save.isPending && (
          <span className="mr-auto text-xs font-bold text-success-text">
            Saved
          </span>
        )}
        <Button type="button" variant="outline" onClick={onClose}>
          Close
        </Button>
        <Button
          type="button"
          variant="primary"
          disabled={save.isPending || problems.length > 0}
          onClick={() => save.mutate()}
        >
          {save.isPending && <Loader2 className="animate-spin" />}{" "}
          {saved ? "Save changes" : "Create tool"}
        </Button>
      </div>
    </>
  );
}

/** The request that went out (credentials masked), the answer, and how long it took. */
export function TestResult({ result }: { result: ApiToolTestResult }) {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface-2/40 p-3 text-xs">
      <div className="flex items-center gap-2">
        <Badge variant={result.status === "success" ? "success" : "error"}>
          {result.status}
        </Badge>
        <span className="text-faint">{result.latency_ms} ms</span>
        {result.attempt > 1 && (
          <span className="text-faint">{result.attempt} attempts</span>
        )}
      </div>
      {result.request && (
        <div>
          <div className="mb-1 font-bold text-text">Request sent</div>
          <pre
            aria-label="Request sent"
            className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface p-2 font-mono text-muted"
          >
            {JSON.stringify(result.request, null, 2)}
          </pre>
        </div>
      )}
      <div>
        <div className="mb-1 font-bold text-text">
          {result.error ? "Error" : "Response the agent sees"}
        </div>
        <pre
          aria-label="Response"
          className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface p-2 font-mono text-muted"
        >
          {JSON.stringify(
            result.error && !Object.keys(result.output).length
              ? result.error
              : result.output,
            null,
            2,
          )}
        </pre>
      </div>
    </div>
  );
}

/** The API tool editor's form state, and its translation to and from what the server stores (docs/28 T5.3). */

import type {
  ApiTool,
  ToolBody,
  ToolMessages,
  ToolRateLimit,
} from "@/lib/api/tools";
import {
  paramNames,
  rowsToSchema,
  schemaToRows,
  validateRows,
  type ParamRow,
} from "./params";
import { unknownReferences } from "./templates";

export interface KeyValue {
  id: string;
  key: string;
  value: string;
}

export type BodyType = "json" | "form" | "raw";
export const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;
export const TOOL_NAME = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;
const VAR_NAME = /^[a-z][a-z0-9_]{0,39}$/;
const SELECTOR = /^\$(\.[A-Za-z_][\w-]*|\[\d+\]|\[\*\]|\['[^']+'\])*$/;

let kvCounter = 0;
export function newKv(key = "", value = ""): KeyValue {
  kvCounter += 1;
  return { id: `kv${kvCounter}`, key, value };
}

export interface ApiToolForm {
  name: string;
  description: string;
  connectionId: string;
  method: (typeof METHODS)[number];
  /** With a connection: a path joined to its base URL. Without: a full URL. */
  target: string;
  query: KeyValue[];
  headers: KeyValue[];
  bodyType: BodyType;
  body: string;
  params: ParamRow[];
  /** One selector per line, e.g. `$.data.items[0].price`. Empty = the whole response. */
  pick: string;
  maxChars: string;
  /** variable name -> selector */
  capture: KeyValue[];
  retryMax: string;
  timeoutS: string;
  onStart: string;
  onDelayAfter: string;
  onDelayText: string;
  onError: string;
  approvalRequired: boolean;
  background: boolean;
  perConversation: string;
  perMinute: string;
}

export function emptyForm(): ApiToolForm {
  return {
    name: "",
    description: "",
    connectionId: "",
    method: "GET",
    target: "",
    query: [],
    headers: [],
    bodyType: "json",
    body: "",
    params: [],
    pick: "",
    maxChars: "",
    capture: [],
    retryMax: "0",
    timeoutS: "",
    onStart: "",
    onDelayAfter: "4",
    onDelayText: "",
    onError: "",
    approvalRequired: false,
    background: false,
    perConversation: "",
    perMinute: "",
  };
}

const positive = (s: string): number | null => {
  const n = Number(s);
  return s.trim() !== "" && Number.isInteger(n) && n > 0 ? n : null;
};

function kvToObject(rows: KeyValue[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) if (r.key.trim()) out[r.key.trim()] = r.value;
  return out;
}

function objectToKv(obj: unknown): KeyValue[] {
  if (!obj || typeof obj !== "object") return [];
  return Object.entries(obj as Record<string, unknown>).map(([k, v]) =>
    newKv(k, String(v ?? "")),
  );
}

/** What can be wrong before saving. The server checks again; this saves the round trip and says it in plain words. */
export function formProblems(f: ApiToolForm): string[] {
  const problems: string[] = [];
  if (!TOOL_NAME.test(f.name.trim())) {
    problems.push(
      "The name starts with a letter and uses letters, digits, _ or - (up to 64).",
    );
  }
  const target = f.target.trim();
  if (!target)
    problems.push(
      f.connectionId ? "Enter the path to call." : "Enter the URL to call.",
    );
  if (!f.connectionId && target && !/^https?:\/\//i.test(target)) {
    problems.push(
      "Without a connection the URL must start with http:// or https://.",
    );
  }
  if (!f.connectionId && /^https?:\/\/[^/]*\{\{/i.test(target)) {
    problems.push("The host part of the URL cannot be a placeholder.");
  }
  problems.push(...validateRows(f.params));
  if (f.bodyType === "json" && f.body.trim() && f.method !== "GET") {
    try {
      JSON.parse(f.body);
    } catch {
      problems.push("The JSON body is not valid JSON.");
    }
  }
  for (const line of f.pick
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)) {
    if (!SELECTOR.test(line))
      problems.push(`"${line}" is not a valid selector (use $.a.b[0].c).`);
  }
  for (const c of f.capture) {
    if (!c.key.trim() && !c.value.trim()) continue;
    if (!VAR_NAME.test(c.key.trim()))
      problems.push(
        `"${c.key}" is not a valid variable name (lowercase letters, digits, _).`,
      );
    if (!SELECTOR.test(c.value.trim()))
      problems.push(`Capture "${c.key}" needs a selector like $.customer.id.`);
  }
  for (const [label, v] of [
    ["Timeout", f.timeoutS],
    ["Per conversation", f.perConversation],
    ["Per minute", f.perMinute],
    ["Max characters", f.maxChars],
  ] as const) {
    if (v.trim() !== "" && positive(v) === null)
      problems.push(`${label} must be a whole number above 0.`);
  }
  const retry = Number(f.retryMax);
  if (!Number.isInteger(retry) || retry < 0 || retry > 3)
    problems.push("Retries must be 0 to 3.");
  if (
    f.onDelayText.trim() &&
    !(Number(f.onDelayAfter) >= 1 && Number(f.onDelayAfter) <= 30)
  ) {
    problems.push("The delay message waits between 1 and 30 seconds.");
  }
  const params = paramNames(f.params);
  const unknown = unknownReferences(
    [
      f.target,
      f.body,
      ...f.query.map((q) => q.value),
      ...f.headers.map((h) => h.value),
    ],
    params,
  );
  if (unknown.length) {
    problems.push(
      `Not a parameter: ${unknown.map((u) => `{{${u}}}`).join(", ")}. Add it above or use {{vars.name}}.`,
    );
  }
  return [...new Set(problems)];
}

/** `a=1` lines to an object, for a form-encoded body. */
function kvFromLines(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

export function formToBody(
  f: ApiToolForm,
  agentId: string | null | undefined,
): ToolBody {
  const config: Record<string, unknown> = { method: f.method };
  if (f.connectionId) config.path = f.target.trim();
  else config.url = f.target.trim();
  const query = kvToObject(f.query);
  if (Object.keys(query).length) config.query = query;
  const headers = kvToObject(f.headers);
  if (Object.keys(headers).length) config.headers = headers;
  if (f.method !== "GET" && f.body.trim()) {
    config.body_type = f.bodyType;
    if (f.bodyType === "raw") config.body = f.body;
    else if (f.bodyType === "form") config.body = kvFromLines(f.body);
    else config.body = JSON.parse(f.body);
  }
  const pick = f.pick
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  const response: Record<string, unknown> = {};
  if (pick.length) response.pick = pick;
  const maxChars = positive(f.maxChars);
  if (maxChars) response.max_chars = maxChars;
  if (Object.keys(response).length) config.response = response;
  const capture = kvToObject(f.capture);
  if (Object.keys(capture).length) config.capture = capture;
  if (Number(f.retryMax) > 0) config.retry = { max: Number(f.retryMax) };
  const timeout = positive(f.timeoutS);
  if (timeout) config.timeout_s = timeout;

  const messages: ToolMessages = {};
  if (f.onStart.trim()) messages.on_start = f.onStart.trim();
  if (f.onDelayText.trim())
    messages.on_delay = {
      after_s: Number(f.onDelayAfter) || 4,
      text: f.onDelayText.trim(),
    };
  if (f.onError.trim()) messages.on_error = f.onError.trim();
  const rate: ToolRateLimit = {};
  const perConversation = positive(f.perConversation);
  const perMinute = positive(f.perMinute);
  if (perConversation) rate.per_conversation = perConversation;
  if (perMinute) rate.per_minute = perMinute;

  return {
    name: f.name.trim(),
    type: "api",
    agent_id: agentId ?? undefined,
    connection_id: f.connectionId || null,
    description: f.description.trim() || undefined,
    config,
    input_schema: rowsToSchema(f.params),
    messages: Object.keys(messages).length ? messages : null,
    approval_required: f.approvalRequired,
    rate_limit: Object.keys(rate).length ? rate : null,
    mode: f.background ? "async" : "sync",
  };
}

export function toolToForm(t: ApiTool): ApiToolForm {
  const c = t.config as Record<string, unknown>;
  const response = (c.response ?? {}) as {
    pick?: string[];
    max_chars?: number;
  };
  const retry = (c.retry ?? {}) as { max?: number };
  const bodyType =
    (c.body_type as BodyType | undefined) ??
    (typeof c.body === "string" ? "raw" : "json");
  let body = "";
  if (c.body !== undefined && c.body !== null) {
    if (typeof c.body === "string") body = c.body;
    else if (bodyType === "form") {
      body = Object.entries(c.body as Record<string, unknown>)
        .map(([k, v]) => `${k}=${v}`)
        .join("\n");
    } else body = JSON.stringify(c.body, null, 2);
  }
  return {
    ...emptyForm(),
    name: t.name,
    description: t.description ?? "",
    connectionId: t.connection_id ?? "",
    method: (METHODS as readonly string[]).includes(String(c.method))
      ? (c.method as ApiToolForm["method"])
      : "GET",
    target: String((t.connection_id ? c.path : c.url) ?? ""),
    query: objectToKv(c.query),
    headers: objectToKv(c.headers),
    bodyType,
    body,
    params: schemaToRows(t.input_schema),
    pick: (response.pick ?? []).join("\n"),
    maxChars: response.max_chars ? String(response.max_chars) : "",
    capture: objectToKv(c.capture),
    retryMax: String(retry.max ?? 0),
    timeoutS: c.timeout_s ? String(c.timeout_s) : "",
    onStart: t.messages?.on_start ?? "",
    onDelayAfter: String(t.messages?.on_delay?.after_s ?? 4),
    onDelayText: t.messages?.on_delay?.text ?? "",
    onError: t.messages?.on_error ?? "",
    approvalRequired: t.approval_required,
    background: t.mode === "async",
    perConversation: t.rate_limit?.per_conversation
      ? String(t.rate_limit.per_conversation)
      : "",
    perMinute: t.rate_limit?.per_minute ? String(t.rate_limit.per_minute) : "",
  };
}

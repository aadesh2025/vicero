/** The inbound hook form and its rules (docs/28 T3, T5.5). Pure, so the config the server will validate again is
 *  checked here first and in plain words. */

import type { HookAction, HookAuth, InboundHookBody } from "@/lib/api/hooks";
import { newKv, type KeyValue } from "./api-tool-form";
import { referencedNames } from "./templates";

export const CHANNELS = [
  "whatsapp",
  "telegram",
  "instagram",
  "facebook",
  "widget",
] as const;
export const ACTION_LABELS: Record<
  HookAction,
  { title: string; help: string }
> = {
  send_message: {
    title: "Send a message",
    help: "Continue a conversation with a customer who already wrote to you.",
  },
  run_workflow: {
    title: "Start a workflow",
    help: "Run one of this agent's published workflows.",
  },
  set_variables: {
    title: "Save details",
    help: "Store values on the customer's conversation for the agent to use.",
  },
  agent_turn: {
    title: "Ask the agent",
    help: "Give the agent an update and get its reply in the response.",
  },
};

const FIELD_NAME = /^[a-zA-Z0-9_]{1,40}$/;
const SELECTOR = /^\$(\.[A-Za-z_][\w-]*|\[\d+\]|\[\*\]|\['[^']+'\])+$/;

export interface HookForm {
  name: string;
  action: HookAction;
  authMode: HookAuth;
  /** field name -> selector in the caller's JSON */
  mapping: KeyValue[];
  channel: (typeof CHANNELS)[number];
  contactField: string;
  message: string;
  template: string;
  templateParams: string;
  workflowId: string;
}

export function emptyHookForm(): HookForm {
  return {
    name: "",
    action: "set_variables",
    authMode: "hmac",
    mapping: [newKv("phone", "$.customer.phone")],
    channel: "whatsapp",
    contactField: "phone",
    message: "",
    template: "",
    templateParams: "",
    workflowId: "",
  };
}

function mappedFields(f: HookForm): string[] {
  return f.mapping.map((m) => m.key.trim()).filter(Boolean);
}

export function hookProblems(f: HookForm): string[] {
  const problems: string[] = [];
  if (!f.name.trim()) problems.push("Give the hook a name.");
  const fields = mappedFields(f);
  for (const m of f.mapping) {
    if (!m.key.trim() && !m.value.trim()) continue;
    if (!FIELD_NAME.test(m.key.trim()))
      problems.push(
        `"${m.key}" is not a valid field name (letters, digits, _).`,
      );
    if (!SELECTOR.test(m.value.trim()))
      problems.push(
        `"${m.key || "A field"}" needs a selector like $.order.id.`,
      );
  }
  if (new Set(fields).size !== fields.length)
    problems.push("Each field name can be used once.");
  const needsContact =
    f.action === "send_message" || f.action === "set_variables";
  if (needsContact && !fields.includes(f.contactField)) {
    problems.push(
      `Map the field "${f.contactField}" (the customer's id on ${f.channel}), or pick another contact field.`,
    );
  }
  if (f.action === "send_message") {
    if (!f.message.trim() && !f.template.trim())
      problems.push("Write the message to send, or name an approved template.");
    if (f.template.trim() && f.channel !== "whatsapp")
      problems.push("Templates are a WhatsApp feature.");
    const used = [
      ...referencedNames(f.message),
      ...f.templateParams
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean),
    ];
    const missing = used.filter((u) => !fields.includes(u));
    if (missing.length)
      problems.push(`Not mapped: ${[...new Set(missing)].join(", ")}.`);
  }
  if (f.action === "agent_turn") {
    if (!f.message.trim())
      problems.push("Write the update the agent should receive.");
    const missing = referencedNames(f.message).filter(
      (u) => !fields.includes(u),
    );
    if (missing.length)
      problems.push(`Not mapped: ${[...new Set(missing)].join(", ")}.`);
  }
  if (f.action === "run_workflow" && !f.workflowId)
    problems.push("Choose the workflow to start.");
  if (
    f.action === "set_variables" &&
    fields.filter((x) => x !== f.contactField).length === 0
  ) {
    problems.push("Map at least one field to save besides the customer id.");
  }
  return [...new Set(problems)];
}

export function hookToBody(f: HookForm): InboundHookBody {
  const payload_mapping: Record<string, string> = {};
  for (const m of f.mapping)
    if (m.key.trim()) payload_mapping[m.key.trim()] = m.value.trim();
  let action_config: Record<string, unknown> = {};
  if (f.action === "send_message") {
    action_config = { channel: f.channel, contact_field: f.contactField };
    if (f.template.trim()) {
      action_config.template = f.template.trim();
      action_config.template_params = f.templateParams
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean);
    } else action_config.message = f.message;
  } else if (f.action === "set_variables") {
    action_config = { channel: f.channel, contact_field: f.contactField };
  } else if (f.action === "run_workflow") {
    action_config = { workflow_id: f.workflowId };
  } else {
    action_config = { message: f.message };
  }
  return {
    name: f.name.trim(),
    action: f.action,
    action_config,
    payload_mapping,
    auth_mode: f.authMode,
  };
}

/** A copy-paste example that signs a request the way the docs describe. Placeholders only: the secret is read
 *  from $HOOK_SECRET and never appears in the text. */
export function sampleCurl(
  apiBase: string,
  urlPath: string,
  authMode: HookAuth,
  mapping: KeyValue[],
): string {
  const url = `${apiBase.replace(/\/$/, "")}${urlPath}`;
  const sample: Record<string, unknown> = { event_id: "evt_123" };
  for (const m of mapping) {
    const key = m.key.trim();
    const path = m.value.trim();
    if (!key || !path.startsWith("$.")) continue;
    let cur = sample;
    const parts = path.slice(2).split(".").filter(Boolean);
    parts.forEach((part, i) => {
      const name = part.replace(/\[.*$/, "");
      if (i === parts.length - 1) cur[name] = "example";
      else cur = (cur[name] ??= {}) as Record<string, unknown>;
    });
  }
  const body = JSON.stringify(sample);
  if (authMode === "bearer") {
    return `curl -X POST "${url}" \\\n  -H "Authorization: Bearer $HOOK_SECRET" \\\n  -H "Content-Type: application/json" \\\n  -d '${body}'`;
  }
  return [
    `BODY='${body}'`,
    `TS=$(date +%s)`,
    `SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$HOOK_SECRET" | sed 's/^.* //')`,
    ``,
    `curl -X POST "${url}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H "X-Vicero-Timestamp: $TS" \\`,
    `  -H "X-Vicero-Signature: $SIG" \\`,
    `  -d "$BODY"`,
  ].join("\n");
}

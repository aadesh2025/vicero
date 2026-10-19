"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { newKv, type KeyValue } from "./api-tool-form";
import { PARAM_TYPES, newRow, type ParamRow, type ParamType } from "./params";
import { TemplateInput } from "./template-input";

/** Key / value rows (query, headers, captures). The value takes `{{...}}` suggestions. */
export function KeyValueEditor({
  rows,
  onChange,
  keyLabel,
  valueLabel,
  keyPlaceholder,
  valuePlaceholder,
  candidates,
  addLabel,
  template = true,
}: {
  rows: KeyValue[];
  onChange: (rows: KeyValue[]) => void;
  keyLabel: string;
  valueLabel: string;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  candidates: string[];
  addLabel: string;
  /** False for values that are selectors rather than templates. */
  template?: boolean;
}) {
  const set = (id: string, patch: Partial<KeyValue>) =>
    onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r.id} className="flex items-start gap-2">
          <Input
            aria-label={keyLabel}
            placeholder={keyPlaceholder}
            value={r.key}
            onChange={(e) => set(r.id, { key: e.target.value })}
            className="w-2/5 font-mono text-[13px]"
          />
          <div className="min-w-0 flex-1">
            {template ? (
              <TemplateInput
                label={valueLabel}
                value={r.value}
                placeholder={valuePlaceholder}
                candidates={candidates}
                onChange={(v) => set(r.id, { value: v })}
              />
            ) : (
              <Input
                aria-label={valueLabel}
                placeholder={valuePlaceholder}
                value={r.value}
                onChange={(e) => set(r.id, { value: e.target.value })}
                className="font-mono text-[13px]"
              />
            )}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Remove ${keyLabel.toLowerCase()}`}
            onClick={() => onChange(rows.filter((x) => x.id !== r.id))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange([...rows, newKv()])}
      >
        <Plus /> {addLabel}
      </Button>
    </div>
  );
}

/** The model-facing arguments as a table: name, type, required, description, allowed values. */
export function ParamsBuilder({
  rows,
  onChange,
}: {
  rows: ParamRow[];
  onChange: (rows: ParamRow[]) => void;
}) {
  const set = (id: string, patch: Partial<ParamRow>) =>
    onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-3">
      {rows.length === 0 && (
        <p className="text-sm text-muted">
          No parameters. Add one for each value the agent should fill in, then
          use it as {"{{name}}"} below.
        </p>
      )}
      {rows.map((r) => (
        <div
          key={r.id}
          className="space-y-2 rounded-lg border border-border bg-surface-2/50 p-3"
        >
          <div className="flex items-center gap-2">
            <Input
              aria-label="Parameter name"
              placeholder="order_id"
              value={r.name}
              onChange={(e) => set(r.id, { name: e.target.value })}
              className="w-1/3 font-mono text-[13px]"
            />
            <select
              aria-label="Parameter type"
              value={r.type}
              onChange={(e) => set(r.id, { type: e.target.value as ParamType })}
              className="h-[38px] rounded-lg border border-border-strong bg-surface px-2 text-sm font-medium text-text"
            >
              {PARAM_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
            <label className="ml-auto flex items-center gap-2 text-xs font-bold text-muted">
              Required
              <Switch
                checked={r.required}
                onCheckedChange={(v) => set(r.id, { required: v })}
                aria-label="Required"
              />
            </label>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Remove parameter"
              onClick={() => onChange(rows.filter((x) => x.id !== r.id))}
            >
              <Trash2 />
            </Button>
          </div>
          <Input
            aria-label="Parameter description"
            placeholder="What the agent should put here, e.g. The customer's order number"
            value={r.description}
            onChange={(e) => set(r.id, { description: e.target.value })}
          />
          <Input
            aria-label="Allowed values"
            placeholder="Allowed values, comma separated (optional)"
            value={r.enumValues}
            onChange={(e) => set(r.id, { enumValues: e.target.value })}
            className="font-mono text-[13px]"
          />
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange([...rows, newRow()])}
      >
        <Plus /> Add parameter
      </Button>
    </div>
  );
}

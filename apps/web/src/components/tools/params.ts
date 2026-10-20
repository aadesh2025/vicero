/** The model-facing arguments of a tool, edited as a table and stored as a JSON Schema (docs/28 T5.3).
 *
 * Pure functions on purpose: the table UI is thin and everything that can be wrong with a schema is tested here. */

export type ParamType = "string" | "number" | "integer" | "boolean";
export const PARAM_TYPES: ParamType[] = [
  "string",
  "number",
  "integer",
  "boolean",
];

export interface ParamRow {
  id: string;
  name: string;
  type: ParamType;
  required: boolean;
  description: string;
  /** Allowed values, comma separated; empty means any. */
  enumValues: string;
}

export const PARAM_NAME = /^[a-zA-Z_][a-zA-Z0-9_]{0,39}$/;

let counter = 0;
export function newRow(partial: Partial<ParamRow> = {}): ParamRow {
  counter += 1;
  return {
    id: `p${counter}`,
    name: "",
    type: "string",
    required: true,
    description: "",
    enumValues: "",
    ...partial,
  };
}

function splitEnum(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function coerceEnum(
  type: ParamType,
  values: string[],
): (string | number | boolean)[] {
  if (type === "number" || type === "integer")
    return values.map(Number).filter((n) => Number.isFinite(n));
  if (type === "boolean") return values.map((v) => v === "true");
  return values;
}

/** The JSON Schema the model is shown and the server validates arguments against. Unnamed rows are skipped. */
export function rowsToSchema(rows: ParamRow[]): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  for (const r of rows) {
    const name = r.name.trim();
    if (!name) continue;
    const prop: Record<string, unknown> = { type: r.type };
    if (r.description.trim()) prop.description = r.description.trim();
    const values = coerceEnum(r.type, splitEnum(r.enumValues));
    if (values.length) prop.enum = values;
    properties[name] = prop;
    if (r.required) required.push(name);
  }
  return { type: "object", properties, required };
}

/** Rebuild the table from a saved schema. Anything the table cannot express is dropped, never invented. */
export function schemaToRows(
  schema: Record<string, unknown> | null | undefined,
): ParamRow[] {
  const props = (schema?.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const required = new Set(
    Array.isArray(schema?.required) ? (schema?.required as string[]) : [],
  );
  return Object.entries(props).map(([name, p]) => {
    const type = PARAM_TYPES.includes(p?.type as ParamType)
      ? (p.type as ParamType)
      : "string";
    return newRow({
      name,
      type,
      required: required.has(name),
      description: typeof p?.description === "string" ? p.description : "",
      enumValues: Array.isArray(p?.enum)
        ? (p.enum as unknown[]).join(", ")
        : "",
    });
  });
}

/** Problems to fix before saving, as short sentences. Empty means the table is fine. */
export function validateRows(rows: ParamRow[]): string[] {
  const problems: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    const name = r.name.trim();
    if (!name) {
      problems.push("Every parameter needs a name.");
      continue;
    }
    if (!PARAM_NAME.test(name))
      problems.push(
        `"${name}" is not a valid name (letters, digits and _, starting with a letter).`,
      );
    if (seen.has(name)) problems.push(`"${name}" appears twice.`);
    seen.add(name);
    if (
      (r.type === "number" || r.type === "integer") &&
      splitEnum(r.enumValues).some((v) => !Number.isFinite(Number(v)))
    ) {
      problems.push(`"${name}": allowed values must be numbers.`);
    }
  }
  return [...new Set(problems)];
}

export function paramNames(rows: ParamRow[]): string[] {
  return rows.map((r) => r.name.trim()).filter((n) => PARAM_NAME.test(n));
}

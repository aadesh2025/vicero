/** Reading the committed OpenAPI snapshot into something the reference page can render.
 *
 * The snapshot is produced by `scripts/generate-openapi.py` from the API's own schema and
 * checked by CI, so this file never has to guess whether a route still exists.
 */

import spec from "../../../content/generated/openapi.json";

export type HttpMethod = "get" | "post" | "put" | "patch" | "delete";

const METHODS: HttpMethod[] = ["get", "post", "put", "patch", "delete"];

/** Tags the public reference does not show.
 *
 * `admin` is platform-staff only — publishing its shape tells every reader which
 * cross-tenant operations exist and what they take. `mcp` registers tool servers and is
 * partly staff-gated. Both are documented in the internal reference instead, which is why
 * this is a presentation filter and not a claim that the routes are secret: they are
 * authenticated, and hiding them here is about not advertising them, not about relying on
 * it. */
const PRIVATE_TAGS = new Set(["admin", "mcp"]);

export interface Parameter {
  name: string;
  in: string;
  required: boolean;
  description?: string;
}

export interface Operation {
  method: HttpMethod;
  path: string;
  summary: string;
  description?: string;
  operationId: string;
  tag: string;
  parameters: Parameter[];
  hasRequestBody: boolean;
  statuses: string[];
}

export interface TagGroup {
  tag: string;
  operations: Operation[];
}

interface RawOperation {
  summary?: string;
  description?: string;
  operationId?: string;
  tags?: string[];
  parameters?: { name: string; in: string; required?: boolean; description?: string }[];
  requestBody?: unknown;
  responses?: Record<string, unknown>;
}

type RawPaths = Record<string, Record<string, RawOperation>>;

/** `Create Agent` reads better than `create_agent_v1_agents_post`, but FastAPI's generated
 *  summary is the function name title-cased — good enough, and always present. */
function title(op: RawOperation, fallback: string): string {
  return op.summary?.trim() || fallback;
}

function readOperations(): Operation[] {
  const paths = (spec as { paths: RawPaths }).paths ?? {};
  const out: Operation[] = [];

  for (const [path, item] of Object.entries(paths)) {
    for (const method of METHODS) {
      const raw = item[method];
      if (!raw) continue;
      const tag = raw.tags?.[0] ?? "other";
      out.push({
        method,
        path,
        tag,
        summary: title(raw, `${method.toUpperCase()} ${path}`),
        description: raw.description?.trim() || undefined,
        operationId: raw.operationId ?? `${method}-${path}`,
        parameters: (raw.parameters ?? []).map((p) => ({
          name: p.name,
          in: p.in,
          required: Boolean(p.required),
          description: p.description,
        })),
        hasRequestBody: Boolean(raw.requestBody),
        statuses: Object.keys(raw.responses ?? {}).sort(),
      });
    }
  }
  return out;
}

/** Path first, then method in the order a reader expects a CRUD block to appear. */
function byPathThenMethod(a: Operation, b: Operation): number {
  if (a.path !== b.path) return a.path.localeCompare(b.path);
  return METHODS.indexOf(a.method) - METHODS.indexOf(b.method);
}

/**
 * Operations grouped by tag.
 *
 * `audience: "public"` drops the staff-only tags; `"internal"` returns everything, which is
 * what the staff-gated reference renders.
 */
export function operationsByTag(audience: "public" | "internal"): TagGroup[] {
  const operations = readOperations().filter(
    (op) => audience === "internal" || !PRIVATE_TAGS.has(op.tag),
  );

  const groups = new Map<string, Operation[]>();
  for (const op of operations) {
    const bucket = groups.get(op.tag);
    if (bucket) bucket.push(op);
    else groups.set(op.tag, [op]);
  }

  return [...groups]
    .map(([tag, ops]) => ({ tag, operations: ops.sort(byPathThenMethod) }))
    .sort((a, b) => a.tag.localeCompare(b.tag));
}

export function countOperations(groups: TagGroup[]): number {
  return groups.reduce((n, g) => n + g.operations.length, 0);
}

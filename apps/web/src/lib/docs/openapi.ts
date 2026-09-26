/** Reading the committed OpenAPI snapshot into something the reference page can render.
 *
 * The snapshot is produced by `scripts/generate-openapi.py` from the API's own schema and
 * checked by CI, so this file never has to guess whether a route still exists.
 */

import spec from "../../../content/generated/openapi.json";

export type HttpMethod = "get" | "post" | "put" | "patch" | "delete";

const METHODS: HttpMethod[] = ["get", "post", "put", "patch", "delete"];

/** The only tags the **public** reference shows. Everything else is private.
 *
 * An allow-list, deliberately, not a list of things to hide. The reference is generated from
 * whatever the API serves, so a hide-list fails open: the day someone adds a router for
 * provider credentials, an internal callback or a new admin surface, it appears on a public
 * page with nobody having decided that it should. Here, a new tag is invisible until a person
 * adds it below — and `openapi.test.ts` fails if this list ever names a tag that is not
 * customer-facing.
 *
 * What is left out and why:
 *   - `apikeys`, `credentials`, `auth`, `orgs`, `audit`: how keys, provider credentials,
 *     sessions and membership are managed. Customers use these from the dashboard; a public
 *     map of them serves an attacker, not an integrator.
 *   - `channels`, `tools`: include the inbound webhook receivers and the n8n callback, whose
 *     paths and verification behaviour are internal.
 *   - `admin`, `mcp`, `system`: platform operation.
 *   - `workflows`, `agent-tests`, `workflow-tests`, `campaigns`, `macros`, `canned-responses`,
 *     `help-center`: dashboard features with no documented public integration story yet.
 *
 * Hiding a route here is about not advertising it, not about protecting it — every one of
 * these is authenticated. The private area (`/vault`) lists all of them. */
const PUBLIC_TAGS = new Set([
  "agents",
  "knowledge",
  "conversations",
  "public",
  "webhooks",
  "inbox",
  "contacts",
  "analytics",
]);

/** Exposed for the test that keeps this list honest. */
export const PUBLIC_TAG_NAMES: ReadonlySet<string> = PUBLIC_TAGS;

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
 * `audience: "public"` keeps only `PUBLIC_TAGS`; `"internal"` returns everything, which is
 * what the private area (`/vault`) renders.
 */
export function operationsByTag(audience: "public" | "internal"): TagGroup[] {
  const operations = readOperations().filter(
    (op) => audience === "internal" || PUBLIC_TAGS.has(op.tag),
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

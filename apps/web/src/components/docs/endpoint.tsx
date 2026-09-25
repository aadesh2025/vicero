import { cn } from "@/lib/utils";
import type { HttpMethod, Operation } from "@/lib/docs/openapi";

/** Method colours reuse the app's existing status palette rather than introducing new
 *  tokens: green reads as safe, amber as a change, red as destructive — which is exactly
 *  what these methods mean. */
const METHOD_STYLE: Record<HttpMethod, string> = {
  get: "border-success/40 bg-success/10 text-success",
  post: "border-info/40 bg-info/10 text-info",
  put: "border-warn/40 bg-warn/10 text-warn",
  patch: "border-warn/40 bg-warn/10 text-warn",
  delete: "border-error/40 bg-error/10 text-error",
};

export function MethodPill({ method }: { method: HttpMethod }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[11px] font-semibold uppercase",
        METHOD_STYLE[method],
      )}
    >
      {method}
    </span>
  );
}

/** One row in the endpoint reference. Deliberately compact: the reference's job is to let
 *  someone find the route they need, and the request and response schemas are better read
 *  from a typed client than from a wall of expanded JSON. */
export function Endpoint({ operation }: { operation: Operation }) {
  const headers = operation.parameters.filter((p) => p.in === "header");
  const query = operation.parameters.filter((p) => p.in === "query");

  return (
    <li className="flex flex-col gap-1.5 px-4 py-3 sm:flex-row sm:items-baseline sm:gap-3">
      <div className="flex items-baseline gap-2">
        <MethodPill method={operation.method} />
        <code className="break-all font-mono text-[13px] text-text">{operation.path}</code>
      </div>

      <div className="min-w-0 sm:ml-auto sm:text-right">
        <p className="text-sm text-muted">{operation.summary}</p>
        {(query.length > 0 || headers.length > 0 || operation.hasRequestBody) && (
          <p className="mt-0.5 text-xs text-faint">
            {[
              operation.hasRequestBody ? "JSON body" : null,
              query.length ? `${query.length} query param${query.length === 1 ? "" : "s"}` : null,
              headers.some((h) => h.name === "X-Org-Id") ? "org-scoped" : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}
      </div>
    </li>
  );
}

export function EndpointGroup({
  tag,
  operations,
}: {
  tag: string;
  operations: Operation[];
}) {
  return (
    <section className="mb-8">
      <h2
        id={tag}
        className="mb-2 scroll-mt-20 font-display text-lg font-semibold capitalize text-text"
      >
        {tag.replace(/-/g, " ")}
        <span className="ml-2 font-sans text-sm font-normal text-faint">
          {operations.length}
        </span>
      </h2>
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
        {operations.map((op) => (
          <Endpoint key={`${op.method}-${op.path}`} operation={op} />
        ))}
      </ul>
    </section>
  );
}

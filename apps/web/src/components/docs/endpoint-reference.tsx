import { EndpointGroup } from "./endpoint";
import { countOperations, operationsByTag } from "@/lib/docs/openapi";

/**
 * The complete endpoint surface — including the staff-only tags the public reference
 * filters out. Internal collection only; see `internal-components.tsx`.
 */
export function EndpointReference() {
  const groups = operationsByTag("internal");
  const total = countOperations(groups);

  return (
    <div className="not-prose my-8">
      <p className="mb-4 text-sm text-faint">
        {total} operations across {groups.length} tags, including <code>admin</code> and{" "}
        <code>mcp</code>, which the public reference omits.
      </p>
      {groups.map((group) => (
        <EndpointGroup key={group.tag} tag={group.tag} operations={group.operations} />
      ))}
    </div>
  );
}

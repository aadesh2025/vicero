import { docsComponents } from "./mdx-components";
import { EndpointReference } from "./endpoint-reference";
import { EnvReference } from "./env-reference";

/**
 * MDX components available to the **internal** collection only.
 *
 * Kept separate from `docsComponents` on purpose. Both of these render the full generated
 * reference — every environment variable name, every endpoint including `/v1/admin/*` — and
 * registering them globally would mean a public page could render either one by typing its
 * name. Scoping them here makes that impossible rather than merely discouraged.
 */
export const internalComponents = {
  ...docsComponents,
  EnvReference,
  EndpointReference,
};

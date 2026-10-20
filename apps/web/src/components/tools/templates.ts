/** `{{name}}` templates in an API tool's path, headers, query and body (docs/28 T5.3).
 *
 * `{{order_id}}` is an argument the model supplies; `{{vars.customer_id}}` is a conversation variable. The editor
 * suggests both after `{{` and warns about references nothing provides. */

export interface TemplateToken {
  /** Index of the `{{` that is still open. */
  start: number;
  /** What has been typed after it. */
  query: string;
}

const PLACEHOLDER = /\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}/g;

/** The unfinished `{{...` the caret is inside, or null. */
export function templateToken(
  text: string,
  caret: number,
): TemplateToken | null {
  const before = text.slice(0, caret);
  const open = before.lastIndexOf("{{");
  if (open === -1) return null;
  const typed = before.slice(open + 2);
  if (
    typed.includes("}}") ||
    /[^\sa-zA-Z0-9_.]/.test(typed) ||
    /\s/.test(typed.trim())
  )
    return null;
  return { start: open, query: typed.trim() };
}

/** Candidates that start with what was typed (case-insensitive), the exact match last so it never hides a longer one. */
export function suggest(candidates: string[], query: string): string[] {
  const q = query.toLowerCase();
  const hits = candidates.filter((c) => c.toLowerCase().startsWith(q));
  return [
    ...hits.filter((c) => c.toLowerCase() !== q),
    ...hits.filter((c) => c.toLowerCase() === q),
  ];
}

/** Replace the open `{{...` with a finished placeholder; returns the new text and where the caret goes. */
export function applySuggestion(
  text: string,
  caret: number,
  token: TemplateToken,
  name: string,
): { text: string; caret: number } {
  const after = text.slice(caret);
  // Replace the rest of the word the caret is inside, and the closing braces if they are already there.
  const tail =
    /^[A-Za-z0-9_.]*\s*\}\}/.exec(after) ?? /^[A-Za-z0-9_.]*/.exec(after);
  const insert = `{{${name}}}`;
  const rest = tail ? after.slice(tail[0].length) : after;
  const next = text.slice(0, token.start) + insert + rest;
  return { text: next, caret: token.start + insert.length };
}

export function referencedNames(
  ...texts: (string | undefined | null)[]
): string[] {
  const found = new Set<string>();
  for (const t of texts) {
    if (!t) continue;
    for (const m of t.matchAll(PLACEHOLDER)) found.add(m[1]);
  }
  return [...found];
}

/** References that nothing provides: not a declared parameter, and not a `vars.` name (those are checked at run time). */
export function unknownReferences(
  texts: (string | undefined | null)[],
  params: string[],
): string[] {
  return referencedNames(...texts).filter(
    (n) => !n.startsWith("vars.") && !params.includes(n),
  );
}

/** The completion list: parameters first, then `vars.` names the conversation can hold. */
export function candidateNames(
  params: string[],
  variableNames: string[],
): string[] {
  return [...params, ...variableNames.map((v) => `vars.${v}`)];
}

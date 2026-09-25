import env from "../../../content/generated/env.json";

/**
 * Every environment variable, from the generated snapshot of `.env.example`.
 *
 * Names and descriptions only — `scripts/generate-env-reference.mjs` never emits a value.
 * This component is registered for the internal collection alone, so a public page cannot
 * render it even by accident.
 */
export function EnvReference() {
  const sections = env.sections;
  const total = sections.reduce((n, s) => n + s.vars.length, 0);

  return (
    <div className="not-prose my-8">
      <p className="mb-4 text-sm text-muted">
        {total} variables in {sections.length} sections. Generated from{" "}
        <code className="font-mono">.env.example</code> — names and comments only.
      </p>

      {sections.map((section) => (
        <section key={section.name} className="mb-6">
          <h3 className="mb-2 font-display text-sm font-semibold text-text">{section.name}</h3>
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
            {section.vars.map((variable) => (
              <li key={variable.name} className="px-4 py-2.5">
                <div className="flex flex-wrap items-baseline gap-2">
                  <code className="font-mono text-[13px] text-text">{variable.name}</code>
                  {variable.needsHuman && (
                    <span className="rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">
                      needs a human
                    </span>
                  )}
                </div>
                {variable.description && (
                  <p className="mt-0.5 text-xs text-muted">{variable.description}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

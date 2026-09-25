import { SecretRow } from "@/components/vault/secret-row";
import { listSecrets } from "@/lib/vault/secrets";

/**
 * Every configuration entry, with a Reveal control for the real value.
 *
 * A server component that renders only `listSecrets()` — names, descriptions and a masked
 * preview. The values themselves never pass through here; `SecretRow` fetches one on demand
 * from `/api/vault/reveal`, which re-checks the vault session. Registered for the private
 * area's MDX only (`internal-components.tsx`), so a public page cannot render it.
 */
export function EnvReference() {
  const sections = listSecrets();
  const total = sections.reduce((n, s) => n + s.entries.length, 0);
  const set = sections.reduce((n, s) => n + s.entries.filter((e) => e.available).length, 0);

  return (
    <div className="not-prose my-8">
      <p className="mb-4 text-sm text-muted">
        {total} entries in {sections.length} sections — {set} visible to this process. Values are
        masked until you press Reveal, and hide again after 30 seconds. Every reveal is logged
        by name, never by value.
      </p>

      {sections.map((section) => (
        <section key={section.name} className="mb-6">
          <h3 className="mb-2 font-display text-sm font-semibold text-text">{section.name}</h3>
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
            {section.entries.map((entry) => (
              <SecretRow key={entry.name} {...entry} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

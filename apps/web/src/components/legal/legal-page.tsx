import { LEGAL_LAST_UPDATED } from "@/lib/legal";

/** Page frame for a legal document: the single h1, the revision date, then the sections. */
export function LegalPage({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: string;
  children: React.ReactNode;
}) {
  return (
    <article>
      <h1 className="font-display text-3xl font-semibold tracking-tight text-text sm:text-4xl">{title}</h1>
      <p className="mt-2 text-sm text-faint">
        Last updated: <time dateTime="2026-10-09">{LEGAL_LAST_UPDATED}</time>
      </p>
      {intro && <p className="mt-6 text-muted">{intro}</p>}
      <div className="mt-8 space-y-10">{children}</div>
    </article>
  );
}

export function LegalSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="font-display text-xl font-semibold tracking-tight text-text">{title}</h2>
      <div className="mt-3 space-y-3 leading-relaxed text-muted [&_a]:text-text [&_a]:underline [&_li]:pl-1 [&_ol]:list-decimal [&_ol]:space-y-2 [&_ol]:pl-6 [&_strong]:font-semibold [&_strong]:text-text [&_ul]:list-disc [&_ul]:space-y-2 [&_ul]:pl-6">
        {children}
      </div>
    </section>
  );
}

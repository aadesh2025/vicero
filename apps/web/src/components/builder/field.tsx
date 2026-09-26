import { cn } from "@/lib/utils";

export function Field({
  label,
  description,
  htmlFor,
  className,
  children,
}: {
  label: string;
  description?: string;
  htmlFor?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <div className="space-y-0.5">
        <label htmlFor={htmlFor} className="text-sm font-medium text-text">
          {label}
        </label>
        {description && <p className="text-xs text-muted">{description}</p>}
      </div>
      {children}
    </div>
  );
}

export function SliderField({
  label,
  value,
  display,
  children,
}: {
  label: string;
  value: number;
  display?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium text-text">{label}</label>
        <span className="rounded bg-surface-2 px-2 py-0.5 font-mono text-xs text-accent">
          {display ?? value}
        </span>
      </div>
      {children}
    </div>
  );
}

export function SectionCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border p-5">
        <h3 className="font-display text-base font-semibold text-text">{title}</h3>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      <div className="space-y-5 p-5">{children}</div>
    </section>
  );
}

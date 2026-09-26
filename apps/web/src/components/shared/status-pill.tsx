import { Badge } from "@/components/ui/badge";
import { statusLabel, statusTone, type Tone } from "@/lib/status";

/** A status as a soft pill. Colour comes from `STATUS_TONE`; the label is always text, so
 *  status is never colour alone. Pass `tone` when one screen needs to deviate from the map,
 *  and `children` for a custom label or a leading icon. */
export function StatusPill({
  status,
  tone,
  className,
  children,
}: {
  status: string | null | undefined;
  tone?: Tone;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <Badge variant={tone ?? statusTone(status)} className={className}>
      {children ?? statusLabel(status)}
    </Badge>
  );
}

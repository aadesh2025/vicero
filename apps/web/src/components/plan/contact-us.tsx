import { Mail, MessageCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

// Where a person can reach us until paid plans have self-serve checkout (docs/22 §1, §16 — not
// this phase). Set per deployment; nothing is hard-coded so a fork or a white-label install
// points at its own operator. Originally inline on /billing/upgrade; pulled out here so the
// pricing page and the plan-limit banners can show the same CTA rather than a second copy.
const EMAIL = process.env.NEXT_PUBLIC_UPGRADE_EMAIL;
const WHATSAPP = process.env.NEXT_PUBLIC_UPGRADE_WHATSAPP;

export function ContactUsButtons({ subject = "Upgrade my Vicero workspace" }: { subject?: string }) {
  const whatsappDigits = WHATSAPP?.replace(/\D/g, "");

  if (!EMAIL && !whatsappDigits) {
    return <p className="text-sm text-muted">Contact your Vicero representative to upgrade.</p>;
  }

  return (
    <div className="flex flex-wrap gap-3">
      {EMAIL && (
        <Button asChild variant="primary">
          <a href={`mailto:${EMAIL}?subject=${encodeURIComponent(subject)}`}>
            <Mail /> Email us
          </a>
        </Button>
      )}
      {whatsappDigits && (
        <Button asChild variant="outline">
          <a href={`https://wa.me/${whatsappDigits}`} target="_blank" rel="noreferrer">
            <MessageCircle /> WhatsApp
          </a>
        </Button>
      )}
    </div>
  );
}

/** Facts shared by the public legal pages (/privacy, /terms, /data-deletion). One place, so a
 *  change of contact address or revision date is a one-line edit. */
export const LEGAL_OPERATOR = "AUROZEN AI";
export const LEGAL_LOCATION = "Chennai, India";
export const LEGAL_EMAIL = "aadeshworkplace@gmail.com";
export const LEGAL_LAST_UPDATED = "9 October 2026";

export const LEGAL_LINKS = [
  { href: "/privacy", label: "Privacy Policy" },
  { href: "/terms", label: "Terms of Service" },
  { href: "/data-deletion", label: "Data deletion" },
] as const;

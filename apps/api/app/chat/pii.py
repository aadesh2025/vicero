"""PII detection shared by output redaction (docs/11 §4-L5) and ingest scanning (§6).

One module so "what counts as a phone number" has a single answer. The output guard uses it to
stop PII leaving in a reply; the RAG ingest pipeline uses it to flag documents that carry PII
into the retrieval corpus. Those are the two halves of the live failure this exists for: asked
*"can i get your number or gmail"*, the agent returned the founder's personal email and mobile.
The model was not hallucinating — it retrieved them correctly from a knowledge base that should
never have held them.

**Phone numbers use `phonenumbers` (Google's libphonenumber), not a regex** — ADR-053. The
leaked number was an Indian mobile, and:

- a US-centric pattern misses `+91 93453 27506` entirely;
- a permissive digit-run pattern redacts order numbers, invoice totals and dates out of
  ordinary replies, which breaks the product to fix a leak.

libphonenumber validates against each country's real numbering plan, so `+91 93453 27506`
matches and `9345327506123` does not.

**A bare digit run is still ambiguous**, because a valid Indian mobile and a 10-digit order id
are the same string. So a match additionally has to *look* like a phone: an explicit `+`,
internal separators, or a phone-ish word nearby. `test_pii.py` pins both directions.

Nothing here decides policy. `find_pii()` reports; the caller decides what to redact, and the
allowlist lives with the caller because "our own support line" is org-level configuration.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import phonenumbers
from phonenumbers import Leniency, PhoneNumberMatcher

PiiKind = Literal["email", "phone", "address"]

# Deliberately not the full RFC 5322 monster: this needs to catch what a model writes into a
# sentence, and an over-clever pattern costs more in false positives than it gains.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Words that make a digit run a phone number rather than a reference number.
_PHONE_CONTEXT = re.compile(
    r"\b(call|phone|mobile|cell|whats\s?app|telephone|tel|contact|reach|dial|ring|"
    r"number|hotline|helpline|landline)\b",
    re.I,
)
_PHONE_SEPARATORS = re.compile(r"[ \-.()]")

# Street addresses, best effort. Kept narrow and **flag-only by default** (`redact_addresses`
# is off) because the precision is nowhere near the other two: "12 Month Plan" and "24 Hour
# Support" both look like a house number followed by a road word.
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+([A-Z][A-Za-z]{2,}\s+){1,3}"
    r"(street|st|road|rd|avenue|ave|lane|ln|boulevard|blvd|drive|dr|nagar|colony|marg|"
    r"sector|block)\b",
    re.I,
)


@dataclass(frozen=True)
class PiiMatch:
    kind: PiiKind
    value: str
    start: int
    end: int


def _normalize_contact(value: str) -> str:
    """Comparison form for the allowlist: an operator writes a number however they like."""
    v = (value or "").strip().lower()
    if "@" in v:
        return v
    digits = re.sub(r"[^\d+]", "", v)
    # Compare on the last 10 digits so "+91 93453 27506", "093453 27506" and "9345327506"
    # all resolve to the same contact.
    tail = re.sub(r"\D", "", digits)
    return tail[-10:] if len(tail) >= 10 else (digits or v)


def classify_contact(value: str) -> PiiKind | None:
    """What kind of contact detail `value` is, or `None` if it is not one.

    Validates an allowlist entry. Deliberately **not** `find_pii()`: that scans prose, so it
    requires a bare digit run to look like a phone *in context* (a separator, a `+`, or a
    nearby "call"). An allowlist entry has no surrounding sentence — it **is** the contact —
    so those heuristics would reject a perfectly good `9345327506`.

    It reuses the same `_EMAIL` pattern and the same libphonenumber validity check, so the
    thing an operator is allowed to allowlist and the thing the redactor recognises cannot
    drift apart.
    """
    text = (value or "").strip()
    if not text:
        return None
    if _EMAIL.fullmatch(text):
        return "email"
    from app.core.config import settings  # local import: config imports nothing from here

    regions = [r.strip() for r in settings.guard_pii_phone_regions.split(",") if r.strip()]
    for region in [None, *regions]:
        try:
            parsed = phonenumbers.parse(text, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return "phone"
    return None


def build_allowlist(entries: list[str] | None) -> set[str]:
    """Normalised set of contacts an agent may share freely."""
    return {_normalize_contact(e) for e in (entries or []) if str(e).strip()}


def is_allowlisted(value: str, allowlist: set[str]) -> bool:
    return _normalize_contact(value) in allowlist


# A digit run a person typed as a phone number: optional +, then digits with the usual separators.
# Deliberately looser than `find_pii()` (which wants a `+`, a separator or a nearby "call"): this
# only ever builds a *permit* list of values the customer literally typed, so over-matching here
# can exempt a number the customer wrote themselves, never one they did not.
_TYPED_PHONE = re.compile(r"\+?\d[\d ().\-\u00a0\t]{5,}\d")


def customer_supplied_contacts(texts: Iterable[str | None]) -> set[str]:
    """Normalised emails and phone numbers the **customer typed themselves** in `texts`.

    This is the second, separate exemption from egress redaction. `build_allowlist()` (the org's
    `public_contacts`) is the business's own contact details, which any visitor may be given. This
    is different in kind: a value the customer wrote in this conversation, reflected back to that
    same customer — "I have you down as +91 98450 12345" — is not a disclosure, it is the product
    confirming what it was told. Scrubbing it (to "our contact page") makes every booking read-back
    look broken.

    **Callers must pass only the customer's own turns** (`role == "user"`). Never the reply, tool
    output, retrieved context or a model-written summary: anything the model *produced or pulled
    from elsewhere* is exactly what egress redaction exists to stop. Addresses and secrets are never
    exempted — only emails and phone numbers.
    """
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        for m in _EMAIL.finditer(text):
            found.add(_normalize_contact(m.group(0)))
        for m in _TYPED_PHONE.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if 7 <= len(digits) <= 15:
                found.add(_normalize_contact(m.group(0)))
    return found


def _matchable(text: str) -> str:
    """A scannable copy of `text` with **exactly the same length**, so offsets stay valid.

    Found against the live corpus, not in a test. The knowledge base that leaked is
    PDF-extracted: the `☎` glyph before the number arrived as a `\\x01` control character and
    the number's internal spacing as **tabs**. `PhoneNumberMatcher` finds **zero** numbers in
    that text and **two** once both are turned into ordinary spaces — so the one document
    Phase B exists for was invisible to the phone detector. Non-breaking spaces do the same.

    Tabs are converted and newlines are not, which is the minimum that works: measured against
    that document, keeping `\\t` yields 0 matches and converting it yields 2. Newlines stay so
    the address pattern and the phone matcher still see line boundaries rather than one run-on
    string, which would invent numbers that span two lines.

    Length-preserving is the point: `find_pii` returns offsets that the output guard slices the
    *original* string with, so NFKC (which can change length, `ﬁ` → `fi`) is not usable here.
    Every offending character maps to exactly one space.

    ⚠️ **Known, filed, deliberately not fixed (docs/14 K1-5 follow-up task 2):** this maps every
    character 1:1, so a *run* of several original spaces stays that many spaces — `PhoneNumberMatcher`
    does not match a number with wide (3+ char) irregular spacing between digit groups, and
    collapsing such a run here would shift every offset after it, breaking the invariant this
    function exists for. Measured against the real corpus (18 chunks, incl. the actual
    `Aurozen_AI` KB the 2026-08-03 incident came from) on 2026-08-17: **zero real instances** of
    this pattern. Filed rather than fixed — a real fix needs an offset-remapping layer, which is
    high-blast-radius for an unmeasured-live gap. Revisit only if a real document surfaces it.
    """
    return "".join(
        ch
        if ch == "\n"
        else " "
        if unicodedata.category(ch).startswith("C") or ch.isspace()
        else ch
        for ch in text
    )


def _phone_matches(scan: str, original: str, regions: list[str]) -> list[PiiMatch]:
    """`scan` is the length-preserving cleaned copy; `original` is what the caller will slice.

    Matching happens on `scan` (tabs and control characters gone), but the reported `value`
    comes from `original`, so a caller comparing it against an allowlist or logging a length
    sees the text as it really is rather than our cleaned rendering of it.
    """
    text = scan
    seen: dict[tuple[int, int], PiiMatch] = {}
    # `None` finds numbers written with an explicit +country code in any country; each named
    # region additionally finds locally-formatted numbers for that country.
    for region in [None, *regions]:
        try:
            matcher = PhoneNumberMatcher(text, region, leniency=Leniency.VALID)
            for m in matcher:
                raw = text[m.start : m.end]
                explicit = raw.lstrip().startswith("+")
                separated = bool(_PHONE_SEPARATORS.search(raw.strip()))
                window = text[max(0, m.start - 30) : m.start]
                contextual = bool(_PHONE_CONTEXT.search(window))
                if not (explicit or separated or contextual):
                    # A bare digit run with no formatting and no phone word: far more likely
                    # an order or invoice reference, and redacting those breaks real answers.
                    continue
                seen[(m.start, m.end)] = PiiMatch(
                    "phone", original[m.start : m.end], m.start, m.end
                )
        except Exception:  # a bad region string must never break a reply
            continue
    return list(seen.values())


def find_pii(
    text: str,
    *,
    regions: list[str] | None = None,
    include_addresses: bool = True,
) -> list[PiiMatch]:
    """Every PII span in `text`, ordered by position. Reports; does not redact."""
    text = text or ""
    if not text.strip():
        return []
    # Same length as `text`, so every offset below indexes the original correctly.
    scan = _matchable(text)
    found: list[PiiMatch] = [
        PiiMatch("email", text[m.start() : m.end()], m.start(), m.end())
        for m in _EMAIL.finditer(scan)
    ]
    email_spans = [(m.start, m.end) for m in found]
    for pm in _phone_matches(scan, text, regions or []):
        # A phone-looking run inside an email address is part of the address, not a number.
        if any(s <= pm.start < e for s, e in email_spans):
            continue
        found.append(pm)
    if include_addresses:
        found.extend(
            PiiMatch("address", text[m.start() : m.end()], m.start(), m.end())
            for m in _ADDRESS.finditer(scan)
        )
    return sorted(found, key=lambda m: m.start)


def scan_document_text(text: str, *, regions: list[str] | None = None) -> dict[str, int]:
    """`{kind: count}` for a document about to enter the retrieval corpus (docs/11 §6).

    **Reports; never rewrites, never blocks.** A business's own support documentation
    legitimately contains its public contact details, and silently mangling a client's
    knowledge base is a worse outcome than the leak it would prevent — the operator has to be
    the one who decides what comes out. So the document ingests normally and carries a flag.

    Secrets reuse `guardrails._SECRET_PATTERNS` rather than a second copy, so "what a secret
    looks like" has one definition across input screening, output redaction and ingest.
    """
    from app.chat.guardrails import _SECRET_PATTERNS  # local: avoids a circular import
    from app.core.config import settings

    regions = regions or [
        r.strip() for r in settings.guard_pii_phone_regions.split(",") if r.strip()
    ]
    flags = summarize(find_pii(text, regions=regions, include_addresses=True))
    secrets = sum(len(p.findall(text or "")) for p in _SECRET_PATTERNS)
    if secrets:
        flags["secret"] = secrets
    return flags


def summarize(matches: list[PiiMatch]) -> dict[str, int]:
    """`{kind: count}` — the shape stored on a document and printed by the audit script.

    Counts only. The value itself is never stored or reported: a PII report that echoes the
    PII is the same leak in a different place.
    """
    out: dict[str, int] = {}
    for m in matches:
        out[m.kind] = out.get(m.kind, 0) + 1
    return out

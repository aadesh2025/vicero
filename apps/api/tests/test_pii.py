"""PII detection and egress redaction (docs/11 Phase B).

Live failure 3: asked *"can i get your number or gmail"*, the deployed agent returned the
founder's personal email address and mobile number. The model was **not** hallucinating — it
retrieved them correctly from a knowledge base that should never have held them. So the
fixtures below are regression tests for a real incident, and are named as such.

The number is a structurally-valid Indian mobile in the same shape as the one that leaked,
not the real one: a test file is not a place to store the PII it exists to suppress.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.chat import output_guard
from app.chat.pii import PiiMatch, build_allowlist, find_pii, is_allowlisted, summarize
from app.llm.fake import FakeChatProvider

REGIONS = ["IN", "US", "GB"]

# Same shape as the number that leaked: +91 followed by a valid 10-digit mobile.
LEAKED_PHONE_VARIANTS = [
    "+91 93453 27506",
    "+919345327506",
    "+91-93453-27506",
    "+91.93453.27506",
    "93453 27506",
    "93453-27506",
    "(093453) 27506",
]


def _kinds(text: str) -> dict[str, int]:
    return summarize(find_pii(text, regions=REGIONS))


# ── Detection: the shapes that actually leaked ───────────────────────────────────────────


@pytest.mark.parametrize("number", LEAKED_PHONE_VARIANTS, ids=lambda n: n)
def test_regression_indian_mobile_is_detected_in_every_written_form(number: str) -> None:
    """A US-centric regex misses every one of these. This is why ADR-053 takes the dependency."""
    assert _kinds(f"you can reach me on {number} anytime").get("phone") == 1


def test_regression_personal_gmail_is_detected() -> None:
    assert _kinds("just email founder.personal@gmail.com directly").get("email") == 1


# The shape the live knowledge base actually had. Found by running the audit script against
# the real corpus, not by writing a test: PDF extraction turned the ☎ glyph into a \x01
# control character and the number's internal spacing into tabs, and libphonenumber matched
# **nothing** in that text. The one document Phase B exists for was invisible to the detector.
PDF_EXTRACTED = "Contact\n\x01 founder.personal@gmail.com | \x01 +91\t93453\t27506\n"


def test_regression_pdf_extracted_contact_line_is_detected() -> None:
    flags = _kinds(PDF_EXTRACTED)
    assert flags.get("email") == 1
    assert flags.get("phone") == 1, "control characters and tabs must not hide a phone number"


def test_offsets_stay_valid_against_the_original_text() -> None:
    """The cleaned copy is length-preserving, so a redaction slices the right span."""
    for m in find_pii(PDF_EXTRACTED, regions=REGIONS):
        assert PDF_EXTRACTED[m.start : m.end] == m.value
    out, counts = output_guard.redact_pii(PDF_EXTRACTED, build_allowlist([]), regions=REGIONS)
    assert counts == {"email": 1, "phone": 1}
    assert "founder.personal@gmail.com" not in out
    assert "93453" not in out


def test_nonbreaking_spaces_do_not_hide_a_number() -> None:
    # chr() rather than a literal or an escape: both are invisible in a diff, and ruff
    # (correctly) refuses ambiguous whitespace in source.
    nbsp = chr(0xA0)
    assert _kinds(f"call us on +91{nbsp}93453{nbsp}27506").get("phone") == 1


def test_bare_digit_run_needs_phone_context() -> None:
    """The precision knife-edge: a valid IN mobile and a 10-digit order id are the same string."""
    assert _kinds("call 9345327506 today").get("phone") == 1  # context word
    assert "phone" not in _kinds("your order 9345327506 has shipped")  # no context, no separators


@pytest.mark.parametrize(
    "text",
    [
        "your order 9345327506 has shipped",
        "the invoice total is 1234567890",
        "your tracking number is 1Z999AA10123456784",
        "the price is 45000 rupees",
        "our office is open 10am to 7pm",
        "we have 12 Month Plan options",
        "reference 2024110800123 was processed",
    ],
    ids=lambda t: t[:28],
)
def test_ordinary_replies_contain_no_pii(text: str) -> None:
    """A false positive here redacts a real answer — an order number becomes 'our contact page'."""
    assert _kinds(text) == {}, f"false positive on: {text}"


def test_addresses_are_detected_but_off_by_default() -> None:
    text = "our office is at 42 Anna Salai Road, Chennai"
    assert summarize(find_pii(text, regions=REGIONS, include_addresses=True)).get("address") == 1
    assert "address" not in summarize(find_pii(text, regions=REGIONS, include_addresses=False))


def test_phone_inside_an_email_is_not_double_counted() -> None:
    assert _kinds("write to 9345327506@example.com please") == {"email": 1}


def test_summarize_reports_counts_only() -> None:
    """A PII report that echoes the PII is the same leak in a different place."""
    matches = [PiiMatch("email", "a@b.com", 0, 7), PiiMatch("email", "c@d.com", 8, 15)]
    out = summarize(matches)
    assert out == {"email": 2}
    assert "a@b.com" not in str(out)


# ── The allowlist: the false-positive case that matters most ─────────────────────────────


def test_allowlisted_business_contacts_pass_through_unredacted() -> None:
    """An agent that cannot give out its own support address is broken, not safe."""
    allow = build_allowlist(["support@acme.com", "+91 80 4000 1000"])
    text = "You can email support@acme.com or call +91 80 4000 1000."
    out, counts = output_guard.redact_pii(text, allow, regions=REGIONS)
    assert out == text
    assert counts == {}


def test_allowlist_matches_however_the_number_is_written() -> None:
    allow = build_allowlist(["+91 93453 27506"])
    for variant in ["+919345327506", "93453 27506", "093453-27506"]:
        assert is_allowlisted(variant, allow), variant
    assert not is_allowlisted("+91 99999 11111", allow)


def test_non_allowlisted_contacts_are_replaced_with_a_natural_phrase() -> None:
    allow = build_allowlist(["support@acme.com"])
    out, counts = output_guard.redact_pii(
        "Email founder.personal@gmail.com or support@acme.com", allow, regions=REGIONS
    )
    assert "founder.personal@gmail.com" not in out
    assert "support@acme.com" in out  # allowlisted, untouched
    assert output_guard.PII_REPLACEMENT in out
    assert "[redacted]" not in out  # reads like a person, not like a bug
    assert counts == {"email": 1}


def test_empty_allowlist_redacts_everything() -> None:
    """An org that has published nothing shares nothing — distinct from PII checks being off."""
    out, counts = output_guard.redact_pii(
        "reach me at someone@example.com", build_allowlist([]), regions=REGIONS
    )
    assert "someone@example.com" not in out
    assert counts == {"email": 1}


def test_redaction_leaves_the_rest_of_the_sentence_intact() -> None:
    out, _ = output_guard.redact_pii(
        "Sure — you can email hidden@example.com and we reply within one business day.",
        build_allowlist([]),
        regions=REGIONS,
    )
    assert out.startswith("Sure — you can email ")
    assert out.endswith(" and we reply within one business day.")


# ── apply(): PII sits alongside the other output checks ──────────────────────────────────


def test_apply_redacts_pii_and_reports_counts() -> None:
    verdict = output_guard.apply(
        "call me on +91 93453 27506",
        None,
        leak_threshold=0.35,
        pii_allowlist=build_allowlist([]),
        pii_regions=REGIONS,
    )
    assert "93453" not in verdict.text
    assert verdict.pii_redacted == {"phone": 1}
    assert verdict.changed


def test_apply_without_an_allowlist_skips_pii_entirely() -> None:
    """`None` means 'do not run this check' (the Playground); an empty set means 'share nothing'."""
    text = "call me on +91 93453 27506"
    verdict = output_guard.apply(text, None, leak_threshold=0.35, pii_allowlist=None)
    assert verdict.text == text
    assert verdict.pii_redacted == {}


# ── End to end ───────────────────────────────────────────────────────────────────────────


async def test_pii_never_reaches_a_visitor(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scripted model reply names contacts the visitor never typed; none may get through."""
    monkeypatch.setattr(
        FakeChatProvider, "scripted_reply", "Contact founder.personal@gmail.com on +91 93453 27506."
    )
    signup = await client.post(
        "/v1/auth/signup", json={"email": "piiguard@example.com", "password": "password123"}
    )
    token = signup.json()["access_token"]
    org = await client.post(
        "/v1/orgs", json={"name": "PiiOrg"}, headers={"Authorization": f"Bearer {token}"}
    )
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    agent = await client.post("/v1/agents", json={"name": "Pii Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": "fake", "model": "fake-1"}},
        headers=headers,
    )

    r = await client.post(
        f"/v1/agents/{aid}/chat",
        json={"message": "who should I contact?", "stream": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    content = r.json()["content"]
    assert "founder.personal@gmail.com" not in content
    assert "93453" not in content

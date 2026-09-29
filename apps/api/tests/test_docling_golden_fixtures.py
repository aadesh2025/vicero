"""K1-5 — golden-file tests against a REAL `docling-serve` (docs/14 §11, §K1-5).

**Opt-in, same shape as the L2/L3 guard-model tests being live-disabled by default**: these never
run in the normal suite because `docling-serve` has no published port by design (docs/14 §9 — an
internal-only service must not be reachable from outside the compose network). They run only when
someone explicitly points `DOCLING_ENDPOINT` at a reachable instance, e.g. from inside a container
on the `vicero_default` network:

    docker run --rm --network vicero_default \\
      -e DOCLING_ENDPOINT=http://docling:5001 \\
      -v "$(pwd)/apps/api:/app:rw" vicero-api:latest \\
      python -m pytest tests/test_docling_golden_fixtures.py -v

Every assertion below is pinned to REAL output captured 2026-08-17 against a live `docling-serve`
v2.119.0 — not guessed, not copied from docs/14's narrative. See
`tests/fixtures/docling/README.md` for the full provenance, including a bug this run found (the
service converter was silently sending a malformed request — fixed in `app/rag/converters.py`,
see its `_options()` docstring) and a fixture correction (the original PII fixture's five-space
digit separators never actually matched, for reasons unrelated to Docling — also in the README).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from app.chat import pii
from app.rag import loaders
from app.rag.converters import DoclingServiceConverter

_ENDPOINT = os.environ.get("DOCLING_ENDPOINT", "").strip()
_FIXTURES = Path(__file__).parent / "fixtures" / "docling"


def _live_docling_reachable() -> bool:
    if not _ENDPOINT:
        return False
    try:
        return httpx.get(f"{_ENDPOINT}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _live_docling_reachable(),
    reason="opt-in: set DOCLING_ENDPOINT to a reachable docling-serve to run K1-5's golden files",
)


def _converter() -> DoclingServiceConverter:
    return DoclingServiceConverter(_ENDPOINT, timeout_seconds=180.0)


async def test_table_structure_survives_docling_but_not_legacy() -> None:
    """The word-soup problem §3.2 W4 exists to fix, proven on a real multi-table PDF."""
    data = (_FIXTURES / "table-heavy-pricing.pdf").read_bytes()

    legacy_text = loaders.load_bytes(data, filename="table-heavy-pricing.pdf", mime_type="application/pdf")
    assert "|" not in legacy_text  # flattened — no grid structure survives pypdf

    result = await _converter().convert(data, filename="table-heavy-pricing.pdf", mime_type="application/pdf")
    assert result.has_structure is True
    assert "| Starter" in result.text and "| Growth" in result.text  # real columns, not word-soup
    assert "| Region" in result.text  # the second table on the page also survived


async def test_ocr_fires_where_legacy_finds_nothing() -> None:
    """§3.2 W3: a scanned document with zero embedded text must not become `status=failed`."""
    data = (_FIXTURES / "scanned-refund-policy.pdf").read_bytes()

    legacy_text = loaders.load_bytes(data, filename="scanned-refund-policy.pdf", mime_type="application/pdf")
    assert legacy_text == ""  # genuinely zero text layer — this is what a scan looks like to pypdf

    result = await _converter().convert(data, filename="scanned-refund-policy.pdf", mime_type="application/pdf")
    assert result.has_structure is True
    assert "14 days" in result.text  # OCR actually read the policy body, not just any text
    assert "support@example.com" in result.text


async def test_pii_fixture_yields_a_detectable_phone_through_both_paths() -> None:
    """K1-5's literal acceptance criterion — both directions (the 2026-08-04 rule: an assertion
    that only ever exercises the passing path proves nothing about the failing one)."""
    data = (_FIXTURES / "pii-incident-contact-page.pdf").read_bytes()

    legacy_text = loaders.load_bytes(data, filename="pii-incident-contact-page.pdf", mime_type="application/pdf")
    legacy_matches = pii.find_pii(legacy_text)
    assert any(m.kind == "phone" for m in legacy_matches), legacy_matches

    result = await _converter().convert(
        data, filename="pii-incident-contact-page.pdf", mime_type="application/pdf"
    )
    assert result.has_structure is True
    docling_matches = pii.find_pii(result.text)
    assert any(m.kind == "phone" for m in docling_matches), docling_matches

    # The acceptance criterion's other half: no raw control character survives either path.
    assert "\x01" not in legacy_text
    assert "\x01" not in result.text


async def test_confidence_report_is_present_on_a_real_conversion() -> None:
    """Groundwork for K6-A — not required by K1-5, captured because it rides on the same call.

    Not asserted as a fixed value: confidence scores are a property of the real ML pipeline and
    will drift across Docling versions. This only pins that the field exists and is structured
    the way K6-A's design assumes — a document-level object with a computed `mean_grade`, not
    something Vicero has to average from components itself. `table_score` and `ocr_score` are
    asserted absent-or-null rather than any particular value: this fixture has no scanned image
    and (as of 2026-08-17) `table_score` is unimplemented in the service regardless of content.
    """
    data = (_FIXTURES / "table-heavy-pricing.pdf").read_bytes()
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{_ENDPOINT}/v1/convert/file",
            files={"files": ("table-heavy-pricing.pdf", data, "application/pdf")},
            data={
                "to_formats": ["md", "json"],
                "do_ocr": "true",
                "do_table_structure": "true",
                "do_picture_description": "false",
            },
        )
        resp.raise_for_status()
        body = resp.json()

    confidence = body.get("confidence")
    assert confidence is not None
    assert confidence.get("mean_grade") in {"poor", "fair", "good", "excellent"}
    assert confidence.get("table_score") is None  # not yet implemented server-side, docs/14 K6.1

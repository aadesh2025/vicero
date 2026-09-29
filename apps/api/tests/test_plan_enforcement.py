"""Phase A4: creation-time plan enforcement (docs/22 §11).

A0-A3 built the entitlement engine and the endpoints that report it; none of it stopped anyone
from exceeding their limits. This is the gate itself, one resource per section — at-cap → 402,
under-cap → succeeds, unlimited (`None`) → always succeeds, `trial_expired`/`plan_expired` → 402
regardless of count, and an org already over a *new*, lower cap keeps what it has.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeBase, Organization
from tests.selfserve_helpers import trial_org

pytestmark = pytest.mark.usefixtures("self_serve")


async def _set_plan(
    db: AsyncSession, org_id: str, plan: str, *, expires_at: dt.datetime | None = None
) -> None:
    org = await db.get(Organization, uuid.UUID(org_id))
    assert org is not None
    org.plan = plan
    org.plan_expires_at = expires_at
    await db.flush()


FUTURE = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=20)
PAST = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)


async def _create_kb(client: AsyncClient, headers: dict[str, str], name: str = "KB") -> Response:
    return await client.post(
        "/v1/knowledge", json={"name": name, "embedding_provider": "fake"}, headers=headers
    )


# ── knowledge bases (max_knowledge_bases) ─────────────────────────────────────
async def test_kb_creation_blocked_at_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`starter` allows exactly 1 knowledge base."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)

    first = await _create_kb(client, headers, "First")
    assert first.status_code == 201, first.text

    second = await _create_kb(client, headers, "Second")
    assert second.status_code == 402, second.text
    assert second.json()["error"]["code"] == "plan_limit"
    assert "Starter" in second.json()["error"]["message"]
    assert second.json()["error"]["details"]["feature"] == "knowledge_bases"


async def test_kb_creation_succeeds_under_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    """`pro` allows 5 — well under is a plain 201."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)
    resp = await _create_kb(client, headers)
    assert resp.status_code == 201, resp.text


async def test_kb_creation_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "legacy")
    for i in range(3):  # legacy has no cap at all
        resp = await _create_kb(client, headers, f"KB {i}")
        assert resp.status_code == 201, resp.text


async def test_kb_creation_blocked_when_the_plan_has_expired_regardless_of_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A lapsed Pro org (well under its 5-KB cap) still can't create one — expiry blocks
    creation of every limited resource, not just messaging."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_kb(client, headers)
    assert resp.status_code == 402, resp.text
    assert resp.json()["error"]["code"] == "plan_limit"


async def test_an_org_already_over_a_new_lower_cap_keeps_its_existing_knowledge_bases(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A `pro` org with 3 KBs downgraded to `starter` (cap 1): the existing 3 are untouched —
    the gate blocks creating a 4th, it never deletes or disables what already exists."""
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "pro", expires_at=FUTURE)
    for i in range(3):
        assert (await _create_kb(client, headers, f"KB {i}")).status_code == 201

    await _set_plan(db_session, org_id, "starter", expires_at=FUTURE)

    existing = (
        await db_session.execute(
            KnowledgeBase.__table__.select().where(KnowledgeBase.organization_id == uuid.UUID(org_id))
        )
    ).fetchall()
    assert len(existing) == 3  # untouched

    blocked = await _create_kb(client, headers, "4th")
    assert blocked.status_code == 402, blocked.text


# ── documents + storage (max_documents, storage_bytes) ───────────────────────
async def _kb_id(client: AsyncClient, headers: dict[str, str]) -> str:
    resp = await _create_kb(client, headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _create_text_doc(client: AsyncClient, headers: dict[str, str], kb_id: str, text: str) -> Response:
    return await client.post(
        f"/v1/knowledge/{kb_id}/documents",
        json={"source_type": "text", "text": text, "filename": "note.txt"},
        headers=headers,
    )


async def test_document_creation_blocked_at_the_document_count_cap(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "trial")  # trial's own cap: max_documents=10
    kb_id = await _kb_id(client, headers)

    for i in range(10):
        resp = await _create_text_doc(client, headers, kb_id, f"document number {i}")
        assert resp.status_code == 201, resp.text

    blocked = await _create_text_doc(client, headers, kb_id, "the 11th")
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["error"]["details"]["feature"] == "documents"


async def test_document_creation_succeeds_under_the_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    _, headers, _org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)
    resp = await _create_text_doc(client, headers, kb_id, "well under the trial's cap of 10")
    assert resp.status_code == 201, resp.text


async def test_document_creation_always_succeeds_on_an_unlimited_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    await _set_plan(db_session, org_id, "legacy")
    kb_id = await _kb_id(client, headers)
    for i in range(12):  # comfortably past the trial's own cap of 10
        resp = await _create_text_doc(client, headers, kb_id, f"doc {i}")
        assert resp.status_code == 201, resp.text


async def test_document_creation_blocked_when_the_plan_has_expired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers, org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)
    await _set_plan(db_session, org_id, "pro", expires_at=PAST)
    resp = await _create_text_doc(client, headers, kb_id, "should be blocked")
    assert resp.status_code == 402, resp.text


async def test_storage_bytes_is_checked_before_the_upload_is_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Trial's storage cap is 100 MB. One oversized "document" must be refused outright rather
    than partially written, and the counter must not move."""
    _, headers, _org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)

    too_big = "x" * (101 * 1024 * 1024)  # 101 MB of text — over the trial's 100 MB cap
    resp = await _create_text_doc(client, headers, kb_id, too_big)
    assert resp.status_code == 402, resp.text
    assert resp.json()["error"]["details"]["feature"] == "storage"

    docs = await client.get(f"/v1/knowledge/{kb_id}/documents", headers=headers)
    assert docs.json() == []  # nothing was created


async def test_org_storage_usage_tracks_uploads_and_deletes_live(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The A1 counter (`org_storage_usage`) was only ever backfilled once; this pins that it now
    moves on every create and delete, which is what makes the storage gate meaningful at all."""
    from app.models import OrgStorageUsage

    _, headers, org_id = await trial_org(client)
    kb_id = await _kb_id(client, headers)

    text = "a" * 1000
    created = await _create_text_doc(client, headers, kb_id, text)
    assert created.status_code == 201, created.text
    doc_id = created.json()["id"]

    row = await db_session.get(OrgStorageUsage, uuid.UUID(org_id))
    assert row is not None
    assert row.bytes_used == 1000
    assert row.documents_count == 1

    deleted = await client.delete(f"/v1/knowledge/documents/{doc_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    await db_session.refresh(row)
    assert row.bytes_used == 0
    assert row.documents_count == 0

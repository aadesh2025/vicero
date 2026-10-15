"""The demo knowledge seed (scripts/seed_demo_knowledge.py): content, ingestion path, agent wiring, idempotency."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, KnowledgeBase, Organization, User
from app.modules.knowledge import service as kb_service
from scripts import seed_demo_knowledge as seed
from tests.test_channels import _fake_agent, _headers

DEMO_DIR = Path(__file__).resolve().parents[3] / "docs" / "demo-knowledge"


@pytest.fixture(autouse=True)
def _no_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_service, "enqueue_document_ingestion", lambda *_a, **_k: None)


def test_the_demo_content_covers_what_the_assistant_is_asked() -> None:
    docs, prompt = seed.collect(DEMO_DIR)
    names = [n for n, _ in docs]
    assert names == sorted(names) and "README.md" not in names
    text = " ".join(t for _, t in docs).lower()
    for topic in ("overview", "pricing", "opening hours", "booking", "frequently asked", "hand over"):
        assert topic in text, topic
    pricing = dict(docs)["04-pricing-demo.md"]
    assert "DEMO" in pricing and "not a real offer" in pricing
    # The agent is told to stay inside the knowledge and to hand over when unsure.
    assert "ONLY from the knowledge base" in prompt
    assert "Hand over to a human" in prompt
    assert "name" in prompt and "email" in prompt
    for tool in ("check_availability", "book_appointment", "get_customer"):
        assert tool in prompt, tool


async def _agent(client: AsyncClient, email: str) -> uuid.UUID:
    headers = await _headers(client, email)
    return uuid.UUID(await _fake_agent(client, headers))


async def test_seeding_creates_the_kb_documents_and_wires_the_agent(
    client: AsyncClient, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    agent_id = await _agent(client, "demoseed1@example.com")
    assert await seed.seed(db_session, agent_id, DEMO_DIR, publish=False) == 0

    kbs = (await db_session.execute(select(KnowledgeBase).where(KnowledgeBase.name == seed.KB_NAME))).scalars().all()
    assert len(kbs) == 1
    docs = (await db_session.execute(select(Document).where(Document.knowledge_base_id == kbs[0].id))).scalars().all()
    assert sorted(d.filename for d in docs) == sorted(n for n, _ in seed.collect(DEMO_DIR)[0])
    assert all(d.status == "queued" and d.source_type == "text" for d in docs)

    from app.modules.agents.service import _latest_version

    version = await _latest_version(db_session, agent_id)
    assert version.rag_config["enabled"] is True and version.rag_config["knowledge_base_ids"] == [str(kbs[0].id)]
    assert version.features["handoff_enabled"] is True
    assert "ONLY from the knowledge base" in (version.system_prompt or "")
    assert version.suggested_prompts == seed.PROMPTS and not version.is_published
    assert "queued for ingestion" in capsys.readouterr().out


async def test_seeding_twice_replaces_documents_instead_of_duplicating(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    agent_id = await _agent(client, "demoseed2@example.com")
    await seed.seed(db_session, agent_id, DEMO_DIR, publish=False)
    await seed.seed(db_session, agent_id, DEMO_DIR, publish=False)
    kbs = (await db_session.execute(select(KnowledgeBase).where(KnowledgeBase.name == seed.KB_NAME))).scalars().all()
    assert len(kbs) == 1
    docs = (await db_session.execute(select(Document).where(Document.knowledge_base_id == kbs[0].id))).scalars().all()
    assert len(docs) == len(seed.collect(DEMO_DIR)[0])


async def test_publish_flag_makes_the_edited_version_live(client: AsyncClient, db_session: AsyncSession) -> None:
    agent_id = await _agent(client, "demoseed3@example.com")
    user = (await db_session.execute(select(User).where(User.email == "demoseed3@example.com"))).scalar_one()
    user.email_verified_at = dt.datetime.now(tz=dt.UTC)
    org = (await db_session.execute(select(Organization).order_by(Organization.created_at.desc()))).scalars().first()
    assert org is not None
    org.plan = "legacy"
    await db_session.flush()

    assert await seed.seed(db_session, agent_id, DEMO_DIR, publish=True) == 0
    from app.modules.agents.service import _latest_version

    assert (await _latest_version(db_session, agent_id)).is_published


async def test_an_unknown_agent_is_a_clean_error(db_session: AsyncSession, capsys: pytest.CaptureFixture[str]) -> None:
    assert await seed.seed(db_session, uuid.uuid4(), DEMO_DIR, publish=False) == 1
    assert "no agent" in capsys.readouterr().err

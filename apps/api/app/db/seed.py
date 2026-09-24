"""Seed the database with a working demo org/user/agent/KB/tool/channel.

Run with: ``make seed`` (or ``uv run python -m app.db.seed``). Idempotent — it no-ops
if the demo organization already exists.
"""

from __future__ import annotations

import asyncio
import os
import secrets

from argon2 import PasswordHasher
from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from app.db.session import SessionFactory
from app.llm.catalog import DEFAULT_CHAT_MODEL
from app.models import (
    Agent,
    AgentVersion,
    Channel,
    Document,
    KnowledgeBase,
    Membership,
    Organization,
    Tool,
    User,
)

log = get_logger("seed")
_ph = PasswordHasher()

OWNER_EMAIL = os.getenv("SEED_OWNER_EMAIL", "owner@botforge.local")
OWNER_PASSWORD = os.getenv("SEED_OWNER_PASSWORD", "changeme-dev-only")
ORG_SLUG = "demo"


async def seed() -> None:
    configure_logging()
    async with SessionFactory() as session:
        existing = (
            await session.execute(select(Organization).where(Organization.slug == ORG_SLUG))
        ).scalar_one_or_none()
        if existing is not None:
            log.info("seed_skipped", reason="demo org already exists", org_id=str(existing.id))
            return

        owner = (
            await session.execute(select(User).where(User.email == OWNER_EMAIL))
        ).scalar_one_or_none()
        if owner is None:
            owner = User(
                email=OWNER_EMAIL,
                password_hash=_ph.hash(OWNER_PASSWORD),
                full_name="Demo Owner",
                is_active=True,
            )
            session.add(owner)
            await session.flush()

        org = Organization(name="AUROZEN Demo", slug=ORG_SLUG, plan="pro", created_by=owner.id)
        session.add(org)
        await session.flush()

        session.add(Membership(organization_id=org.id, user_id=owner.id, role="owner", status="active"))

        agent = Agent(
            organization_id=org.id,
            name="Support Concierge",
            slug="support-concierge",
            description="Answers from the demo knowledge base.",
            status="published",
            public_key=f"bf_pub_{secrets.token_urlsafe(16)}",
            is_public=True,
            created_by=owner.id,
        )
        session.add(agent)
        await session.flush()

        version = AgentVersion(
            agent_id=agent.id,
            version=1,
            is_published=True,
            system_prompt=(
                "You are a helpful support concierge. Answer only from the knowledge base, in your "
                "own words — never mention the documents or write source markers like [1]."
            ),
            persona={"character": "friendly concierge", "role": "support", "tone": "Friendly", "guardrails": []},
            welcome_message="Hi! How can I help you today?",
            fallback_message="I'm not sure — want me to connect you with a teammate?",
            suggested_prompts=["Where is my order?", "What is your return policy?"],
            model_config_json={
                "provider": "groq",
                "model": DEFAULT_CHAT_MODEL,
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 1024,
            },
            rag_config={"enabled": True, "knowledge_base_ids": [], "top_k": 5, "score_threshold": 0.35, "hybrid": True},
            features={"tools_enabled": True, "memory_enabled": True, "handoff_enabled": True},
            created_by=owner.id,
        )
        session.add(version)
        await session.flush()
        agent.current_version_id = version.id

        kb = KnowledgeBase(organization_id=org.id, name="Demo Docs", created_by=owner.id)
        session.add(kb)
        await session.flush()
        # rag_config now points at the KB.
        version.rag_config = {**version.rag_config, "knowledge_base_ids": [str(kb.id)]}

        for name, text in [
            ("returns-policy.txt", "Returns accepted within 30 days for a full refund."),
            ("shipping.txt", "We ship worldwide; delivery takes 3-7 business days."),
        ]:
            session.add(
                Document(
                    knowledge_base_id=kb.id,
                    organization_id=org.id,
                    source_type="text",
                    filename=name,
                    mime_type="text/plain",
                    size_bytes=len(text),
                    status="ready",
                    chunk_count=0,  # embedding happens in Phase 7 ingestion
                    created_by=owner.id,
                )
            )

        session.add(
            Tool(
                organization_id=org.id,
                agent_id=agent.id,
                name="lookup_order",
                type="http",
                description="Look up an order by id.",
                enabled=True,
                config={"method": "GET", "url": "https://example.com/orders/{id}", "headers": {}},
                input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
                created_by=owner.id,
            )
        )

        session.add(
            Channel(
                organization_id=org.id,
                agent_id=agent.id,
                type="widget",
                name="Website widget",
                enabled=True,
                config={"theme": "dark", "colors": {"accent": "#FF6A3D"}, "position": "bottom-right"},
                created_by=owner.id,
            )
        )

        await session.commit()
        log.info("seed_complete", org_id=str(org.id), owner=OWNER_EMAIL)


if __name__ == "__main__":
    asyncio.run(seed())

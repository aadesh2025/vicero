"""Load the demo knowledge base into an agent and point the agent at it.

    python scripts/seed_demo_knowledge.py --agent-id <uuid> --dir docs/demo-knowledge [--publish]

Goes through the same service layer as the dashboard (knowledge base, text documents queued for the
worker's ingestion, agent version edit, publish), acting as the workspace owner. Idempotent: the knowledge base
is found by name, a document with the same filename is replaced, and re-running creates one more agent draft
at most. Every *.md file in the directory except README.md is ingested; `agent-system-prompt.txt` becomes the
agent's system prompt. All of it is demo content.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

KB_NAME = "Demo knowledge (AUROZEN AI / Vicero)"
PROMPT_FILE = "agent-system-prompt.txt"
WELCOME = "Hi! I'm the AUROZEN AI assistant. Ask me about our services or pricing, or I can book you a call."
PROMPTS = ["What do you sell?", "How much does it cost?", "Can I book a demo?"]


def collect(directory: Path) -> tuple[list[tuple[str, str]], str]:
    """`([(filename, text)], system_prompt)` from the demo directory."""
    docs = [
        (p.name, p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("*.md"))
        if p.name.lower() != "readme.md" and p.read_text(encoding="utf-8").strip()
    ]
    prompt = (directory / PROMPT_FILE).read_text(encoding="utf-8").strip()
    if not docs or not prompt:
        raise SystemExit(f"error: need *.md files and {PROMPT_FILE} in {directory}")
    return docs, prompt


async def seed(session: AsyncSession, agent_id: uuid.UUID, directory: Path, publish: bool) -> int:
    """The whole job on an open session (the caller commits). Returns a process exit code."""
    from sqlalchemy import select

    from app.models import Agent, Membership, Organization, User
    from app.modules.agents import schemas as agent_schemas
    from app.modules.agents import service as agents
    from app.modules.knowledge import schemas as kb_schemas
    from app.modules.knowledge import service as kb
    from app.modules.orgs.deps import OrgContext

    docs, prompt = collect(directory)
    agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        print(f"error: no agent {agent_id}", file=sys.stderr)
        return 1
    org = await session.get(Organization, agent.organization_id)
    owner = (
        await session.execute(
            select(Membership).where(Membership.organization_id == agent.organization_id, Membership.role == "owner")
        )
    ).scalars().first()
    user = await session.get(User, owner.user_id) if owner else None
    if org is None or owner is None or user is None:
        print("error: the agent's workspace has no owner", file=sys.stderr)
        return 1
    ctx = OrgContext(org=org, membership=owner, user=user)

    kbs = [k for k in await kb.list_kbs(session, ctx) if k.name == KB_NAME]
    kb_out = kbs[0] if kbs else await kb.create_kb(
        session, ctx, kb_schemas.CreateKBRequest(name=KB_NAME, description="Demo content, not a real offer.")
    )
    existing = {d.filename: d for d in await kb.list_documents(session, ctx, kb_out.id)}
    for filename, text in docs:
        if filename in existing:
            await kb.delete_document(session, ctx, existing[filename].id)
        await kb.create_document(
            session, ctx, kb_out.id, kb_schemas.CreateDocumentRequest(source_type="text", text=text, filename=filename)
        )
    print(f"knowledge base {kb_out.id}: {len(docs)} documents queued for ingestion")

    latest = (await agents.list_versions(session, ctx, agent_id))[-1]
    rag = {**latest.rag_config, "enabled": True, "knowledge_base_ids": [str(kb_out.id)]}
    features = {**latest.features, "handoff_enabled": True}
    patched = await agents.update_version(
        session,
        ctx,
        agent_id,
        latest.version,
        agent_schemas.UpdateVersionRequest(
            system_prompt=prompt,
            welcome_message=WELCOME,
            suggested_prompts=PROMPTS,
            rag_config=rag,
            features=features,
        ),
    )
    print(f"agent version {patched.version} saved ({'published' if patched.is_published else 'draft'})")
    if publish and not patched.is_published:
        await agents.publish_version(session, ctx, agent_id, patched.version)
        print(f"agent version {patched.version} published")
    return 0


async def run(agent_id: uuid.UUID, directory: Path, publish: bool) -> int:
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        code = await seed(session, agent_id, directory, publish)
        if code == 0:
            await session.commit()
        return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-id", type=uuid.UUID, required=True)
    parser.add_argument("--dir", type=Path, default=Path("docs/demo-knowledge"))
    parser.add_argument("--publish", action="store_true", help="publish the edited version (else it stays a draft)")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.agent_id, args.dir, args.publish))


if __name__ == "__main__":
    raise SystemExit(main())

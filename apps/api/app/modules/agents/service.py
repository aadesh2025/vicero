"""Agents & versions service, plus the playground chat runtime (no RAG yet)."""

from __future__ import annotations

import datetime as dt
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import variables
from app.chat.agent_trace import persist_agent_steps
from app.chat.assembly import compose_system_prompt
from app.chat.budget import agentic_loop_enabled, turn_budget
from app.chat.handoff import trigger_handoff, wants_handoff
from app.chat.runtime import ToolExecutor, TurnResult, run_turn
from app.core import rbac
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.templates import AGENT_TEMPLATES, get_template
from app.llm.base import ChatProvider
from app.llm.catalog import DEFAULT_CHAT_MODEL
from app.llm.registry import get_chat_provider, get_chat_provider_chain
from app.llm.types import ChatRequest, Message, StreamEvent
from app.models import PLAYGROUND_CHANNEL, Agent, AgentVersion, Conversation, WidgetConfig
from app.models import Message as DBMessage
from app.modules.agents import schemas
from app.modules.orgs.deps import OrgContext
from app.rag.agent_retrieval import retrieve_for_version
from app.rag.retrieval import Citation
from app.tools.service import build_tooling

log = get_logger("agents")

DEFAULT_MODEL_CONFIG = {
    "provider": "groq",
    "model": DEFAULT_CHAT_MODEL,
    "temperature": 0.7,
    "top_p": 1.0,
    "max_tokens": 1024,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stop": [],
}
# score_threshold 0.35: nomic-embed-text scores genuinely-relevant chunks ~0.65-0.76 and
# tangential ones ~0.5, so 0.35 keeps real matches while dropping noise. (0.7 was too high —
# it filtered out relevant chunks; see the RAG diagnosis.)
DEFAULT_RAG_CONFIG = {"enabled": False, "knowledge_base_ids": [], "top_k": 5, "score_threshold": 0.35, "hybrid": True}
DEFAULT_FEATURES = {"tools_enabled": False, "memory_enabled": True, "handoff_enabled": False}

# Seeded default so a new agent behaves like a grounded support bot out of the box: answer only
# from the knowledge base, admit when it doesn't know instead of inventing, stay in support tone.
# The accuracy rules are deliberately unchanged; only the *voice* is — a grounded answer that
# narrates its own sourcing ("according to the documents…") reads like a citation list, not a
# person, and that is what customers actually see. See also `app/rag/context.py`'s header.
#
# The "don't name your sources" rule is deliberately scoped to *phrasing*, and the no-context case
# is spelled out. Measured, not guessed: an earlier draft ended that rule with "Just answer." and,
# when retrieval returned nothing, invented support hours and a refund window in 3 of 3 samples
# where the pre-rewrite prompt refused in 3 of 3. Do not soften these lines without re-running
# that check — the tone fix must not cost the grounding.
DEFAULT_SYSTEM_PROMPT = (
    "You are a customer-support assistant talking to a customer in a live chat. Answer using ONLY "
    "the information in the knowledge base context provided to you in this conversation.\n\n"
    "Rules:\n"
    "- If the answer is not in the provided context, say you don't have that information and offer "
    "to connect the user with a human. Do NOT guess, and do NOT use outside/general knowledge.\n"
    "- Never invent facts, prices, hours, features, policies, or links that aren't in the context. "
    "If no context was provided at all, you have nothing to answer from — say exactly that.\n"
    "- Write the way a real support teammate talks: warm, direct, and in your own words. Never "
    "narrate where the answer came from — no '[1]', no 'according to the documents', no 'based on "
    "the provided context'.\n"
    "- That last rule is about phrasing only. It never licenses answering something the context "
    "doesn't cover: when you don't have it, say so plainly, in the same human voice.\n"
    "- Keep it short. A sentence or two is usually enough; use a short list only when the answer "
    "genuinely has several parts.\n"
    "- Match the customer's energy — if they just say hi, say hi back and ask how you can help "
    "instead of listing everything you know.\n"
    "- If the user asks something unrelated to the knowledge base, politely steer them back to "
    "what you can help with."
)


def _public_key() -> str:
    return f"bf_pub_{secrets.token_urlsafe(16)}"


def _slug_base(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"


async def _unique_slug(session: AsyncSession, org_id: uuid.UUID, name: str) -> str:
    base = _slug_base(name)
    candidate, n = base, 1
    while True:
        exists = (
            await session.execute(
                select(Agent.id).where(Agent.organization_id == org_id, Agent.slug == candidate)
            )
        ).scalar_one_or_none()
        if exists is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


async def _get_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.organization_id != ctx.org.id or agent.deleted_at is not None:
        raise AppError("agents.not_found", "Agent not found.", 404)
    return agent


async def _latest_version(session: AsyncSession, agent_id: uuid.UUID) -> AgentVersion:
    stmt = (
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version.desc())
        .limit(1)
    )
    version = (await session.execute(stmt)).scalar_one_or_none()
    if version is None:
        raise AppError("agents.no_version", "Agent has no versions.", 500)
    return version


async def _get_version(session: AsyncSession, agent_id: uuid.UUID, number: int) -> AgentVersion:
    stmt = select(AgentVersion).where(
        AgentVersion.agent_id == agent_id, AgentVersion.version == number
    )
    version = (await session.execute(stmt)).scalar_one_or_none()
    if version is None:
        raise AppError("agents.version_not_found", "Version not found.", 404)
    return version


async def _agent_out(session: AsyncSession, agent: Agent) -> schemas.AgentOut:
    draft = await _latest_version(session, agent.id)
    return schemas.AgentOut(
        id=agent.id,
        name=agent.name,
        slug=agent.slug,
        description=agent.description,
        status=agent.status,
        public_key=agent.public_key,
        is_public=agent.is_public,
        current_version_id=agent.current_version_id,
        draft_version=draft.version,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _version_out(v: AgentVersion) -> schemas.VersionOut:
    return schemas.VersionOut(
        id=v.id,
        version=v.version,
        is_published=v.is_published,
        system_prompt=v.system_prompt,
        persona=v.persona,
        welcome_message=v.welcome_message,
        fallback_message=v.fallback_message,
        suggested_prompts=v.suggested_prompts,
        llm_config=v.model_config_json,
        rag_config=v.rag_config,
        features=v.features,
        created_at=v.created_at,
    )


# ── Agent CRUD ────────────────────────────────────────────────────────────────
def list_templates() -> list[schemas.AgentTemplateOut]:
    """The creation-time template catalog. Static and identical for every org, so no RBAC
    beyond the normal org auth the route already requires."""
    return [
        schemas.AgentTemplateOut(
            id=t.id,
            label=t.label,
            icon=t.icon,
            description=t.description,
            system_prompt=t.system_prompt,
            welcome_message=t.welcome_message,
            suggested_prompts=list(t.suggested_prompts),
            tone=t.tone,
            suggested_next_step=t.suggested_next_step,
        )
        for t in AGENT_TEMPLATES
    ]


def _seed_from_template(template_id: str | None) -> dict[str, Any]:
    """The first draft's field values: template-seeded when asked for, blank defaults otherwise.

    The template is copied, never referenced — only its id is recorded (so the builder can show
    the matching next-step hint). Editing `templates.py` later must not mutate a live agent.
    """
    blank: dict[str, Any] = {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "welcome_message": "Hi! How can I help you today?",
        "fallback_message": "I'm not sure about that — want me to connect you with a teammate?",
        "suggested_prompts": [],
        "persona": {},
        "model_config_json": dict(DEFAULT_MODEL_CONFIG),
    }
    if template_id is None:
        return blank

    template = get_template(template_id)
    if template is None:
        raise AppError("agents.unknown_template", f"Unknown agent template '{template_id}'.", 400)
    return {
        **blank,
        "system_prompt": template.system_prompt,
        "welcome_message": template.welcome_message,
        "suggested_prompts": list(template.suggested_prompts),
        "persona": {"tone": template.tone, "template_id": template.id},
        "model_config_json": {**DEFAULT_MODEL_CONFIG, **template.model_overrides},
    }


async def create_agent(session: AsyncSession, ctx: OrgContext, data: schemas.CreateAgentRequest) -> schemas.AgentOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    seed = _seed_from_template(data.template_id)  # validates before anything is written
    agent = Agent(
        organization_id=ctx.org.id,
        name=data.name,
        slug=await _unique_slug(session, ctx.org.id, data.name),
        description=data.description,
        status="draft",
        public_key=_public_key(),
        created_by=ctx.user.id,
    )
    session.add(agent)
    await session.flush()
    session.add(
        AgentVersion(
            agent_id=agent.id,
            version=1,
            is_published=False,
            rag_config=dict(DEFAULT_RAG_CONFIG),
            features=dict(DEFAULT_FEATURES),
            created_by=ctx.user.id,
            **seed,
        )
    )
    await session.flush()
    return await _agent_out(session, agent)


async def list_agents(session: AsyncSession, ctx: OrgContext) -> list[schemas.AgentOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    stmt = (
        select(Agent)
        .where(Agent.organization_id == ctx.org.id, Agent.deleted_at.is_(None))
        .order_by(Agent.created_at.desc())
    )
    agents = (await session.execute(stmt)).scalars().all()
    return [await _agent_out(session, a) for a in agents]


async def get_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> schemas.AgentOut:
    rbac.require_permission(ctx.role, rbac.READ)
    return await _agent_out(session, await _get_agent(session, ctx, agent_id))


async def update_agent(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.UpdateAgentRequest
) -> schemas.AgentOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    agent = await _get_agent(session, ctx, agent_id)
    if data.name is not None:
        agent.name = data.name
    if data.description is not None:
        agent.description = data.description
    if data.is_public is not None:
        agent.is_public = data.is_public
    if data.status is not None:
        agent.status = data.status
    return await _agent_out(session, agent)


async def delete_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    agent = await _get_agent(session, ctx, agent_id)
    agent.deleted_at = dt.datetime.now(tz=dt.UTC)


async def duplicate_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> schemas.AgentOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    source = await _get_agent(session, ctx, agent_id)
    src_version = await _latest_version(session, source.id)
    clone = Agent(
        organization_id=ctx.org.id,
        name=f"{source.name} (copy)",
        slug=await _unique_slug(session, ctx.org.id, f"{source.name}-copy"),
        description=source.description,
        status="draft",
        public_key=_public_key(),
        created_by=ctx.user.id,
    )
    session.add(clone)
    await session.flush()
    session.add(
        AgentVersion(
            agent_id=clone.id,
            version=1,
            is_published=False,
            system_prompt=src_version.system_prompt,
            persona=dict(src_version.persona),
            welcome_message=src_version.welcome_message,
            fallback_message=src_version.fallback_message,
            suggested_prompts=list(src_version.suggested_prompts),
            model_config_json=dict(src_version.model_config_json),
            rag_config=dict(src_version.rag_config),
            features=dict(src_version.features),
            created_by=ctx.user.id,
        )
    )
    await session.flush()
    return await _agent_out(session, clone)


# ── Versions ──────────────────────────────────────────────────────────────────
async def list_versions(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> list[schemas.VersionOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_agent(session, ctx, agent_id)
    stmt = select(AgentVersion).where(AgentVersion.agent_id == agent_id).order_by(AgentVersion.version.desc())
    return [_version_out(v) for v in (await session.execute(stmt)).scalars().all()]


async def _draft_from_latest(session: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> AgentVersion:
    """Create a new draft version copied from the latest version. Returns the ORM object."""
    latest = await _latest_version(session, agent_id)
    draft = AgentVersion(
        agent_id=agent_id,
        version=latest.version + 1,
        is_published=False,
        system_prompt=latest.system_prompt,
        persona=dict(latest.persona),
        welcome_message=latest.welcome_message,
        fallback_message=latest.fallback_message,
        suggested_prompts=list(latest.suggested_prompts),
        model_config_json=dict(latest.model_config_json),
        rag_config=dict(latest.rag_config),
        features=dict(latest.features),
        created_by=user_id,
    )
    session.add(draft)
    await session.flush()
    return draft


async def create_version(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> schemas.VersionOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    await _get_agent(session, ctx, agent_id)
    return _version_out(await _draft_from_latest(session, agent_id, ctx.user.id))


# Widget logo upload. SVG is rejected outright (it can carry executable script → stored-XSS on an
# origin that serves uploads cross-origin). Both MIME and extension are checked because MIME is
# client-supplied and spoofable. 2 MB cap (mirrors the KB upload's size-reasoning).
_LOGO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_LOGO_MAX_BYTES = 2 * 1024 * 1024


async def save_widget_logo(
    session: AsyncSession,
    ctx: OrgContext,
    agent_id: uuid.UUID,
    *,
    filename: str | None,
    content_type: str | None,
    data: bytes,
) -> dict[str, str]:
    """Validate + store a widget logo image; return its public URL (a relative API path)."""
    from pathlib import Path

    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    agent = await _get_agent(session, ctx, agent_id)
    if not data:
        raise AppError("widget.empty_logo", "The uploaded image is empty.", 400)
    if len(data) > _LOGO_MAX_BYTES:
        raise AppError("widget.logo_too_large", "Logo must be 2 MB or smaller.", 400)
    ext = Path(filename or "").suffix.lower()
    ctype = (content_type or "").lower().split(";")[0].strip()
    if ctype == "image/svg+xml" or ext == ".svg":
        raise AppError("widget.logo_svg_rejected", "SVG images aren't allowed for security reasons.", 400)
    if ctype not in _LOGO_TYPES or ext not in _LOGO_EXTS:
        raise AppError("widget.logo_bad_type", "Logo must be a PNG, JPG, WEBP, or GIF image.", 400)

    dest_dir = Path(settings.upload_dir) / str(ctx.org.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob(f"widget-logo-{agent_id}.*"):  # drop a prior logo (possibly a different ext)
        old.unlink(missing_ok=True)
    dest = dest_dir / f"widget-logo-{agent_id}{_LOGO_TYPES[ctype]}"
    dest.write_bytes(data)
    # Relative path — the widget prepends its own API base (port/host-agnostic).
    return {"logo_url": f"/v1/public/agents/{agent.public_key}/widget-logo"}


def _merge_persona(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge an incoming persona into the stored one. The ``widget`` sub-object is deep-merged so
    a partial update (e.g. just a logo) never nulls out previously-set colors, and the incoming
    widget config is validated (hex/enums) → typed error on bad values (CLAUDE.md §8)."""
    from pydantic import ValidationError

    from app.modules.public.schemas import WidgetConfigIn

    base = dict(existing or {})
    merged: dict[str, Any] = {**base}
    for key, value in incoming.items():
        if key == "widget" and isinstance(value, dict):
            try:
                WidgetConfigIn(**value)
            except ValidationError as exc:
                raise AppError(
                    "widget.invalid_config",
                    "Invalid widget configuration.",
                    400,
                    details=[{"field": e["loc"][-1], "error": e["msg"]} for e in exc.errors()],
                ) from exc
            existing_widget = base.get("widget")
            merged["widget"] = {**(existing_widget if isinstance(existing_widget, dict) else {}), **value}
        else:
            merged[key] = value
    return merged


def _apply_version_patch(version: AgentVersion, data: schemas.UpdateVersionRequest) -> None:
    if data.system_prompt is not None:
        version.system_prompt = data.system_prompt
    if data.persona is not None:
        version.persona = _merge_persona(version.persona, data.persona)
    if data.welcome_message is not None:
        version.welcome_message = data.welcome_message
    if data.fallback_message is not None:
        version.fallback_message = data.fallback_message
    if data.suggested_prompts is not None:
        version.suggested_prompts = data.suggested_prompts
    if data.llm_config is not None:
        version.model_config_json = data.llm_config
    if data.rag_config is not None:
        version.rag_config = data.rag_config
    if data.features is not None:
        version.features = data.features


async def update_version(
    session: AsyncSession,
    ctx: OrgContext,
    agent_id: uuid.UUID,
    number: int,
    data: schemas.UpdateVersionRequest,
) -> schemas.VersionOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    await _get_agent(session, ctx, agent_id)

    # Backward compatibility: appearance used to live at `persona.widget`. It now has its
    # own unversioned store, so route a legacy write there rather than letting it land in
    # the persona where nothing would ever read it again.
    if data.persona and isinstance(data.persona.get("widget"), dict):
        persona = dict(data.persona)
        widget = persona.pop("widget")
        await update_widget_config(session, ctx, agent_id, widget)
        data = data.model_copy(update={"persona": persona or None})

    version = await _get_version(session, agent_id, number)
    if version.is_published:
        # Branch-on-edit (ADR-023): a published version is immutable, so the first edit
        # transparently forks a new draft copied from the latest version and patches that.
        # The response carries the new (higher) version number so the client can re-target.
        version = await _draft_from_latest(session, agent_id, ctx.user.id)
        log.info("branch_on_edit", agent_id=str(agent_id), new_version=version.version)
    _apply_version_patch(version, data)
    return _version_out(version)


async def publish_version(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, number: int) -> schemas.AgentOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_PUBLISH)
    agent = await _get_agent(session, ctx, agent_id)
    version = await _get_version(session, agent_id, number)
    # docs/17 Phase 3 publish gate (ADR-079) — local import to avoid a circular import:
    # app.modules.agent_tests.service imports FROM this module (to build a Playground-style
    # request), so this module cannot also import it at the top level.
    from app.modules.agent_tests.service import latest_batch_has_failures

    if await latest_batch_has_failures(session, agent_id):
        raise AppError(
            "agents.tests_failing",
            "The latest test run has failures — fix them before publishing.",
            400,
        )
    version.is_published = True
    agent.current_version_id = version.id
    agent.status = "published"
    return await _agent_out(session, agent)


async def rollback(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, number: int) -> schemas.AgentOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_PUBLISH)
    agent = await _get_agent(session, ctx, agent_id)
    version = await _get_version(session, agent_id, number)
    if not version.is_published:
        raise AppError("agents.rollback_unpublished", "Can only roll back to a published version.", 400)
    agent.current_version_id = version.id
    agent.status = "published"
    return await _agent_out(session, agent)


# ── Playground (uses the draft version + LLM layer + RAG) ──────────────────────
async def _retrieve_context(
    session: AsyncSession, ctx: OrgContext, version: AgentVersion, query: str
) -> tuple[str, list[Citation]]:
    return await retrieve_for_version(session, ctx.org.id, version, query)


def _build_request(
    version: AgentVersion,
    data: schemas.PlaygroundRequest,
    stream: bool,
    context_block: str = "",
    *,
    agent_name: str | None = None,
    business_name: str | None = None,
) -> ChatRequest:
    mc = version.model_config_json or {}
    messages: list[Message] = []
    # The Playground runs the same identity lock as production. An operator asking "is my
    # agent configured correctly?" has to be shown what a visitor would actually get.
    system_prompt = compose_system_prompt(
        version.system_prompt,
        version.persona,
        agent_name=agent_name,
        business_name=business_name,
        # The Playground previews what a real turn renders, so an operator writing
        # `Hi {{user_name}}` sees "Hi there" rather than the raw placeholder.
        variables=variables.build_context(agent_name=agent_name, business_name=business_name),
    )
    if system_prompt:
        messages.append(Message(role="system", content=system_prompt))
    if context_block:
        messages.append(Message(role="system", content=context_block))
    for turn in data.history or []:
        messages.append(Message(role=turn.role, content=turn.content))
    messages.append(Message(role="user", content=data.message))
    return ChatRequest(
        model=mc.get("model", "fake-1"),
        messages=messages,
        temperature=mc.get("temperature", 0.7),
        top_p=mc.get("top_p", 1.0),
        max_tokens=mc.get("max_tokens", 1024),
        frequency_penalty=mc.get("frequency_penalty", 0.0),
        presence_penalty=mc.get("presence_penalty", 0.0),
        stop=mc.get("stop") or None,
        stream=stream,
    )


async def _resolve_playground_provider(
    session: AsyncSession,
    ctx: OrgContext,
    agent: Agent,
    provider: str,
    model_config: dict[str, Any] | None = None,
) -> ChatProvider:
    try:
        if model_config:
            return await get_chat_provider_chain(
                session, ctx.org.id, model_config, agent_id=agent.id, resolve=get_chat_provider
            )
        return await get_chat_provider(session, ctx.org.id, provider, agent_id=agent.id)
    except AppError:
        # Deliberately re-raised rather than stubbed. The Playground exists to answer "is my
        # agent configured correctly?", and a fake reply answers it wrongly: `echo: hi` looks
        # like a broken model, so the operator debugs the persona instead of the missing key.
        # The typed `llm.provider_unavailable` names the actual problem. (This supersedes the
        # CLAUDE §7 build-time stub here; §7 is about not blocking the *build*, and the fake
        # provider is still reachable by configuring `provider: "fake"` explicitly.)
        log.warning("playground_provider_unavailable", provider=provider, agent_id=str(agent.id))
        raise


async def _playground_conversation(
    session: AsyncSession, ctx: OrgContext, agent: Agent, conversation_id: uuid.UUID | None
) -> Conversation:
    """The conversation this playground turn belongs to, created on the first turn.

    Playground turns are persisted (they spend real tokens against a real key, and an
    operator asking "why is my bill that?" needs to see them) but tagged so they can be told
    apart from customer traffic.
    """
    if conversation_id is not None:
        conv = await session.get(Conversation, conversation_id)
        if conv is None or conv.organization_id != ctx.org.id:
            raise AppError("conversations.not_found", "Conversation not found.", 404)
        if conv.agent_id != agent.id:
            raise AppError(
                "conversations.agent_mismatch", "Conversation belongs to another agent.", 400
            )
        return conv
    conv = Conversation(
        organization_id=ctx.org.id,
        agent_id=agent.id,
        channel=PLAYGROUND_CHANNEL,
        status="active",
        title="Playground session",
    )
    session.add(conv)
    await session.flush()
    return conv


async def _persist_playground_turn(
    session: AsyncSession,
    conv: Conversation,
    user_message: str,
    result: TurnResult,
    latency_ms: int,
) -> None:
    """Record both halves of the turn, with the usage that turn actually cost.

    Deliberately mirrors `conversations.service._persist_assistant_message`'s columns so the
    analytics queries — which read `messages` and know nothing about who produced them —
    count a playground turn exactly like any other.
    """
    now = dt.datetime.now(tz=dt.UTC)
    session.add(
        DBMessage(
            conversation_id=conv.id,
            organization_id=conv.organization_id,
            role="user",
            content=user_message,
        )
    )
    assistant_msg = DBMessage(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        role="assistant",
        content=(result.content or "").strip() or None,
        citations=result.citations,
        provider=result.provider or None,
        model=result.model or None,
        tokens_prompt=result.prompt_tokens,
        tokens_completion=result.completion_tokens,
        cost_micros=result.cost_micros,
        latency_ms=latency_ms,
        error=result.error,
    )
    session.add(assistant_msg)
    # `id` is a Python-side UUIDv7 default, already set on the instance before flush — an
    # operator testing the agentic loop from the Playground gets a trace too, same as a real
    # conversation (see conversations/service.py `_finalize_turn`).
    await persist_agent_steps(
        session, result,
        conversation_id=conv.id, organization_id=conv.organization_id, message_id=assistant_msg.id,
    )
    conv.last_message_at = now
    conv.last_inbound_at = now
    await session.flush()


async def _playground_tooling(
    session: AsyncSession, ctx: OrgContext, agent: Agent, version: AgentVersion, provider: ChatProvider
) -> tuple[list[Any], ToolExecutor | None]:
    include_mcp = agentic_loop_enabled(ctx.org.agentic_loop_enabled)
    specs, executor = await build_tooling(
        session, ctx.org.id, agent, version, None, include_mcp=include_mcp
    )
    if specs and executor is not None and provider.supports_tools():
        return specs, executor
    return [], None


async def playground_stream(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.PlaygroundRequest
) -> AsyncIterator[str]:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    agent = await _get_agent(session, ctx, agent_id)
    version = await _latest_version(session, agent.id)

    # Keyword handoff (mirrors app.chat.inbound.InboundTurn — the real widget/channel path).
    # Checked before resolving a provider or spending a retrieval call, same ordering reason
    # as inbound.py: an operator testing "can you hand me off to a human" should see the same
    # behavior in the Playground that a real visitor gets, not a conversational guess from the
    # model with no Handoff record behind it.
    features = version.features or {}
    if features.get("handoff_enabled") and wants_handoff(data.message):
        conv = await _playground_conversation(session, ctx, agent, data.conversation_id)
        yield f'data: {{"type": "conversation", "conversation_id": "{conv.id}"}}\n\n'
        await trigger_handoff(session, conv, requested_by="user", reason="keyword")
        canned = (
            version.fallback_message
            or "Let me connect you with a teammate — someone will be with you shortly."
        )
        result = TurnResult()
        result.content = canned
        result.provider = "system"
        result.finish_reason = "handoff"
        await _persist_playground_turn(session, conv, data.message, result, 0)
        yield f"data: {StreamEvent(type='token', delta=canned).model_dump_json()}\n\n"
        yield f"data: {StreamEvent(type='done', finish_reason='handoff').model_dump_json()}\n\n"
        return

    provider_name = (version.model_config_json or {}).get("provider", "fake")
    provider = await _resolve_playground_provider(
        session, ctx, agent, provider_name, version.model_config_json or {}
    )
    context_block, citations = await _retrieve_context(session, ctx, version, data.message)
    req = _build_request(
        version, data, stream=True, context_block=context_block,
        agent_name=agent.name, business_name=ctx.org.name,
    )
    specs, executor = await _playground_tooling(session, ctx, agent, version, provider)
    if specs:
        req.tools = specs
    budget = turn_budget(ctx.org.agentic_loop_enabled, has_tools=executor is not None)
    conv = await _playground_conversation(session, ctx, agent, data.conversation_id)
    # Emitted before the first token so the client can thread the *next* turn onto this
    # conversation even if the stream is abandoned halfway.
    yield f'data: {{"type": "conversation", "conversation_id": "{conv.id}"}}\n\n'
    result = TurnResult()
    t0 = time.perf_counter()
    async for ev in run_turn(
        provider, req, [c.model_dump(mode="json") for c in citations], result,
        executor=executor, max_iters=settings.tool_max_iterations, budget=budget,
        # The Playground shows the operator what the model actually said — same reason it
        # withholds `fallback_message` and re-raises provider errors (docs/11 §4-L5).
        guard_output=False,
    ):
        yield f"data: {ev.model_dump_json()}\n\n"
    await _persist_playground_turn(
        session, conv, data.message, result, int((time.perf_counter() - t0) * 1000)
    )


async def playground_once(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.PlaygroundRequest
) -> dict[str, Any]:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    agent = await _get_agent(session, ctx, agent_id)
    version = await _latest_version(session, agent.id)

    # Keyword handoff — see the matching block in playground_stream() for why this must not
    # be skipped just because this is the non-streaming path.
    features = version.features or {}
    if features.get("handoff_enabled") and wants_handoff(data.message):
        conv = await _playground_conversation(session, ctx, agent, data.conversation_id)
        await trigger_handoff(session, conv, requested_by="user", reason="keyword")
        canned = (
            version.fallback_message
            or "Let me connect you with a teammate — someone will be with you shortly."
        )
        result = TurnResult()
        result.content = canned
        result.provider = "system"
        result.finish_reason = "handoff"
        await _persist_playground_turn(session, conv, data.message, result, 0)
        return {
            "conversation_id": str(conv.id),
            "content": canned,
            "citations": [],
            "tool_runs": [],
            "provider": "system",
            "model": None,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost_micros": 0},
            "finish_reason": "handoff",
        }

    provider_name = (version.model_config_json or {}).get("provider", "fake")
    provider = await _resolve_playground_provider(
        session, ctx, agent, provider_name, version.model_config_json or {}
    )
    context_block, citations = await _retrieve_context(session, ctx, version, data.message)
    req = _build_request(
        version, data, stream=False, context_block=context_block,
        agent_name=agent.name, business_name=ctx.org.name,
    )
    specs, executor = await _playground_tooling(session, ctx, agent, version, provider)
    if specs:
        req.tools = specs
    budget = turn_budget(ctx.org.agentic_loop_enabled, has_tools=executor is not None)
    conv = await _playground_conversation(session, ctx, agent, data.conversation_id)
    result = TurnResult()
    t0 = time.perf_counter()
    async for _ev in run_turn(
        provider, req, [c.model_dump(mode="json") for c in citations], result,
        executor=executor, max_iters=settings.tool_max_iterations, budget=budget,
        # The Playground shows the operator what the model actually said — same reason it
        # withholds `fallback_message` and re-raises provider errors (docs/11 §4-L5).
        guard_output=False,
    ):
        pass
    latency_ms = int((time.perf_counter() - t0) * 1000)
    if result.error:
        # Persisted before raising: a failed turn can still have burned prompt tokens, and a
        # cost report that only counts successes understates the bill.
        await _persist_playground_turn(session, conv, data.message, result, latency_ms)
        raise AppError("llm.provider_error", result.error, 502)
    await _persist_playground_turn(session, conv, data.message, result, latency_ms)
    return {
        "conversation_id": str(conv.id),
        "content": result.content.strip(),
        "citations": result.citations,
        "tool_runs": result.tool_runs,
        "provider": result.provider,
        "model": result.model,
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cost_micros": result.cost_micros,
        },
    }


async def agent_count(session: AsyncSession, org_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Agent).where(
        Agent.organization_id == org_id, Agent.deleted_at.is_(None)
    )
    return int((await session.execute(stmt)).scalar_one())


# ── Widget appearance (unversioned — a save is live) ─────────────────────────────
def _validate_widget(config: dict[str, Any]) -> None:
    from pydantic import ValidationError

    from app.modules.public.schemas import WidgetConfigIn

    try:
        WidgetConfigIn(**config)
    except ValidationError as exc:
        raise AppError(
            "widget.invalid_config",
            "Invalid widget configuration.",
            400,
            details=[{"field": e["loc"][-1], "error": e["msg"]} for e in exc.errors()],
        ) from exc


async def get_widget_config(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID
) -> dict[str, Any]:
    rbac.require_permission(ctx.role, rbac.READ)
    agent = await _get_agent(session, ctx, agent_id)
    row = (
        await session.execute(select(WidgetConfig).where(WidgetConfig.agent_id == agent.id))
    ).scalar_one_or_none()
    return dict(row.theme) if row else {}


async def update_widget_config(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    """Save the widget's appearance. Live immediately — no draft, no publish.

    Gated on AGENTS_WRITE, never AGENTS_PUBLISH: a colour is not a behaviour change, and
    making a client wait for review to fix their own branding would be absurd.
    Merge-on-write, so a partial update (just a logo) never nulls sibling colours.
    """
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    agent = await _get_agent(session, ctx, agent_id)
    _validate_widget(config)

    row = (
        await session.execute(select(WidgetConfig).where(WidgetConfig.agent_id == agent.id))
    ).scalar_one_or_none()
    if row is None:
        row = WidgetConfig(agent_id=agent.id, theme=config)
        session.add(row)
    else:
        row.theme = {**row.theme, **config}
    await session.flush()
    return dict(row.theme)

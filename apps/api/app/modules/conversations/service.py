"""Conversation persistence, the dashboard chat runtime, and memory."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import blocks as chat_blocks
from app.chat import guard_models, guardrails, memory, variables
from app.chat.agent_trace import persist_agent_steps
from app.chat.assembly import build_messages, compose_system_prompt, instruction_prompt_of
from app.chat.budget import AgentBudget, agentic_loop_enabled, turn_budget
from app.chat.pii import build_allowlist
from app.chat.runtime import ToolExecutor, TurnResult, run_turn
from app.core import rbac
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.crm import capture_from_message
from app.llm.base import ChatProvider
from app.llm.fake import RefusalProvider
from app.llm.registry import get_chat_provider, get_chat_provider_chain
from app.llm.types import ChatRequest, StreamEvent
from app.llm.types import Message as LLMMessage
from app.models import Agent, AgentVersion, Contact, Conversation, CrmContact, Message
from app.modules.conversations import schemas
from app.modules.orgs.deps import OrgContext
from app.rag.agent_retrieval import retrieve_for_version
from app.rag.retrieval import Citation
from app.tools.service import build_tooling
from app.webhooks.dispatch import emit_event

log = get_logger("conversations")

_HISTORY_FETCH = 100  # most-recent messages loaded before trimming to the prompt window


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


# ── Lookups ─────────────────────────────────────────────────────────────────────
async def _get_agent(session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.organization_id != ctx.org.id or agent.deleted_at is not None:
        raise AppError("agents.not_found", "Agent not found.", 404)
    return agent


async def _live_version(session: AsyncSession, agent: Agent) -> AgentVersion:
    """The version that answers: the published current version, else the latest draft."""
    if agent.current_version_id is not None:
        version = await session.get(AgentVersion, agent.current_version_id)
        if version is not None:
            return version
    stmt = (
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.version.desc())
        .limit(1)
    )
    version = (await session.execute(stmt)).scalar_one_or_none()
    if version is None:
        raise AppError("agents.no_version", "Agent has no versions.", 500)
    return version


async def _get_conversation(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> Conversation:
    conv = await session.get(Conversation, cid)
    if conv is None or conv.organization_id != ctx.org.id:
        raise AppError("conversations.not_found", "Conversation not found.", 404)
    return conv


async def _get_or_create_conversation(
    session: AsyncSession, ctx: OrgContext, agent: Agent, conversation_id: uuid.UUID | None
) -> Conversation:
    if conversation_id is not None:
        conv = await _get_conversation(session, ctx, conversation_id)
        if conv.agent_id != agent.id:
            raise AppError("conversations.agent_mismatch", "Conversation belongs to another agent.", 400)
        return conv
    conv = Conversation(
        organization_id=ctx.org.id,
        agent_id=agent.id,
        channel="dashboard",
        status="active",
    )
    session.add(conv)
    await session.flush()
    await emit_event(
        session,
        ctx.org.id,
        "conversation.created",
        {"conversation_id": str(conv.id), "channel": "dashboard"},
        agent_id=conv.agent_id,
    )
    return conv


async def _load_history(session: AsyncSession, conversation_id: uuid.UUID) -> list[LLMMessage]:
    stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role.in_(("user", "assistant", "tool")),
            Message.content.is_not(None),
        )
        .order_by(Message.created_at.desc())
        .limit(_HISTORY_FETCH)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()
    return [LLMMessage(role=m.role, content=m.content) for m in rows]


def _build_chat_request(version: AgentVersion, messages: list[LLMMessage], stream: bool) -> ChatRequest:
    mc = version.model_config_json or {}
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


async def _resolve_provider(
    session: AsyncSession,
    org_id: uuid.UUID,
    agent: Agent,
    provider: str,
    model_config: dict[str, Any] | None = None,
    fallback_message: str | None = None,
) -> ChatProvider:
    """The agent's provider, wrapped in its configured fallback chain when it has one.

    When no provider can be resolved — key missing, revoked, or every fallback exhausted —
    the visitor gets the agent's own fallback message. It must **never** be the fake provider:
    that answers with a literal `echo: <whatever they just typed>`, so an expired API key
    would have every customer on a client's site talking to a parrot that repeats them back,
    with nothing but a log line to say why. A plain "I can't answer right now" is honest, and
    the loud error below is what tells the operator to go fix the key.
    """
    try:
        if model_config:
            return await get_chat_provider_chain(
                session, org_id, model_config, agent_id=agent.id, resolve=get_chat_provider
            )
        return await get_chat_provider(session, org_id, provider, agent_id=agent.id)
    except AppError as exc:
        log.error(
            "chat_provider_unavailable",
            provider=provider,
            agent_id=str(agent.id),
            error=exc.message,
            effect="answering with the agent's fallback message",
        )
        return RefusalProvider(fallback_message or _DEFAULT_REFUSAL, name="unavailable")


# ── Persistence ─────────────────────────────────────────────────────────────────
async def _persist_user_message(
    session: AsyncSession, conv: Conversation, text: str, *, attachments: list[dict[str, Any]] | None = None
) -> Message:
    msg = Message(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        role="user",
        content=text,
        attachments=attachments or [],
    )
    session.add(msg)
    # Every inbound path funnels through here, so this is the one place the 24-hour
    # WhatsApp window clock is reset.
    conv.last_inbound_at = dt.datetime.now(tz=dt.UTC)
    # …and the one place CRM capture can see every customer message, including ones sent
    # while a human has taken the conversation over (which never reach a bot turn).
    try:
        await capture_from_message(session, conv, text)
    except Exception as exc:  # capture is an enrichment, never a reason to drop a message
        log.warning("crm_capture_failed", conversation=str(conv.id), error=str(exc))
    return msg


_DEFAULT_REFUSAL = "I'm not able to help with that topic. Is there something else I can do for you?"


def _refusal_text(version: AgentVersion) -> str:
    return version.fallback_message or _DEFAULT_REFUSAL


def _persist_assistant_message(
    session: AsyncSession, conv: Conversation, result: TurnResult, latency_ms: int
) -> Message:
    # Output redaction guardrail: strip secret-looking strings before the text is stored/returned.
    redacted = guardrails.redact_secrets((result.content or "").strip()) or None
    result.content = redacted or ""
    blocks = list(result.blocks or [])
    content = redacted
    if blocks:
        # `content` carries the blocks' plain-text version, so the inbox, transcripts, exports and the next
        # turn's history read a normal message and need no knowledge of blocks (docs/27 Phase 2).
        content = ((redacted or "") + chat_blocks.fallback_suffix(blocks)).strip() or None
    msg = Message(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        role="assistant",
        content=content,
        blocks=blocks or None,
        citations=result.citations,
        provider=result.provider or None,
        model=result.model or None,
        tokens_prompt=result.prompt_tokens,
        tokens_completion=result.completion_tokens,
        cost_micros=result.cost_micros,
        latency_ms=latency_ms,
        error=result.error,
    )
    session.add(msg)
    return msg


async def _message_count(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    return int((await session.execute(stmt)).scalar_one())


async def _maybe_summarize(session: AsyncSession, org_id: uuid.UUID, conv: Conversation) -> None:
    """Fold newly aged-out messages into `conv.memory_summary` when over the threshold."""
    total = await _message_count(session, conv.id)
    if total <= settings.memory_summary_threshold:
        return
    window = settings.memory_window_messages
    cut = total - window  # messages older than the recent window
    summarized_upto = int(conv.meta.get("summarized_upto", 0))
    if cut - summarized_upto < window:
        return  # not enough new aged-out messages to bother
    stmt = (
        select(Message)
        .where(Message.conversation_id == conv.id, Message.role.in_(("user", "assistant")))
        .order_by(Message.created_at.asc())
        .offset(summarized_upto)
        .limit(cut - summarized_upto)
    )
    older = list((await session.execute(stmt)).scalars().all())
    llm_older = [LLMMessage(role=m.role, content=m.content) for m in older if m.content]
    await memory.summarize(session, org_id, conv, llm_older)
    conv.meta = {**conv.meta, "summarized_upto": cut}


# ── Chat runtime ─────────────────────────────────────────────────────────────────
async def _prepare_turn(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.ChatRequest, stream: bool
) -> tuple[
    Conversation, ChatProvider, ChatRequest, list[dict[str, Any]], ToolExecutor | None, AgentBudget | None
]:
    agent = await _get_agent(session, ctx, agent_id)
    version = await _live_version(session, agent)
    conv = await _get_or_create_conversation(session, ctx, agent, data.conversation_id)

    history = await _load_history(session, conv.id)
    await _persist_user_message(session, conv, data.message)
    await session.flush()

    # L1 input guard (docs/11 §4-L1) — same screen the widget and channels run, so an
    # operator testing from the dashboard sees the behaviour a visitor would get.
    guard = (
        guardrails.screen_user_message(data.message, max_chars=settings.max_user_message_chars)
        if settings.guard_input_enabled
        else guardrails.InputVerdict()
    )
    # L2 classifier (docs/11 §4-L2), only when L1 let the message through — a regex-obvious
    # attack costs no tokens.
    l2_score: float | None = None
    if guard.blocked:
        l2_blocked = False
    else:
        l2_blocked, l2_score = await guard_models.is_injection(
            data.message, org_enabled=ctx.org.guard_injection_enabled
        )

    if guard.blocked or l2_blocked:
        log.warning(
            "guard_input_blocked",
            agent_id=str(agent.id),
            conversation_id=str(conv.id),
            layer="L1" if guard.blocked else "L2",
            category=guard.category,
            flags=guard.flags,
            l2_score=round(l2_score, 4) if l2_score is not None else None,
            message_sha256=hashlib.sha256(data.message.encode("utf-8")).hexdigest()[:16],
        )
        context_block, citations = "", cast(list[Citation], [])
    else:
        context_block, citations = await retrieve_for_version(
            session, ctx.org.id, version, data.message
        )
    messages = build_messages(
        system_prompt=compose_system_prompt(
            version.system_prompt,
            version.persona,
            agent_name=agent.name,
            business_name=ctx.org.name,
            # No contact on the dashboard path, so `{{user_name}}` resolves to its fallback
            # rather than a stale name from another conversation.
            variables=variables.build_context(
                agent_name=agent.name, business_name=ctx.org.name, conversation_variables=conv.variables
            ),
        ),
        context_block=context_block,
        memory_summary=conv.memory_summary,
        history=history,
        user_message=data.message,
        window_messages=settings.memory_window_messages,
    )
    provider_name = (version.model_config_json or {}).get("provider", "fake")
    provider = await _resolve_provider(
        session,
        ctx.org.id,
        agent,
        provider_name,
        version.model_config_json or {},
        fallback_message=version.fallback_message,
    )
    req = _build_chat_request(version, messages, stream=stream)

    # Pre-LLM refusals. The input guard redirects without naming a rule (docs/11 §4a); a
    # blocked topic uses the agent's own refusal line.
    topics = guardrails.blocked_topics_for(version.persona)
    if guard.blocked or l2_blocked:
        return conv, RefusalProvider(guardrails.INJECTION_REDIRECT), req, [], None, None
    if topics and guardrails.matches_blocked_topic(data.message, topics):
        provider = RefusalProvider(_refusal_text(version))
        return conv, provider, req, [], None, None

    # Tool calling: attach the agent's enabled tools when the provider supports them.
    # MCP tools are only attached (and a budget only built) when the agentic runtime is on
    # for this org — docs/17 Phase 1's platform-and-per-org flag, off by default.
    include_mcp = agentic_loop_enabled(ctx.org.agentic_loop_enabled)
    specs, executor = await build_tooling(
        session, ctx.org.id, agent, version, conv.id, include_mcp=include_mcp
    )
    if specs and executor is not None and provider.supports_tools():
        req.tools = specs
    else:
        executor = None
    budget = turn_budget(ctx.org.agentic_loop_enabled, has_tools=executor is not None)
    return conv, provider, req, [c.model_dump(mode="json") for c in citations], executor, budget


async def _finalize_turn(
    session: AsyncSession, conv: Conversation, result: TurnResult, latency_ms: int, first_text: str
) -> Message:
    """Persist the assistant message, bump the conversation, and maybe summarize. Reused by
    the widget/channel path (chat/inbound.py) as well as the dashboard chat below — one place
    to also persist `result.agent_steps`, so every turn-finishing path traces identically."""
    msg = _persist_assistant_message(session, conv, result, latency_ms)
    await persist_agent_steps(
        session, result,
        conversation_id=conv.id, organization_id=conv.organization_id, message_id=msg.id,
    )
    conv.last_message_at = _now()
    if not conv.title:
        conv.title = first_text[:80]
    await session.flush()
    await _maybe_summarize(session, conv.organization_id, conv)
    await emit_event(
        session,
        conv.organization_id,
        "message.created",
        {"conversation_id": str(conv.id), "message_id": str(msg.id), "role": "assistant"},
        agent_id=conv.agent_id,
    )
    return msg


async def _enqueue_finalize_turn(
    conv: Conversation, result: TurnResult, latency_ms: int, first_text: str
) -> None:
    """Hand a cancelled turn's persistence off to a Celery task (RISK-REGISTER R14) — used
    when the client disconnects before `_finalize_turn` could run inline. Not a first attempt:
    retrying `_finalize_turn` inline, on the SAME request session, inside this same except
    block (even wrapped in `asyncio.shield()` / `anyio.to_thread.run_sync()`) kept failing,
    because anyio's cancel scope re-raises `CancelledError` at EVERY subsequent checkpoint
    until the scope is actually exited — not once — so every further `await` in this handler,
    including `to_thread.run_sync()`'s own internal checkpoint, was cancelled again before the
    thread it was meant to dispatch ever ran (confirmed empirically: no `chat.finalize_turn`
    task ever reached the worker with that version). See `app.worker.tasks.finalize_turn_task`'s
    docstring for where the actual persistence happens — including why it may need to recreate
    `conv` itself, not just append the assistant reply: `conv` is only ever flushed, never
    committed, until this SAME request's own happy path finishes, so a disconnect landing early
    enough can cancel the conversation/user-message flush too, not just the assistant one.
    Every field this passes is already populated on the in-memory `conv` object regardless —
    `UUIDPrimaryKey` assigns its id client-side at construction, before any DB round trip.

    Deliberately a **plain synchronous call, not `queue_email`'s off-thread pattern** — this
    function's body contains no `await` at all, so there is no checkpoint left for the same
    scope to interrupt. `Task.delay()` is a synchronous network round-trip to Redis, so this
    does briefly block the event loop; accepted here (unlike in `queue_email`, which runs on
    every signup/invite) because this path only ever runs once a request is already cancelled
    and no client is waiting on it — a slow broker delays this cleanup, not a real request.
    """
    import dataclasses

    from app.worker.tasks import finalize_turn_task

    conv_data = {
        "id": str(conv.id),
        "organization_id": str(conv.organization_id),
        "agent_id": str(conv.agent_id),
        "channel": conv.channel,
        "channel_user_id": conv.channel_user_id,
        "contact_id": str(conv.contact_id) if conv.contact_id else None,
        "external_id": conv.external_id,
    }
    try:
        finalize_turn_task.delay(conv_data, dataclasses.asdict(result), latency_ms, first_text)
    except Exception:
        log.exception("finalize_turn_enqueue_failed", conversation_id=str(conv.id))


async def chat_events(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.ChatRequest
) -> AsyncIterator[StreamEvent]:
    """Yield StreamEvents for one turn, persisting user + assistant messages. SSE/WS format upstream."""
    rbac.require_permission(ctx.role, rbac.READ)
    conv, provider, req, citations, executor, budget = await _prepare_turn(
        session, ctx, agent_id, data, stream=True
    )

    result = TurnResult()
    t0 = time.perf_counter()
    msg: Message | None = None
    try:
        # Tell the client which conversation this is (esp. for a freshly created one). Inside
        # the `try` on purpose: a client that drops on the very first event has already had
        # `conv` and the user message flushed, and that is the earliest place to lose them.
        yield StreamEvent(type="conversation", conversation_id=str(conv.id))
        async for ev in run_turn(
            provider, req, citations, result, executor=executor,
            max_iters=settings.tool_max_iterations, budget=budget,
            protected_prompt=instruction_prompt_of(req.messages),
            pii_allowlist=build_allowlist(list(ctx.org.public_contacts or [])),
        ):
            yield ev
        latency_ms = int((time.perf_counter() - t0) * 1000)
        msg = await _finalize_turn(session, conv, result, latency_ms, data.message)
    except (asyncio.CancelledError, GeneratorExit):
        # The client disconnected mid-stream (tab closed, navigated away — RISK-REGISTER R14:
        # a load test proved this is common, not rare, and every prior version of this
        # function let the reply vanish from history when it happened). Covers a disconnect
        # landing ANYWHERE in the block above — during generation or mid-`_finalize_turn`
        # (`msg` still `None` either way, since a cancelled `await` never returns a value).
        #
        # BOTH exceptions, because Starlette ends a dropped stream two different ways and
        # the load test hit both: task cancellation (`CancelledError`, ~1/3 of drops — the
        # session is left mid-flush) and `aclose()` on the generator (`GeneratorExit`, the
        # other ~2/3 — the session is fine, so the request commits normally and the user
        # message survives, but nothing ever writes the reply). The first draft of this fix
        # caught only `CancelledError` and still lost 61 of 91 replies.
        if msg is None:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            await _enqueue_finalize_turn(conv, result, latency_ms, data.message)
        raise
    yield StreamEvent(type="message", message_id=str(msg.id))


async def chat_sse(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.ChatRequest
) -> AsyncIterator[str]:
    async for ev in chat_events(session, ctx, agent_id, data):
        yield f"data: {ev.model_dump_json()}\n\n"


async def chat_once(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID, data: schemas.ChatRequest
) -> dict[str, Any]:
    rbac.require_permission(ctx.role, rbac.READ)
    conv, provider, req, citations, executor, budget = await _prepare_turn(
        session, ctx, agent_id, data, stream=False
    )

    result = TurnResult()
    t0 = time.perf_counter()
    async for _ev in run_turn(
        provider, req, citations, result, executor=executor,
        max_iters=settings.tool_max_iterations, budget=budget,
        protected_prompt=instruction_prompt_of(req.messages),
        pii_allowlist=build_allowlist(list(ctx.org.public_contacts or [])),
    ):
        pass
    latency_ms = int((time.perf_counter() - t0) * 1000)

    msg = await _finalize_turn(session, conv, result, latency_ms, data.message)
    return {
        "conversation_id": str(conv.id),
        "message_id": str(msg.id),
        "content": (result.content or "").strip(),
        "citations": result.citations,
        "tool_runs": result.tool_runs,
        "provider": result.provider,
        "model": result.model,
        "usage": {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens},
        "cost_micros": result.cost_micros,
        "error": result.error,
    }


# ── Conversation CRUD ─────────────────────────────────────────────────────────────
def _message_out(m: Message) -> schemas.MessageOut:
    return schemas.MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        tool_calls=m.tool_calls,
        tool_call_id=m.tool_call_id,
        citations=m.citations,
        attachments=m.attachments or [],
        provider=m.provider,
        model=m.model,
        tokens_prompt=m.tokens_prompt,
        tokens_completion=m.tokens_completion,
        cost_micros=m.cost_micros,
        latency_ms=m.latency_ms,
        error=m.error,
        created_at=m.created_at,
    )


def contact_out(contact: Contact | None, person: CrmContact | None = None) -> schemas.ContactOut | None:
    """The name to show. A CRM link wins: a customer recognised by phone on a channel
    they've never used before should still appear as themselves, not as a raw handle."""
    if contact is None:
        return None
    username = (contact.extra or {}).get("username")
    return schemas.ContactOut(
        id=contact.id,
        display_name=(person.display_name if person else None) or contact.display_name,
        avatar_url=contact.avatar_url,
        username=str(username).lstrip("@") if username else None,
        crm_contact_id=contact.crm_contact_id,
    )


async def contacts_out_by_id(
    session: AsyncSession, convs: Sequence[Conversation]
) -> dict[uuid.UUID, schemas.ContactOut]:
    """Contacts for a whole page of conversations in two queries, keyed by contact id."""
    ids = {c.contact_id for c in convs if c.contact_id is not None}
    if not ids:
        return {}
    contacts = (await session.execute(select(Contact).where(Contact.id.in_(ids)))).scalars().all()
    person_ids = {c.crm_contact_id for c in contacts if c.crm_contact_id is not None}
    people: dict[uuid.UUID, CrmContact] = {}
    if person_ids:
        found = (await session.execute(select(CrmContact).where(CrmContact.id.in_(person_ids)))).scalars().all()
        people = {p.id: p for p in found}
    out: dict[uuid.UUID, schemas.ContactOut] = {}
    for c in contacts:
        built = contact_out(c, people.get(c.crm_contact_id) if c.crm_contact_id else None)
        if built is not None:
            out[c.id] = built
    return out


def _conversation_out(
    conv: Conversation, count: int, contact: schemas.ContactOut | None = None
) -> schemas.ConversationOut:
    return schemas.ConversationOut(
        id=conv.id,
        agent_id=conv.agent_id,
        channel=conv.channel,
        status=conv.status,
        title=conv.title,
        message_count=count,
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        contact=contact,
        channel_user_id=conv.channel_user_id,
    )


async def list_conversations(
    session: AsyncSession, ctx: OrgContext, agent_id: uuid.UUID | None
) -> list[schemas.ConversationOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    count_sub = (
        select(Message.conversation_id, func.count().label("n"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    stmt = (
        select(Conversation, func.coalesce(count_sub.c.n, 0))
        .outerjoin(count_sub, count_sub.c.conversation_id == Conversation.id)
        .where(Conversation.organization_id == ctx.org.id)
        .order_by(func.coalesce(Conversation.last_message_at, Conversation.created_at).desc())
    )
    if agent_id is not None:
        stmt = stmt.where(Conversation.agent_id == agent_id)
    rows = (await session.execute(stmt)).all()
    contacts = await contacts_out_by_id(session, [conv for conv, _ in rows])
    return [
        _conversation_out(conv, int(n), contacts.get(conv.contact_id) if conv.contact_id else None)
        for conv, n in rows
    ]


async def get_conversation(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> schemas.ConversationDetail:
    rbac.require_permission(ctx.role, rbac.READ)
    conv = await _get_conversation(session, ctx, cid)
    stmt = select(Message).where(Message.conversation_id == cid).order_by(Message.created_at.asc())
    msgs = list((await session.execute(stmt)).scalars().all())
    contacts = await contacts_out_by_id(session, [conv])
    base = _conversation_out(conv, len(msgs), contacts.get(conv.contact_id) if conv.contact_id else None)
    return schemas.ConversationDetail(
        **base.model_dump(),
        memory_summary=conv.memory_summary,
        messages=[_message_out(m) for m in msgs],
    )


async def get_messages(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> list[schemas.MessageOut]:
    rbac.require_permission(ctx.role, rbac.READ)
    await _get_conversation(session, ctx, cid)
    stmt = select(Message).where(Message.conversation_id == cid).order_by(Message.created_at.asc())
    return [_message_out(m) for m in (await session.execute(stmt)).scalars().all()]


async def update_conversation(
    session: AsyncSession, ctx: OrgContext, cid: uuid.UUID, data: schemas.UpdateConversationRequest
) -> schemas.ConversationOut:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    conv = await _get_conversation(session, ctx, cid)
    if data.title is not None:
        conv.title = data.title
    if data.status is not None:
        conv.status = data.status
    return _conversation_out(conv, await _message_count(session, cid))


async def delete_conversation(session: AsyncSession, ctx: OrgContext, cid: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.AGENTS_WRITE)
    conv = await _get_conversation(session, ctx, cid)
    await session.delete(conv)


# Public names for the pieces the follow-up generator reuses (app/chat/followup.py); the underscore ones stay private.
load_history = _load_history
resolve_provider = _resolve_provider
build_chat_request = _build_chat_request
finalize_turn = _finalize_turn


def variables_for(conv: Conversation, *, agent_name: str | None, business_name: str | None) -> dict[str, str]:
    """Prompt variables for a conversation, including `{{vars.name}}` from what tools collected."""
    return variables.build_context(
        agent_name=agent_name, business_name=business_name, conversation_variables=conv.variables
    )

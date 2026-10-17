"""Public (widget) config + chat runtime — no dashboard auth, keyed by public_key.

Reuses the dashboard chat building blocks (assembly, retrieval, tools, run_turn, persistence);
resolves the agent/org from its ``public_key`` instead of an OrgContext.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import ContactProfile
from app.chat import blocks
from app.chat.inbound import InboundTurn
from app.contacts import upsert_contact
from app.core.errors import AppError
from app.llm.types import StreamEvent
from app.models import Agent, AgentVersion, Conversation, Message, WidgetConfig
from app.modules.campaigns import service as campaign_service
from app.modules.conversations.service import _live_version
from app.modules.public import schemas


async def _resolve_agent(session: AsyncSession, public_key: str) -> tuple[Agent, AgentVersion]:
    stmt = select(Agent).where(Agent.public_key == public_key, Agent.deleted_at.is_(None))
    agent = (await session.execute(stmt)).scalar_one_or_none()
    if agent is None:
        raise AppError("public.agent_not_found", "No agent for this key.", 404)
    version = await _live_version(session, agent)
    return agent, version


def _theme_from(widget: dict[str, Any]) -> schemas.WidgetTheme:
    """Map the stored camelCase config onto the widget's snake_case response shape."""
    buttons = widget.get("inputBarButtons")
    ib = blocks.normalize_flags(widget.get("interactiveBlocks"))
    layout = widget.get("widgetLayout")
    radius = widget.get("cornerRadius")
    density = widget.get("density")
    return schemas.WidgetTheme(
        primary_color=widget.get("primaryColor", "#1F2937"),
        position=widget.get("position", "bottom-right"),
        launcher_text=widget.get("launcherText", "Chat with us"),
        branding=widget.get("branding", True),
        mode=widget.get("mode", "dark"),
        widget_style=widget.get("widgetStyle", "solid"),
        background_color=widget.get("backgroundColor"),
        text_color=widget.get("textColor"),
        bubble_color=widget.get("bubbleColor"),
        typing_area_color=widget.get("typingAreaColor"),
        font_family=widget.get("fontFamily", "system"),
        logo_url=widget.get("logoUrl"),
        floating_button_style=widget.get("floatingButtonStyle"),
        floating_button_color=widget.get("floatingButtonColor"),
        input_bar_buttons=buttons if isinstance(buttons, list) else ["attachment"],
        widget_layout=layout if layout in schemas.WIDGET_LAYOUTS else None,
        # bool is an int subclass; a stray `true` must not become radius 1.
        corner_radius=radius if isinstance(radius, int) and not isinstance(radius, bool) else None,
        density=density if density in schemas.WIDGET_DENSITIES else None,
        interactive_blocks=ib,
    )


_LOGO_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def widget_logo_file(org_id: uuid.UUID, agent_id: uuid.UUID) -> tuple[str, str] | None:
    """Return (path, media_type) for an agent's stored widget logo, or None."""
    from pathlib import Path

    from app.core.config import settings

    matches = sorted(Path(settings.upload_dir, str(org_id)).glob(f"widget-logo-{agent_id}.*"))
    if not matches:
        return None
    path = matches[0]
    return str(path), _LOGO_MEDIA.get(path.suffix.lower(), "application/octet-stream")


async def widget_theme(session: AsyncSession, agent: Agent) -> schemas.WidgetTheme:
    """Appearance comes from `widget_configs`, **not** from the resolved version.

    Deliberately bypasses `_live_version()`: a colour change shouldn't wait behind a publish
    approval meant for changes to what the AI says. A save here is live immediately.
    """
    row = (
        await session.execute(select(WidgetConfig).where(WidgetConfig.agent_id == agent.id))
    ).scalar_one_or_none()
    return _theme_from(row.theme if row else {})


async def get_config(session: AsyncSession, public_key: str) -> schemas.PublicConfig:
    agent, version = await _resolve_agent(session, public_key)
    persona = version.persona or {}
    name = str(persona.get("displayName") or agent.name)
    return schemas.PublicConfig(
        agent_id=agent.id,
        name=name,
        welcome_message=version.welcome_message or "Hi! How can I help you today?",
        suggested_prompts=list(version.suggested_prompts or []),
        theme=await widget_theme(session, agent),
        campaigns=[
            schemas.WidgetCampaign(**c.model_dump())
            for c in await campaign_service.active_widget_campaigns(session, agent)
        ],
    )


def _visitor_profile(visitor: schemas.Visitor | None) -> ContactProfile:
    """Whatever the embedding site chose to tell us about the visitor."""
    if visitor is None:
        return ContactProfile()
    extra: dict[str, Any] = {}
    if visitor.email:
        extra["email"] = visitor.email
    if visitor.metadata:
        extra.update(visitor.metadata)
    return ContactProfile(display_name=visitor.name or None, extra=extra)


async def _get_or_create_conversation(
    session: AsyncSession, agent: Agent, data: schemas.PublicChatRequest, visitor_id: str
) -> Conversation:
    """Resume the visitor's own conversation, or start a new one.

    **Resuming requires owning it.** A `conversation_id` is not a secret: it lives in the
    embedding page's `localStorage`, and leaks through browser history, a screenshot, a pasted
    support link or a referrer. Without the `channel_user_id` check below, presenting one was
    enough to read *and* post into a stranger's entire thread with a 200 — the whole transcript
    is then handed to the model as history, so "summarise what we discussed" reads it back out.

    A mismatch returns the same 404 as a conversation that does not exist. Distinguishing
    "exists but is not yours" would confirm an id is live, which is an enumeration oracle on a
    public, unauthenticated route.
    """
    if data.conversation_id is not None:
        conv = await session.get(Conversation, data.conversation_id)
        if (
            conv is None
            or conv.agent_id != agent.id
            or conv.organization_id != agent.organization_id
            or conv.channel != "widget"
            or conv.channel_user_id != visitor_id
        ):
            raise AppError("public.conversation_not_found", "Conversation not found.", 404)
        return conv
    meta: dict[str, Any] = {}
    if data.visitor is not None:
        meta["visitor"] = data.visitor.model_dump(exclude_none=True)
    # The widget is a channel like any other in the inbox, so its visitors get contact
    # rows too — an anonymous one still anchors the thread to a stable identity.
    contact = await upsert_contact(
        session,
        organization_id=agent.organization_id,
        channel="widget",
        external_id=visitor_id,
        profile=_visitor_profile(data.visitor),
    )
    conv = Conversation(
        organization_id=agent.organization_id,
        agent_id=agent.id,
        channel="widget",
        channel_user_id=visitor_id,
        contact_id=contact.id,
        status="active",
        meta=meta,
    )
    session.add(conv)
    await session.flush()
    return conv


async def resolve_turn(
    session: AsyncSession, public_key: str, data: schemas.PublicChatRequest, visitor_id: str
) -> tuple[Agent, AgentVersion, Conversation]:
    """Everything that can legitimately 4xx, done **before** any response has started.

    Both rejections here — unknown agent, and a conversation that is missing or not this
    visitor's — have to reach the client as an ordinary typed error. Resolving them inside the
    streaming generator instead means the status line and headers are already on the wire when
    the error is raised, so Starlette can only abort mid-body: the caller sees a truncated 200
    and the widget's 404 recovery never fires. Caught by
    `test_public_chat_hijack.py::test_the_streaming_path_is_guarded_too`.
    """
    agent, version = await _resolve_agent(session, public_key)
    conv = await _get_or_create_conversation(session, agent, data, visitor_id)
    return agent, version, conv


async def public_chat_events(
    session: AsyncSession,
    resolved: tuple[Agent, AgentVersion, Conversation],
    data: schemas.PublicChatRequest,
) -> AsyncIterator[StreamEvent]:
    agent, version, conv = resolved

    yield StreamEvent(type="conversation", conversation_id=str(conv.id))
    if data.block_reply is not None:
        # Marks the block answered so it reloads read-only. A miss (unknown id, already answered, another
        # thread's id) is ignored: the message below is a normal turn either way, with every guard on it.
        await blocks.resolve_block(session, conv.id, data.block_reply.block_id, data.block_reply.value)
    turn = InboundTurn(session, agent, version, conv, data.message)
    async for ev in turn.events():
        yield ev


async def public_chat_sse(
    session: AsyncSession,
    resolved: tuple[Agent, AgentVersion, Conversation],
    data: schemas.PublicChatRequest,
) -> AsyncIterator[str]:
    async for ev in public_chat_events(session, resolved, data):
        yield f"data: {ev.model_dump_json()}\n\n"


async def public_chat_once(
    session: AsyncSession,
    resolved: tuple[Agent, AgentVersion, Conversation],
    data: schemas.PublicChatRequest,
) -> dict[str, Any]:
    """The non-streaming reply. Must end up with the **guarded** text, not the raw stream.

    L5 runs on the accumulated reply after the provider has finished, so a streaming client has
    already painted the unguarded version and is told to discard it via a `replace` event
    (ADR-049). Summing `token` events alone therefore reproduces exactly what the guard removed
    — unredacted PII, an unrepaired persona break, a replaced prompt leak — while the message
    written to the database is the corrected one, so nothing afterwards shows that it happened.
    `{"stream": false}` is public and unauthenticated; the shipped widget streams, but anything
    built against this endpoint next would have inherited a silent hole.
    """
    content = ""
    replaced: str | None = None
    conversation_id = ""
    citations: list[Any] = []
    shown_blocks: list[dict[str, Any]] = []
    async for ev in public_chat_events(session, resolved, data):
        if ev.type == "conversation" and ev.conversation_id:
            conversation_id = ev.conversation_id
        elif ev.type == "token" and ev.delta:
            content += ev.delta
        elif ev.type == "replace":
            # Supersedes everything accumulated so far — the guard rewrites the whole reply,
            # it never patches a span. `delta` may legitimately be empty.
            replaced = ev.delta or ""
        elif ev.type == "citations" and ev.citations:
            citations = ev.citations
        elif ev.type == "block" and ev.block:
            shown_blocks.append(ev.block)
    final = content if replaced is None else replaced
    out: dict[str, Any] = {"conversation_id": conversation_id, "content": final.strip(), "citations": citations}
    if shown_blocks:  # absent for every agent that has not opted in, so the legacy response is unchanged
        out["blocks"] = shown_blocks
    return out


async def conversation_history(
    session: AsyncSession, public_key: str, conversation_id: uuid.UUID, visitor_id: str
) -> list[schemas.PublicHistoryMessage]:
    """The thread so far, for a widget reloading mid-conversation. Only for agents with interactive blocks on:
    the legacy widget has never restored history and an agent that did not opt in must not start to.

    The same ownership rule as resuming a chat: only the visitor who started the conversation can read it,
    and anyone else gets the same 404 as a conversation that does not exist.
    """
    agent, _version = await _resolve_agent(session, public_key)
    conv = await session.get(Conversation, conversation_id)
    if (
        conv is None
        or conv.agent_id != agent.id
        or conv.organization_id != agent.organization_id
        or conv.channel != "widget"
        or conv.channel_user_id != visitor_id
    ):
        raise AppError("public.conversation_not_found", "Conversation not found.", 404)
    if await blocks.load_flags(session, agent.id) is None:
        return []
    rows = list(
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conv.id,
                    Message.role.in_(("user", "assistant")),
                    Message.content.is_not(None),
                )
                .order_by(Message.created_at.desc())
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    rows.reverse()
    out: list[schemas.PublicHistoryMessage] = []
    for m in rows:
        shown = blocks.public_view(list(m.blocks or [])) if m.role == "assistant" else []
        out.append(
            schemas.PublicHistoryMessage(
                role=m.role,
                text=blocks.strip_fallback(m.content, list(m.blocks or [])) if shown else (m.content or ""),
                blocks=shown,
            )
        )
    return out


def visitor_id_for(data: schemas.PublicChatRequest) -> str:
    if data.visitor and data.visitor.id:
        return str(data.visitor.id)[:255]
    return f"anon-{uuid.uuid4().hex[:16]}"

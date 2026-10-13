"""Create or update a WhatsApp channel from Meta's free test number (no Embedded Signup).

The same row the one-click connect writes (`type=whatsapp`, `external_id` = phone number id,
`external_parent_id` = WABA id, token encrypted with the same key), marked `connection_source="manual"`,
so the shared `/api/meta/webhook` routes the test number's messages through the unchanged inbound path:
guards, RAG, LLM, and a reply sent through the Graph API with this token.

Run through `scripts/add_whatsapp_test_channel.py`. The token never leaves this function except
encrypted into the channel config: it is not logged, printed or returned.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import meta_graph as graph
from app.channels.meta_graph import MetaGraphError
from app.core.crypto import encrypt
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Agent, Channel

log = get_logger("whatsapp_test_channel")

#: Meta's temporary test-number token lasts 24 hours.
TOKEN_LIFETIME = dt.timedelta(hours=24)


async def add_test_channel(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    phone_number_id: str,
    waba_id: str,
    display_name: str,
    token: str,
) -> tuple[Channel, bool]:
    """`(channel, created)`. Running it again for the same number updates the row (new token, agent, name)."""
    if not token.strip():
        raise AppError("whatsapp_test.no_token", "No access token was provided.", 400)
    agent = (
        await session.execute(select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if agent is None:
        raise AppError("whatsapp_test.agent_not_found", f"No agent with id {agent_id}.", 404)

    channel = (
        await session.execute(
            select(Channel).where(Channel.type == "whatsapp", Channel.external_id == phone_number_id)
        )
    ).scalar_one_or_none()
    if channel is not None and channel.organization_id != agent.organization_id:
        raise AppError(
            "whatsapp_test.number_in_use",
            "That phone number is already connected to a different workspace.",
            409,
        )

    try:
        await graph.call("POST", f"/{waba_id}/subscribed_apps", token=token)
    except MetaGraphError as exc:
        # Meta's message can name ids and scopes but never echoes the token.
        raise AppError(
            "whatsapp_test.subscribe_failed",
            f"Meta refused to subscribe the app to the WABA: {exc.message}",
            502,
        ) from exc

    config = {"phone_number_id": phone_number_id, "waba_id": waba_id, "access_token": encrypt(token)}
    created = channel is None
    if channel is None:
        channel = Channel(
            organization_id=agent.organization_id,
            agent_id=agent.id,
            type="whatsapp",
            config=config,
            webhook_secret=secrets.token_urlsafe(24),
        )
        session.add(channel)
    else:
        channel.agent_id = agent.id
        channel.config = {**channel.config, **config}
    channel.name = display_name[:255]
    channel.external_id = phone_number_id
    channel.external_parent_id = waba_id
    channel.connection_source = "manual"
    channel.status = "active"
    channel.enabled = True
    channel.token_expires_at = dt.datetime.now(tz=dt.UTC) + TOKEN_LIFETIME
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise AppError("whatsapp_test.number_in_use", "That phone number is already connected.", 409) from exc
    log.info("whatsapp_test_channel_saved", channel=str(channel.id), created=created, phone_number_id=phone_number_id)
    return channel, created

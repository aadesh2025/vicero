"""The one shared Meta webhook (ADR-113).

Meta allows a single webhook URL per app, so every customer's WhatsApp number, Facebook Page and Instagram
account arrives at `/api/meta/webhook`. This module only *routes*: it splits a delivery into one event per
message and names the channel (type + provider id) each belongs to. Parsing, the bot turn and the reply all
stay in the existing adapters and `service.run_and_send`.
"""

from __future__ import annotations

import uuid
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import service
from app.core.logging import get_logger
from app.models import Channel

log = get_logger("channels.meta_webhook")

_OBJECT_TYPE = {"page": "facebook", "instagram": "instagram"}


class MetaEvent(NamedTuple):
    channel_type: str
    external_id: str
    #: Provider message id, used to drop Meta's retries. None when the event carries none.
    message_id: str | None
    #: A single-event payload shaped exactly like what the per-channel webhooks hand the adapters.
    payload: dict[str, Any]


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def split_events(payload: dict[str, Any]) -> list[MetaEvent]:
    """One event per inbound message, whatever batching Meta applied. Unknown shapes yield nothing."""
    obj = payload.get("object")
    events: list[MetaEvent] = []
    for entry in _dicts(payload.get("entry")):
        if obj == "whatsapp_business_account":
            for change in _dicts(entry.get("changes")):
                if change.get("field") not in (None, "messages"):
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
                if not phone_number_id:
                    continue
                for message in _dicts(value.get("messages")):
                    sub = {
                        "object": obj,
                        "entry": [
                            {
                                "id": entry.get("id"),
                                "changes": [{"field": "messages", "value": {**value, "messages": [message]}}],
                            }
                        ],
                    }
                    events.append(MetaEvent("whatsapp", str(phone_number_id), message.get("id"), sub))
        elif obj in _OBJECT_TYPE:
            for event in _dicts(entry.get("messaging")):
                external_id = entry.get("id") or (event.get("recipient") or {}).get("id")
                if not external_id:
                    continue
                inner = event.get("message")
                mid = inner.get("mid") if isinstance(inner, dict) else None
                sub = {
                    "object": obj,
                    "entry": [{"id": entry.get("id"), "time": entry.get("time"), "messaging": [event]}],
                }
                events.append(MetaEvent(_OBJECT_TYPE[str(obj)], str(external_id), mid, sub))
    return events


async def find_channel(session: AsyncSession, channel_type: str, external_id: str) -> Channel | None:
    """The channel that owns this provider id (unique per type), or None if it is not ours."""
    stmt = select(Channel).where(Channel.type == channel_type, Channel.external_id == external_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def handle_inbound(session: Any, channel_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Run one routed event through the channel's adapter. Runs in the worker (or inline as a fallback)."""
    from app.channels import get_channel  # importing the package registers every adapter

    channel = await session.get(Channel, channel_id)
    adapter = get_channel(channel.type) if channel is not None else None
    if channel is None or adapter is None:
        return
    if channel.status != "active":
        log.info("meta_inbound_ignored_inactive", channel=str(channel.id), status=channel.status)
        return
    await service.run_and_send(session, channel, adapter, payload)

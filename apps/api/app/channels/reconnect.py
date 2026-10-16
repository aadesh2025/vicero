"""A provider that refuses a channel's token flips the channel to `needs_reconnect` (ADR-122).

Before this, an expired WhatsApp token showed up only as a `whatsapp_send_rejected` log line: the bot
generated a reply, Meta refused it, and the dashboard kept saying "Connected". `mark_needs_reconnect`
(ADR-113) already audits the change, emits the `channel.needs_reconnect` webhook event and makes the
Channels tab show the badge; this is the missing trigger for channels whose token dies between the
daily health checks, and for manually connected channels the health check never covers.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Channel

log = get_logger("channels.reconnect")


async def flag_token_rejected(session: AsyncSession, channel: Channel) -> bool:
    """Mark the channel `needs_reconnect` (once). The caller owns the commit."""
    # Imported here: meta_connect pulls in the whole Meta stack, and channels/service imports this module.
    from app.channels.meta_connect import mark_needs_reconnect

    changed = await mark_needs_reconnect(session, channel, "token_rejected")
    if changed:
        log.warning(
            "channel_needs_reconnect",
            channel=str(channel.id),
            channel_type=channel.type,
            reason="token_rejected",
            fix="generate a new access token and save it on the channel (Channels tab), or reconnect with Meta",
        )
    return changed

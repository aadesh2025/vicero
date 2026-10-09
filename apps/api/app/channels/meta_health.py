"""Daily token health for one-click Meta channels (ADR-113).

Asks Meta whether each stored token is still valid. A channel that has lost its connection flips to
`needs_reconnect` exactly once (`mark_needs_reconnect` only acts on `active`), which also emits the
`channel.needs_reconnect` webhook event and an audit row. A Meta outage never flips anything: transient
errors skip the channel until tomorrow.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel
from app.channels import meta_graph as graph
from app.channels.meta_connect import _token_expiry, mark_needs_reconnect, meta_enabled
from app.channels.meta_graph import MetaGraphError
from app.core.logging import get_logger
from app.models import Channel

log = get_logger("channels.meta_health")


async def run_health_check(session: AsyncSession) -> dict[str, int]:
    result = {"checked": 0, "needs_reconnect": 0, "skipped": 0}
    if not meta_enabled():
        return result
    stmt = select(Channel).where(Channel.connection_source == "meta_oauth", Channel.status == "active")
    channels = (await session.execute(stmt)).scalars().all()
    now = dt.datetime.now(tz=dt.UTC)
    for channel in channels:
        adapter = get_channel(channel.type)
        field = "access_token" if channel.type == "whatsapp" else "page_access_token"
        token = adapter.secret(channel, field) if adapter else None
        if not token:
            result["needs_reconnect"] += await mark_needs_reconnect(session, channel, "token_missing")
            continue
        try:
            info: dict[str, Any] = await graph.debug_token(token)
        except MetaGraphError as exc:
            log.warning("meta_health_skipped", channel=str(channel.id), status=exc.status, meta_code=exc.code)
            result["skipped"] += 1
            continue
        result["checked"] += 1
        channel.last_health_check_at = now
        channel.token_expires_at = _token_expiry(("at", info.get("expires_at")))
        expired = channel.token_expires_at is not None and channel.token_expires_at <= now
        if info.get("is_valid") is False or expired:
            result["needs_reconnect"] += await mark_needs_reconnect(session, channel, "token_invalid")
    return result

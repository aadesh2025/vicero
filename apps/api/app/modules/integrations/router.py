"""Integrations routes (ADR-121).

* `/v1/integrations/google-calendar`  tenant: status, `connect` (returns the Google URL), disconnect.
* `/api/google/callback`   Google's redirect back. No bearer token (a browser redirect), so the signed `state`
                           carries the organisation and user, and is re-checked.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rbac
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limit
from app.db.session import get_session
from app.modules.integrations import google_calendar as gcal
from app.modules.orgs.deps import OrgContext, current_org

log = get_logger("integrations")

router = APIRouter(prefix="/v1/integrations/google-calendar", tags=["integrations"])
callback_router = APIRouter(tags=["integrations"], include_in_schema=False)


@router.get("")
async def status(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> dict[str, Any]:
    rbac.require_permission(ctx.role, rbac.READ)
    return gcal.status_out(await gcal.get_connection(session, ctx.org.id))


@router.get("/connect")
async def connect(
    agent_id: uuid.UUID | None = Query(default=None, description="Where to return to in the dashboard."),
    ctx: OrgContext = Depends(current_org),
) -> dict[str, str]:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    return {"url": gcal.authorize_url(ctx.org.id, ctx.user.id, agent_id)}


@router.delete("", status_code=204)
async def disconnect(session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    await gcal.disconnect(session, ctx.org.id)


def _dashboard(agent_id: uuid.UUID | None, **params: str) -> RedirectResponse:
    base = settings.web_base_url.rstrip("/")
    path = f"/agents/{agent_id}" if agent_id else "/agents"
    return RedirectResponse(f"{base}{path}?{urlencode({'tab': 'tools', **params})}", status_code=303)


@callback_router.get("/api/google/callback", dependencies=[Depends(rate_limit("google_callback", limit=30, window=60))])
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Google sends the browser here. Whatever happens, the user lands back in the dashboard with a plain result."""
    agent_id: uuid.UUID | None = None
    try:
        raw = gcal.read_state(state or "").get("a")
        agent_id = uuid.UUID(raw) if raw else None
    except (AppError, ValueError, TypeError):
        agent_id = None
    if error or not code or not state:
        # The person pressed "Cancel" (error=access_denied), or the request is malformed.
        return _dashboard(agent_id, google="cancelled" if error == "access_denied" else "error")
    try:
        returned = await gcal.complete_connection(session, code=code, state=state)
    except AppError as exc:
        log.warning("google_calendar_connect_failed", code=exc.code)
        return _dashboard(agent_id, google="error", reason=exc.code)
    return _dashboard(returned or agent_id, google="connected")

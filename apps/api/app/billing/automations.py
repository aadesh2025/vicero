"""The automation registry rules the tool path needs, and the monthly run count.

Lives in `app.billing` (not `app.modules.automations`) so `app.tools` can enforce them without importing the
automations package, which depends on the admin module, which depends on tools.
"""

from __future__ import annotations

import datetime as dt
import uuid
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.core.errors import AppError
from app.models import Automation, AutomationRun, Organization


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def month_start(now: dt.datetime | None = None) -> dt.datetime:
    now = now or _now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def runs_this_month(session: AsyncSession, org_id: uuid.UUID, now: dt.datetime | None = None) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(AutomationRun)
                .where(AutomationRun.organization_id == org_id, AutomationRun.started_at >= month_start(now))
            )
        ).scalar_one()
    )


# ── agent path: may this tool call run? ───────────────────────────────────────────
RUN_LIMIT_MESSAGE = (
    "This automation has reached the monthly run limit of the current plan. It will work again next month, "
    "or you can upgrade your plan."
)


async def registered_for_agent(
    session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID | None, webhook_url: str | None
) -> Automation | None:
    """The ACTIVE automation of this org (and agent) that serves `webhook_url`'s /webhook/<path>, or None."""
    path = urlparse(str(webhook_url or "")).path.rstrip("/")
    if not path.startswith("/webhook/"):
        return None
    automation = (
        await session.execute(
            select(Automation).where(
                Automation.organization_id == org_id,
                Automation.webhook_path == path,
                Automation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if automation is None:
        return None
    if automation.agent_id is not None and automation.agent_id != agent_id:
        return None
    return automation


async def check_agent_call(
    session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID, webhook_url: str | None
) -> str | None:
    """None if the call may go ahead, else a plain sentence for the agent/user. Enforces both the registry
    (same org, same agent, Active) and the monthly run cap."""
    automation = await registered_for_agent(session, org_id, agent_id, webhook_url)
    if automation is None:
        return "This automation is not available."
    org = await session.get(Organization, org_id)
    if org is not None:
        cap = (await usage.load_entitlements(session, org)).spec.max_automation_runs
        if cap is not None and await runs_this_month(session, org_id) >= cap:
            return RUN_LIMIT_MESSAGE
    return None


async def require_registered_for_bind(
    session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID | None, webhook_url: str
) -> None:
    if await registered_for_agent(session, org_id, agent_id, webhook_url) is None:
        # Same words as a workflow the org was never shown: nothing reveals whether it exists elsewhere.
        raise AppError("tools.n8n_forbidden", "This workflow is not available to your organization.", 403)

"""Free-trial lifecycle emails (docs/18 §8) — run hourly from Celery beat.

Five notices: trial day 7 (3 days left), day 9 (1 day left), trial ended, 80% of messages used,
100% used. Each goes out **at most once per workspace**.

Why at-most-once, and how
    Sending is claimed *before* it happens, with one atomic statement
    (`UPDATE … SET emails_sent = emails_sent || {kind: now} WHERE NOT emails_sent ? kind
    RETURNING …`). Two sweeps racing for the same workspace cannot both win it, and a crash
    between claim and send loses one notice rather than sending it twice. For a nudge email that
    is the right trade; the dashboard banner is the durable signal.

A sweep that first meets a workspace late (e.g. the beat process was down for a day) sends only
the most urgent notice that is due and quietly marks the milder ones done, so nobody gets "3 days
left" and "1 day left" in the same minute.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.email import EmailMessage, get_email_backend
from app.core.email_templates import trial_notice_email
from app.core.logging import get_logger
from app.core.plans import MESSAGES_PER_EXCHANGE, Entitlements, get_entitlements
from app.models import Membership, Organization, OrgMessageUsage, User

log = get_logger("worker.trial")

# When the two countdown notices fire, in whole days remaining. A notice schedule, not a plan
# limit — the trial's length itself comes from `core.plans`.
_COUNTDOWN_DAYS = {"trial_day7": 3, "trial_day9": 1}
_WARN_FRACTION = 0.8


def _due(ent: Entitlements, now: dt.datetime) -> list[str]:
    """Notices whose condition is currently true, most urgent first."""
    due: list[str] = []
    limit = ent.meter_limit
    if ent.is_expired and ent.expired_reason == "time":
        due.append("trial_ended")
    if limit:
        if ent.messages_used >= limit or (limit - ent.messages_used) < MESSAGES_PER_EXCHANGE:
            due.append("messages_100")
        elif ent.messages_used >= limit * _WARN_FRACTION:
            due.append("messages_80")
    if not ent.is_expired:
        left = ent.days_left(now)
        if left is not None:
            for kind in ("trial_day9", "trial_day7"):  # most urgent first
                if left <= _COUNTDOWN_DAYS[kind]:
                    due.append(kind)
    return due


async def _claim(session: AsyncSession, org_id: uuid.UUID, kind: str, now: dt.datetime) -> bool:
    row = await session.execute(
        text(
            "UPDATE org_message_usage SET emails_sent = emails_sent || "
            "jsonb_build_object(CAST(:kind AS text), CAST(:at AS text)) "
            "WHERE organization_id = :org AND NOT (emails_sent ? CAST(:kind AS text)) "
            "RETURNING organization_id"
        ),
        {"kind": kind, "at": now.isoformat(), "org": org_id},
    )
    return row.first() is not None


async def _owner_emails(session: AsyncSession, org_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == org_id,
            Membership.role == "owner",
            Membership.status == "active",
        )
    )
    return list(rows.scalars().all())


async def sweep(session: AsyncSession, *, now: dt.datetime | None = None) -> dict[str, int]:
    """Send every due notice once. Returns counts per kind. The caller commits."""
    now = now or dt.datetime.now(tz=dt.UTC)
    sent: dict[str, int] = {}
    orgs = (
        await session.execute(
            select(Organization).where(
                Organization.trial_ends_at.is_not(None), Organization.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    for org in orgs:
        usage_row = await session.get(OrgMessageUsage, org.id, populate_existing=True)
        used = usage_row.messages_used if usage_row else 0
        unanswered = usage_row.unanswered_messages if usage_row else 0
        ent = get_entitlements(org.plan, trial_ends_at=org.trial_ends_at, messages_used=used, now=now)
        if not ent.is_metered:
            continue
        already = set((usage_row.emails_sent if usage_row else None) or {})
        todo = [k for k in _due(ent, now) if k not in already]
        if not todo:
            continue
        # Most urgent first: send only that one, mark the milder ones done without sending.
        primary = todo[0]
        for kind in todo:
            if not await _claim(session, org.id, kind, now) or kind != primary:
                continue
            await _deliver(session, org, kind, ent, now, unanswered)
            sent[kind] = sent.get(kind, 0) + 1
    return sent


async def _deliver(
    session: AsyncSession,
    org: Organization,
    kind: str,
    ent: Entitlements,
    now: dt.datetime,
    unanswered: int,
) -> None:
    if kind == "trial_ended":
        await write_audit(
            session, org.id, None, "plan.trial_expired", target_type="org", target_id=str(org.id),
            meta={"reason": ent.expired_reason},
        )
    subject, body, html = trial_notice_email(
        kind,
        org_name=org.name,
        link=f"{settings.web_base_url}/billing/upgrade",
        days_left=ent.days_left(now) or 0,
        used=ent.messages_used,
        limit=ent.meter_limit or 0,
        unanswered=unanswered,
    )
    for to in await _owner_emails(session, org.id):
        try:
            await get_email_backend().send(
                EmailMessage(to=to, subject=subject, body=body, html_body=html)
            )
        except Exception:  # one bad mailbox must not stop the sweep
            log.exception("trial_email_failed", org_id=str(org.id), kind=kind)

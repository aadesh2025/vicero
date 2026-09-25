"""Trial lifecycle emails (docs/18 §8, §13): each is sent once, and only when it is due."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import get_email_backend
from app.core.email_templates import trial_notice_email
from app.models import AuditLog, Organization, OrgMessageUsage
from app.worker.trial import sweep
from tests.selfserve_helpers import signup, trial_org, unique_email

pytestmark = pytest.mark.usefixtures("self_serve")


async def _start(client: AsyncClient, db: AsyncSession) -> tuple[Organization, dt.datetime]:
    auth = await signup(client, unique_email("mail"))
    org = (
        await db.execute(select(Organization).where(Organization.created_by == uuid.UUID(auth["user"]["id"])))
    ).scalar_one()
    assert org.trial_started_at is not None
    get_email_backend().outbox.clear()  # drop the signup verification email
    return org, org.trial_started_at


def _subjects() -> list[str]:
    return [m.subject for m in get_email_backend().outbox]


async def test_a_fresh_trial_sends_nothing(client: AsyncClient, db_session: AsyncSession) -> None:
    _, start = await _start(client, db_session)
    assert await sweep(db_session, now=start + dt.timedelta(hours=1)) == {}
    assert _subjects() == []


async def test_the_countdown_emails_go_out_once_each(client: AsyncClient, db_session: AsyncSession) -> None:
    _, start = await _start(client, db_session)

    assert await sweep(db_session, now=start + dt.timedelta(days=6, hours=23)) == {}
    assert await sweep(db_session, now=start + dt.timedelta(days=7, hours=1)) == {"trial_day7": 1}
    assert len(_subjects()) == 1 and "3 days" in _subjects()[0]

    # Re-running (the sweep is hourly) must not send it again.
    assert await sweep(db_session, now=start + dt.timedelta(days=7, hours=2)) == {}
    assert await sweep(db_session, now=start + dt.timedelta(days=8)) == {}

    assert await sweep(db_session, now=start + dt.timedelta(days=9, hours=1)) == {"trial_day9": 1}
    assert await sweep(db_session, now=start + dt.timedelta(days=9, hours=2)) == {}
    assert len(_subjects()) == 2


async def test_a_sweep_that_meets_the_trial_late_sends_only_the_most_urgent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, start = await _start(client, db_session)  # e.g. the beat process was down for days
    assert await sweep(db_session, now=start + dt.timedelta(days=9, hours=12)) == {"trial_day9": 1}
    assert len(_subjects()) == 1
    # The milder notice was marked done rather than sent late.
    assert await sweep(db_session, now=start + dt.timedelta(days=9, hours=13)) == {}


async def test_the_trial_ended_email_is_sent_once_and_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, start = await _start(client, db_session)
    await db_session.execute(
        update(OrgMessageUsage).where(OrgMessageUsage.organization_id == org.id).values(unanswered_messages=3)
    )
    await db_session.flush()

    over = start + dt.timedelta(days=10, minutes=5)
    assert await sweep(db_session, now=over) == {"trial_ended": 1}
    assert await sweep(db_session, now=over + dt.timedelta(hours=1)) == {}

    (mail,) = get_email_backend().outbox
    assert "ended" in mail.subject.lower()
    assert "3 visitor messages" in mail.body
    assert mail.html_body and "Upgrade" in mail.html_body
    audits = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.organization_id == org.id, AuditLog.action == "plan.trial_expired")
        )
    ).scalars().all()
    assert len(audits) == 1


async def test_eighty_and_one_hundred_percent_of_messages(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, start = await _start(client, db_session)
    now = start + dt.timedelta(hours=2)

    async def set_used(n: int) -> None:
        await db_session.execute(
            update(OrgMessageUsage).where(OrgMessageUsage.organization_id == org.id).values(messages_used=n)
        )
        await db_session.flush()

    await set_used(399)
    assert await sweep(db_session, now=now) == {}
    await set_used(400)
    assert await sweep(db_session, now=now) == {"messages_80": 1}
    await set_used(450)
    assert await sweep(db_session, now=now) == {}  # still only the one 80% email
    await set_used(500)
    assert await sweep(db_session, now=now) == {"messages_100": 1}
    assert await sweep(db_session, now=now) == {}
    assert len(_subjects()) == 2
    assert "80%" in _subjects()[0] and "all" in _subjects()[1].lower()


async def test_legacy_orgs_get_no_trial_emails(client: AsyncClient, db_session: AsyncSession) -> None:
    org, start = await _start(client, db_session)
    org.plan = "legacy"
    await db_session.flush()
    assert await sweep(db_session, now=start + dt.timedelta(days=30)) == {}
    assert _subjects() == []


async def test_the_email_goes_to_the_workspace_owner(client: AsyncClient, db_session: AsyncSession) -> None:
    auth, _, org_id = await trial_org(client)
    org = await db_session.get(Organization, uuid.UUID(org_id))
    assert org is not None and org.trial_started_at is not None
    get_email_backend().outbox.clear()
    await sweep(db_session, now=org.trial_started_at + dt.timedelta(days=7, hours=1))
    assert [m.to for m in get_email_backend().outbox] == [auth["user"]["email"]]


def test_every_notice_names_the_workspace_and_links_to_upgrade() -> None:
    for kind in ("trial_day7", "trial_day9", "trial_ended", "messages_80", "messages_100"):
        subject, text, html = trial_notice_email(
            kind, org_name="Acme <b>", link="https://app.test/billing/upgrade", days_left=3, used=400, limit=500
        )
        assert subject and "https://app.test/billing/upgrade" in text
        assert "&lt;b&gt;" in html and "<b>" not in html  # the workspace name is escaped in HTML


def test_an_unknown_notice_is_a_bug() -> None:
    with pytest.raises(ValueError):
        trial_notice_email("nope", org_name="x", link="y")


def test_the_sweep_is_scheduled_hourly() -> None:
    from app.worker.celery_app import celery_app

    job = celery_app.conf.beat_schedule["trial-lifecycle-sweep"]
    assert job["task"] == "trial.sweep" and job["schedule"] == 3600.0

"""Throwaway-email detection: a maintained list plus a soft MX signal (ADR-091)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AuditLog
from app.modules.auth import disposable
from tests.selfserve_helpers import STRONG, signup, unique_email

SAMPLE = "# comment\nfreshthrowaway.example\nSub.Burner-Mail.example  # trailing comment\n\nnot a domain\n"


async def _signup_audit(db: AsyncSession, auth: dict) -> AuditLog:  # type: ignore[type-arg]
    """This test's own signup row — the dev database also holds real ones."""
    stmt = select(AuditLog).where(
        AuditLog.action == "auth.signup", AuditLog.actor_user_id == uuid.UUID(auth["user"]["id"])
    )
    return (await db.execute(stmt)).scalar_one()


def _many(n: int = 1200) -> str:
    return "\n".join(f"burner{i}.example" for i in range(n)) + "\nrotatingmail.example\n"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disposable, "_cache", frozenset())
    monkeypatch.setattr(disposable, "_cache_loaded_at", -1e9)

    async def no_redis() -> None:
        return None

    monkeypatch.setattr(disposable, "_redis", no_redis)  # memory only: no shared state between runs


# ── parsing and matching ─────────────────────────────────────────────────────
def test_parse_keeps_domains_and_drops_comments_blanks_and_junk() -> None:
    assert disposable.parse_list(SAMPLE) == {"freshthrowaway.example", "sub.burner-mail.example"}


def test_a_subdomain_of_a_listed_domain_counts() -> None:
    assert disposable._suffixes("a.b.mailinator.com") == ["a.b.mailinator.com", "b.mailinator.com", "mailinator.com"]


async def test_the_offline_seed_still_works_with_no_refreshed_list() -> None:
    assert await disposable.is_listed("mailinator.com")
    assert await disposable.is_listed("x.yopmail.com")
    assert not await disposable.is_listed("example.org")


async def test_a_refresh_adds_domains_the_seed_has_never_heard_of() -> None:
    assert not await disposable.is_listed("rotatingmail.example")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=_many()))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await disposable.refresh(client=client) == 1201
    assert await disposable.is_listed("rotatingmail.example")
    assert await disposable.is_listed("mail.rotatingmail.example")  # a subdomain of it
    assert await disposable.is_listed("mailinator.com")  # the seed is kept alongside


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="oops"),
        httpx.Response(200, text="<html>rate limited</html>"),  # a real page, not a list
        httpx.Response(200, text="only.three\ndomains.here\nnot.enough\n"),  # truncated
    ],
)
async def test_a_bad_download_is_refused_and_leaves_the_previous_list_alone(response: httpx.Response) -> None:
    good = httpx.MockTransport(lambda req: httpx.Response(200, text=_many()))
    async with httpx.AsyncClient(transport=good) as client:
        await disposable.refresh(client=client)

    bad = httpx.MockTransport(lambda req: response)
    async with httpx.AsyncClient(transport=bad) as client:
        with pytest.raises((httpx.HTTPStatusError, ValueError)):
            await disposable.refresh(client=client)
    assert await disposable.is_listed("rotatingmail.example")  # still there


# ── signup: the list blocks, the MX signal only records ──────────────────────
@pytest.mark.usefixtures("self_serve")
async def test_a_domain_from_the_refreshed_list_is_refused_at_signup(client: AsyncClient) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=_many()))
    async with httpx.AsyncClient(transport=transport) as c:
        await disposable.refresh(client=c)
    resp = await client.post("/v1/auth/signup", json={"email": "someone@rotatingmail.example", "password": STRONG})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.email_not_allowed"


@pytest.mark.usefixtures("self_serve")
async def test_a_failed_feed_never_blocks_a_normal_signup(client: AsyncClient) -> None:
    """Fail open: with nothing but the seed, an ordinary address signs up."""
    resp = await client.post("/v1/auth/signup", json={"email": unique_email("ok"), "password": STRONG})
    assert resp.status_code == 200


@pytest.mark.usefixtures("self_serve")
@pytest.mark.parametrize("signal", ["no_mail_server", "mx_on_listed_domain"])
async def test_the_mx_signal_is_recorded_on_the_signup_and_never_blocks_it(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, signal: str
) -> None:
    async def fake(_domain: str) -> str:
        return signal

    monkeypatch.setattr(disposable, "mx_signal", fake)
    resp = await signup(client)
    assert resp["user"]["email"]  # the signup went through
    assert (await _signup_audit(db_session, resp)).meta.get("email_risk") == signal


@pytest.mark.usefixtures("self_serve")
async def test_no_signal_leaves_the_audit_row_clean(client: AsyncClient, db_session: AsyncSession) -> None:
    resp = await signup(client)
    assert "email_risk" not in (await _signup_audit(db_session, resp)).meta


# ── the MX heuristic itself (stubbed DNS) ────────────────────────────────────
@pytest.fixture
def mx_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "disposable_mx_check_enabled", True)


async def test_a_domain_with_no_mail_servers_is_flagged(mx_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disposable, "_resolve_mx", lambda d, t: ("no_domain", []))
    assert await disposable.mx_signal("nonexistent.example") == "no_mail_server"


async def test_mail_servers_on_a_listed_throwaway_service_are_flagged(
    mx_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disposable, "_resolve_mx", lambda d, t: ("ok", ["mail.mailinator.com"]))
    assert await disposable.mx_signal("rotating-domain.example") == "mx_on_listed_domain"


async def test_ordinary_mail_servers_are_not_flagged(mx_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disposable, "_resolve_mx", lambda d, t: ("ok", ["aspmx.l.google.com"]))
    assert await disposable.mx_signal("company.example") is None


async def test_a_dns_failure_is_not_a_signal(mx_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disposable, "_resolve_mx", lambda d, t: ("unknown", []))
    assert await disposable.mx_signal("company.example") is None

    def boom(d: str, t: float) -> tuple[str, list[str]]:
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(disposable, "_resolve_mx", boom)
    assert await disposable.mx_signal("company.example") is None


async def test_the_heuristic_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "disposable_mx_check_enabled", False)
    monkeypatch.setattr(disposable, "_resolve_mx", lambda d, t: ("no_domain", []))
    assert await disposable.mx_signal("nonexistent.example") is None


def test_the_refresh_is_scheduled_weekly() -> None:
    from app.worker.celery_app import celery_app

    job = celery_app.conf.beat_schedule["disposable-list-refresh"]
    assert job["task"] == "disposable.refresh" and job["schedule"] == 7 * 86400.0

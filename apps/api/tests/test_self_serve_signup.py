"""Self-serve signup: every method provisions exactly one trial workspace (docs/18 §3, §13).

Runs with `SELF_SERVE_ENABLED` on and the test-only org bootstrap off — production's behaviour.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import get_email_backend
from app.models import Membership, OAuthAccount, Organization, OrgMessageUsage, User
from app.modules.auth import oauth
from app.modules.auth.oauth import OAuthUser
from tests.selfserve_helpers import (
    STRONG,
    bearer,
    org_headers,
    owned_orgs,
    signup,
    unique_email,
    users_named,
    verify_email,
)

pytestmark = pytest.mark.usefixtures("self_serve")


async def _assert_one_trial_workspace(db: AsyncSession, email: str) -> Organization:
    (user,) = await users_named(db, email)
    orgs = await owned_orgs(db, user.id)
    assert len(orgs) == 1, [o.name for o in orgs]
    org = orgs[0]
    assert org.plan == "trial"
    assert org.trial_started_at is not None and org.trial_ends_at is not None
    assert org.trial_ends_at - org.trial_started_at == dt.timedelta(days=10)
    assert abs(org.trial_started_at - dt.datetime.now(tz=dt.UTC)) < dt.timedelta(minutes=2)
    usage = await db.get(OrgMessageUsage, org.id)
    assert usage is not None and usage.messages_used == 0
    assert user.is_staff is False
    return org


# ── password ─────────────────────────────────────────────────────────────────
async def test_password_signup_creates_one_user_one_workspace_and_a_trial(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    email = unique_email("pw")
    auth = await signup(client, email)
    await _assert_one_trial_workspace(db_session, email)

    org_id = (await org_headers(client, auth))["X-Org-Id"]
    plan = (await client.get(f"/v1/orgs/{org_id}/plan", headers=bearer(auth))).json()
    assert plan["status"] == "trial"
    assert plan["messages_limit"] == 500 and plan["messages_used"] == 0
    assert plan["days_left"] == 10
    assert plan["can_create_agent"] is True
    assert plan["features"] == {"workflows": False, "n8n": False, "tool_calling": False}


async def test_the_owner_membership_is_owner_and_only_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    auth = await signup(client)
    rows = (
        await db_session.execute(
            select(Membership).where(Membership.user_id == uuid.UUID(auth["user"]["id"]))
        )
    ).scalars().all()
    assert [m.role for m in rows] == ["owner"]


async def test_self_signup_can_never_produce_staff(client: AsyncClient, db_session: AsyncSession) -> None:
    resp = await client.post(
        "/v1/auth/signup",
        json={"email": unique_email("st"), "password": STRONG, "is_staff": True, "role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["is_staff"] is False
    user = await db_session.get(User, uuid.UUID(resp.json()["user"]["id"]))
    assert user is not None and user.is_staff is False


async def test_a_second_signup_of_the_same_email_is_refused_and_creates_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    email = unique_email("dup")
    await signup(client, email)
    again = await client.post("/v1/auth/signup", json={"email": email, "password": STRONG})
    assert again.status_code == 409
    before = (await db_session.execute(select(func.count()).select_from(Organization))).scalar_one()
    await client.post("/v1/auth/signup", json={"email": email, "password": STRONG})
    after = (await db_session.execute(select(func.count()).select_from(Organization))).scalar_one()
    assert before == after
    await _assert_one_trial_workspace(db_session, email)


# ── magic link ───────────────────────────────────────────────────────────────
async def test_magic_link_provisions_at_first_verified_sign_in_not_at_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    email = unique_email("magic")
    assert (await client.post("/v1/auth/magic-link", json={"email": email})).status_code == 200
    # Requesting a link proves nothing about the address, so no workspace yet.
    (user,) = await users_named(db_session, email)
    assert await owned_orgs(db_session, user.id) == []

    token = re.search(r"Token: (\S+)", get_email_backend().outbox[-1].body)
    assert token
    verify = await client.post("/v1/auth/magic-link/verify", json={"token": token.group(1)})
    assert verify.status_code == 200, verify.text
    assert verify.json()["user"]["email_verified"] is True
    await _assert_one_trial_workspace(db_session, email)

    # A second sign-in by link must not mint a second workspace.
    await client.post("/v1/auth/magic-link", json={"email": email})
    token2 = re.search(r"Token: (\S+)", get_email_backend().outbox[-1].body)
    assert token2
    assert (await client.post("/v1/auth/magic-link/verify", json={"token": token2.group(1)})).status_code == 200
    await _assert_one_trial_workspace(db_session, email)


# ── OAuth ────────────────────────────────────────────────────────────────────
def _profile(provider: str, pid: str, email: str | None, *, verified: bool = True) -> OAuthUser:
    return OAuthUser(
        provider=provider,
        provider_account_id=pid,
        email=email,
        full_name="OAuth Person",
        avatar_url=None,
        access_token="tok",
        refresh_token=None,
        expires_at=None,
        email_verified=verified,
    )


async def _authorize_state(client: AsyncClient, provider: str) -> str:
    authorize = await client.get(f"/v1/auth/oauth/{provider}/authorize")
    return parse_qs(urlparse(authorize.json()["authorize_url"]).query)["state"][0]


async def _oauth_callback(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, provider: str, profile: OAuthUser
) -> dict[str, object]:
    monkeypatch.setattr(settings, f"{provider}_client_id", "cid")
    monkeypatch.setattr(settings, f"{provider}_client_secret", "secret")

    async def fake_fetch(_p: str, _code: str, _verifier: str) -> OAuthUser:
        return profile

    monkeypatch.setattr(oauth, "fetch_oauth_user", fake_fetch)
    authorize = await client.get(f"/v1/auth/oauth/{provider}/authorize")
    assert authorize.status_code == 200, authorize.text
    state = parse_qs(urlparse(authorize.json()["authorize_url"]).query)["state"][0]
    callback = await client.get(f"/v1/auth/oauth/{provider}/callback?code=abc&state={state}")
    assert callback.status_code == 200, callback.text
    return callback.json()  # type: ignore[no-any-return]


async def test_google_signup_creates_one_verified_user_and_workspace(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = unique_email("g")
    body = await _oauth_callback(client, monkeypatch, "google", _profile("google", "g-1", email))
    assert body["user"]["email_verified"] is True  # type: ignore[index]
    await _assert_one_trial_workspace(db_session, email)
    # Signing in again links nothing new and creates no second workspace.
    await _oauth_callback(client, monkeypatch, "google", _profile("google", "g-1", email))
    await _assert_one_trial_workspace(db_session, email)
    linked = select(OAuthAccount).where(OAuthAccount.provider_account_id == "g-1")
    assert len((await db_session.execute(linked)).scalars().all()) == 1


async def test_google_email_the_provider_does_not_vouch_for_creates_nothing(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = unique_email("gu")
    body = await _oauth_callback(
        client, monkeypatch, "google", _profile("google", "g-unv", email, verified=False)
    )
    assert body["needs_email"] is True
    assert await users_named(db_session, email) == []


async def test_facebook_without_an_email_asks_for_one_and_verifies_it_before_anything_is_created(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile("facebook", "fb-77", None, verified=False)
    pending = await _oauth_callback(client, monkeypatch, "facebook", profile)
    assert pending["needs_email"] is True and pending["provider"] == "facebook"
    token = str(pending["pending_token"])

    email = unique_email("fb")
    ask = await client.post("/v1/auth/oauth/email", json={"pending_token": token, "email": email})
    assert ask.status_code == 200, ask.text
    # Nothing exists until the mailbox is proven.
    assert await users_named(db_session, email) == []

    link = re.search(r"Sign in: \S+token=(\S+)", get_email_backend().outbox[-1].body)
    assert link, get_email_backend().outbox[-1].body
    done = await client.post("/v1/auth/oauth/email/verify", json={"token": link.group(1)})
    assert done.status_code == 200, done.text
    assert done.json()["user"]["email_verified"] is True
    await _assert_one_trial_workspace(db_session, email)

    # The link is single-use.
    replay = await client.post("/v1/auth/oauth/email/verify", json={"token": link.group(1)})
    assert replay.status_code == 400


async def test_a_forged_pending_token_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/oauth/email", json={"pending_token": "not-a-token", "email": unique_email()}
    )
    assert resp.status_code == 400


# ── account linking ──────────────────────────────────────────────────────────
async def test_google_then_password_is_one_user(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = unique_email("link")
    await _oauth_callback(client, monkeypatch, "google", _profile("google", "g-link", email))
    second = await client.post("/v1/auth/signup", json={"email": email, "password": STRONG})
    assert second.status_code == 409  # same person, same account — sign in / reset instead
    assert len(await users_named(db_session, email)) == 1
    await _assert_one_trial_workspace(db_session, email)


async def test_password_then_google_links_only_after_the_email_is_verified(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = unique_email("link2")
    await signup(client, email)  # password signup, email NOT verified yet

    # Unverified local account: the OAuth sign-in must NOT be attached to it.
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")

    async def fake_fetch(_p: str, _c: str, _v: str) -> OAuthUser:
        return _profile("google", "g-link2", email)

    monkeypatch.setattr(oauth, "fetch_oauth_user", fake_fetch)
    state = await _authorize_state(client, "google")
    refused = await client.get(f"/v1/auth/oauth/google/callback?code=abc&state={state}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "auth.oauth_email_unverified"
    none_linked = select(func.count()).select_from(OAuthAccount).where(
        OAuthAccount.provider_account_id == "g-link2"
    )
    assert (await db_session.execute(none_linked)).scalar_one() == 0

    # Once the owner has verified the address, the same sign-in links to the same user.
    await verify_email(client)
    state2 = await _authorize_state(client, "google")
    ok = await client.get(f"/v1/auth/oauth/google/callback?code=abc&state={state2}")
    assert ok.status_code == 200, ok.text
    assert len(await users_named(db_session, email)) == 1
    await _assert_one_trial_workspace(db_session, email)


async def test_the_web_flow_redirects_with_a_one_time_code_and_no_tokens_in_the_url(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    email = unique_email("web")

    async def fake_fetch(_p: str, _c: str, _v: str) -> OAuthUser:
        return _profile("google", "g-web", email)

    monkeypatch.setattr(oauth, "fetch_oauth_user", fake_fetch)
    url = (await client.get("/v1/auth/oauth/google/authorize?redirect=web")).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    cb = await client.get(f"/v1/auth/oauth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert cb.status_code == 302
    location = cb.headers["location"]
    assert location.startswith(f"{settings.web_base_url}/oauth/callback?code=")
    assert "access_token" not in location and "refresh_token" not in location

    code = parse_qs(urlparse(location).query)["code"][0]
    exchanged = await client.post("/v1/auth/oauth/exchange", json={"code": code})
    assert exchanged.status_code == 200 and exchanged.json()["user"]["email"] == email
    replay = await client.post("/v1/auth/oauth/exchange", json={"code": code})
    assert replay.status_code == 400  # single use


async def test_a_cancelled_web_login_returns_to_the_login_page(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    url = (await client.get("/v1/auth/oauth/google/authorize?redirect=web")).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    cb = await client.get(f"/v1/auth/oauth/google/callback?error=access_denied&state={state}", follow_redirects=False)
    assert cb.status_code == 302
    assert cb.headers["location"].startswith(f"{settings.web_base_url}/login?error=")


# ── the workspace rule ───────────────────────────────────────────────────────
async def test_a_second_workspace_is_a_402_plan_limit(client: AsyncClient) -> None:
    auth = await signup(client)
    resp = await client.post("/v1/orgs", json={"name": "Another"}, headers=bearer(auth))
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "plan_limit"
    assert resp.json()["error"]["details"]["feature"] == "workspaces"


async def test_an_invited_teammate_does_not_get_a_stray_trial_workspace(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup(client)
    headers = await org_headers(client, owner)
    invitee = unique_email("inv")
    inv = await client.post(
        f"/v1/orgs/{headers['X-Org-Id']}/invitations",
        json={"email": invitee, "role": "editor"},
        headers=bearer(owner),
    )
    assert inv.status_code == 201, inv.text
    await signup(client, invitee)
    (user,) = await users_named(db_session, invitee)
    assert await owned_orgs(db_session, user.id) == []


async def test_a_user_with_no_workspace_can_create_their_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The lapsed-invitation case: no workspace yet, so the first create is allowed."""
    owner = await signup(client)
    headers = await org_headers(client, owner)
    invitee = unique_email("late")
    await client.post(
        f"/v1/orgs/{headers['X-Org-Id']}/invitations",
        json={"email": invitee, "role": "editor"},
        headers=bearer(owner),
    )
    auth = await signup(client, invitee)
    made = await client.post("/v1/orgs", json={"name": "Mine"}, headers=bearer(auth))
    assert made.status_code == 201, made.text
    (user,) = await users_named(db_session, invitee)
    assert len(await owned_orgs(db_session, user.id)) == 1
    again = await client.post("/v1/orgs", json={"name": "Mine 2"}, headers=bearer(auth))
    assert again.status_code == 402

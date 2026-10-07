"""Phase 2: end-to-end auth flows against the real DB (transaction-rolled-back)."""

from __future__ import annotations

import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.email import get_email_backend
from app.core.security import create_access_token
from app.modules.auth import oauth
from app.modules.auth.oauth import OAuthUser


def _last_token() -> str:
    body = get_email_backend().outbox[-1].body
    match = re.search(r"Token:\s*(\S+)", body)
    assert match, f"no token in email body: {body}"
    return match.group(1)


async def _signup(client: AsyncClient, email: str = "a@example.com", password: str = "password123") -> dict:
    resp = await client.post(
        "/v1/auth/signup", json={"email": email, "password": password, "full_name": "A"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Password auth ─────────────────────────────────────────────────────────────
async def test_signup_login_me(client: AsyncClient) -> None:
    data = await _signup(client)
    assert data["user"]["email"] == "a@example.com"
    assert data["user"]["email_verified"] is False
    assert data["access_token"] and data["refresh_token"]

    login = await client.post("/v1/auth/login", json={"email": "a@example.com", "password": "password123"})
    assert login.status_code == 200
    access = login.json()["access_token"]

    me = await client.get("/v1/auth/me", headers=_auth(access))
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "a@example.com"
    assert me.json()["memberships"] == []


async def test_signup_duplicate_email(client: AsyncClient) -> None:
    await _signup(client)
    resp = await client.post("/v1/auth/signup", json={"email": "a@example.com", "password": "password123"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "auth.email_taken"


async def test_login_wrong_password(client: AsyncClient) -> None:
    await _signup(client)
    resp = await client.post("/v1/auth/login", json={"email": "a@example.com", "password": "nope-wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.invalid_credentials"


async def test_me_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/v1/auth/me")
    assert resp.status_code == 401


async def test_refresh_rotates_and_revokes_old(client: AsyncClient) -> None:
    data = await _signup(client)
    r1 = data["refresh_token"]

    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": r1})
    assert rotated.status_code == 200
    r2 = rotated.json()["refresh_token"]
    assert r2 != r1

    # Old refresh token is now dead.
    reuse = await client.post("/v1/auth/refresh", json={"refresh_token": r1})
    assert reuse.status_code == 401


async def test_refresh_reuse_does_not_revoke_the_new_token(client: AsyncClient) -> None:
    """Replaying an already-rotated token is refused, but it must NOT burn the legitimate new one: a
    late duplicate from the same browser is a race, not an attack (live incident 2026-10-07)."""
    data = await _signup(client)
    r1 = data["refresh_token"]

    r2 = (await client.post("/v1/auth/refresh", json={"refresh_token": r1})).json()["refresh_token"]
    r3 = (await client.post("/v1/auth/refresh", json={"refresh_token": r2})).json()["refresh_token"]

    reuse = await client.post("/v1/auth/refresh", json={"refresh_token": r1})
    assert reuse.status_code == 401
    # The latest legitimately-issued token still works.
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": r3})).status_code == 200


async def test_logout_all_devices_revokes_every_session(client: AsyncClient) -> None:
    data = await _signup(client)
    # A second device: log in again (a separate session/family from the signup's own).
    second = await client.post(
        "/v1/auth/login", json={"email": "a@example.com", "password": "password123"}
    )
    assert second.status_code == 200
    r_second = second.json()["refresh_token"]

    # No refresh_token in the body = revoke every session, not just the caller's.
    logout_all = await client.post(
        "/v1/auth/logout", json={}, headers=_auth(data["access_token"])
    )
    assert logout_all.status_code == 200

    assert (await client.post("/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": r_second})).status_code == 401


# ── Email verification ────────────────────────────────────────────────────────
async def test_email_verification(client: AsyncClient) -> None:
    data = await _signup(client)
    token = _last_token()  # from the signup verification email
    resp = await client.post("/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 200

    me = await client.get("/v1/auth/me", headers=_auth(data["access_token"]))
    assert me.json()["user"]["email_verified"] is True

    # Reused token now fails.
    assert (await client.post("/v1/auth/verify-email", json={"token": token})).status_code == 400


# ── Password reset ────────────────────────────────────────────────────────────
async def test_password_reset_flow(client: AsyncClient) -> None:
    await _signup(client, password="password123")
    get_email_backend().outbox.clear()

    forgot = await client.post("/v1/auth/password/forgot", json={"email": "a@example.com"})
    assert forgot.status_code == 200
    token = _last_token()

    reset = await client.post("/v1/auth/password/reset", json={"token": token, "password": "newpassword456"})
    assert reset.status_code == 200

    ok = await client.post("/v1/auth/login", json={"email": "a@example.com", "password": "newpassword456"})
    assert ok.status_code == 200
    old = await client.post("/v1/auth/login", json={"email": "a@example.com", "password": "password123"})
    assert old.status_code == 401


async def test_forgot_password_unknown_email_is_generic(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/password/forgot", json={"email": "nobody@example.com"})
    assert resp.status_code == 200  # never reveals whether the account exists


# ── Magic link ────────────────────────────────────────────────────────────────
async def test_magic_link_login(client: AsyncClient) -> None:
    req = await client.post("/v1/auth/magic-link", json={"email": "magic@example.com"})
    assert req.status_code == 200
    token = _last_token()

    verify = await client.post("/v1/auth/magic-link/verify", json={"token": token})
    assert verify.status_code == 200
    body = verify.json()
    assert body["user"]["email"] == "magic@example.com"
    assert body["user"]["email_verified"] is True
    assert body["access_token"]


# ── Sessions ──────────────────────────────────────────────────────────────────
async def test_sessions_list_and_revoke(client: AsyncClient) -> None:
    data = await _signup(client)
    access = data["access_token"]

    listed = await client.get("/v1/auth/sessions", headers=_auth(access))
    assert listed.status_code == 200
    sessions = listed.json()
    assert len(sessions) >= 1

    revoke = await client.delete(f"/v1/auth/sessions/{sessions[0]['id']}", headers=_auth(access))
    assert revoke.status_code == 204


async def test_sessions_flag_the_callers_own_device(client: AsyncClient) -> None:
    """`current` marks the session behind the presented access token, and only that one.

    It used to be hardcoded `False`, so the profile page could never say "This device".
    """
    first = await _signup(client)
    # A second sign-in for the same account opens a second session.
    second = await client.post(
        "/v1/auth/login", json={"email": "a@example.com", "password": "password123"}
    )
    assert second.status_code == 200, second.text

    listed = await client.get("/v1/auth/sessions", headers=_auth(second.json()["access_token"]))
    assert listed.status_code == 200
    sessions = listed.json()
    assert len(sessions) >= 2
    assert [s["current"] for s in sessions].count(True) == 1

    # Listing with the *first* token flags a different row — the flag follows the token,
    # rather than always naming the newest session.
    other = await client.get("/v1/auth/sessions", headers=_auth(first["access_token"]))
    first_current = {s["id"] for s in other.json() if s["current"]}
    second_current = {s["id"] for s in sessions if s["current"]}
    assert len(first_current) == 1
    assert first_current != second_current


async def test_sessions_current_is_false_for_a_token_without_sid(client: AsyncClient) -> None:
    """Access tokens minted before the `sid` claim existed stay valid; nothing is flagged."""
    data = await _signup(client)
    legacy = create_access_token(uuid.UUID(data["user"]["id"]))  # no session_id

    listed = await client.get("/v1/auth/sessions", headers=_auth(legacy))
    assert listed.status_code == 200
    assert listed.json()  # the session rows are still returned
    assert all(s["current"] is False for s in listed.json())


# ── OAuth ─────────────────────────────────────────────────────────────────────
async def test_oauth_not_configured(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force "unconfigured" regardless of any ambient OAuth env vars.
    monkeypatch.setattr(settings, "github_client_id", None)
    monkeypatch.setattr(settings, "github_client_secret", None)
    resp = await client.get("/v1/auth/oauth/github/authorize")
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "auth.oauth_not_configured"


async def test_oauth_callback_creates_user(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "github_client_id", "cid")
    monkeypatch.setattr(settings, "github_client_secret", "secret")

    async def fake_fetch(provider: str, code: str, verifier: str) -> OAuthUser:
        return OAuthUser(
            provider="github",
            provider_account_id="gh-123",
            email="oauth@example.com",
            full_name="OAuth User",
            avatar_url=None,
            access_token="tok",
            refresh_token=None,
            expires_at=None,
        )

    monkeypatch.setattr(oauth, "fetch_oauth_user", fake_fetch)

    authorize = await client.get("/v1/auth/oauth/github/authorize")
    assert authorize.status_code == 200
    state = parse_qs(urlparse(authorize.json()["authorize_url"]).query)["state"][0]

    callback = await client.get(f"/v1/auth/oauth/github/callback?code=abc&state={state}")
    assert callback.status_code == 200, callback.text
    assert callback.json()["user"]["email"] == "oauth@example.com"
    assert callback.json()["user"]["email_verified"] is True

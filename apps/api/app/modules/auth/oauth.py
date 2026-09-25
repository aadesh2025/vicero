"""OAuth provider integration (Google, Facebook, GitHub) with state + PKCE.

State/verifier are held in a process-local store (fine for a single API process; move to
Redis when scaling out). The network calls in ``fetch_oauth_user`` are monkeypatched in tests.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.errors import AppError

STATE_TTL = 600  # seconds


@dataclass(slots=True)
class OAuthUser:
    provider: str
    provider_account_id: str
    #: `None` when the provider did not return one (Facebook users who signed up by phone, a
    #: GitHub account with a private address). The caller must obtain and *verify* an email
    #: itself before creating or linking anything — see `service.oauth_login`.
    email: str | None
    full_name: str | None
    avatar_url: str | None
    access_token: str | None
    refresh_token: str | None
    expires_at: dt.datetime | None
    #: Whether the provider vouches that the user controls `email`. An unverified provider
    #: email is treated exactly like a missing one: it must never be used to find or link an
    #: account, because whoever typed it into the provider may not own that mailbox.
    email_verified: bool = True


_PROVIDERS: dict[str, dict[str, Any]] = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "pkce": True,
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scope": "read:user user:email",
        "pkce": False,
    },
    # Facebook Login. `pkce` is off on purpose: Meta documents no PKCE for the server-side code
    # flow, so a `code_challenge` would be ignored at best. The request is still protected by
    # `state` (CSRF) and the app secret on the token exchange; flip this once verified against a
    # real app (ADR-090). Facebook only returns `email` when the user has a confirmed one.
    "facebook": {
        "authorize_url": "https://www.facebook.com/v19.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v19.0/oauth/access_token",
        "userinfo_url": "https://graph.facebook.com/v19.0/me?fields=id,name,email,picture.type(large)",
        "scope": "email public_profile",
        "pkce": False,
    },
}

# state -> (code_verifier, expires_at_monotonic, web_flow)
_state_store: dict[str, tuple[str, float, bool]] = {}
# One-time codes the web app trades for a session after the provider redirect (60s, single use).
# Keeps tokens out of URLs: the redirect carries only this opaque code.
EXCHANGE_TTL = 60
_exchange_store: dict[str, tuple[dict[str, object], float]] = {}


def _creds(provider: str) -> tuple[str, str]:
    if provider not in _PROVIDERS:
        raise AppError("auth.oauth_unknown_provider", f"Unknown provider '{provider}'.", 404)
    cid = getattr(settings, f"{provider}_client_id")
    secret = getattr(settings, f"{provider}_client_secret")
    if not cid or not secret:
        raise AppError(
            "auth.oauth_not_configured",
            f"{provider.title()} login is not configured on this server.",
            501,
        )
    return cid, secret


def _redirect_uri(provider: str) -> str:
    return f"{settings.oauth_redirect_base}/v1/auth/oauth/{provider}/callback"


def build_authorize_url(provider: str, *, web_flow: bool = False) -> str:
    cid, _ = _creds(provider)
    cfg = _PROVIDERS[provider]
    state = secrets.token_urlsafe(24)
    params: dict[str, str] = {
        "client_id": cid,
        "redirect_uri": _redirect_uri(provider),
        "response_type": "code",
        "scope": cfg["scope"],
        "state": state,
    }
    verifier = ""
    if cfg["pkce"]:
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    _state_store[state] = (verifier, time.monotonic() + STATE_TTL, web_flow)
    return f"{cfg['authorize_url']}?{urlencode(params)}"


def pop_state(state: str) -> tuple[str, bool]:
    """Consume an OAuth `state` → (PKCE verifier, whether the browser flow started it)."""
    entry = _state_store.pop(state, None)
    if entry is None or entry[1] < time.monotonic():
        raise AppError("auth.oauth_invalid_state", "Invalid or expired OAuth state.", 400)
    return entry[0], entry[2]


def put_exchange(payload: dict[str, object]) -> str:
    """Stash a finished sign-in and hand back the one-time code that redeems it."""
    now = time.monotonic()
    for k in [k for k, (_, exp) in _exchange_store.items() if exp < now]:
        _exchange_store.pop(k, None)
    code = secrets.token_urlsafe(32)
    _exchange_store[code] = (payload, now + EXCHANGE_TTL)
    return code


def pop_exchange(code: str) -> dict[str, object]:
    entry = _exchange_store.pop(code, None)
    if entry is None or entry[1] < time.monotonic():
        raise AppError("auth.oauth_invalid_code", "This sign-in link has expired. Try again.", 400)
    return entry[0]


async def fetch_oauth_user(provider: str, code: str, code_verifier: str) -> OAuthUser:
    cid, secret = _creds(provider)
    cfg = _PROVIDERS[provider]
    data = {
        "client_id": cid,
        "client_secret": secret,
        "code": code,
        "redirect_uri": _redirect_uri(provider),
        "grant_type": "authorization_code",
    }
    if cfg["pkce"]:
        data["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(cfg["token_url"], data=data, headers={"Accept": "application/json"})
        token_resp.raise_for_status()
        token = token_resp.json()
        access_token = token.get("access_token")
        if not access_token:
            raise AppError("auth.oauth_exchange_failed", "OAuth code exchange failed.", 400)

        info_resp = await client.get(
            cfg["userinfo_url"], headers={"Authorization": f"Bearer {access_token}"}
        )
        info_resp.raise_for_status()
        info = info_resp.json()

    if provider == "google":
        return OAuthUser(
            provider="google",
            provider_account_id=str(info["sub"]),
            email=info.get("email"),
            full_name=info.get("name"),
            avatar_url=info.get("picture"),
            access_token=access_token,
            refresh_token=token.get("refresh_token"),
            expires_at=None,
            # Google says so explicitly; anything but a literal true is not trusted.
            email_verified=info.get("email_verified") is True,
        )
    if provider == "facebook":
        picture = ((info.get("picture") or {}).get("data") or {}).get("url")
        return OAuthUser(
            provider="facebook",
            provider_account_id=str(info["id"]),
            email=info.get("email"),
            full_name=info.get("name"),
            avatar_url=picture,
            access_token=access_token,
            refresh_token=None,
            expires_at=None,
            # Facebook returns `email` only when it is a confirmed address.
            email_verified=bool(info.get("email")),
        )
    email = info.get("email")
    verified = bool(email)
    if not email:
        email, verified = await _github_primary_email(access_token)
    return OAuthUser(
        provider="github",
        provider_account_id=str(info["id"]),
        email=email,
        full_name=info.get("name") or info.get("login"),
        avatar_url=info.get("avatar_url"),
        access_token=access_token,
        refresh_token=None,
        expires_at=None,
        email_verified=verified,
    )


async def _github_primary_email(access_token: str) -> tuple[str | None, bool]:
    """A GitHub profile's `email` is null when the user keeps it private; the emails API has it."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.github.com/user/emails", headers={"Authorization": f"Bearer {access_token}"}
        )
    if resp.status_code != 200:
        return None, False
    for row in resp.json():
        if row.get("primary") and row.get("verified"):
            return row.get("email"), True
    return None, False

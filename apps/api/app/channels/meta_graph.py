"""Thin client for the Meta Graph API, shared by the channel adapters and the one-click connect flow.

One place owns the API version (`META_GRAPH_VERSION`), the base URL and error mapping, so a Graph
version bump is a config change. Tokens travel in the ``Authorization`` header, never the URL; the
only secret that ever appears in a request is the app secret on the code/token exchange, which is why
httpx's INFO request logging (it prints full URLs) is silenced below.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("channels.meta_graph")

# httpx logs "HTTP Request: GET <full url>" at INFO; the code exchange carries client_secret.
logging.getLogger("httpx").setLevel(logging.WARNING)

#: Tests set this to an httpx.MockTransport; production leaves it None.
transport: httpx.AsyncBaseTransport | None = None


def graph_base() -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_version}"


def app_token() -> str:
    """App access token (`app_id|app_secret`) for calls Meta wants made as the app, e.g. debug_token."""
    return f"{settings.meta_app_id}|{settings.meta_app_secret}"


class MetaGraphError(Exception):
    """A Graph call failed. ``message`` is Meta's own text (safe to show); it never contains a token."""

    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    @property
    def is_transient(self) -> bool:
        return self.status == 0 or self.status >= 500

    def to_app_error(self) -> AppError:
        return AppError(
            "meta.graph_error",
            f"Meta rejected the request: {self.message}",
            502 if self.is_transient else 400,
            details={"meta_code": self.code},
        )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=graph_base(), timeout=20.0, transport=transport)


async def call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One Graph request; returns the JSON object or raises `MetaGraphError`."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with _client() as client:
            resp = await client.request(method, path, params=params, data=data, headers=headers)
    except httpx.HTTPError as exc:
        raise MetaGraphError(0, None, f"could not reach Meta ({type(exc).__name__})") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if resp.status_code >= 400 or "error" in body:
        raw_error = body.get("error")
        error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        raise MetaGraphError(
            resp.status_code,
            error.get("code") if isinstance(error.get("code"), int) else None,
            str(error.get("message") or f"HTTP {resp.status_code}")[:300],
        )
    return body


async def exchange_code(code: str) -> dict[str, Any]:
    """Embedded Signup / JS-SDK code -> access token (no redirect_uri for the JS SDK flow)."""
    return await call(
        "GET",
        "/oauth/access_token",
        params={"client_id": settings.meta_app_id, "client_secret": settings.meta_app_secret, "code": code},
    )


async def exchange_long_lived(user_token: str) -> dict[str, Any]:
    return await call(
        "GET",
        "/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "fb_exchange_token": user_token,
        },
    )


async def debug_token(token: str) -> dict[str, Any]:
    """`data` object of GET /debug_token (is_valid, app_id, user_id, expires_at, granular_scopes, ...)."""
    body = await call("GET", "/debug_token", token=app_token(), params={"input_token": token})
    data = body.get("data")
    return data if isinstance(data, dict) else {}

"""CORS for the embeddable widget: `/v1/public/*` is open to any website, everything else stays strict.

The widget runs on customers' sites, which no `CORS_ORIGINS` list can enumerate. The rest of the API is the
dashboard's and keeps the strict, credentialed `CORSMiddleware`. This middleware sits **outside** it and
takes over the public prefix only:

* any `Origin` is echoed back, never `*` together with credentials; `Access-Control-Allow-Credentials` is
  never sent, and the `Cookie` and `Authorization` request headers are dropped before the app sees them, so
  a public route cannot be driven by (or leak) a visitor's session;
* the inner `CORSMiddleware` never sees an `Origin` on these paths, so it cannot add its credentialed
  headers on top;
* for `config`, `chat`, `ws` and `subscribe`, an agent with an "Allowed websites" list answers 403 to any
  other `Origin` (preflight, request and WebSocket handshake alike) and sends no CORS headers to it. A
  request with no `Origin` header is not a cross-site browser call and is let through, as are the
  dashboard's own origins (`CORS_ORIGINS`), so the dashboard preview keeps working.

Rate limits and the public-key lookup are the routes' own and are untouched.
"""

from __future__ import annotations

import json
import re
from collections.abc import MutableMapping
from typing import Any

from sqlalchemy import select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionFactory, get_session
from app.models import Agent, WidgetConfig
from app.modules.public.origins import origin_allowed, origin_host

log = get_logger("public_cors")

_PREFIX = "/v1/public/"
_GATED = re.compile(r"^/v1/public/agents/([^/]+)/(config|chat|ws|subscribe)$")
_ALLOW_METHODS = "GET, POST, OPTIONS"
_STRIPPED_REQUEST_HEADERS = {b"cookie", b"authorization", b"origin"}


async def allowed_sites(app: Any, public_key: str) -> list[str]:
    """The agent's allow-list; empty when unset or when the key matches no agent (the route 404s it)."""
    stmt = (
        select(WidgetConfig.theme)
        .join(Agent, Agent.id == WidgetConfig.agent_id)
        .where(Agent.public_key == public_key, Agent.deleted_at.is_(None))
    )
    # Tests replace the session dependency with one shared transaction; honour that override.
    override = getattr(app, "dependency_overrides", {}).get(get_session)
    if override is not None:
        gen = override()
        try:
            session = await gen.__anext__()
            theme = (await session.execute(stmt)).scalar_one_or_none()
        finally:
            await gen.aclose()
    else:
        async with SessionFactory() as session:
            theme = (await session.execute(stmt)).scalar_one_or_none()
    sites = (theme or {}).get("allowedSites")
    return [s for s in sites if isinstance(s, str)] if isinstance(sites, list) else []


def _dashboard_host(origin: str) -> bool:
    host = origin_host(origin)
    return host is not None and any(origin_host(o) == host for o in settings.cors_origin_list)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return bytes(value).decode("latin-1")
    return None


def _cors_headers(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"access-control-expose-headers", b"*"),
        (b"vary", b"Origin"),
    ]


class PublicCorsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not scope["path"].startswith(_PREFIX):
            await self.app(scope, receive, send)
            return

        origin = _header(scope, b"origin")
        gated = _GATED.match(scope["path"])
        permitted = True
        if origin is not None and gated and not _dashboard_host(origin):
            permitted = origin_allowed(origin, await allowed_sites(scope.get("app"), gated.group(1)))

        if scope["type"] == "websocket":
            if not permitted:
                log.info("public_ws_origin_rejected", path_kind=gated.group(2) if gated else None)
                await receive()  # websocket.connect
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(self._without_credentials(scope), receive, send)
            return

        if scope["method"] == "OPTIONS" and origin is not None and _header(scope, b"access-control-request-method"):
            await self._preflight(scope, send, origin, permitted)
            return

        if not permitted:
            await self._forbidden(send)
            return

        inner = self._without_credentials(scope)
        if origin is None:
            await self.app(inner, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                drop = b"access-control-allow-credentials"
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != drop]
                if not any(k.lower() == b"access-control-allow-origin" for k, _ in headers):
                    headers.extend(_cors_headers(origin))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(inner, receive, send_with_cors)

    @staticmethod
    def _without_credentials(scope: Scope) -> Scope:
        """The scope the app sees: no cookies, no Authorization, and no Origin for the strict CORS layer."""
        headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() not in _STRIPPED_REQUEST_HEADERS]
        return {**scope, "headers": headers}

    @staticmethod
    async def _preflight(scope: Scope, send: Send, origin: str, permitted: bool) -> None:
        if not permitted:
            await PublicCorsMiddleware._forbidden(send)
            return
        headers: MutableMapping[bytes, bytes] = {
            b"access-control-allow-methods": _ALLOW_METHODS.encode(),
            b"access-control-allow-headers": b"*",
            b"access-control-max-age": b"600",
            b"content-length": b"0",
        }
        out = [(k, v) for k, v in headers.items()] + _cors_headers(origin)
        await send({"type": "http.response.start", "status": 200, "headers": out})
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _forbidden(send: Send) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "public.origin_not_allowed",
                    "message": "This website is not allowed to embed this assistant.",
                    "details": {},
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

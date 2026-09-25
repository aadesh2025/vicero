"""Request-scoped middleware: request id + structured access logging."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("http")

# A conservative API-only CSP: the API serves JSON, not HTML, so nothing needs to load.
# (The Next.js frontend sets its own CSP; the embeddable widget is served from /widget.js.)
_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": _CSP,
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every response (docs/02 §Security, docs/SECURITY.md).

    HSTS is only emitted in production (behind TLS) — it would wrongly pin http:// in dev.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        # Swagger/ReDoc need to load their own JS/CSS; skip the strict CSP for the docs UIs only.
        # In prod those routes don't exist (main.py serves no schema there), so the carve-out
        # is withheld too — otherwise a 404 at /docs would answer with a weaker CSP than every
        # other 404, which is both pointless and a hint that something used to live there.
        is_docs = not settings.is_prod and (
            request.url.path in ("/docs", "/redoc") or request.url.path.startswith("/docs/")
        )
        for header, value in _SECURITY_HEADERS.items():
            if is_docs and header in ("Content-Security-Policy", "X-Frame-Options"):
                continue
            response.headers.setdefault(header, value)
        if settings.is_prod:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, bind it to the log context, and log each request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        elapsed = time.perf_counter() - start
        elapsed_ms = round(elapsed * 1000, 2)
        response.headers["x-request-id"] = request_id
        # Record for /metrics (skip the metrics endpoint itself).
        if request.url.path != "/metrics":
            from app.core.metrics import observe_request

            observe_request(request.method, response.status_code, elapsed)
        # Skip noise from health probes.
        if not request.url.path.startswith(("/healthz", "/readyz")):
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=elapsed_ms,
                request_id=request_id,
            )
        return response

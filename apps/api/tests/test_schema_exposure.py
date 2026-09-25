"""The interactive schema is a dev tool and must not be served in production.

Until this was gated, `/docs`, `/redoc` and `/openapi.json` were served in every
environment: a complete map of all 215 operations, `/v1/admin/*` included, to any
unauthenticated caller. Nothing needs it in prod — the docs site's public API reference is
built from a committed snapshot of the same schema (`scripts/generate-openapi.py`).

These tests build the app twice, once per environment, because the decision is made in
`create_app()` and cannot be changed on a running instance.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import create_app

SCHEMA_ROUTES = ("/openapi.json", "/docs", "/redoc")


def _app_for(env: str, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(settings, "env", env)
    return create_app()


async def _get(app: FastAPI, path: str) -> int:
    # No lifespan: these routes are served by the router, and starting it would need
    # Postgres, Redis and a reachable embedding endpoint.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return (await client.get(path)).status_code


@pytest.mark.parametrize("path", SCHEMA_ROUTES)
async def test_schema_is_served_in_dev(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev keeps Swagger — docs/guides/API-USAGE.md tells people to use it."""
    assert await _get(_app_for("dev", monkeypatch), path) == 200


@pytest.mark.parametrize("path", SCHEMA_ROUTES)
async def test_schema_is_absent_in_prod(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """404, not 403: the route is removed, so nothing confirms it ever existed."""
    assert await _get(_app_for("prod", monkeypatch), path) == 404


async def test_prod_404_keeps_the_strict_csp(monkeypatch: pytest.MonkeyPatch) -> None:
    """The middleware's CSP carve-out for /docs must not outlive the routes it existed for.

    Left unconditional, a prod request to /docs would answer with a weaker CSP than every
    other 404 on the API — and say so to anyone probing.
    """
    app = _app_for("prod", monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")
    assert response.status_code == 404
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    assert response.headers["x-frame-options"] == "DENY"


def test_schema_is_still_generatable_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning the route off must not stop the docs generator building the snapshot."""
    schema = _app_for("prod", monkeypatch).openapi()
    assert schema["paths"], "the generator would have nothing to write"
    assert "/v1/agents" in schema["paths"]

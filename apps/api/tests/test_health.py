"""Health, readiness, and version endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_healthz(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_version(client: AsyncClient) -> None:
    resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "vicero-api"
    assert body["version"]
    assert body["env"] in {"dev", "test", "prod"}


async def test_readyz_reports_checks(client: AsyncClient) -> None:
    # No DB/Redis in the unit test env, so readiness should be 503 but well-formed.
    resp = await client.get("/readyz")
    assert resp.status_code in {200, 503}
    body = resp.json()
    assert set(body["checks"]) == {"database", "redis"}
    assert body["status"] in {"ready", "not_ready"}


async def test_request_id_header(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.headers.get("x-request-id")


async def test_metrics_prometheus_exposition(client: AsyncClient) -> None:
    await client.get("/version")  # generate at least one observation
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "vicero_build_info" in body
    assert "vicero_http_requests_total" in body
    assert "vicero_http_request_duration_seconds_bucket" in body
    # +Inf bucket must equal the histogram count (monotonic, well-formed).
    lines = body.splitlines()
    inf = next(ln for ln in lines if 'le="+Inf"' in ln).split()[-1]
    prefix = "vicero_http_request_duration_seconds_count"
    count = next(ln for ln in lines if ln.startswith(prefix)).split()[-1]
    assert inf == count


@pytest.mark.parametrize("path", ["/does-not-exist"])
async def test_typed_error_shape(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 404
    assert "error" in resp.json()
    assert set(resp.json()["error"]) >= {"code", "message"}

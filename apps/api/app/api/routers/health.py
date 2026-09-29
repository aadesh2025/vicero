"""Liveness, readiness, and version endpoints (docs/09 §5)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app import __version__
from app.core.config import settings
from app.core.probes import check_database, check_redis

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str


class VersionResponse(BaseModel):
    name: str
    version: str
    env: str


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, bool]


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness — the process is up and serving."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadyResponse)
async def readyz(response: Response) -> ReadyResponse:
    """Readiness — dependencies (DB, Redis) are reachable."""
    db_ok, redis_ok = await asyncio.gather(check_database(), check_redis())
    checks = {"database": db_ok, "redis": redis_ok}
    ready = all(checks.values())
    response.status_code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if ready else "not_ready", checks=checks)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(name="vicero-api", version=__version__, env=settings.env)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition — request counts, latency histogram, build info + uptime."""
    from app.core.metrics import render

    return Response(content=render(), media_type="text/plain; version=0.0.4")

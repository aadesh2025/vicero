"""`/internal/tools/*`: n8n -> Vicero, HMAC-signed. Not part of the public API surface (ADR-120).

Same transport rules as `/internal/automations/runs`: HMAC-SHA256 over `"<timestamp>.<raw body>"` with
`AUTOMATION_REPORT_SECRET`, a five-minute window, a small body cap, and fail-closed when the secret is unset.
On top of that every call names the tool run it serves and carries that run's token (see `service.resolve_run`).
Validation problems come back as `200 {"ok": false, ...}` so the workflow can hand them to the model unchanged;
only authentication problems are HTTP errors.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limit
from app.db.session import get_session
from app.modules.bookings import schemas, service

log = get_logger("bookings")

internal_router = APIRouter(
    prefix="/internal/tools",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(rate_limit("internal_tools", limit=300, window=60))],
)

MAX_BODY_BYTES = 16 * 1024
WINDOW_SECONDS = 300
_M = TypeVar("_M", bound=BaseModel)


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


async def _signed_json(request: Request) -> dict[str, Any]:
    secret = (settings.automation_report_secret or "").strip()
    if not secret:
        raise AppError("bookings.not_configured", "Booking tools are not configured.", 503)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise AppError("bookings.too_large", "Request too large.", 413)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise AppError("bookings.too_large", "Request too large.", 413)
    signature = request.headers.get("X-Vicero-Signature") or ""
    timestamp = request.headers.get("X-Vicero-Timestamp") or ""
    try:
        fresh = abs(int(time.time()) - int(timestamp)) <= WINDOW_SECONDS
    except ValueError:
        fresh = False
    if not fresh or not hmac.compare_digest(_sign(secret, timestamp, body), signature):
        log.warning("booking_tool_bad_signature", fresh=fresh)
        raise AppError("bookings.bad_signature", "Invalid signature.", 401)
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise AppError("bookings.bad_request", "Invalid JSON.", 422) from exc
    if not isinstance(data, dict):
        raise AppError("bookings.bad_request", "Invalid request.", 422)
    return data


def _model(model: type[_M], data: dict[str, Any]) -> _M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise AppError("bookings.bad_request", f"Invalid request (fields: {', '.join(fields)}).", 422) from exc


async def _tool(
    request: Request,
    session: AsyncSession,
    args_model: type[BaseModel],
    handler: Callable[[AsyncSession, service.RunContext, Any], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    call = _model(schemas.ToolCall, await _signed_json(request))
    run = await service.resolve_run(session, call.run_id, call.callback_token)
    try:
        args = args_model.model_validate(call.args)
    except ValidationError as exc:
        return service.invalid(exc)
    return await handler(session, run, args)


@internal_router.post("/book")
async def book(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _tool(request, session, schemas.BookArgs, service.book)


@internal_router.post("/availability")
async def availability(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _tool(request, session, schemas.AvailabilityArgs, service.availability)


@internal_router.post("/customer")
async def customer(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _tool(request, session, schemas.CustomerArgs, service.get_customer)

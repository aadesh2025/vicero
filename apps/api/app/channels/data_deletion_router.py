"""Meta Data Deletion Request Callback endpoints (public; the callback is signature-verified)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Form
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import data_deletion
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limit
from app.db.session import get_session
from app.worker.tasks import enqueue_data_deletion

log = get_logger("channels.data_deletion.router")

router = APIRouter(prefix="/api/meta", tags=["channels"])

_public_rl = Depends(rate_limit("meta_data_deletion", limit=60, window=60))


class DataDeletionAck(BaseModel):
    url: str
    confirmation_code: str


class DataDeletionStatus(BaseModel):
    confirmation_code: str
    status: str
    requested_at: dt.datetime
    completed_at: dt.datetime | None


@router.post("/data-deletion", response_model=DataDeletionAck, dependencies=[_public_rl])
async def request_data_deletion(
    signed_request: Annotated[str, Form()], session: AsyncSession = Depends(get_session)
) -> DataDeletionAck:
    if not settings.meta_app_secret:
        raise AppError("meta.not_configured", "Data deletion callback is not configured.", 503)
    payload = data_deletion.parse_signed_request(signed_request, settings.meta_app_secret)
    user_id = str(payload.get("user_id", "")) if payload else ""
    if not user_id:
        raise AppError("meta.bad_signature", "Invalid signed_request.", 400)

    request = await data_deletion.create_request(session, user_id)
    code = request.confirmation_code
    await session.commit()  # the worker looks the row up by code; it must exist before it is queued
    try:
        enqueue_data_deletion(code, user_id)
    except Exception:
        # Row stays "pending" and is visible on the status page; Meta must still get its answer.
        log.exception("data_deletion_enqueue_failed", confirmation_code=code)
    return DataDeletionAck(url=f"{settings.web_base_url}/data-deletion?code={code}", confirmation_code=code)


@router.get("/data-deletion/{code}", response_model=DataDeletionStatus, dependencies=[_public_rl])
async def data_deletion_status(code: str, session: AsyncSession = Depends(get_session)) -> DataDeletionStatus:
    request = await data_deletion.get_request(session, code)
    if request is None:
        raise AppError("meta.not_found", "Unknown confirmation code.", 404)
    return DataDeletionStatus(
        confirmation_code=request.confirmation_code,
        status=request.status,
        requested_at=request.created_at,
        completed_at=request.completed_at,
    )

"""Meta "Data Deletion Request Callback": signature check, request log, and the deletion itself.

Meta POSTs a `signed_request` (`<base64url sig>.<base64url payload>`) when someone removes the app
from their Facebook/Instagram settings and asks for their data to go. The payload's `user_id` is
the app-scoped id — the same value we store as `Contact.external_id` for Messenger/Instagram.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import secrets
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Contact, Conversation, CrmContact, DataDeletionRequest

log = get_logger("channels.data_deletion")


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def parse_signed_request(signed_request: str, secret: str) -> dict[str, Any] | None:
    """Verified payload, or None when the request is malformed or the signature is wrong.

    The HMAC is over the *encoded* payload string exactly as received, not over the decoded JSON.
    """
    if not secret:
        return None
    encoded_sig, dot, payload_part = signed_request.partition(".")
    if not dot or not encoded_sig or not payload_part:
        return None
    try:
        sig = _b64url_decode(encoded_sig)
        payload = json.loads(_b64url_decode(payload_part))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(payload, dict) or str(payload.get("algorithm", "")).upper() != "HMAC-SHA256":
        return None
    expected = hmac.new(secret.encode(), payload_part.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return None
    return payload


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()


async def create_request(session: AsyncSession, user_id: str) -> DataDeletionRequest:
    request = DataDeletionRequest(confirmation_code=secrets.token_hex(16), user_id_hash=hash_user_id(user_id))
    session.add(request)
    await session.flush()
    return request


async def get_request(session: AsyncSession, code: str) -> DataDeletionRequest | None:
    return (
        await session.execute(select(DataDeletionRequest).where(DataDeletionRequest.confirmation_code == code))
    ).scalar_one_or_none()


async def delete_user_data(session: AsyncSession, user_id: str) -> int:
    """Erase every conversation and contact stored for this platform user id, across all orgs.

    Messages, agent steps and flags go with their conversation (FK cascade). A person-level CRM
    record is removed only once no other channel handle still points at it.
    """
    contacts = (
        await session.execute(select(Contact.id, Contact.crm_contact_id).where(Contact.external_id == user_id))
    ).all()
    contact_ids = [c.id for c in contacts]
    crm_ids = {c.crm_contact_id for c in contacts if c.crm_contact_id}

    convo_filter = Conversation.channel_user_id == user_id
    if contact_ids:
        convo_filter = or_(convo_filter, Conversation.contact_id.in_(contact_ids))
    result = await session.execute(delete(Conversation).where(convo_filter))
    conversations = int(result.rowcount or 0)  # type: ignore[attr-defined]

    if contact_ids:
        await session.execute(delete(Contact).where(Contact.id.in_(contact_ids)))
    for crm_id in crm_ids:
        still_linked = (
            await session.execute(select(func.count()).select_from(Contact).where(Contact.crm_contact_id == crm_id))
        ).scalar_one()
        if not still_linked:
            await session.execute(delete(CrmContact).where(CrmContact.id == crm_id))
    return conversations + len(contact_ids)


async def run_request(session: AsyncSession, confirmation_code: str, user_id: str) -> str:
    """Do the deletion for one logged request and record the outcome. Idempotent."""
    request = await get_request(session, confirmation_code)
    if request is None:
        return "missing"
    if request.status == "completed":
        return "completed"
    try:
        request.records_deleted = await delete_user_data(session, user_id)
        request.status = "completed"
        request.completed_at = dt.datetime.now(dt.UTC)
    except Exception:
        log.exception("data_deletion_failed", confirmation_code=confirmation_code)
        await session.rollback()
        request = await get_request(session, confirmation_code)
        if request is not None:
            request.status = "failed"
        return "failed"
    return "completed"

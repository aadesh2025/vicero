"""Inbox routes under /v1/inbox (docs/04 §Inbox)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.db.session import SessionFactory, get_session
from app.models import User
from app.modules.conversations.schemas import MessageOut
from app.modules.inbox import schemas, service
from app.modules.orgs.deps import OrgContext, _load_context, current_org
from app.realtime.hub import hub, inbox_topic

log = get_logger("inbox.router")

router = APIRouter(prefix="/v1/inbox", tags=["inbox"])


@router.get("/conversations", response_model=list[schemas.InboxItemOut])
async def list_conversations(
    status: str | None = Query(default=None),
    channel: str | None = Query(default=None, description="Filter to one channel, e.g. `instagram`."),
    include_bot: bool = Query(
        default=False,
        description="Also list conversations the bot is handling (no handoff). Read-only triage; take-over is allowed.",
    ),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.InboxItemOut]:
    return await service.list_conversations(session, ctx, status, channel, include_bot)


@router.get("/conversations/{cid}", response_model=schemas.InboxDetail)
async def get_detail(
    cid: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.InboxDetail:
    return await service.get_detail(session, ctx, cid)


@router.post("/conversations/{cid}/takeover", response_model=schemas.InboxItemOut)
async def takeover(
    cid: uuid.UUID, session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> schemas.InboxItemOut:
    return await service.takeover(session, ctx, cid)


@router.post("/conversations/{cid}/handback", response_model=schemas.InboxItemOut)
async def handback(
    cid: uuid.UUID, session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> schemas.InboxItemOut:
    return await service.handback(session, ctx, cid)


@router.post("/conversations/{cid}/close", response_model=schemas.InboxItemOut)
async def close(
    cid: uuid.UUID, session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> schemas.InboxItemOut:
    return await service.close(session, ctx, cid)


@router.post("/conversations/{cid}/messages", response_model=MessageOut)
async def reply(
    cid: uuid.UUID,
    data: schemas.ReplyRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> MessageOut:
    return await service.operator_reply(session, ctx, cid, data.text)


@router.post("/conversations/{cid}/template", response_model=MessageOut)
async def send_template(
    cid: uuid.UUID,
    data: schemas.TemplateRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> MessageOut:
    """Re-open a WhatsApp conversation whose 24-hour free-form window has closed."""
    return await service.send_template(session, ctx, cid, data.template, data.params)


@router.post("/conversations/{cid}/assign", response_model=schemas.InboxItemOut)
async def assign(
    cid: uuid.UUID,
    data: schemas.AssignRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.InboxItemOut:
    return await service.assign(session, ctx, cid, data.user_id)


@router.post("/conversations/{cid}/notes", response_model=schemas.HandoffOut)
async def add_note(
    cid: uuid.UUID,
    data: schemas.NoteRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.HandoffOut:
    return await service.add_note(session, ctx, cid, data.text)


@router.post("/conversations/{cid}/tags", response_model=schemas.HandoffOut)
async def set_tags(
    cid: uuid.UUID,
    data: schemas.TagsRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.HandoffOut:
    return await service.set_tags(session, ctx, cid, data.tags)


@router.websocket("/ws")
async def inbox_ws(websocket: WebSocket) -> None:
    """Operator inbox stream. Auth via `?token=<access>&org_id=<uuid>`."""
    token = websocket.query_params.get("token")
    org_raw = websocket.query_params.get("org_id")
    await websocket.accept()
    claims = decode_access_token(token) if token else None
    if claims is None or not org_raw:
        await websocket.close(code=4401)
        return
    try:
        user_id = uuid.UUID(claims["sub"])
        org_id = uuid.UUID(org_raw)
    except (KeyError, ValueError):
        await websocket.close(code=4401)
        return

    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        if user is None or not user.is_active or user.deleted_at is not None:
            await websocket.close(code=4401)
            return
        try:
            await _load_context(session, user, org_id)  # membership check
        except Exception:
            await websocket.close(code=4403)
            return

    queue = hub.subscribe(inbox_topic(org_id))
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        log.warning("inbox_ws_error", error=str(exc))
    finally:
        hub.unsubscribe(inbox_topic(org_id), queue)


@router.get("/attention", response_model=list[schemas.AttentionItemOut])
async def attention_queue(
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.AttentionItemOut]:
    """Conversations flagged for a human to look at (docs/11 §L6).

    Distinct from `/conversations`, which is the handoff queue. Here the bot is usually still
    answering — `bot_still_answering` says which.
    """
    return await service.attention_queue(session, ctx)


@router.post("/conversations/{cid}/attention/resolve", response_model=schemas.InboxItemOut)
async def resolve_attention(
    cid: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.InboxItemOut:
    """Clear the flags — the only way a conversation's severity goes down."""
    return await service.resolve_attention(session, ctx, cid)


@router.get("/workflow-approvals", response_model=list[schemas.WorkflowApprovalOut])
async def list_workflow_approvals(
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.WorkflowApprovalOut]:
    """Workflows paused on an Approval node, waiting on a human (docs/17 Phase 2 item 4)."""
    return await service.list_workflow_approvals(session, ctx)


@router.post("/workflow-approvals/{handoff_id}/decide", response_model=schemas.WorkflowApprovalOut)
async def decide_workflow_approval(
    handoff_id: uuid.UUID,
    data: schemas.WorkflowApprovalDecisionRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.WorkflowApprovalOut:
    return await service.decide_workflow_approval(session, ctx, handoff_id, data.decision)

"""Public widget/channel routes (docs/04 §Public chat). No dashboard auth."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limit
from app.db.session import SessionFactory, get_session
from app.models import Conversation
from app.modules.help_articles import schemas as help_schemas
from app.modules.help_articles import service as help_service
from app.modules.public import schemas, service
from app.realtime.hub import conv_topic, hub

log = get_logger("public")

router = APIRouter(prefix="/v1/public", tags=["public"])


@router.get("/agents/{public_key}/config", response_model=schemas.PublicConfig)
async def get_config(
    public_key: str, session: AsyncSession = Depends(get_session)
) -> schemas.PublicConfig:
    return await service.get_config(session, public_key)


@router.get("/agents/{public_key}/widget-logo")
async def get_widget_logo(
    public_key: str, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    """Serve an agent's widget logo. Public + cross-origin (the widget embeds on any site)."""
    agent, _version = await service._resolve_agent(session, public_key)
    found = service.widget_logo_file(agent.organization_id, agent.id)
    if found is None:
        raise AppError("widget.logo_not_found", "No logo set for this agent.", 404)
    path, media_type = found
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"},
    )


@router.get("/agents/{public_key}/help", response_model=list[help_schemas.PublicHelpArticleSummary])
async def list_help_articles(
    public_key: str, session: AsyncSession = Depends(get_session)
) -> list[help_schemas.PublicHelpArticleSummary]:
    """Published Help Center articles for this agent. Unauthenticated by design."""
    agent, _version = await service._resolve_agent(session, public_key)
    return await help_service.public_list(session, agent)


@router.get("/agents/{public_key}/help/{slug}", response_model=help_schemas.PublicHelpArticle)
async def get_help_article(
    public_key: str, slug: str, session: AsyncSession = Depends(get_session)
) -> help_schemas.PublicHelpArticle:
    agent, _version = await service._resolve_agent(session, public_key)
    return await help_service.public_get(session, agent, slug)


@router.post(
    "/agents/{public_key}/chat",
    response_model=None,
    dependencies=[Depends(rate_limit("public_chat", limit=60, window=60))],
)
async def public_chat(
    public_key: str,
    data: schemas.PublicChatRequest,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse | dict[str, Any]:
    visitor_id = service.visitor_id_for(data)
    # Resolved here rather than inside the generator so an unknown agent or a conversation that
    # is not this visitor's returns a normal typed 404 — once a StreamingResponse has begun,
    # the status is already sent and the error can only truncate the body.
    resolved = await service.resolve_turn(session, public_key, data, visitor_id)
    if data.stream:
        return StreamingResponse(
            service.public_chat_sse(session, resolved, data),
            media_type="text/event-stream",
        )
    return await service.public_chat_once(session, resolved, data)


@router.get(
    "/agents/{public_key}/conversations/{conversation_id}/messages",
    response_model=list[schemas.PublicHistoryMessage],
    dependencies=[Depends(rate_limit("public_history", limit=60, window=60))],
)
async def public_history(
    public_key: str,
    conversation_id: uuid.UUID,
    visitor_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[schemas.PublicHistoryMessage]:
    """The visitor's own thread, so an interactive-blocks widget can reload mid-conversation (docs/27)."""
    return await service.conversation_history(session, public_key, conversation_id, visitor_id[:255])


@router.websocket("/agents/{public_key}/ws")
async def public_chat_ws(websocket: WebSocket, public_key: str) -> None:
    await websocket.accept()
    async with SessionFactory() as session:
        try:
            while True:
                payload = await websocket.receive_json()
                # One session serves the whole socket, and `SessionFactory` sets
                # `expire_on_commit=False`, so without this every row this connection has ever
                # loaded stays in the identity map at its first-read values for the life of the
                # socket. `session.get()` then answers from cache and never re-queries. That
                # silently pins the org's `public_contacts` (the PII allowlist), its guard
                # toggles, and the agent's published version to whatever they were when the
                # socket opened — an operator publishing a support address mid-chat would see
                # it ignored until the visitor reconnected. Each HTTP request gets a fresh
                # session and so never had this problem; the socket has to ask for it.
                session.expire_all()
                try:
                    data = schemas.PublicChatRequest(**payload)
                except Exception as exc:  # malformed client frame
                    await websocket.send_json({"type": "error", "error": f"bad request: {exc}"})
                    continue
                visitor_id = service.visitor_id_for(data)
                try:
                    resolved = await service.resolve_turn(session, public_key, data, visitor_id)
                except AppError as exc:
                    # An unknown agent or someone else's conversation. A rejected frame is not
                    # a broken socket — report it and keep listening, the same way a malformed
                    # frame is handled above.
                    await websocket.send_json({"type": "error", "error": exc.code})
                    continue
                async for ev in service.public_chat_events(session, resolved, data):
                    await websocket.send_text(ev.model_dump_json())
                await session.commit()
        except WebSocketDisconnect:
            return
        except Exception as exc:  # surface + close on unexpected failure
            log.warning("public_ws_error", error=str(exc))
            await session.rollback()
            await websocket.close(code=1011)


@router.websocket("/agents/{public_key}/subscribe")
async def public_subscribe_ws(websocket: WebSocket, public_key: str) -> None:
    """Listen-only socket: the widget subscribes to a conversation to receive operator pushes."""
    conversation_id = websocket.query_params.get("conversation_id")
    await websocket.accept()
    if not conversation_id:
        await websocket.close(code=4400)
        return
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError:
        await websocket.close(code=4400)
        return

    # Verify the conversation belongs to the agent for this public key.
    async with SessionFactory() as session:
        agent, _version = await service._resolve_agent(session, public_key)
        conv = await session.get(Conversation, cid)
        if conv is None or conv.agent_id != agent.id:
            await websocket.close(code=4404)
            return

    queue = hub.subscribe(conv_topic(cid))
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        log.warning("public_subscribe_ws_error", error=str(exc))
    finally:
        hub.unsubscribe(conv_topic(cid), queue)

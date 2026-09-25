"""Agent routes under /v1/agents."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.agents import schemas, service
from app.modules.orgs.deps import OrgContext, current_org

router = APIRouter(prefix="/v1/agents", tags=["agents"])

# Separate router: the catalog is not scoped to an agent, so it can't hang off /v1/agents/{id}.
templates_router = APIRouter(prefix="/v1/agent-templates", tags=["agents"])


@templates_router.get("", response_model=list[schemas.AgentTemplateOut])
async def list_agent_templates(
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.AgentTemplateOut]:
    """Creation-time role templates. Static catalog data — the org context is only here so the
    endpoint isn't public; the response is identical for every org."""
    return service.list_templates()


class WidgetLogoResponse(BaseModel):
    logo_url: str


@router.post("", response_model=schemas.AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    data: schemas.CreateAgentRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.AgentOut:
    return await service.create_agent(session, ctx, data)


@router.get("", response_model=list[schemas.AgentOut])
async def list_agents(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(current_org)
) -> list[schemas.AgentOut]:
    return await service.list_agents(session, ctx)


@router.get("/{agent_id}", response_model=schemas.AgentOut)
async def get_agent(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.AgentOut:
    return await service.get_agent(session, ctx, agent_id)


@router.patch("/{agent_id}", response_model=schemas.AgentOut)
async def update_agent(
    agent_id: uuid.UUID,
    data: schemas.UpdateAgentRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.AgentOut:
    return await service.update_agent(session, ctx, agent_id, data)


@router.post("/{agent_id}/widget/logo", response_model=WidgetLogoResponse)
async def upload_widget_logo(
    agent_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> WidgetLogoResponse:
    """Upload a widget/assistant logo (png/jpg/webp/gif, ≤2MB). Returns its public URL; the
    caller stores it in the draft's persona.widget.logoUrl and autosaves like any other field."""
    data = await file.read()
    result = await service.save_widget_logo(
        session, ctx, agent_id, filename=file.filename, content_type=file.content_type, data=data
    )
    return WidgetLogoResponse(**result)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> None:
    await service.delete_agent(session, ctx, agent_id)


@router.post("/{agent_id}/duplicate", response_model=schemas.AgentOut, status_code=status.HTTP_201_CREATED)
async def duplicate_agent(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.AgentOut:
    return await service.duplicate_agent(session, ctx, agent_id)


# ── Versions ──────────────────────────────────────────────────────────────────
@router.get("/{agent_id}/versions", response_model=list[schemas.VersionOut])
async def list_versions(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> list[schemas.VersionOut]:
    return await service.list_versions(session, ctx, agent_id)


@router.post("/{agent_id}/versions", response_model=schemas.VersionOut, status_code=status.HTTP_201_CREATED)
async def create_version(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.VersionOut:
    return await service.create_version(session, ctx, agent_id)


@router.patch("/{agent_id}/versions/{number}", response_model=schemas.VersionOut)
async def update_version(
    agent_id: uuid.UUID,
    number: int,
    data: schemas.UpdateVersionRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.VersionOut:
    return await service.update_version(session, ctx, agent_id, number, data)


@router.post("/{agent_id}/versions/{number}/publish", response_model=schemas.AgentOut)
async def publish_version(
    agent_id: uuid.UUID,
    number: int,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.AgentOut:
    return await service.publish_version(session, ctx, agent_id, number)


@router.post("/{agent_id}/rollback", response_model=schemas.AgentOut)
async def rollback(
    agent_id: uuid.UUID,
    data: schemas.RollbackRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> schemas.AgentOut:
    return await service.rollback(session, ctx, agent_id, data.version)


# ── Playground ────────────────────────────────────────────────────────────────
@router.post("/{agent_id}/playground/chat", response_model=None)
async def playground_chat(
    agent_id: uuid.UUID,
    data: schemas.PlaygroundRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> StreamingResponse | dict[str, Any]:
    await service.check_playground_allowed(session, ctx)
    if data.stream:
        return StreamingResponse(
            service.playground_stream(session, ctx, agent_id, data),
            media_type="text/event-stream",
        )
    return await service.playground_once(session, ctx, agent_id, data)


# ── Widget appearance ─────────────────────────────────────────────────────────
# Separate from the versioned draft on purpose: a save here is live immediately, which is
# what the embed snippet already promises. It needs AGENTS_WRITE, never AGENTS_PUBLISH.
@router.get("/{agent_id}/widget-config", response_model=dict)
async def get_widget_config(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> dict[str, Any]:
    return await service.get_widget_config(session, ctx, agent_id)


@router.patch("/{agent_id}/widget-config", response_model=dict)
async def update_widget_config(
    agent_id: uuid.UUID,
    data: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(current_org),
) -> dict[str, Any]:
    return await service.update_widget_config(session, ctx, agent_id, data)

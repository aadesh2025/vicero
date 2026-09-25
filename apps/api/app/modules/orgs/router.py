"""Organization routes under /v1/orgs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import rate_limit
from app.db.session import get_session
from app.models import User
from app.modules.auth.deps import get_current_user
from app.modules.orgs import schemas, service
from app.modules.orgs.deps import OrgContext, org_context
from app.modules.orgs.service import _org_out

router = APIRouter(prefix="/v1/orgs", tags=["orgs"])


# Literal route declared before /{org_id} so "invitations" isn't parsed as an org id.
# Unauthenticated: the invitee usually has no account yet, and this is what tells the page
# whether to offer sign-in or signup. Rate-limited because it is readable by anyone holding a
# token (see `InvitationPreview` on the disclosure).
@router.get(
    "/invitations/{token}",
    response_model=schemas.InvitationPreview,
    dependencies=[Depends(rate_limit("invite-preview"))],
)
async def preview_invitation(
    token: str, session: AsyncSession = Depends(get_session)
) -> schemas.InvitationPreview:
    return await service.preview_invitation(session, token)


@router.post("/invitations/{token}/accept", response_model=schemas.OrgOut)
async def accept_invitation(
    token: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> schemas.OrgOut:
    return await service.accept_invitation(session, user, token)


@router.post("", response_model=schemas.OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    data: schemas.CreateOrgRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> schemas.OrgOut:
    return await service.create_org(session, user, data.name)


@router.get("", response_model=list[schemas.OrgOut])
async def list_orgs(
    session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> list[schemas.OrgOut]:
    return await service.list_orgs(session, user)


@router.get("/{org_id}", response_model=schemas.OrgOut)
async def get_org(ctx: OrgContext = Depends(org_context)) -> schemas.OrgOut:
    return _org_out(ctx.org, ctx.role)


@router.get("/{org_id}/plan", response_model=schemas.PlanStatusOut)
async def get_plan(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(org_context)
) -> schemas.PlanStatusOut:
    """Trial status, usage meter and feature locks for the dashboard. Any member may read it."""
    return await service.plan_status(session, ctx)


@router.patch("/{org_id}", response_model=schemas.OrgOut)
async def update_org(
    data: schemas.UpdateOrgRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(org_context),
) -> schemas.OrgOut:
    return await service.update_org(session, ctx, data)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(org_context)
) -> None:
    await service.delete_org(session, ctx)


@router.get("/{org_id}/members", response_model=list[schemas.MemberOut])
async def list_members(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(org_context)
) -> list[schemas.MemberOut]:
    return await service.list_members(session, ctx)


@router.patch("/{org_id}/members/{user_id}", response_model=schemas.MessageResponse)
async def change_role(
    user_id: uuid.UUID,
    data: schemas.ChangeRoleRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(org_context),
) -> schemas.MessageResponse:
    await service.change_role(session, ctx, user_id, data.role)
    return schemas.MessageResponse(message="Role updated.")


@router.delete("/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(org_context),
) -> None:
    await service.remove_member(session, ctx, user_id)


@router.post(
    "/{org_id}/invitations", response_model=schemas.InvitationOut, status_code=status.HTTP_201_CREATED
)
async def create_invitation(
    data: schemas.InvitationCreate,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(org_context),
) -> schemas.InvitationOut:
    return await service.create_invitation(session, ctx, data.email, data.role)


@router.get("/{org_id}/invitations", response_model=list[schemas.InvitationOut])
async def list_invitations(
    session: AsyncSession = Depends(get_session), ctx: OrgContext = Depends(org_context)
) -> list[schemas.InvitationOut]:
    return await service.list_invitations(session, ctx)


@router.post("/{org_id}/invitations/{invitation_id}/link", response_model=schemas.InvitationLinkOut)
async def regenerate_invitation_link(
    invitation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(org_context),
) -> schemas.InvitationLinkOut:
    return await service.regenerate_invitation_link(session, ctx, invitation_id)


@router.delete("/{org_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(org_context),
) -> None:
    await service.revoke_invitation(session, ctx, invitation_id)


@router.post("/{org_id}/transfer-ownership", response_model=schemas.MessageResponse)
async def transfer_ownership(
    data: schemas.TransferOwnershipRequest,
    session: AsyncSession = Depends(get_session),
    ctx: OrgContext = Depends(org_context),
) -> schemas.MessageResponse:
    await service.transfer_ownership(session, ctx, data.user_id)
    return schemas.MessageResponse(message="Ownership transferred.")

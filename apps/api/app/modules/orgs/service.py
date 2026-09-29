"""Organizations, memberships, and invitations service."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.billing.usage import load_entitlements, unanswered_messages
from app.core import rbac
from app.core.audit import write_audit
from app.core.config import settings
from app.core.email import EmailMessage, queue_email
from app.core.email_templates import invitation_email
from app.core.errors import AppError
from app.core.plans import PLANS, SELF_SERVE_PLAN, get_entitlements, plan_limit
from app.core.security import generate_opaque_token, hash_token
from app.models import (
    Agent,
    AuditLog,
    Invitation,
    Membership,
    Organization,
    OrgMessageUsage,
    User,
)
from app.modules.orgs import schemas
from app.modules.orgs.deps import OrgContext

INVITE_TTL = dt.timedelta(days=7)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _slug_base(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "org"


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = _slug_base(name)
    candidate = base
    n = 1
    while True:
        exists = (
            await session.execute(select(Organization.id).where(Organization.slug == candidate))
        ).scalar_one_or_none()
        if exists is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


async def _write_audit(
    session: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    await write_audit(
        session, org_id, actor_id, action, target_type=target_type, target_id=target_id, meta=meta
    )


def _org_out(org: Organization, role: str) -> schemas.OrgOut:
    return schemas.OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        plan=org.plan,
        avatar_url=org.avatar_url,
        role=role,
        auto_crm_capture_enabled=org.auto_crm_capture_enabled,
        public_contacts=list(org.public_contacts or []),
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


async def _active_membership(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    stmt = select(Membership).where(
        Membership.organization_id == org_id,
        Membership.user_id == user_id,
        Membership.status == "active",
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ── Org CRUD ──────────────────────────────────────────────────────────────────
async def _owned_workspace_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Membership)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            Membership.user_id == user_id,
            Membership.role == "owner",
            Membership.status == "active",
            Organization.deleted_at.is_(None),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


def _workspace_name(user: User) -> str:
    base = (user.full_name or "").strip().split(" ")[0] or user.email.partition("@")[0]
    return f"{base}'s workspace"[:255]


async def provision_trial_workspace(
    session: AsyncSession, user: User, name: str | None = None
) -> Organization:
    """One workspace, one owner membership, one usage row and a trial clock — in the caller's
    transaction, so a failure anywhere leaves no half-provisioned account (docs/18 §3).

    The trial length comes from the plan table; nothing here knows what "10" is.
    """
    spec = PLANS[SELF_SERVE_PLAN]
    assert spec.trial_days is not None
    now = _now()
    ends = now + dt.timedelta(days=spec.trial_days)
    display = name or _workspace_name(user)
    org = Organization(
        name=display,
        slug=await _unique_slug(session, display),
        plan=SELF_SERVE_PLAN,
        trial_started_at=now,
        trial_ends_at=ends,
        created_by=user.id,
        # The owner's own address is one the agent may share, or output redaction would strip it
        # from replies (same reason operator provisioning seeds it).
        public_contacts=[user.email],
    )
    session.add(org)
    await session.flush()
    session.add(Membership(organization_id=org.id, user_id=user.id, role="owner", status="active"))
    session.add(OrgMessageUsage(organization_id=org.id))
    await _write_audit(session, org.id, user.id, "org.created", target_type="org", target_id=str(org.id))
    await _write_audit(
        session, org.id, user.id, "plan.trial_started", target_type="org", target_id=str(org.id),
        meta={"trial_ends_at": ends.isoformat()},
    )
    return org


async def has_pending_invitation(session: AsyncSession, email: str) -> bool:
    stmt = select(Invitation.id).where(
        Invitation.email == email.lower(),
        Invitation.accepted_at.is_(None),
        Invitation.expires_at > _now(),
    )
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def ensure_self_serve_workspace(session: AsyncSession, user: User) -> Organization | None:
    """Give a new self-serve user their workspace — once, and only when it is theirs to get.

    A no-op when self-serve is off, when they already own a workspace, and when they hold a
    pending invitation: an invited teammate is joining a client's org and must not also spawn a
    stray trial workspace of their own.
    """
    if not settings.self_serve_enabled or user.is_staff:
        return None
    if await _owned_workspace_count(session, user.id) > 0:
        return None
    if await has_pending_invitation(session, user.email):
        return None
    return await provision_trial_workspace(session, user)


async def create_org(session: AsyncSession, user: User, name: str) -> schemas.OrgOut:
    """Create an organization.

    Three cases, in order:

    * **Staff**, or the test bootstrap switch: an unmetered (`legacy`) workspace — how an
      operator provisions a client, unchanged.
    * **Self-serve on**: a non-staff user may own **one** workspace (the limit is read from the
      trial plan); a second gets a 402 `plan_limit`. Signup normally created it already, so this
      serves a user who has none (e.g. their invitation lapsed).
    * **Self-serve off**: staff-only, as before. Enforced server-side because hiding the button
      doesn't stop a direct API call.

    Invitations are unaffected: `accept_invitation` adds a `Membership` to an org that already
    exists and never comes through here.
    """
    if user.is_staff or settings.allow_self_serve_orgs:
        org = Organization(name=name, slug=await _unique_slug(session, name), created_by=user.id)
        session.add(org)
        await session.flush()
        session.add(Membership(organization_id=org.id, user_id=user.id, role="owner", status="active"))
        await _write_audit(session, org.id, user.id, "org.created", target_type="org", target_id=str(org.id))
        return _org_out(org, "owner")
    if not settings.self_serve_enabled:
        raise AppError(
            "orgs.create_forbidden",
            "Organization creation is staff-only. Ask your BotForge contact to set one up "
            "for you.",
            403,
        )
    limit = get_entitlements(SELF_SERVE_PLAN).max_workspaces
    if limit is not None and await _owned_workspace_count(session, user.id) >= limit:
        raise plan_limit("workspaces", "Your plan includes one workspace. Upgrade to add more.")
    org = await provision_trial_workspace(session, user, name)
    return _org_out(org, "owner")


async def plan_status(session: AsyncSession, ctx: OrgContext) -> schemas.PlanStatusOut:
    """Everything the dashboard needs to draw the trial banner, meter and locked states."""
    now = _now()
    ent = await load_entitlements(session, ctx.org, now=now)
    agents = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Agent)
                .where(Agent.organization_id == ctx.org.id, Agent.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    return schemas.PlanStatusOut(
        plan=ctx.org.plan,
        status=ent.status,
        trial_ends_at=ent.trial_ends_at,
        days_left=ent.days_left(now),
        expired_reason=ent.expired_reason,
        messages_used=ent.messages_used,
        messages_limit=ent.meter_limit,
        messages_remaining=ent.messages_remaining,
        unanswered_messages=await unanswered_messages(session, ctx.org.id),
        agents_used=agents,
        max_agents=ent.max_agents,
        can_create_agent=ent.agents_writable and (ent.max_agents is None or agents < ent.max_agents),
        features={f: ent.allows(f) for f in ("workflows", "n8n", "tool_calling")},
    )


async def list_orgs(session: AsyncSession, user: User) -> list[schemas.OrgOut]:
    stmt = (
        select(Organization, Membership.role)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(
            Membership.user_id == user.id,
            Membership.status == "active",
            Organization.deleted_at.is_(None),
        )
        .order_by(Organization.created_at.asc())
    )
    return [_org_out(org, role) for org, role in (await session.execute(stmt)).all()]


async def update_org(session: AsyncSession, ctx: OrgContext, data: schemas.UpdateOrgRequest) -> schemas.OrgOut:
    rbac.require_permission(ctx.role, rbac.ORG_MANAGE)
    if data.name is not None:
        ctx.org.name = data.name
    if data.avatar_url is not None:
        ctx.org.avatar_url = data.avatar_url
    if data.settings is not None:
        ctx.org.settings = data.settings
    if data.auto_crm_capture_enabled is not None:
        ctx.org.auto_crm_capture_enabled = data.auto_crm_capture_enabled
    if data.public_contacts is not None:
        ctx.org.public_contacts = data.public_contacts
    await _write_audit(session, ctx.org.id, ctx.user.id, "org.updated", target_type="org", target_id=str(ctx.org.id))
    return _org_out(ctx.org, ctx.role)


async def delete_org(session: AsyncSession, ctx: OrgContext) -> None:
    rbac.require_permission(ctx.role, rbac.ORG_MANAGE)
    ctx.org.deleted_at = _now()
    await _write_audit(session, ctx.org.id, ctx.user.id, "org.deleted", target_type="org", target_id=str(ctx.org.id))


# ── Members ───────────────────────────────────────────────────────────────────
async def list_members(session: AsyncSession, ctx: OrgContext) -> list[schemas.MemberOut]:
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == ctx.org.id, Membership.status == "active")
        .order_by(Membership.created_at.asc())
    )
    return [
        schemas.MemberOut(
            user_id=u.id,
            email=u.email,
            full_name=u.full_name,
            avatar_url=u.avatar_url,
            role=m.role,
            status=m.status,
            joined_at=m.created_at,
        )
        for m, u in (await session.execute(stmt)).all()
    ]


async def change_role(
    session: AsyncSession, ctx: OrgContext, target_user_id: uuid.UUID, role: str
) -> None:
    rbac.require_permission(ctx.role, rbac.MEMBERS_MANAGE)
    membership = await _active_membership(session, ctx.org.id, target_user_id)
    if membership is None:
        raise AppError("org.member_not_found", "Member not found.", 404)
    if membership.role == "owner":
        raise AppError("org.cannot_change_owner", "Use transfer ownership to change the owner.", 400)
    membership.role = role
    await _write_audit(
        session, ctx.org.id, ctx.user.id, "member.role_changed",
        target_type="user", target_id=str(target_user_id), meta={"role": role},
    )


async def remove_member(session: AsyncSession, ctx: OrgContext, target_user_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.MEMBERS_MANAGE)
    membership = await _active_membership(session, ctx.org.id, target_user_id)
    if membership is None:
        raise AppError("org.member_not_found", "Member not found.", 404)
    if membership.role == "owner":
        raise AppError("org.cannot_remove_owner", "The owner cannot be removed.", 400)
    membership.status = "removed"
    await _write_audit(
        session, ctx.org.id, ctx.user.id, "member.removed", target_type="user", target_id=str(target_user_id)
    )


async def transfer_ownership(session: AsyncSession, ctx: OrgContext, target_user_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.ORG_MANAGE)
    target = await _active_membership(session, ctx.org.id, target_user_id)
    if target is None:
        raise AppError("org.member_not_found", "Target member not found.", 404)
    ctx.membership.role = "admin"  # step down
    target.role = "owner"
    await _write_audit(
        session, ctx.org.id, ctx.user.id, "org.ownership_transferred",
        target_type="user", target_id=str(target_user_id),
    )


# ── Invitations ───────────────────────────────────────────────────────────────
async def create_invitation(
    session: AsyncSession, ctx: OrgContext, email: str, role: str
) -> schemas.InvitationOut:
    rbac.require_permission(ctx.role, rbac.MEMBERS_MANAGE)
    await usage.require_team_member_slot(session, ctx.org)
    email = email.lower()
    existing_user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing_user is not None and await _active_membership(session, ctx.org.id, existing_user.id):
        raise AppError("org.already_member", "That person is already a member.", 409)

    raw = generate_opaque_token()
    invitation = Invitation(
        organization_id=ctx.org.id,
        email=email,
        role=role,
        token_hash=hash_token(raw),
        invited_by=ctx.user.id,
        expires_at=_now() + INVITE_TTL,
    )
    session.add(invitation)
    await session.flush()

    link = f"{settings.web_base_url}/invitations/accept?token={raw}"
    subject, text, html = invitation_email(ctx.org.name, role, link, raw)
    await queue_email(EmailMessage(to=email, subject=subject, body=text, html_body=html))
    await _write_audit(
        session, ctx.org.id, ctx.user.id, "invitation.created",
        target_type="invitation", target_id=str(invitation.id), meta={"email": email, "role": role},
    )
    return schemas.InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accept_token=None if settings.is_prod else raw,
        # Already looked up above; carrying it back means the row renders its badge without
        # waiting for a refetch.
        account_exists=existing_user is not None,
    )


async def list_invitations(session: AsyncSession, ctx: OrgContext) -> list[schemas.InvitationOut]:
    rbac.require_permission(ctx.role, rbac.MEMBERS_MANAGE)
    stmt = (
        select(Invitation)
        .where(
            Invitation.organization_id == ctx.org.id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > _now(),
        )
        .order_by(Invitation.created_at.desc())
    )
    invitations = list((await session.execute(stmt)).scalars().all())
    # One lookup for the whole page, not one per row.
    registered: set[str] = set()
    if invitations:
        emails = [i.email for i in invitations]
        registered = set(
            (await session.execute(select(User.email).where(User.email.in_(emails)))).scalars().all()
        )
    return [
        schemas.InvitationOut(
            id=i.id,
            email=i.email,
            role=i.role,
            expires_at=i.expires_at,
            created_at=i.created_at,
            account_exists=i.email in registered,
        )
        for i in invitations
    ]


async def regenerate_invitation_link(
    session: AsyncSession, ctx: OrgContext, invitation_id: uuid.UUID
) -> schemas.InvitationLinkOut:
    """Mint a fresh acceptance link for a pending invitation, for sending by hand.

    Needed because the emailed link can go astray — a spam filter, a typo'd address, or an
    unconfigured SMTP relay (the default `EMAIL_BACKEND=console` delivers to nobody). Without
    this an admin has no way to get an invited client into the org.

    Minting is unavoidable rather than a choice: `token_hash` is a one-way hash, so the original
    token cannot be read back out. **The previously sent link stops working**, which is why the
    UI has to warn before calling this, and the expiry clock restarts.

    Deliberately available in production, unlike `create_invitation`'s `accept_token` (withheld
    there so a token is never an incidental part of a response body). This one is an explicit,
    permissioned, audited action whose entire purpose is to hand the link to a human.
    """
    rbac.require_permission(ctx.role, rbac.MEMBERS_MANAGE)
    invitation = await session.get(Invitation, invitation_id)
    if (
        invitation is None
        or invitation.organization_id != ctx.org.id
        or invitation.accepted_at is not None
        or invitation.expires_at <= _now()
    ):
        raise AppError("org.invitation_not_found", "That invitation is no longer pending.", 404)

    raw = generate_opaque_token()
    invitation.token_hash = hash_token(raw)
    invitation.expires_at = _now() + INVITE_TTL
    await session.flush()

    await _write_audit(
        session, ctx.org.id, ctx.user.id, "invitation.link_regenerated",
        target_type="invitation", target_id=str(invitation.id), meta={"email": invitation.email},
    )
    return schemas.InvitationLinkOut(
        accept_url=f"{settings.web_base_url}/invitations/accept?token={raw}",
        expires_at=invitation.expires_at,
    )


async def preview_invitation(session: AsyncSession, token: str) -> schemas.InvitationPreview:
    """Read an invitation without redeeming it, for the acceptance page.

    Unauthenticated by necessity — the invitee usually has no session yet, and knowing whether
    they already have an account is precisely what decides whether the page should offer sign-in
    or signup. Without it the page defaults to signup and anyone with an existing account walks
    into `auth.email_taken` with no way forward.

    The lookup deliberately matches `accept_invitation`'s exactly, so a spent, revoked, expired
    or invented token are indistinguishable from here.
    """
    stmt = select(Invitation).where(
        Invitation.token_hash == hash_token(token),
        Invitation.accepted_at.is_(None),
        Invitation.expires_at > _now(),
    )
    invitation = (await session.execute(stmt)).scalar_one_or_none()
    if invitation is None:
        raise AppError("org.invitation_invalid", "Invalid or expired invitation.", 400)

    org = await session.get(Organization, invitation.organization_id)
    if org is None or org.deleted_at is not None:
        raise AppError("org.not_found", "Organization not found.", 404)

    account = (
        await session.execute(select(User.id).where(User.email == invitation.email))
    ).scalar_one_or_none()
    return schemas.InvitationPreview(
        organization_name=org.name,
        role=invitation.role,
        email=invitation.email,
        account_exists=account is not None,
        expires_at=invitation.expires_at,
    )


async def accept_invitation(session: AsyncSession, user: User, token: str) -> schemas.OrgOut:
    stmt = select(Invitation).where(
        Invitation.token_hash == hash_token(token),
        Invitation.accepted_at.is_(None),
        Invitation.expires_at > _now(),
    )
    invitation = (await session.execute(stmt)).scalar_one_or_none()
    if invitation is None:
        raise AppError("org.invitation_invalid", "Invalid or expired invitation.", 400)
    if invitation.email != user.email.lower():
        raise AppError("org.invite_email_mismatch", "This invitation was sent to a different email.", 403)

    org = await session.get(Organization, invitation.organization_id)
    if org is None or org.deleted_at is not None:
        raise AppError("org.not_found", "Organization not found.", 404)

    existing = (
        await session.execute(
            select(Membership).where(
                Membership.organization_id == org.id, Membership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "active":
        existing.role = invitation.role
    else:
        # A brand-new membership, or one being reactivated, adds a seat — gate it here too
        # (docs/22 §11): gating only at invite-creation lets an org pre-invite past its cap.
        await usage.require_team_member_slot(session, org)
        if existing is not None:
            existing.status = "active"
            existing.role = invitation.role
        else:
            session.add(
                Membership(organization_id=org.id, user_id=user.id, role=invitation.role, status="active")
            )
    invitation.accepted_at = _now()
    await _write_audit(
        session, org.id, user.id, "invitation.accepted",
        target_type="invitation", target_id=str(invitation.id),
    )
    return _org_out(org, invitation.role)


async def revoke_invitation(session: AsyncSession, ctx: OrgContext, invitation_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.MEMBERS_MANAGE)
    invitation = await session.get(Invitation, invitation_id)
    if invitation is None or invitation.organization_id != ctx.org.id:
        raise AppError("org.invitation_not_found", "Invitation not found.", 404)
    await session.delete(invitation)
    await _write_audit(
        session, ctx.org.id, ctx.user.id, "invitation.revoked",
        target_type="invitation", target_id=str(invitation_id),
    )


async def audit_count(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Helper for tests/analytics: number of audit entries for an org."""
    stmt = select(func.count()).select_from(AuditLog).where(AuditLog.organization_id == org_id)
    return int((await session.execute(stmt)).scalar_one())

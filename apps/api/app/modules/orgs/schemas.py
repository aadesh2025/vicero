"""Organization / membership / invitation schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.chat.pii import classify_contact
from app.core.plans import PlanStatus
from app.core.rbac import ASSIGNABLE_ROLES


class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class UpdateOrgRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=1024)
    settings: dict[str, Any] | None = None
    #: Detect contact details customers share in chat and file them in the CRM.
    auto_crm_capture_enabled: bool | None = None
    #: Contact details the agent may share with visitors (docs/11 Phase B, ADR-053/056).
    #: Anything else that looks like a contact detail is redacted from replies.
    public_contacts: list[str] | None = Field(default=None, max_length=50)

    @field_validator("public_contacts")
    @classmethod
    def _validate_contacts(cls, v: list[str] | None) -> list[str] | None:
        """Each entry must be something the redactor can actually recognise.

        Rejecting a URL or free text is deliberate rather than unhelpful: output redaction
        only acts on emails and phone numbers, so any other entry would sit in the list
        looking configured while doing nothing. A silently inert safety setting is worse
        than an error message.
        """
        if v is None:
            return None
        cleaned: list[str] = []
        for raw in v:
            entry = (raw or "").strip()
            if not entry:
                continue
            if classify_contact(entry) is None:
                raise ValueError(
                    f"{entry!r} is not a valid email address or phone number. Only contacts the "
                    f"reply filter can recognise may be allowlisted."
                )
            if entry not in cleaned:
                cleaned.append(entry)
        return cleaned


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    avatar_url: str | None
    role: str
    auto_crm_capture_enabled: bool = True
    public_contacts: list[str] = []
    created_at: dt.datetime
    updated_at: dt.datetime


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None
    avatar_url: str | None
    role: str
    status: str
    joined_at: dt.datetime


class ChangeRoleRequest(BaseModel):
    role: str = Field(pattern="^(admin|editor|viewer|operator)$")


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(admin|editor|viewer|operator)$")


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: str
    expires_at: dt.datetime
    created_at: dt.datetime
    # Raw accept token — returned ONLY outside production (email delivers it in prod).
    # Lets local/dev/CI accept an invite without a live SMTP inbox.
    accept_token: str | None = None
    # Whether this address already has a Vicero account, so the admin knows to tell them
    # "sign in with your usual password" rather than "create an account".
    account_exists: bool = False


class InvitationPreview(BaseModel):
    """What an invitation says, readable without redeeming it.

    Lets the accept page name the org and role, prefill the invited address, and open in the
    right mode instead of defaulting to signup and failing for anyone who already has an account.

    `account_exists` tells a token holder whether that address is registered. They already hold a
    single-use token that was emailed to it, so this is not an enumeration oracle — but it is a
    disclosure, which is why the route is rate-limited and returns the same opaque
    `org.invitation_invalid` for a spent, revoked, expired or fabricated token.
    """

    organization_name: str
    role: str
    email: EmailStr
    account_exists: bool
    expires_at: dt.datetime


class InvitationLinkOut(BaseModel):
    """A freshly minted acceptance link for a pending invitation.

    Invitation tokens are stored hashed, so the original link cannot be read back — issuing one
    necessarily mints a new token and **invalidates any link already sent**. Callers must say so.
    """

    accept_url: str
    expires_at: dt.datetime


class TransferOwnershipRequest(BaseModel):
    user_id: uuid.UUID


class MessageResponse(BaseModel):
    message: str


class PlanStatusOut(BaseModel):
    """The org's plan as the dashboard needs it (docs/18 §8). Limits come from `core.plans`."""

    plan: str
    status: PlanStatus
    trial_ends_at: dt.datetime | None
    days_left: int | None
    expired_reason: Literal["time", "messages"] | None
    messages_used: int
    messages_limit: int | None
    messages_remaining: int | None
    #: Visitor messages saved but not answered because the plan ran out.
    unanswered_messages: int
    agents_used: int
    max_agents: int | None
    can_create_agent: bool
    features: dict[str, bool]


ASSIGNABLE = set(ASSIGNABLE_ROLES)

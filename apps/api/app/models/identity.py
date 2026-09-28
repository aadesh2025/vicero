"""Identity & tenancy models."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey, normalize_email

_UUID = PgUUID(as_uuid=True)


class User(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    #: The address with cosmetic variants collapsed (case, Gmail dots, `+tag`) — what "one trial
    #: per person" is checked against. Indexed, deliberately not UNIQUE: legacy rows could collide
    #: and a constraint failing mid-migration would strand the deploy. See modules/auth/policy.py.
    email_normalized: Mapped[str | None] = mapped_column(String(320), index=True)
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str | None] = mapped_column(String(255))  # null for oauth-only
    full_name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: A machine account, not a person — e.g. `PROVISION_STAFF_EMAIL`, the login
    #: `scripts/provision-client.mjs` signs in as. Hidden from the admin Users roster by
    #: default: it is a working credential, so deleting it to tidy the list would break
    #: provisioning, but it is also not a human anyone needs to see next to real operators.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    @validates("email")
    def _keep_normalized_in_step(self, _key: str, value: str) -> str:
        """Every path that sets an email — signup, OAuth, seed, a future script — gets its
        normalised form for free, so the one-trial-per-person check cannot be bypassed by a code
        path that forgot to fill it in."""
        self.email_normalized = normalize_email(value)
        return value


class OAuthAccount(Base, UUIDPrimaryKey):
    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_account_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # google|github|facebook
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_enc: Mapped[str | None] = mapped_column(String)
    refresh_token_enc: Mapped[str | None] = mapped_column(String)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MagicLinkToken(Base, UUIDPrimaryKey):
    __tablename__ = "magic_link_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PasswordResetToken(Base, UUIDPrimaryKey):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EmailVerificationToken(Base, UUIDPrimaryKey):
    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Session(Base, UUIDPrimaryKey):
    """Refresh-token session."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Organization(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    #: `trial` (self-serve signups) | `legacy` (everyone provisioned before self-serve — unlimited).
    #: Defaults to `legacy` on purpose: only the signup path writes `trial`, so a path that
    #: forgets to set a plan can never lock a client out. `trial_expired` is computed, not stored
    #: (app/core/plans.py). Every limit lives there; nothing else reads this column.
    plan: Mapped[str] = mapped_column(
        String(32), default="legacy", server_default="legacy", nullable=False
    )
    trial_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: Detect names/emails/phones customers share in chat and file them in the CRM.
    #: Org-wide rather than per-agent: a client thinks about their business's CRM, not
    #: about which bot happened to take the message. On by default, with an off switch.
    auto_crm_capture_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Emails / phone numbers / URLs the agent may share freely (docs/11 Phase B). Everything
    #: else that looks like a contact detail is redacted from replies before a visitor sees it.
    #:
    #: An explicit column rather than a key in `settings`, because this is a security control:
    #: in the generic bag an unrelated settings write could clobber it, and nothing in the model
    #: would say it existed. Empty means the agent shares no contact details at all, which is
    #: the safe default but not a useful one — provisioning seeds it from the org's own details.
    public_contacts: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    #: Per-org override for the L2 injection classifier (docs/11 §4-L2). `None` follows the
    #: platform default, so an operator who never touches this tracks `GUARD_INJECTION_ENABLED`
    #: rather than being pinned to whatever it was the day their org was created. `False` is an
    #: explicit opt-out for a client on a plan that does not carry the per-turn cost.
    guard_injection_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Per-org opt-in for the agentic runtime (docs/17 Phase 1). Unlike
    #: `guard_injection_enabled`, `None`/`False` both mean "off" here — an org must be
    #: explicitly flipped to `True`, and even then only runs the loop if the platform-wide
    #: `settings.agentic_loop_enabled` is also on (app.chat.budget.agentic_loop_enabled).
    agentic_loop_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    #: Who set `plan` to what it currently is (docs/22 §5): `system` (the default nothing ever
    #: chose), `trial` (self-serve signup), `admin` (a staff grant), `stripe` (reserved, docs/23
    #: — nothing in this codebase writes it yet). Display/audit only; no access check reads it.
    plan_source: Mapped[str] = mapped_column(
        String(16), default="system", server_default="system", nullable=False
    )
    #: When a granted paid plan lapses. `NULL` = until revoked. A past value means `plan_expired`
    #: (`app/core/plans.py`), computed the same way `trial_expired` already is — never flipped by
    #: a cron, so a dead scheduler can never either lock out or fail to lock out a client.
    plan_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    plan_granted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    plan_granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    #: Why — client name, invoice number, what they paid. For the human, never read by code.
    plan_note: Mapped[str | None] = mapped_column(String(500))


class Membership(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # owner|admin|editor|viewer|operator
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class Invitation(Base, UUIDPrimaryKey):
    __tablename__ = "invitations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

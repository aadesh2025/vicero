"""Auth service — signup, login, tokens, email verification, reset, magic-link, OAuth, sessions.

Self-serve (docs/18, ADR-088): a new account gets exactly one trial workspace, in the same
transaction, through `orgs.service.ensure_self_serve_workspace`. Every path that can create a
user calls `_after_new_account` — signup, magic-link (at first verified sign-in) and OAuth.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.crypto import encrypt
from app.core.email import EmailMessage, queue_email
from app.core.email_templates import (
    magic_link_email,
    password_reset_email,
    verification_email,
)
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.plans import get_entitlements
from app.core.ratelimit import limiter
from app.core.security import (
    ALGORITHM,
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import (
    EmailVerificationToken,
    MagicLinkToken,
    Membership,
    OAuthAccount,
    Organization,
    PasswordResetToken,
    Session,
    User,
)
from app.modules.auth import disposable, policy, schemas
from app.modules.auth.oauth import OAuthUser

log = get_logger("auth")

VERIFY_TTL = dt.timedelta(hours=24)
RESET_TTL = dt.timedelta(hours=2)
MAGIC_TTL = dt.timedelta(minutes=15)
REFRESH_TTL = dt.timedelta(days=30)
# How long after a rotation a late duplicate is still told "just rotated" (keep your cookie) instead of
# "dead". Must outlast a slow commit (observed ~12 s here). A logout inside it reads the same; harmless.
REFRESH_ROTATION_GRACE = dt.timedelta(seconds=60)
OAUTH_PENDING_TTL = dt.timedelta(minutes=15)

# Per-email throttles for endpoints that send mail or accept credentials. The per-IP limits are
# route dependencies; these are the other half, so rotating IPs does not lift them. Keyed on the
# address the caller typed — never on whether an account exists — so a 429 leaks nothing.
_EMAIL_LIMIT = 5
_EMAIL_WINDOW = 3600

# Verified against on unknown accounts so a wrong email costs the same time as a wrong password
# (otherwise response time separates "no such account" from "bad password").
_DUMMY_HASH = hash_password("vicero-timing-equaliser")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _user_out(user: User) -> schemas.UserOut:
    return schemas.UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        is_staff=user.is_staff,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )


async def _issue_tokens(
    session: AsyncSession, user: User, user_agent: str | None, ip: str | None
) -> schemas.TokenPair:
    refresh = generate_opaque_token()
    row = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh),
        user_agent=user_agent,
        ip=ip,
        expires_at=_now() + REFRESH_TTL,
    )
    session.add(row)
    await session.flush()
    # The access token carries its session id so `list_sessions` can flag this device.
    return schemas.TokenPair(
        access_token=create_access_token(user.id, row.id), refresh_token=refresh
    )


async def _auth_response(
    session: AsyncSession, user: User, user_agent: str | None, ip: str | None
) -> schemas.AuthResponse:
    tokens = await _issue_tokens(session, user, user_agent, ip)
    return schemas.AuthResponse(
        access_token=tokens.access_token, refresh_token=tokens.refresh_token, user=_user_out(user)
    )


async def _send_email(to: str, template: tuple[str, str, str]) -> None:
    subject, text, html = template
    await queue_email(EmailMessage(to=to, subject=subject, body=text, html_body=html))


async def _get_user_by_email(session: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


# ── Abuse controls (docs/18 §3, §10) ──────────────────────────────────────────
async def _throttle_email(action: str, email: str) -> None:
    allowed, retry = await limiter.hit(f"rl:{action}-email:{email.lower()}", _EMAIL_LIMIT, _EMAIL_WINDOW)
    if not allowed:
        raise AppError(
            "auth.too_many_requests",
            "Too many requests for this address. Try again later.",
            429,
            details={"retry_after": retry},
        )


def _check_password(password: str, email: str) -> None:
    """Weak-password floor. Self-serve only: it would reject passwords existing accounts hold."""
    if not settings.self_serve_enabled:
        return
    problem = policy.password_problem(password, email)
    if problem:
        raise AppError("auth.weak_password", problem, 400)


async def _guard_new_account(session: AsyncSession, email: str, ip: str | None) -> None:
    """One trial per person, and not from a throwaway mailbox or one network in bulk."""
    if not settings.self_serve_enabled:
        return
    if settings.block_disposable_emails and await disposable.is_listed(email.rpartition("@")[2]):
        raise AppError(
            "auth.email_not_allowed", "Please sign up with a permanent email address.", 400
        )
    norm = policy.normalize_email(email)
    dup = select(User.id).where(User.email_normalized == norm, User.deleted_at.is_(None)).limit(1)
    if (await session.execute(dup)).scalar_one_or_none() is not None:
        raise AppError("auth.email_taken", "An account with this email already exists.", 409)
    cap = settings.signups_per_ip_per_day
    if cap > 0 and ip:
        allowed, _ = await limiter.hit(f"rl:signup-ip:{ip}", cap, 86400)
        if not allowed:
            raise AppError(
                "auth.signup_rate_limited",
                "Too many accounts have been created from this network today. Try again tomorrow.",
                429,
            )


async def _after_new_account(session: AsyncSession, user: User, method: str) -> Organization | None:
    """Provision the trial workspace and write the signup audit row (needs an org to attach to)."""
    # Imported here, not at module level: `orgs` already depends on `auth` (its deps resolve the
    # current user), so a top-level import back would close a package cycle that
    # tests/test_architecture.py rejects. A function-level import is the codebase's way of
    # breaking one.
    from app.modules.orgs.service import ensure_self_serve_workspace

    org = await ensure_self_serve_workspace(session, user)
    if org is not None:
        meta: dict[str, Any] = {"method": method}
        # Soft signal, recorded and never acted on here (modules/auth/disposable.py): a domain
        # with no mail servers, or mail servers on a listed throwaway service.
        signal = await disposable.mx_signal(user.email.rpartition("@")[2])
        if signal:
            meta["email_risk"] = signal
            log.warning("signup_email_risk", user_id=str(user.id), domain=user.email.rpartition("@")[2], signal=signal)
        await write_audit(
            session, org.id, user.id, "auth.signup", target_type="user", target_id=str(user.id), meta=meta,
        )
    return org


async def _audit_login(session: AsyncSession, user: User, method: str, ip: str | None) -> None:
    """Login audit on metered (trial) workspaces. Existing client tenants' logs are left as they were."""
    stmt = (
        select(Organization)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user.id, Membership.status == "active")
    )
    for org in (await session.execute(stmt)).scalars().all():
        if get_entitlements(org.plan).is_metered:
            await write_audit(
                session, org.id, user.id, "auth.login", target_type="user", target_id=str(user.id),
                meta={"method": method}, ip=ip,
            )


# ── Signup / login ────────────────────────────────────────────────────────────
async def signup(
    session: AsyncSession, data: schemas.SignupRequest, user_agent: str | None, ip: str | None
) -> schemas.AuthResponse:
    email = data.email.lower()
    await _throttle_email("signup", email)
    _check_password(data.password, email)
    # Deliberately distinguishable: a signup that hands back a session cannot also be
    # indistinguishable from a refusal. Mitigated by the per-IP and per-email limits above and
    # documented as a residual in docs/SECURITY.md; login, reset, magic-link and resend never
    # reveal whether an address has an account.
    if await _get_user_by_email(session, email):
        raise AppError("auth.email_taken", "An account with this email already exists.", 409)
    await _guard_new_account(session, email, ip)

    user = User(email=email, password_hash=hash_password(data.password), full_name=data.full_name)
    session.add(user)
    await session.flush()

    await _create_and_send_verification(session, user)
    await _after_new_account(session, user, "password")
    return await _auth_response(session, user, user_agent, ip)


async def login(
    session: AsyncSession, data: schemas.LoginRequest, user_agent: str | None, ip: str | None
) -> schemas.AuthResponse:
    email = data.email.lower()
    lock_key = f"rl:login-fail:{email}"
    if await limiter.count(lock_key, settings.login_lockout_window) >= settings.login_lockout_failures:
        raise AppError(
            "auth.too_many_attempts",
            "Too many failed sign-in attempts. Wait a while, or reset your password.",
            429,
        )
    user = await _get_user_by_email(session, email)
    # Always run one Argon2 verification, against a dummy hash for unknown/passwordless accounts.
    password_ok = verify_password(
        data.password, user.password_hash if user and user.password_hash else _DUMMY_HASH
    )
    if user is None or not user.password_hash or not password_ok:
        await limiter.hit(lock_key, settings.login_lockout_failures, settings.login_lockout_window)
        raise AppError("auth.invalid_credentials", "Incorrect email or password.", 401)
    if not user.is_active:
        raise AppError("auth.account_inactive", "This account is disabled.", 403)

    user.last_login_at = _now()
    await _audit_login(session, user, "password", ip)
    return await _auth_response(session, user, user_agent, ip)


async def refresh(
    session: AsyncSession, refresh_token: str, user_agent: str | None, ip: str | None
) -> schemas.TokenPair:
    now = _now()
    token_hash = hash_token(refresh_token)
    # Claim the token with ONE atomic statement. Reading the row and then writing `revoked_at` let two
    # simultaneous refreshes both see "not revoked" and both succeed, and this host's slow commits
    # stretched that window to ~10 s (live incident 2026-10-07). With UPDATE ... RETURNING a second
    # caller blocks on the row lock until the first commits, re-checks `revoked_at IS NULL`, matches
    # nothing, and gets a clean 401. Exactly one caller can ever claim a token.
    claimed_user_id = (
        await session.execute(
            update(Session)
            .where(
                Session.refresh_token_hash == token_hash,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
            .values(revoked_at=now)  # rotate: the old refresh token is now dead
            .returning(Session.user_id)
        )
    ).scalar_one_or_none()
    if claimed_user_id is None:
        # Tell "another request just rotated this exact token" (the browser already holds, or is about
        # to hold, the new one: the caller must KEEP its cookie) from a token that is truly dead
        # (expired, revoked long ago, unknown: the caller may drop its cookie).
        prior = (
            await session.execute(
                select(Session.id, Session.user_id, Session.revoked_at).where(
                    Session.refresh_token_hash == token_hash
                )
            )
        ).one_or_none()
        if prior is not None and prior.revoked_at is not None:
            if now - prior.revoked_at <= REFRESH_ROTATION_GRACE:
                raise AppError(
                    "auth.refresh_rotated",
                    "This refresh token was just rotated by another request.",
                    401,
                )
            # A token that was rotated away (or logged out) long ago is being presented again: a stale
            # client at best, a stolen token at worst. It gets nothing (its session is already revoked,
            # and no new tokens are issued); leave a security trail. Ids only, never the token or its hash.
            log.warning(
                "refresh_token_reuse",
                user_id=str(prior.user_id),
                session_id=str(prior.id),
                revoked_seconds_ago=int((now - prior.revoked_at).total_seconds()),
                ip=ip,
            )
        raise AppError("auth.invalid_token", "Invalid or expired refresh token.", 401)

    user = await session.get(User, claimed_user_id)
    if user is None or not user.is_active:
        raise AppError("auth.invalid_token", "Invalid or expired refresh token.", 401)
    return await _issue_tokens(session, user, user_agent, ip)


async def logout(session: AsyncSession, user: User, refresh_token: str | None) -> None:
    if refresh_token:
        stmt = select(Session).where(
            Session.refresh_token_hash == hash_token(refresh_token),
            Session.user_id == user.id,
            Session.revoked_at.is_(None),
        )
        s = (await session.execute(stmt)).scalar_one_or_none()
        if s is not None:
            s.revoked_at = _now()
        return
    # No token supplied → revoke every active session (log out everywhere).
    stmt = select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))
    for s in (await session.execute(stmt)).scalars().all():
        s.revoked_at = _now()


async def me(session: AsyncSession, user: User) -> schemas.MeResponse:
    stmt = (
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user.id, Membership.status == "active")
    )
    rows = (await session.execute(stmt)).all()
    memberships = [
        schemas.MembershipOut(
            organization_id=org.id,
            organization_name=org.name,
            organization_slug=org.slug,
            role=m.role,
            status=m.status,
        )
        for m, org in rows
    ]
    return schemas.MeResponse(user=_user_out(user), memberships=memberships)


# ── Email verification ────────────────────────────────────────────────────────
async def _create_and_send_verification(session: AsyncSession, user: User) -> None:
    raw = generate_opaque_token()
    session.add(
        EmailVerificationToken(
            user_id=user.id, token_hash=hash_token(raw), expires_at=_now() + VERIFY_TTL
        )
    )
    link = f"{settings.web_base_url}/verify-email?token={raw}"
    await _send_email(user.email, verification_email(link, raw))


async def verify_email(session: AsyncSession, token: str) -> None:
    stmt = select(EmailVerificationToken).where(
        EmailVerificationToken.token_hash == hash_token(token),
        EmailVerificationToken.used_at.is_(None),
        EmailVerificationToken.expires_at > _now(),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AppError("auth.invalid_token", "Invalid or expired verification token.", 400)
    row.used_at = _now()
    user = await session.get(User, row.user_id)
    if user is not None and user.email_verified_at is None:
        user.email_verified_at = _now()


async def resend_verification(session: AsyncSession, email: str) -> None:
    await _throttle_email("resend", email)
    user = await _get_user_by_email(session, email)
    if user is not None and user.email_verified_at is None:
        await _create_and_send_verification(session, user)


# ── Password reset ────────────────────────────────────────────────────────────
async def forgot_password(session: AsyncSession, email: str) -> None:
    await _throttle_email("forgot", email)
    user = await _get_user_by_email(session, email)
    if user is None:
        return  # do not reveal whether the account exists
    raw = generate_opaque_token()
    session.add(
        PasswordResetToken(user_id=user.id, token_hash=hash_token(raw), expires_at=_now() + RESET_TTL)
    )
    link = f"{settings.web_base_url}/reset-password?token={raw}"
    await _send_email(user.email, password_reset_email(link, raw))


async def reset_password(session: AsyncSession, token: str, new_password: str) -> None:
    stmt = select(PasswordResetToken).where(
        PasswordResetToken.token_hash == hash_token(token),
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > _now(),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AppError("auth.invalid_token", "Invalid or expired reset token.", 400)
    user = await session.get(User, row.user_id)
    if user is None:
        raise AppError("auth.invalid_token", "Invalid or expired reset token.", 400)
    _check_password(new_password, user.email)
    row.used_at = _now()
    user.password_hash = hash_password(new_password)
    # Revoke all sessions so a compromised session can't survive a reset.
    active = select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))
    for s in (await session.execute(active)).scalars().all():
        s.revoked_at = _now()


# ── Magic link (passwordless) ─────────────────────────────────────────────────
async def magic_link(session: AsyncSession, email: str, ip: str | None = None) -> None:
    email = email.lower()
    await _throttle_email("magic", email)
    user = await _get_user_by_email(session, email)
    if user is None:
        # Same guards as any other new account. Refusing here is a policy about the *address*
        # (disposable domain / one-trial-per-person), not a statement about whether an account
        # exists, but the per-IP cap is checked only when we would really create one.
        await _guard_new_account(session, email, ip)
        user = User(email=email)  # passwordless signup — workspace is provisioned on first verified sign-in
        session.add(user)
        await session.flush()
    raw = generate_opaque_token()
    session.add(
        MagicLinkToken(user_id=user.id, token_hash=hash_token(raw), expires_at=_now() + MAGIC_TTL)
    )
    link = f"{settings.web_base_url}/magic?token={raw}"
    await _send_email(user.email, magic_link_email(link, raw))


async def magic_link_verify(
    session: AsyncSession, token: str, user_agent: str | None, ip: str | None
) -> schemas.AuthResponse:
    stmt = select(MagicLinkToken).where(
        MagicLinkToken.token_hash == hash_token(token),
        MagicLinkToken.used_at.is_(None),
        MagicLinkToken.expires_at > _now(),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AppError("auth.invalid_token", "Invalid or expired sign-in link.", 400)
    row.used_at = _now()
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AppError("auth.invalid_token", "Invalid or expired sign-in link.", 400)
    if user.email_verified_at is None:
        user.email_verified_at = _now()  # clicking the emailed link proves the address
    # Provisioned here, not when the link was requested: nobody has proved the address yet, and
    # a stranger typing someone else's email must not get a workspace created for it.
    await _after_new_account(session, user, "magic_link")
    user.last_login_at = _now()
    await _audit_login(session, user, "magic_link", ip)
    return await _auth_response(session, user, user_agent, ip)


# ── Sessions ──────────────────────────────────────────────────────────────────
async def list_sessions(
    session: AsyncSession, user: User, current_session_id: uuid.UUID | None = None
) -> list[schemas.SessionOut]:
    stmt = (
        select(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .order_by(Session.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        schemas.SessionOut(
            id=s.id,
            user_agent=s.user_agent,
            ip=s.ip,
            created_at=s.created_at,
            expires_at=s.expires_at,
            # From the caller's own access token (`sid`). None for tokens minted before
            # that claim existed, in which case no row is flagged.
            current=current_session_id is not None and s.id == current_session_id,
        )
        for s in rows
    ]


async def revoke_session(session: AsyncSession, user: User, session_id: uuid.UUID) -> None:
    s = await session.get(Session, session_id)
    if s is None or s.user_id != user.id:
        raise AppError("auth.session_not_found", "Session not found.", 404)
    s.revoked_at = _now()


# ── OAuth ─────────────────────────────────────────────────────────────────────
def _sign(claims: dict[str, Any], kind: str, ttl: dt.timedelta) -> str:
    now = _now()
    body = {**claims, "type": kind, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
    return str(jwt.encode(body, settings.secret_key, algorithm=ALGORITHM))


def _unsign(token: str, kind: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise AppError("auth.invalid_token", "This link is invalid or has expired.", 400) from None
    if claims.get("type") != kind:
        raise AppError("auth.invalid_token", "This link is invalid or has expired.", 400)
    return claims


async def _oauth_sign_in(
    session: AsyncSession, user: User, method: str, user_agent: str | None, ip: str | None
) -> schemas.AuthResponse:
    if not user.is_active:
        raise AppError("auth.account_inactive", "This account is disabled.", 403)
    user.last_login_at = _now()
    await _audit_login(session, user, method, ip)
    return await _auth_response(session, user, user_agent, ip)


async def _link_or_create(
    session: AsyncSession,
    profile: OAuthUser,
    user_agent: str | None,
    ip: str | None,
    *,
    mailbox_proven: bool = False,
) -> schemas.AuthResponse:
    """Attach `profile` to the account that owns its (verified) email, creating one if none does.

    **Never auto-links to an unverified account.** If a password signup for this address was never
    verified, whoever made it may not own the mailbox, and linking a provider identity to it
    would hand them a login to the real owner's account. `mailbox_proven` is set only when the
    caller has just had the user click a link we mailed to that very address.
    """
    assert profile.email is not None
    email = profile.email.lower()
    user = await _get_user_by_email(session, email)
    created = False
    if user is None:
        await _guard_new_account(session, email, ip)
        user = User(
            email=email,
            full_name=profile.full_name,
            avatar_url=profile.avatar_url,
            email_verified_at=_now(),  # the provider (or the mailed link) vouches for the address
        )
        session.add(user)
        await session.flush()
        created = True
    elif user.email_verified_at is None:
        if not mailbox_proven:
            raise AppError(
                "auth.oauth_email_unverified",
                "An account with this email exists but hasn't been verified. Verify it from the "
                "email we sent when you signed up, or sign in with your password.",
                409,
            )
        user.email_verified_at = _now()
    session.add(
        OAuthAccount(
            user_id=user.id,
            provider=profile.provider,
            provider_account_id=profile.provider_account_id,
            access_token_enc=encrypt(profile.access_token) if profile.access_token else None,
            refresh_token_enc=encrypt(profile.refresh_token) if profile.refresh_token else None,
            expires_at=profile.expires_at,
        )
    )
    await session.flush()
    if created:
        await _after_new_account(session, user, f"oauth:{profile.provider}")
    return await _oauth_sign_in(session, user, f"oauth:{profile.provider}", user_agent, ip)


async def oauth_login(
    session: AsyncSession, profile: OAuthUser, user_agent: str | None, ip: str | None
) -> schemas.AuthResponse | schemas.OAuthPendingResponse:
    """Complete a provider sign-in, or ask the caller to collect an email first."""
    stmt = select(OAuthAccount).where(
        OAuthAccount.provider == profile.provider,
        OAuthAccount.provider_account_id == profile.provider_account_id,
    )
    account = (await session.execute(stmt)).scalar_one_or_none()
    if account is not None:  # already linked: sign in, whatever the provider now says about email
        user = await session.get(User, account.user_id)
        if user is None:
            raise AppError("auth.invalid_token", "Linked account is missing.", 401)
        return await _oauth_sign_in(session, user, f"oauth:{profile.provider}", user_agent, ip)

    if not profile.email or not profile.email_verified:
        # No usable email (Facebook phone signups, private GitHub) or one the provider does not
        # vouch for. Nothing is created or linked; the user proves an address by mail first.
        pending = _sign(
            {
                "provider": profile.provider,
                "pid": profile.provider_account_id,
                "name": profile.full_name,
                "avatar": profile.avatar_url,
            },
            "oauth_pending",
            OAUTH_PENDING_TTL,
        )
        return schemas.OAuthPendingResponse(pending_token=pending, provider=profile.provider)
    return await _link_or_create(session, profile, user_agent, ip)


async def oauth_request_email(session: AsyncSession, pending_token: str, email: str) -> None:
    """Step 2 for a provider that gave no verified email: mail a single-use link to the address."""
    claims = _unsign(pending_token, "oauth_pending")
    email = email.lower()
    await _throttle_email("oauth-email", email)
    if (
        settings.self_serve_enabled
        and settings.block_disposable_emails
        and await disposable.is_listed(email.rpartition("@")[2])
    ):
        raise AppError("auth.email_not_allowed", "Please use a permanent email address.", 400)
    proof = _sign(
        {
            "provider": claims["provider"],
            "pid": claims["pid"],
            "name": claims.get("name"),
            "avatar": claims.get("avatar"),
            "email": email,
            "jti": uuid.uuid4().hex,
        },
        "oauth_email",
        OAUTH_PENDING_TTL,
    )
    link = f"{settings.web_base_url}/oauth/verify?token={proof}"
    await _send_email(email, magic_link_email(link, proof))


async def oauth_verify_email(
    session: AsyncSession, token: str, user_agent: str | None, ip: str | None
) -> schemas.AuthResponse:
    """Step 3: the user clicked the mailed link, which proves they control the address."""
    claims = _unsign(token, "oauth_email")
    # Single use, on the same limiter every other counter uses: the first hit is allowed.
    fresh, _ = await limiter.hit(f"rl:once:{claims['jti']}", 1, int(OAUTH_PENDING_TTL.total_seconds()))
    if not fresh:
        raise AppError("auth.invalid_token", "This link is invalid or has expired.", 400)
    profile = OAuthUser(
        provider=claims["provider"],
        provider_account_id=claims["pid"],
        email=claims["email"],
        full_name=claims.get("name"),
        avatar_url=claims.get("avatar"),
        access_token=None,
        refresh_token=None,
        expires_at=None,
        email_verified=True,
    )
    stmt = select(OAuthAccount).where(
        OAuthAccount.provider == profile.provider,
        OAuthAccount.provider_account_id == profile.provider_account_id,
    )
    account = (await session.execute(stmt)).scalar_one_or_none()
    if account is not None:
        user = await session.get(User, account.user_id)
        if user is None:
            raise AppError("auth.invalid_token", "Linked account is missing.", 401)
        return await _oauth_sign_in(session, user, f"oauth:{profile.provider}", user_agent, ip)
    return await _link_or_create(session, profile, user_agent, ip, mailbox_proven=True)

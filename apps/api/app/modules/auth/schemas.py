"""Auth request/response schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    avatar_url: str | None
    is_staff: bool
    email_verified: bool
    created_at: dt.datetime


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    role: str
    status: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


class MessageResponse(BaseModel):
    message: str


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerifyRequest(BaseModel):
    token: str


class SessionOut(BaseModel):
    id: uuid.UUID
    user_agent: str | None
    ip: str | None
    created_at: dt.datetime
    expires_at: dt.datetime
    current: bool


class OAuthAuthorizeResponse(BaseModel):
    authorize_url: str


class OAuthPendingResponse(BaseModel):
    """The provider gave us no verified email. Nothing was created; collect one and verify it."""

    needs_email: bool = True
    pending_token: str
    provider: str


class OAuthEmailRequest(BaseModel):
    pending_token: str
    email: EmailStr


class OAuthEmailVerifyRequest(BaseModel):
    token: str


class OAuthExchangeRequest(BaseModel):
    code: str

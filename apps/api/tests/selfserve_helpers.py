"""Shared helpers for the self-serve / free-trial tests (docs/18). Not a test module."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import get_email_backend
from app.models import Membership, Organization, User

STRONG = "Tr1al-Str0ng-Pass!"


def unique_email(prefix: str = "u") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@example.com"


def bearer(auth: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


async def signup(client: AsyncClient, email: str | None = None, password: str = STRONG) -> dict[str, Any]:
    email = email or unique_email()
    resp = await client.post(
        "/v1/auth/signup", json={"email": email, "password": password, "full_name": "Trial Tester"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def org_id_of(client: AsyncClient, auth: dict[str, Any]) -> str:
    orgs = (await client.get("/v1/orgs", headers=bearer(auth))).json()
    assert len(orgs) == 1, orgs
    return str(orgs[0]["id"])


async def org_headers(client: AsyncClient, auth: dict[str, Any]) -> dict[str, str]:
    return {**bearer(auth), "X-Org-Id": await org_id_of(client, auth)}


async def trial_org(client: AsyncClient) -> tuple[dict[str, Any], dict[str, str], str]:
    """A fresh self-serve user → (auth, org headers, org id)."""
    auth = await signup(client)
    headers = await org_headers(client, auth)
    return auth, headers, headers["X-Org-Id"]


async def verify_email(client: AsyncClient) -> None:
    """Click the verification link in the most recent verification email."""
    for msg in reversed(get_email_backend().outbox):
        if "verify" in msg.subject.lower() or "confirm" in msg.body.lower():
            token = re.search(r"Token: (\S+)", msg.body)
            assert token, msg.body
            resp = await client.post("/v1/auth/verify-email", json={"token": token.group(1)})
            assert resp.status_code == 200, resp.text
            return
    raise AssertionError("no verification email in the outbox")


async def make_agent_public(client: AsyncClient, headers: dict[str, str]) -> str:
    """Create an agent on the fake provider and return its public key."""
    agent = await client.post("/v1/agents", json={"name": "Trial Bot"}, headers=headers)
    assert agent.status_code == 201, agent.text
    aid = agent.json()["id"]
    patched = await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": "fake", "model": "fake-1"}},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    return str(agent.json()["public_key"])


async def chat(client: AsyncClient, key: str, message: str, visitor: str = "v-1") -> Any:
    return await client.post(
        f"/v1/public/agents/{key}/chat",
        json={"message": message, "stream": False, "visitor": {"id": visitor}},
    )


async def set_trial_end(db: AsyncSession, org_id: str, when: dt.datetime) -> None:
    await db.execute(
        update(Organization).where(Organization.id == uuid.UUID(org_id)).values(trial_ends_at=when)
    )
    await db.flush()


async def users_named(db: AsyncSession, email: str) -> list[User]:
    return list((await db.execute(select(User).where(User.email == email.lower()))).scalars().all())


async def owned_orgs(db: AsyncSession, user_id: uuid.UUID) -> list[Organization]:
    rows = await db.execute(
        select(Organization)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user_id, Membership.role == "owner")
    )
    return list(rows.scalars().all())

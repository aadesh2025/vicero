"""Creating an organization is staff-only — with no first-org exception.

Vicero is run as one organization per client, provisioned for them. The gate used to allow
every user their *first* org, which is what made the product self-serve: a stranger could sign
up on the public form and the create-first-org screen handed them a workspace. Now every
creation needs `is_staff`. Enforced server-side: hiding the button doesn't stop a direct API call.

Invitations are deliberately unaffected — `accept_invitation` adds a `Membership` to an org
that already exists and never reaches `create_org`.

The whole module runs with `allow_self_serve_orgs` **off**, overriding the conftest fixture
that turns it on so other suites can bootstrap a tenant. This is the file that pins the real
production rule, so it must see production's configuration.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import get_email_backend
from app.models import User


@pytest.fixture(autouse=True)
def _enforce_production_gate() -> Iterator[None]:
    # Module-level autouse runs after conftest's, so this wins for every test here.
    previous = settings.allow_self_serve_orgs
    settings.allow_self_serve_orgs = False
    yield
    settings.allow_self_serve_orgs = previous


def _last_invite_token() -> str:
    body = get_email_backend().outbox[-1].body
    m = re.search(r"Token:\s*(\S+)", body)
    assert m, body
    return m.group(1)


async def _signup(client: AsyncClient, email: str) -> str:
    r = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_staff(db_session: AsyncSession, email: str) -> None:
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.is_staff = True
    await db_session.flush()


async def test_a_brand_new_user_cannot_create_an_org(client: AsyncClient) -> None:
    """The loophole this closes: sign up on the public form, get a free workspace.

    A fresh account has zero orgs, which is exactly the state the old gate let through.
    """
    token = await _signup(client, "stranger@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    blocked = await client.post("/v1/orgs", json={"name": "Free Workspace"}, headers=headers)
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"]["code"] == "orgs.create_forbidden"

    # And they're left with nothing, rather than a half-made org.
    assert (await client.get("/v1/orgs", headers=headers)).json() == []


async def test_staff_can_create_orgs_for_clients(client: AsyncClient, db_session: AsyncSession) -> None:
    """The provisioning path — how every real client org comes into being."""
    token = await _signup(client, "staff-orgs@example.com")
    await _make_staff(db_session, "staff-orgs@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    for name in ("Client A", "Client B", "Client C"):
        r = await client.post("/v1/orgs", json={"name": name}, headers=headers)
        assert r.status_code == 201, f"{name}: {r.text}"

    assert len((await client.get("/v1/orgs", headers=headers)).json()) == 3


async def test_an_invited_client_joins_without_ever_creating_an_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The one legitimate route in for a brand-new person, and it must stay open.

    Accepting an invitation never calls `create_org`, so tightening that gate can't break it.
    """
    owner_token = await _signup(client, "owner-gate@example.com")
    await _make_staff(db_session, "owner-gate@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    org = await client.post("/v1/orgs", json={"name": "Host Org"}, headers=owner_headers)
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]

    invite = await client.post(
        f"/v1/orgs/{org_id}/invitations",
        json={"email": "invitee-gate@example.com", "role": "editor"},
        headers={**owner_headers, "X-Org-Id": org_id},
    )
    assert invite.status_code == 201, invite.text
    token = _last_invite_token()  # capture before signup adds a verification email

    invitee_token = await _signup(client, "invitee-gate@example.com")
    invitee_headers = {"Authorization": f"Bearer {invitee_token}"}
    accepted = await client.post(f"/v1/orgs/invitations/{token}/accept", headers=invitee_headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["id"] == org_id

    # They're a real member of the org they were invited to…
    assert [o["id"] for o in (await client.get("/v1/orgs", headers=invitee_headers)).json()] == [org_id]
    # …and still can't spin up one of their own.
    blocked = await client.post("/v1/orgs", json={"name": "Mine"}, headers=invitee_headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "orgs.create_forbidden"


async def test_deleting_their_only_org_leaves_a_client_with_none(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A consequence worth pinning: a client can no longer re-create after deleting.

    The old gate let them, because a user with zero orgs was allowed a "first" one. Now the
    workspace has to be provisioned again by staff — deliberate, but it means the delete-org
    button is a one-way door for a client.
    """
    staff_token = await _signup(client, "staff-del@example.com")
    await _make_staff(db_session, "staff-del@example.com")
    org = await client.post(
        "/v1/orgs", json={"name": "Temporary"}, headers={"Authorization": f"Bearer {staff_token}"}
    )
    org_id = org.json()["id"]

    client_token = await _signup(client, "client-del@example.com")
    client_headers = {"Authorization": f"Bearer {client_token}"}
    invite = await client.post(
        f"/v1/orgs/{org_id}/invitations",
        json={"email": "client-del@example.com", "role": "editor"},
        headers={"Authorization": f"Bearer {staff_token}", "X-Org-Id": org_id},
    )
    assert invite.status_code == 201, invite.text
    accepted = await client.post(
        f"/v1/orgs/invitations/{_last_invite_token()}/accept", headers=client_headers
    )
    assert accepted.status_code == 200, accepted.text

    # An editor can't delete it anyway (ORG_MANAGE is owner-only) — the staff owner does.
    gone = await client.delete(
        f"/v1/orgs/{org_id}",
        headers={"Authorization": f"Bearer {staff_token}", "X-Org-Id": org_id},
    )
    assert gone.status_code == 204, gone.text

    assert (await client.get("/v1/orgs", headers=client_headers)).json() == []
    stranded = await client.post("/v1/orgs", json={"name": "Fresh Start"}, headers=client_headers)
    assert stranded.status_code == 403
    assert stranded.json()["error"]["code"] == "orgs.create_forbidden"

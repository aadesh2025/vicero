"""Meta Data Deletion Request Callback: signature verification, request log, and the erasure."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import data_deletion
from app.core.config import settings
from app.models import Contact, Conversation, Message

SECRET = "meta-test-secret"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _body(user_id: str, secret: str = SECRET) -> dict[str, str]:
    return {"signed_request": _signed({"algorithm": "HMAC-SHA256", "user_id": user_id}, secret)}


def _signed(payload: dict[str, object], secret: str = SECRET) -> str:
    part = _b64(json.dumps(payload).encode())
    return _b64(hmac.new(secret.encode(), part.encode(), hashlib.sha256).digest()) + "." + part


@pytest.fixture(autouse=True)
def _meta_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    monkeypatch.setattr(settings, "meta_app_secret", SECRET)
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.channels.data_deletion_router.enqueue_data_deletion", lambda code, uid: queued.append((code, uid))
    )
    yield queued


async def _seed(client: AsyncClient, db_session: AsyncSession, external_id: str) -> Contact:
    email = f"del{external_id}@example.com"
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "DelOrg"}, headers={"Authorization": f"Bearer {token}"})
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    agent = await client.post("/v1/agents", json={"name": "Del Bot"}, headers=headers)
    org_id, agent_id = org.json()["id"], agent.json()["id"]
    contact = Contact(organization_id=org_id, channel="facebook", external_id=external_id, display_name="Someone")
    db_session.add(contact)
    await db_session.flush()
    convo = Conversation(organization_id=org_id, agent_id=agent_id, channel="facebook", contact_id=contact.id)
    db_session.add(convo)
    await db_session.flush()
    db_session.add(Message(conversation_id=convo.id, organization_id=org_id, role="user", content="hi"))
    await db_session.flush()
    return contact


# ── signature verification ───────────────────────────────────────────────────────
def test_parse_accepts_valid_signature() -> None:
    payload = data_deletion.parse_signed_request(_signed({"algorithm": "HMAC-SHA256", "user_id": "42"}), SECRET)
    assert payload is not None and payload["user_id"] == "42"


@pytest.mark.parametrize(
    "bad",
    [
        _signed({"algorithm": "HMAC-SHA256", "user_id": "42"}, secret="wrong-secret"),
        _signed({"algorithm": "HMAC-SHA1", "user_id": "42"}),
        "no-dot-here",
        ".",
        "abc.!!!",
        "",
    ],
)
def test_parse_rejects_bad_requests(bad: str) -> None:
    assert data_deletion.parse_signed_request(bad, SECRET) is None


def test_parse_rejects_tampered_payload() -> None:
    sig, _, _ = _signed({"algorithm": "HMAC-SHA256", "user_id": "42"}).partition(".")
    forged = _b64(json.dumps({"algorithm": "HMAC-SHA256", "user_id": "99"}).encode())
    assert data_deletion.parse_signed_request(f"{sig}.{forged}", SECRET) is None


def test_parse_fails_closed_without_secret() -> None:
    assert data_deletion.parse_signed_request(_signed({"algorithm": "HMAC-SHA256", "user_id": "42"}), "") is None


# ── endpoint ─────────────────────────────────────────────────────────────────────
async def test_valid_request_returns_url_and_queues(client: AsyncClient, _meta_secret: list[tuple[str, str]]) -> None:
    r = await client.post("/api/meta/data-deletion", data=_body("777"))
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == f"{settings.web_base_url}/data-deletion?code={body['confirmation_code']}"
    assert _meta_secret == [(body["confirmation_code"], "777")]
    status = await client.get(f"/api/meta/data-deletion/{body['confirmation_code']}")
    assert status.status_code == 200 and status.json()["status"] == "pending"


async def test_invalid_signature_is_400_and_logs_nothing(
    client: AsyncClient, _meta_secret: list[tuple[str, str]]
) -> None:
    r = await client.post(
        "/api/meta/data-deletion",
        data=_body("777", "nope"),
    )
    assert r.status_code == 400
    assert _meta_secret == []


async def test_missing_form_field_is_rejected(client: AsyncClient) -> None:
    assert (await client.post("/api/meta/data-deletion", data={})).status_code == 422


async def test_unconfigured_secret_refuses(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "meta_app_secret", "")
    r = await client.post("/api/meta/data-deletion", data=_body("1"))
    assert r.status_code == 503


async def test_unknown_code_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/meta/data-deletion/nope")).status_code == 404


# ── erasure ──────────────────────────────────────────────────────────────────────
async def test_run_request_erases_only_that_user(client: AsyncClient, db_session: AsyncSession) -> None:
    gone = await _seed(client, db_session, "900001")
    kept = await _seed(client, db_session, "900002")
    request = await data_deletion.create_request(db_session, "900001")

    assert await data_deletion.run_request(db_session, request.confirmation_code, "900001") == "completed"

    contacts = (await db_session.execute(select(Contact.id).where(Contact.id.in_([gone.id, kept.id])))).scalars().all()
    assert contacts == [kept.id]
    convos = (
        (await db_session.execute(select(Conversation.id).where(Conversation.contact_id.in_([gone.id, kept.id]))))
        .scalars()
        .all()
    )
    assert len(convos) == 1
    msgs = (await db_session.execute(select(Message.id).where(Message.conversation_id.in_(convos)))).scalars().all()
    assert len(msgs) == 1
    assert not (await db_session.execute(select(Conversation).where(Conversation.channel_user_id == "900001"))).first()

    await db_session.refresh(request)
    assert request.status == "completed" and request.records_deleted == 2 and request.completed_at is not None
    assert request.user_id_hash == data_deletion.hash_user_id("900001")

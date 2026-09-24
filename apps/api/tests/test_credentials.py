"""Phase 5: provider-credential endpoints + resolution (real DB, tx-rollback)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.fake import FakeChatProvider
from app.llm.registry import resolve_credential


async def _org_headers(client: AsyncClient, email: str = "a@example.com") -> tuple[dict[str, str], dict]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "Acme"}, headers={"Authorization": f"Bearer {token}"})
    org_json = org.json()
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org_json["id"]}, org_json


async def test_providers_catalog(client: AsyncClient) -> None:
    headers, _ = await _org_headers(client)
    resp = await client.get("/v1/credentials/providers", headers=headers)
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert {"groq", "gemini", "openai", "anthropic"} <= names
    groq = next(p for p in resp.json() if p["name"] == "groq")
    assert groq["free"] is True and "openai/gpt-oss-120b" in groq["models"]


async def test_credentials_crud_and_masking(client: AsyncClient) -> None:
    headers, _ = await _org_headers(client)

    created = await client.post(
        "/v1/credentials",
        json={"provider": "groq", "label": "prod", "api_key": "gsk_supersecret_9999", "is_default": True},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["masked_key"].endswith("9999")
    assert "supersecret" not in body["masked_key"]
    cred_id = body["id"]

    listed = await client.get("/v1/credentials", headers=headers)
    assert len(listed.json()) == 1

    deleted = await client.delete(f"/v1/credentials/{cred_id}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get("/v1/credentials", headers=headers)).json() == []


async def test_credentials_requires_org_header(client: AsyncClient) -> None:
    signup = await client.post("/v1/auth/signup", json={"email": "a@example.com", "password": "password123"})
    token = signup.json()["access_token"]
    resp = await client.get("/v1/credentials", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "org.missing_header"


async def test_credential_test_endpoint(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.credentials import service

    monkeypatch.setattr(service, "build_chat_provider", lambda *a, **k: FakeChatProvider())
    headers, _ = await _org_headers(client)
    created = await client.post(
        "/v1/credentials", json={"provider": "groq", "api_key": "x"}, headers=headers
    )
    cred_id = created.json()["id"]

    result = await client.post(f"/v1/credentials/{cred_id}/test", headers=headers)
    assert result.status_code == 200
    assert result.json()["ok"] is True
    assert result.json()["models"] == ["fake-1"]


async def test_resolve_credential_decrypts_stored_key(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, org = await _org_headers(client)
    await client.post(
        "/v1/credentials",
        json={"provider": "groq", "api_key": "stored_key_1", "is_default": True},
        headers=headers,
    )
    key, _base_url = await resolve_credential(db_session, uuid.UUID(org["id"]), "groq")
    assert key == "stored_key_1"  # round-trips through Fernet encryption

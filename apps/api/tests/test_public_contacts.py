"""The PII allowlist, end to end (docs/11 Phase B, ADR-053/056).

Phase B shipped `Organization.public_contacts` and read it in both chat paths, but **nothing
could write to it** — no schema field, no router, no UI. So the allowlist was permanently
empty for every org and output redaction stripped a client's own support address out of its
own replies. Phase B's tests only ever exercised the not-allowlisted direction, which is
exactly why the gap survived: the failing case passed.

Both directions are asserted here, and the same value is used for both so the difference is
provably the allowlist and nothing else.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.llm.fake import FakeChatProvider

SUPPORT_EMAIL = "support@acme.com"
SUPPORT_PHONE = "+91 80 4000 1000"


async def _org(client: AsyncClient, email: str, name: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": name}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _agent(client: AsyncClient, headers: dict[str, str]) -> str:
    agent = await client.post("/v1/agents", json={"name": "Contact Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": "fake", "model": "fake-1"}},
        headers=headers,
    )
    return aid


# ── The API surface that was missing ─────────────────────────────────────────────────────


async def test_public_contacts_round_trips_through_the_org_api(client: AsyncClient) -> None:
    headers = await _org(client, "pc.roundtrip@example.com", "PC Roundtrip")
    assert (await client.get("/v1/orgs", headers=headers)).json()[0]["public_contacts"] == []

    r = await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL, SUPPORT_PHONE]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["public_contacts"] == [SUPPORT_EMAIL, SUPPORT_PHONE]

    listed = (await client.get("/v1/orgs", headers=headers)).json()[0]
    assert listed["public_contacts"] == [SUPPORT_EMAIL, SUPPORT_PHONE]


async def test_other_org_fields_are_untouched_by_a_contacts_patch(client: AsyncClient) -> None:
    """A partial PATCH must not reset anything it did not mention."""
    headers = await _org(client, "pc.partial@example.com", "PC Partial")
    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"auto_crm_capture_enabled": False},
        headers=headers,
    )
    r = await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}", json={"public_contacts": [SUPPORT_EMAIL]}, headers=headers
    )
    assert r.json()["auto_crm_capture_enabled"] is False
    assert r.json()["name"] == "PC Partial"


@pytest.mark.parametrize(
    "bad",
    ["https://acme.com/contact", "our contact page", "12345", "@acme.com"],
    ids=["url", "prose", "too-short", "malformed-email"],
)
async def test_entries_the_redactor_cannot_recognise_are_rejected(
    client: AsyncClient, bad: str
) -> None:
    """An inert allowlist entry looks configured and does nothing — worse than an error."""
    headers = await _org(client, f"pc.bad.{abs(hash(bad))}@example.com", "PC Bad")
    r = await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}", json={"public_contacts": [bad]}, headers=headers
    )
    assert r.status_code == 422, r.text


async def test_blanks_are_dropped_and_duplicates_collapsed(client: AsyncClient) -> None:
    headers = await _org(client, "pc.clean@example.com", "PC Clean")
    r = await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL, "  ", SUPPORT_EMAIL, ""]},
        headers=headers,
    )
    assert r.json()["public_contacts"] == [SUPPORT_EMAIL]


async def test_only_org_managers_can_change_the_allowlist(client: AsyncClient) -> None:
    """It is a security control, so it rides ORG_MANAGE like the rest of the org profile."""
    owner = await _org(client, "pc.owner@example.com", "PC Owner Org")
    member = await _org(client, "pc.viewer@example.com", "PC Viewer Own")
    invite = await client.post(
        f"/v1/orgs/{owner['X-Org-Id']}/invitations",
        json={"email": "pc.viewer@example.com", "role": "viewer"},
        headers=owner,
    )
    token = invite.json()["accept_token"]
    await client.post(
        f"/v1/orgs/invitations/{token}/accept",
        headers={"Authorization": member["Authorization"]},
    )
    r = await client.patch(
        f"/v1/orgs/{owner['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL]},
        headers={"Authorization": member["Authorization"], "X-Org-Id": owner["X-Org-Id"]},
    )
    assert r.status_code == 403


# ── The behaviour the allowlist exists for: both directions, same value ──────────────────


async def test_an_allowlisted_contact_reaches_the_visitor(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeChatProvider, "scripted_reply", f"Reach us on {SUPPORT_EMAIL} or {SUPPORT_PHONE}.")
    headers = await _org(client, "pc.allowed@example.com", "PC Allowed")
    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL, SUPPORT_PHONE]},
        headers=headers,
    )
    aid = await _agent(client, headers)
    # The scripted model reply carries these values out; the visitor typed none of them.
    r = await client.post(
        f"/v1/agents/{aid}/chat",
        json={"message": "what is the right contact?", "stream": False},
        headers=headers,
    )
    content = r.json()["content"]
    assert SUPPORT_EMAIL in content, "an agent must be able to give out its own support address"
    assert "4000 1000" in content


async def test_the_same_contact_is_redacted_for_an_org_that_has_not_allowlisted_it(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeChatProvider, "scripted_reply", f"Reach us on {SUPPORT_EMAIL} or {SUPPORT_PHONE}.")
    """Identical value, identical message — only the allowlist differs."""
    headers = await _org(client, "pc.denied@example.com", "PC Denied")
    aid = await _agent(client, headers)
    r = await client.post(
        f"/v1/agents/{aid}/chat",
        json={"message": "what is the right contact?", "stream": False},
        headers=headers,
    )
    content = r.json()["content"]
    assert SUPPORT_EMAIL not in content
    assert "4000 1000" not in content
    assert "our contact page" in content


async def test_allowlisting_one_contact_does_not_allowlist_another(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        FakeChatProvider, "scripted_reply", f"Mail {SUPPORT_EMAIL} or the founder at founder.personal@gmail.com."
    )
    """The allowlist is per-value, not a switch that turns redaction off for the org."""
    headers = await _org(client, "pc.mixed@example.com", "PC Mixed")
    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}", json={"public_contacts": [SUPPORT_EMAIL]}, headers=headers
    )
    aid = await _agent(client, headers)
    r = await client.post(
        f"/v1/agents/{aid}/chat",
        json={"message": "what is the right contact?", "stream": False},
        headers=headers,
    )
    content = r.json()["content"]
    assert SUPPORT_EMAIL in content
    assert "founder.personal@gmail.com" not in content

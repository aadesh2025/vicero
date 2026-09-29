"""The PII allowlist on the path real visitors actually use (docs/12 checklist 4.5).

`test_public_contacts.py` covers the dashboard/Playground path, which reads the allowlist from
`ctx.org.public_contacts` in `modules/conversations/service.py`. The **widget** goes through
`chat/inbound.py::_pii_allowlist()` instead — a different lookup, on the only endpoint an
unauthenticated visitor can reach. It had no coverage at all, which is how the live run found
the agent telling a customer to email "our contact page".

Both directions with the same value, per the lesson in `test_public_contacts.py`: a suite that
only asserts the redact direction passes just as happily when the allowlist is broken.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.llm.fake import FakeChatProvider

SUPPORT_EMAIL = "support@acme.com"
SUPPORT_PHONE = "+91 80 4000 1000"
VISITOR = {"id": "w-allowlist"}


async def _org(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post(
        "/v1/orgs", json={"name": "Widget PII Org"}, headers={"Authorization": f"Bearer {token}"}
    )
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _public_key(client: AsyncClient, headers: dict[str, str]) -> str:
    agent = await client.post("/v1/agents", json={"name": "Contact Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": "fake", "model": "fake-1"}},
        headers=headers,
    )
    return str(agent.json()["public_key"])


async def _ask(client: AsyncClient, key: str, message: str, cid: str | None = None) -> dict:
    body: dict = {"message": message, "stream": False, "visitor": VISITOR}
    if cid:
        body["conversation_id"] = cid
    r = await client.post(f"/v1/public/agents/{key}/chat", json=body)
    assert r.status_code == 200, r.text
    return dict(r.json())


# The *model* names these contacts (the visitor asks a neutral question and types none of them), so
# they come back through the output guard exactly as a real model's reply would. If the visitor typed
# them instead they would be the customer's own and exempt from redaction — a different case, covered
# in test_pii_customer_echo.py.
_MODEL_REPLY = f"is {SUPPORT_EMAIL} or {SUPPORT_PHONE} the right contact?"
_NEUTRAL = "what is the right contact?"


@pytest.fixture(autouse=True)
def _model_names_the_contacts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeChatProvider, "scripted_reply", _MODEL_REPLY)


async def test_an_allowlisted_contact_survives_the_widget_reply(client: AsyncClient) -> None:
    headers = await _org(client, "wpc.allowed@example.com")
    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL, SUPPORT_PHONE]},
        headers=headers,
    )
    key = await _public_key(client, headers)

    content = (await _ask(client, key, _NEUTRAL))["content"]
    assert SUPPORT_EMAIL in content, "an agent must be able to give out its own support address"
    assert "4000 1000" in content
    assert "our contact page" not in content


async def test_the_same_contact_is_redacted_when_the_org_has_published_nothing(
    client: AsyncClient,
) -> None:
    """Identical value, identical message, identical endpoint — only the allowlist differs.

    This is the state every org in the live database was in: `public_contacts == []`, which is
    correctly read as "share nothing" and was never populated.
    """
    headers = await _org(client, "wpc.denied@example.com")
    key = await _public_key(client, headers)

    content = (await _ask(client, key, _NEUTRAL))["content"]
    assert SUPPORT_EMAIL not in content
    assert "4000 1000" not in content
    assert "our contact page" in content


async def test_the_allowlist_is_read_per_turn_not_captured_at_conversation_start(
    client: AsyncClient,
) -> None:
    """Publishing a contact takes effect on the next message of an existing conversation.

    The allowlist is resolved inside the turn (`InboundTurn._pii_allowlist()`), not stored on
    the conversation, so an operator fixing their settings mid-chat does not have to wait for
    the visitor to start a new one. Asserted rather than assumed: capturing it once at
    conversation start would be an easy and invisible optimisation for someone to add later.
    """
    headers = await _org(client, "wpc.midchat@example.com")
    key = await _public_key(client, headers)

    first = await _ask(client, key, _NEUTRAL)
    assert SUPPORT_EMAIL not in first["content"]
    cid = first["conversation_id"]

    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL, SUPPORT_PHONE]},
        headers=headers,
    )

    second = await _ask(client, key, _NEUTRAL, cid=cid)
    assert second["conversation_id"] == cid
    assert SUPPORT_EMAIL in second["content"], "the same conversation must pick up the new value"


async def test_removing_a_contact_takes_effect_on_the_next_turn_too(client: AsyncClient) -> None:
    """The direction that matters if someone publishes a contact by mistake."""
    headers = await _org(client, "wpc.revoke@example.com")
    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL]},
        headers=headers,
    )
    key = await _public_key(client, headers)

    first = await _ask(client, key, _NEUTRAL)
    assert SUPPORT_EMAIL in first["content"]

    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}", json={"public_contacts": []}, headers=headers
    )
    second = await _ask(client, key, _NEUTRAL, cid=first["conversation_id"])
    assert SUPPORT_EMAIL not in second["content"]
    assert "our contact page" in second["content"]


async def test_publishing_one_contact_does_not_publish_the_founders(client: AsyncClient) -> None:
    """Checklist 4.2's shape: the allowlist is per value, never a switch for the whole org."""
    headers = await _org(client, "wpc.mixed@example.com")
    await client.patch(
        f"/v1/orgs/{headers['X-Org-Id']}",
        json={"public_contacts": [SUPPORT_EMAIL]},
        headers=headers,
    )
    key = await _public_key(client, headers)

    content = (
        await _ask(
            client,
            key,
            f"mail {SUPPORT_EMAIL} or the founder on founder.personal@gmail.com",
        )
    )["content"]
    assert SUPPORT_EMAIL in content
    assert "founder.personal@gmail.com" not in content
    assert "our contact page" in content

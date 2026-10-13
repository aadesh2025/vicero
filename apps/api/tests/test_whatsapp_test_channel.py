"""The WhatsApp test-number channel (scripts/add_whatsapp_test_channel.py): created without Embedded Signup,
idempotent, token never leaked, and the real inbound path answers it. The Graph API is mocked."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel, meta_graph, meta_webhook, whatsapp_test
from app.core.crypto import decrypt
from app.core.errors import AppError
from app.models import Channel
from scripts import add_whatsapp_test_channel as script
from tests.test_channels import _fake_agent, _headers
from tests.test_meta_connect import (
    _meta_settings,  # noqa: F401  (autouse: Meta settings + queue stub)
    _post_webhook,
    _wa_msg,
    _wa_payload,
)

PN = "1356911374173965"
WABA = "3368583013326109"
TOKEN = "EAAtest-secret-token-value-123"


class _Graph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.subscribe_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {"method": request.method, "path": request.url.path, "auth": request.headers.get("authorization", "")}
        )
        if request.url.path.endswith("/subscribed_apps"):
            if self.subscribe_status != 200:
                return httpx.Response(
                    self.subscribe_status, json={"error": {"message": "Invalid OAuth token", "code": 190}}
                )
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={"error": {"message": "no route", "code": 1}})


@pytest.fixture
def graph() -> Iterator[_Graph]:
    fake = _Graph()
    meta_graph.transport = httpx.MockTransport(fake.handler)
    yield fake
    meta_graph.transport = None


@pytest.fixture
def queued(_meta_settings: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:  # noqa: F811
    return _meta_settings


@pytest.fixture(autouse=True)
def _reset_wa() -> Iterator[None]:
    yield
    get_channel("whatsapp").transport = None


async def _agent(client: AsyncClient, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    headers = await _headers(client, email)
    return uuid.UUID(await _fake_agent(client, headers)), uuid.UUID(headers["X-Org-Id"])


async def _add(session: AsyncSession, agent_id: uuid.UUID, token: str = TOKEN) -> tuple[Channel, bool]:
    return await whatsapp_test.add_test_channel(
        session, agent_id=agent_id, phone_number_id=PN, waba_id=WABA, display_name="WhatsApp test", token=token
    )


async def test_creates_the_channel_like_embedded_signup_would(
    client: AsyncClient, db_session: AsyncSession, graph: _Graph
) -> None:
    agent_id, org_id = await _agent(client, "wat1@example.com")
    channel, created = await _add(db_session, agent_id)

    assert created
    assert (channel.type, channel.enabled, channel.status) == ("whatsapp", True, "active")
    assert (channel.external_id, channel.external_parent_id) == (PN, WABA)
    assert channel.connection_source == "manual" and channel.organization_id == org_id
    assert channel.name == "WhatsApp test"
    assert channel.config["phone_number_id"] == PN and channel.config["waba_id"] == WABA
    assert channel.config["access_token"] != TOKEN  # stored encrypted...
    assert decrypt(channel.config["access_token"]) == TOKEN  # ...with the same key the connect flow uses
    assert channel.token_expires_at is not None
    left = channel.token_expires_at - dt.datetime.now(tz=dt.UTC)
    assert dt.timedelta(hours=23, minutes=55) < left <= dt.timedelta(hours=24)

    subscribe = [c for c in graph.calls if c["path"].endswith(f"/{WABA}/subscribed_apps")]
    assert len(subscribe) == 1 and subscribe[0]["method"] == "POST"
    assert subscribe[0]["auth"] == f"Bearer {TOKEN}"


async def test_it_is_idempotent_and_a_rerun_refreshes_the_token(
    client: AsyncClient, db_session: AsyncSession, graph: _Graph
) -> None:
    agent_id, _ = await _agent(client, "wat2@example.com")
    first, created = await _add(db_session, agent_id)
    second, created_again = await _add(db_session, agent_id, token="EAAnew-token")

    assert created and not created_again and second.id == first.id
    rows = (
        await db_session.execute(select(func.count()).select_from(Channel).where(Channel.external_id == PN))
    ).scalar_one()
    assert rows == 1
    assert decrypt(second.config["access_token"]) == "EAAnew-token"
    assert second.status == "active" and second.enabled


async def test_a_dead_channel_is_revived_by_a_rerun(
    client: AsyncClient, db_session: AsyncSession, graph: _Graph
) -> None:
    agent_id, _ = await _agent(client, "wat3@example.com")
    channel, _ = await _add(db_session, agent_id)
    channel.status, channel.enabled = "needs_reconnect", False
    await db_session.flush()
    again, _ = await _add(db_session, agent_id)
    assert again.id == channel.id and again.status == "active" and again.enabled


async def test_the_token_never_reaches_logs_or_stdout(
    client: AsyncClient, db_session: AsyncSession, graph: _Graph, capsys: pytest.CaptureFixture[str]
) -> None:
    agent_id, _ = await _agent(client, "wat4@example.com")
    with structlog.testing.capture_logs() as logs:
        await _add(db_session, agent_id)
    graph.subscribe_status = 400
    with structlog.testing.capture_logs() as failing, pytest.raises(AppError) as err:
        await _add(db_session, agent_id)
    out = capsys.readouterr()
    assert TOKEN not in str(logs) + str(failing) + out.out + out.err + err.value.message
    assert err.value.code == "whatsapp_test.subscribe_failed"


async def test_a_rejected_subscribe_saves_nothing(client: AsyncClient, db_session: AsyncSession, graph: _Graph) -> None:
    graph.subscribe_status = 400
    agent_id, _ = await _agent(client, "wat5@example.com")
    with pytest.raises(AppError):
        await _add(db_session, agent_id)
    count = (
        await db_session.execute(select(func.count()).select_from(Channel).where(Channel.external_id == PN))
    ).scalar_one()
    assert count == 0


async def test_unknown_agent_and_another_workspaces_number_are_refused(
    client: AsyncClient, db_session: AsyncSession, graph: _Graph
) -> None:
    with pytest.raises(AppError) as missing:
        await _add(db_session, uuid.uuid4())
    assert missing.value.code == "whatsapp_test.agent_not_found"

    mine, _ = await _agent(client, "wat6@example.com")
    theirs, _ = await _agent(client, "wat7@example.com")
    await _add(db_session, mine)
    with pytest.raises(AppError) as taken:
        await _add(db_session, theirs)
    assert taken.value.code == "whatsapp_test.number_in_use"


# ── the real inbound path ────────────────────────────────────────────────────────────


async def test_an_inbound_message_reaches_the_channel_and_a_reply_is_sent(
    client: AsyncClient, db_session: AsyncSession, graph: _Graph, queued: list[tuple[str, dict[str, Any]]]
) -> None:
    agent_id, _ = await _agent(client, "wat8@example.com")
    channel, _ = await _add(db_session, agent_id)

    # 1. The shared webhook resolves the test number's phone_number_id to this channel.
    r = await _post_webhook(client, _wa_payload(PN, _wa_msg("what are your hours")))
    assert r.status_code == 200
    assert [cid for cid, _ in queued] == [str(channel.id)]

    # 2. The worker's unchanged path runs the turn and sends the reply with this token.
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "path": request.url.path,
                "auth": request.headers.get("authorization", ""),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(200, json={"ok": True})

    get_channel("whatsapp").transport = httpx.MockTransport(handler)
    await meta_webhook.handle_inbound(db_session, channel.id, queued[0][1])

    assert len(sent) == 1
    assert sent[0]["path"].endswith(f"/{PN}/messages")
    assert sent[0]["auth"] == f"Bearer {TOKEN}"
    assert sent[0]["body"]["to"] == "919999"
    assert "echo: what are your hours" in sent[0]["body"]["text"]["body"]


async def test_group_messages_are_ignored(
    client: AsyncClient, db_session: AsyncSession, graph: _Graph, queued: list[tuple[str, dict[str, Any]]]
) -> None:
    agent_id, _ = await _agent(client, "wat9@example.com")
    channel, _ = await _add(db_session, agent_id)
    group = {**_wa_msg("hello group"), "group_id": "120363000000000000@g.us"}

    # Never queued...
    assert meta_webhook.split_events(_wa_payload(PN, group)) == []
    r = await _post_webhook(client, _wa_payload(PN, group))
    assert r.status_code == 200 and queued == []
    # ...and if one reaches the adapter anyway, it is not parsed and nothing is sent.
    assert get_channel("whatsapp").parse_inbound(channel, _wa_payload(PN, group)) is None
    sent: list[Any] = []
    get_channel("whatsapp").transport = httpx.MockTransport(
        lambda req: sent.append(req) or httpx.Response(200, json={})
    )
    await meta_webhook.handle_inbound(db_session, channel.id, _wa_payload(PN, group))
    assert sent == []


async def test_a_one_to_one_message_in_the_same_batch_still_gets_through(client: AsyncClient) -> None:
    group = {**_wa_msg("g"), "group_id": "1@g.us"}
    events = meta_webhook.split_events(_wa_payload(PN, group, _wa_msg("direct")))
    assert len(events) == 1


# ── the script's own surface ─────────────────────────────────────────────────────────


def test_the_token_cannot_be_passed_on_the_command_line() -> None:
    with pytest.raises(SystemExit):
        script.main(["--agent-id", str(uuid.uuid4()), "--token", TOKEN])


def test_the_token_is_read_from_stdin_or_the_environment() -> None:
    import io

    assert script.read_token(io.StringIO(f"  {TOKEN}\n"), {}) == TOKEN
    assert script.read_token(io.StringIO("ignored"), {"WHATSAPP_TEST_TOKEN": "from-env"}) == "from-env"
    assert script.read_token(io.StringIO(""), {}) == ""

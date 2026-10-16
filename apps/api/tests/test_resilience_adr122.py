"""ADR-122: L3 retry/fallback/sticky/alert, warm-up and cold-stage logging, token-rejected -> needs_reconnect."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import get_channel, meta_webhook
from app.channels.base import ChannelAuthRejected
from app.chat import policy_guard
from app.core.config import settings
from app.models import AuditLog, Channel
from app.rag import retrieval, warmup
from tests.test_meta_connect import _seed_channel, _wa_msg, _wa_payload

PRIMARY = "openai/gpt-oss-safeguard-20b"
FALLBACK = "openai/gpt-oss-20b"

CALM = (
    '{"distress":{"level":"none","signals":[]},"abuse":{"violation":false},'
    '"off_topic":{"violation":false},"pii_request":{"violation":false}}'
)
ELEVATED = CALM.replace('"none"', '"elevated"')


@pytest.fixture(autouse=True)
def _l3(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "guard_policy_enabled", True)
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test_platform_key")
    monkeypatch.setattr(settings, "guard_policy_model", PRIMARY)
    monkeypatch.setattr(settings, "guard_policy_fallback_model", FALLBACK)
    monkeypatch.setattr(settings, "guard_policy_retries", 2)
    monkeypatch.setattr(settings, "guard_policy_retry_backoff_ms", 0)
    monkeypatch.setattr(settings, "guard_policy_alert_threshold", 3)
    policy_guard._sticky.clear()
    policy_guard._failures.clear()
    monkeypatch.setattr(policy_guard, "_last_alert", -1e9)
    yield


def _scripted(statuses: dict[str, list[int]], content: str = CALM) -> tuple[httpx.MockTransport, list[str]]:
    """Per-model queue of HTTP statuses (the last one repeats). Returns the transport and the models called."""
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        called.append(model)
        queue = statuses.get(model, [200])
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "x"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 9}})

    return httpx.MockTransport(handler), called


# ── (2) L3 retry, fallback, sticky verdict, alert ───────────────────────────────────────────────────────
async def test_a_429_is_retried_on_the_same_model() -> None:
    transport, called = _scripted({PRIMARY: [429, 200]})
    verdict = await policy_guard.classify("hello", transport=transport)
    assert verdict is not None and verdict.distress == "none"
    assert called == [PRIMARY, PRIMARY]


async def test_exhausted_retries_fall_back_to_the_other_model() -> None:
    transport, called = _scripted({PRIMARY: [429]})
    verdict = await policy_guard.classify("hello", transport=transport)
    assert verdict is not None
    assert called == [PRIMARY, PRIMARY, PRIMARY, FALLBACK]


async def test_a_413_skips_primary_retries_and_goes_straight_to_the_fallback() -> None:
    transport, called = _scripted({PRIMARY: [413]})
    verdict = await policy_guard.classify("hello", transport=transport)
    assert verdict is not None
    assert called == [PRIMARY, FALLBACK]


async def test_an_empty_fallback_disables_that_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "guard_policy_fallback_model", "")
    transport, called = _scripted({PRIMARY: [500]})
    assert await policy_guard.classify("hello", transport=transport) is None
    assert called == [PRIMARY, PRIMARY, PRIMARY]


async def test_the_retry_budget_caps_the_total_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "guard_policy_retry_budget_ms", 0)
    transport, called = _scripted({PRIMARY: [429]})
    assert await policy_guard.classify("hello", transport=transport) is None
    assert called == [PRIMARY]


async def test_repeated_unavailability_raises_one_alert_per_window() -> None:
    transport, _ = _scripted({PRIMARY: [429], FALLBACK: [429]})
    with structlog.testing.capture_logs() as logs:
        for _ in range(5):
            assert await policy_guard.classify("hello", transport=transport) is None
    alerts = [e for e in logs if e["event"] == "guard_l3_unavailable_repeated"]
    assert len(alerts) == 1 and alerts[0]["log_level"] == "error"
    assert not any("hello" in str(e) for e in logs), "message text must never be logged"


async def test_an_elevated_verdict_is_reused_while_l3_is_down() -> None:
    ok, _ = _scripted({}, content=ELEVATED)
    first = await policy_guard.classify("I am at the end of my rope", cache_key="conv-1", transport=ok)
    assert first is not None and first.distress == "elevated"

    down, _ = _scripted({PRIMARY: [429], FALLBACK: [429]})
    again = await policy_guard.classify("hello?", cache_key="conv-1", transport=down)
    assert again is not None and again.distress == "elevated"
    # Another conversation gets no such help: not graded, as before.
    assert await policy_guard.classify("hello?", cache_key="conv-2", transport=down) is None


async def test_a_calm_verdict_clears_the_reused_one() -> None:
    elevated, _ = _scripted({}, content=ELEVATED)
    await policy_guard.classify("x", cache_key="c", transport=elevated)
    calm, _ = _scripted({}, content=CALM)
    await policy_guard.classify("thanks, all sorted", cache_key="c", transport=calm)
    down, _ = _scripted({PRIMARY: [429], FALLBACK: [429]})
    assert await policy_guard.classify("hello", cache_key="c", transport=down) is None


async def test_the_reused_verdict_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    elevated, _ = _scripted({}, content=ELEVATED)
    await policy_guard.classify("x", cache_key="c", transport=elevated)
    monkeypatch.setattr(settings, "guard_policy_sticky_ttl_seconds", 0)
    await asyncio.sleep(0.01)
    down, _ = _scripted({PRIMARY: [429], FALLBACK: [429]})
    assert await policy_guard.classify("hello", cache_key="c", transport=down) is None


# ── (1) warm-up and cold-stage logging ──────────────────────────────────────────────────────────────────
async def test_warm_up_times_each_stage_and_names_the_cold_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_force_fake", False)
    monkeypatch.setattr(settings, "warmup_enabled", True)
    monkeypatch.setattr(settings, "warmup_cold_ms", 20)

    async def slow() -> None:
        await asyncio.sleep(0.05)

    async def fast() -> None:
        return None

    async def broken() -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(warmup, "_embed", slow)
    monkeypatch.setattr(warmup, "_db", lambda _f: fast)
    monkeypatch.setattr(warmup, "_HOSTS", {"groq": "x", "graph": "y"})
    monkeypatch.setattr(warmup, "_host", lambda url: broken if url == "y" else fast)

    with structlog.testing.capture_logs() as logs:
        result = await warmup.warm(None)  # type: ignore[arg-type]
    assert result["embed"] >= 20 and result["db"] < 20 and result["graph"] == -1
    cold = [e["stage"] for e in logs if e["event"] == "warmup_stage_cold"]
    assert cold == ["embed"]
    assert [e["stage"] for e in logs if e["event"] == "warmup_stage_failed"] == ["graph"]


async def test_warm_up_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "warmup_enabled", False)
    assert await warmup.warm(None) == {}  # type: ignore[arg-type]


def test_a_slow_retrieval_stage_is_logged_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "warmup_cold_ms", 100)
    with structlog.testing.capture_logs() as logs:
        retrieval._note_cold("embed", __import__("time").perf_counter() - 8.0)
        retrieval._note_cold("vector", __import__("time").perf_counter())
    assert [(e["stage"], e["event"]) for e in logs] == [("embed", "rag_stage_cold")]
    assert logs[0]["ms"] >= 8000


def test_ollama_is_asked_to_keep_the_model_loaded() -> None:
    from app.llm.embeddings import OllamaEmbeddingProvider

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.0, 1.0]]})

    provider = OllamaEmbeddingProvider(transport=httpx.MockTransport(handler))
    asyncio.run(provider.embed(["hi"]))
    assert seen[0]["keep_alive"] == settings.ollama_keep_alive == "24h"


# ── (3) a rejected token flips the channel to needs_reconnect ───────────────────────────────────────────
def _rejecting(code: int, calls: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url)})
        error = {"code": code, "message": "Authentication Error"}
        return httpx.Response(401 if code == 190 else 400, json={"error": error})

    return httpx.MockTransport(handler)


async def test_whatsapp_token_rejected_marks_the_channel(client: AsyncClient, db_session: AsyncSession) -> None:
    calls: list[dict[str, Any]] = []
    get_channel("whatsapp").transport = _rejecting(190, calls)
    channel, *_ = await _seed_channel(
        client, db_session, "whatsapp", "PN900", "wa190@example.com",
        config={"phone_number_id": "PN900", "access_token": "t"},
    )  # fmt: skip
    event = meta_webhook.split_events(_wa_payload("PN900", _wa_msg("hello")))[0]
    try:
        await meta_webhook.handle_inbound(db_session, channel.id, event.payload)
    finally:
        get_channel("whatsapp").transport = None
    assert calls, "the reply was attempted"
    row = (await db_session.execute(select(Channel).where(Channel.id == channel.id))).scalar_one()
    assert row.status == "needs_reconnect"
    audit = (await db_session.execute(select(AuditLog).where(AuditLog.target_id == str(channel.id)))).scalars().all()
    assert [a.meta["reason"] for a in audit if a.action == "channel.meta_needs_reconnect"] == ["token_rejected"]


async def test_other_whatsapp_errors_do_not_flag_the_channel(client: AsyncClient, db_session: AsyncSession) -> None:
    get_channel("whatsapp").transport = _rejecting(131056, [])
    channel, *_ = await _seed_channel(
        client, db_session, "whatsapp", "PN901", "wa131@example.com",
        config={"phone_number_id": "PN901", "access_token": "t"},
    )  # fmt: skip
    event = meta_webhook.split_events(_wa_payload("PN901", _wa_msg("hello")))[0]
    try:
        await meta_webhook.handle_inbound(db_session, channel.id, event.payload)
    finally:
        get_channel("whatsapp").transport = None
    row = (await db_session.execute(select(Channel).where(Channel.id == channel.id))).scalar_one()
    assert row.status == "active"


async def test_messenger_and_instagram_sends_report_a_rejected_token() -> None:
    for kind in ("facebook", "instagram"):
        adapter = get_channel(kind)
        assert adapter is not None
        adapter.transport = _rejecting(190, [])
        ch = Channel(type=kind, config={"page_access_token": "t"}, status="active")
        try:
            with pytest.raises(ChannelAuthRejected):
                await adapter.send(ch, "USER1", "hi")
        finally:
            adapter.transport = None


async def test_saving_a_new_token_clears_needs_reconnect(client: AsyncClient, db_session: AsyncSession) -> None:
    channel, headers, _aid = await _seed_channel(
        client, db_session, "whatsapp", "PN902", "wa-new@example.com",
        config={"phone_number_id": "PN902", "access_token": "old"}, status="needs_reconnect",
    )  # fmt: skip
    r = await client.patch(
        f"/v1/channels/{channel.id}", json={"config": {"access_token": "brand-new-token"}}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    # Re-saving the masked secret changes nothing.
    channel.status = "needs_reconnect"
    await db_session.flush()
    r = await client.patch(f"/v1/channels/{channel.id}", json={"config": {"access_token": "••••oken"}}, headers=headers)
    assert r.json()["status"] == "needs_reconnect"

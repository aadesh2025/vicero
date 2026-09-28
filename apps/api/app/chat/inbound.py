"""Run one inbound turn for a conversation — the shared core behind the widget and channels.

The widget streams the events; channel senders consume the turn and forward the final text.
When a conversation is handed off to a human, the user's message is still persisted but the
bot does not generate (the operator replies via the inbox).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import time
from collections.abc import AsyncIterator
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.chat import attention, guard_models, guardrails, policy_guard, variables
from app.chat.assembly import build_messages, compose_system_prompt
from app.chat.budget import agentic_loop_enabled, turn_budget
from app.chat.handoff import (
    PLAN_LIMIT_REASON,
    has_open_plan_limit_handoff,
    trigger_handoff,
    wants_handoff,
)
from app.chat.pii import build_allowlist
from app.chat.runtime import TurnResult, run_turn
from app.core.audit import write_audit
from app.core.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import limiter
from app.llm.fake import RefusalProvider
from app.llm.types import StreamEvent
from app.models import Agent, AgentVersion, Contact, Conversation, Message, Organization
from app.rag.agent_retrieval import retrieve_for_version
from app.rag.retrieval import Citation

log = get_logger("chat.inbound")

_DEFAULT_REFUSAL = "I'm not able to help with that topic. Is there something else I can do for you?"
# Said to a visitor when the provider itself fails and the agent has no fallback line of its
# own. Never surfaces the provider error: that text can carry key fragments and account details.
_DEFAULT_PROVIDER_FAILURE = (
    "Sorry — I'm having trouble responding right now. Please try again in a moment."
)


class InboundTurn:
    """One inbound message → bot turn. Read `handed_off` / `result` / `assistant_message` after."""

    def __init__(
        self,
        session: AsyncSession,
        agent: Agent,
        version: AgentVersion,
        conversation: Conversation,
        message: str,
    ) -> None:
        self.session = session
        self.agent = agent
        self.version = version
        self.conversation = conversation
        self.message = message
        self.result = TurnResult()
        self.handed_off = False
        #: The plan is out (trial over / messages spent), so the bot stayed silent. The visitor's
        #: message is saved and the owner can still answer from the inbox; nothing is shown to
        #: the visitor — no error, no "upgrade", no quota text (docs/18 §8).
        self.silenced = False
        self.assistant_message: Message | None = None

    async def events(self) -> AsyncIterator[StreamEvent]:
        # Import here to avoid a module cycle (conversations.service imports chat.*).
        from app.modules.conversations.service import (
            _build_chat_request,
            _finalize_turn,
            _load_history,
            _persist_user_message,
            _resolve_provider,
        )
        from app.tools.service import build_tooling

        session = self.session
        conv = self.conversation
        org_id = conv.organization_id

        history = await _load_history(session, conv.id)
        await _persist_user_message(session, conv, self.message)
        await session.flush()

        # Paused for a human — persist the visitor message, but the bot stays silent.
        if conv.status == "handoff":
            self.handed_off = True
            conv.last_message_at = dt.datetime.now(tz=dt.UTC)
            await session.flush()
            # A conversation parked for the plan limit keeps collecting visitor messages that
            # nobody answers, and the count of them is the upgrade argument (docs/18 §8).
            if await has_open_plan_limit_handoff(session, conv.id):
                await usage.record_unanswered(session, org_id)
            return

        # Plan gate (docs/18 §7-8) — the one choke point every visitor path goes through: the
        # widget over HTTP/SSE/WebSocket and every messaging channel. It sits *after* the
        # visitor's message is persisted (the owner must still see it) and *before* anything
        # that costs money or emits text, including the canned handoff reply below.
        gate_org = await self._org()
        ent = await usage.load_entitlements(session, gate_org) if gate_org else None
        if ent is not None and not ent.bot_replies:
            await self._stay_silent(ent.expired_reason or "messages")
            return
        if ent is not None and ent.is_metered:
            allowed, _ = await limiter.hit(
                f"rl:org-chat:{org_id}", settings.org_chat_rate_limit, settings.org_chat_rate_window
            )
            if not allowed:  # one bot must not burn a whole trial in seconds
                log.warning("org_chat_rate_limited", org_id=str(org_id))
                await self._stay_silent(None)
                return

        # Keyword handoff: the visitor is asking for a human.
        features = self.version.features or {}
        if features.get("handoff_enabled") and wants_handoff(self.message):
            await trigger_handoff(session, conv, requested_by="user", reason="keyword")
            self.handed_off = True
            canned = (
                self.version.fallback_message
                or "Let me connect you with a teammate — someone will be with you shortly."
            )
            msg = Message(
                conversation_id=conv.id,
                organization_id=conv.organization_id,
                role="assistant",
                content=canned,
                provider="system",
            )
            session.add(msg)
            conv.last_message_at = dt.datetime.now(tz=dt.UTC)
            await session.flush()
            self.assistant_message = msg
            self.result.content = canned
            yield StreamEvent(type="token", delta=canned)
            yield StreamEvent(type="done", finish_reason="handoff")
            yield StreamEvent(type="message", message_id=str(msg.id))
            return

        # L1 input guard (docs/11 §4-L1). The visitor's own message is untrusted content —
        # this is the half of the threat model that was missing, and the fix for the live
        # "ignore all previous instructions" / "developer mode" failures. Screened before
        # retrieval so an attack doesn't spend an embedding call.
        guard = (
            guardrails.screen_user_message(
                self.message, max_chars=settings.max_user_message_chars
            )
            if settings.guard_input_enabled
            else guardrails.InputVerdict()
        )
        # L2 classifier (docs/11 §4-L2). Runs only when L1 let the message through, so a
        # regex-obvious attack costs no tokens; it exists for what L1 structurally cannot see
        # — paraphrase, and every language other than English.
        l2_score: float | None = None
        if not guard.blocked:
            org = await self._org()
            l2_blocked, l2_score = await guard_models.is_injection(
                self.message,
                org_enabled=org.guard_injection_enabled if org else None,
            )
        else:
            l2_blocked = False

        if guard.blocked or l2_blocked:
            # Hash, never the payload: the raw text is an attack string and may carry PII.
            log.warning(
                "guard_input_blocked",
                agent_id=str(self.agent.id),
                conversation_id=str(conv.id),
                layer="L1" if guard.blocked else "L2",
                category=guard.category,
                flags=guard.flags,
                l2_score=round(l2_score, 4) if l2_score is not None else None,
                message_sha256=hashlib.sha256(self.message.encode("utf-8")).hexdigest()[:16],
            )
            context_block, citations = "", cast(list[Citation], [])
        else:
            context_block, citations = await retrieve_for_version(
                session, org_id, self.version, self.message
            )
        # L3 policy/emotion grading (docs/11 §4-L3). Runs on messages that got past L1/L2,
        # because a blocked injection is not a distressed customer and grading it wastes a call.
        policy = None
        if not (guard.blocked or l2_blocked) and settings.guard_distress_enabled:
            org = await self._org()
            policy = await policy_guard.classify(
                self.message,
                history=[m.content for m in history if m.role == "user" and m.content],
                org_enabled=org.guard_injection_enabled if org else None,
            )
        if policy is not None:
            await attention.apply_policy_verdict(session, conv, policy)

        org = await self._org()
        system_prompt = compose_system_prompt(
            self.version.system_prompt,
            self.version.persona,
            agent_name=self.agent.name,
            business_name=org.name if org else None,
            variables=await self._variables(org),
        )
        # mild/elevated steer the tone; the bot keeps answering either way. Only `crisis`
        # suppresses generation, and it is handled below with a written holding message.
        if policy is not None and (directive := policy_guard.TONE_DIRECTIVES.get(policy.distress)):
            system_prompt = "\n\n".join(p for p in (system_prompt, directive) if p)

        messages = build_messages(
            system_prompt=system_prompt,
            context_block=context_block,
            memory_summary=conv.memory_summary,
            history=history,
            user_message=self.message,
            window_messages=settings.memory_window_messages,
        )
        # CRISIS — the one case that stops the bot answering (docs/11 §4-L3).
        # A fixed, human-written line: an 8B model improvising to someone in crisis is not
        # acceptable. It is still a real message, never silence.
        if policy is not None and policy.suppresses_generation:
            await trigger_handoff(session, conv, requested_by="system", reason="distress:crisis")
            self.handed_off = True
            msg = Message(
                conversation_id=conv.id,
                organization_id=conv.organization_id,
                role="assistant",
                content=policy_guard.CRISIS_HOLDING_MESSAGE,
                provider="system",
            )
            session.add(msg)
            conv.last_message_at = dt.datetime.now(tz=dt.UTC)
            await session.flush()
            self.assistant_message = msg
            self.result.content = policy_guard.CRISIS_HOLDING_MESSAGE
            yield StreamEvent(type="token", delta=policy_guard.CRISIS_HOLDING_MESSAGE)
            yield StreamEvent(type="done", finish_reason="crisis")
            yield StreamEvent(type="message", message_id=str(msg.id))
            return

        # Reserve the question + answer pair atomically, just before the reply is generated.
        # The early read above is a courtesy that avoids paying for guards on an org that is
        # already out; this UPDATE is the authority, so two visitors racing for the last
        # messages cannot both get through (billing/usage.py).
        reserved = False
        # `effective_max_messages`, never `max_messages`: it is the plan's allowance plus any
        # extra-message packs bought this period (docs/22 §7). Enforcing the raw plan cap here
        # would take a customer's money for a pack and still leave the bot silent.
        if ent is not None and ent.is_metered and ent.effective_max_messages is not None:
            if not await usage.reserve(session, org_id, ent.effective_max_messages):
                await self._stay_silent("messages")
                return
            reserved = True

        provider_name = (self.version.model_config_json or {}).get("provider", "fake")
        provider = await _resolve_provider(
            session,
            org_id,
            self.agent,
            provider_name,
            self.version.model_config_json or {},
            fallback_message=self.version.fallback_message,
        )
        req = _build_chat_request(self.version, messages, stream=True)

        # Pre-LLM refusals. The input guard redirects without naming a rule; a blocked topic
        # uses the agent's own refusal line, which the operator wrote for exactly that case.
        topics = guardrails.blocked_topics_for(self.version.persona)
        if guard.blocked or l2_blocked:
            provider = RefusalProvider(guardrails.INJECTION_REDIRECT)
            citations = []
            executor = None
            budget = None
        elif topics and guardrails.matches_blocked_topic(self.message, topics):
            provider = RefusalProvider(self.version.fallback_message or _DEFAULT_REFUSAL)
            citations = []
            executor = None
            budget = None
        else:
            org_for_flags = await self._org()
            include_mcp = agentic_loop_enabled(org_for_flags.agentic_loop_enabled if org_for_flags else None)
            specs, executor = await build_tooling(
                session, org_id, self.agent, self.version, conv.id, include_mcp=include_mcp
            )
            if specs and executor is not None and provider.supports_tools():
                req.tools = specs
            else:
                executor = None
            budget = turn_budget(
                org_for_flags.agentic_loop_enabled if org_for_flags else None,
                has_tools=executor is not None,
            )

        t0 = time.perf_counter()
        try:
            async for ev in run_turn(
                provider, req, [c.model_dump(mode="json") for c in citations], self.result,
                executor=executor, max_iters=settings.tool_max_iterations, budget=budget,
                fallback_message=self.version.fallback_message or _DEFAULT_PROVIDER_FAILURE,
                protected_prompt=system_prompt,
                pii_allowlist=await self._pii_allowlist(),
            ):
                yield ev
            latency_ms = int((time.perf_counter() - t0) * 1000)
            self.assistant_message = await _finalize_turn(session, conv, self.result, latency_ms, self.message)
            if reserved and self.result.error:
                # The model never produced the reply we reserved for (the visitor got the
                # agent's canned fallback line). Give that one back; the visitor's own message
                # still counts.
                await usage.refund(session, org_id, 1)
        except (asyncio.CancelledError, GeneratorExit):
            # Same fix as conversations/service.py::chat_events, for the same reason — both
            # exceptions, since a dropped stream ends either way (see that comment)
            # (RISK-REGISTER R14) — the widget/channel path shares this exact vulnerability:
            # a visitor's client disconnecting mid-stream must not silently drop the reply
            # from conversation history. See `_enqueue_finalize_turn`'s docstring for why this
            # hands off to a Celery task rather than retrying inline on the request's own
            # session (an in-process retry was tried first and does not work — anyio's cancel
            # scope re-raises `CancelledError` at every subsequent checkpoint, not just once).
            if self.assistant_message is None:
                from app.modules.conversations.service import _enqueue_finalize_turn

                latency_ms = int((time.perf_counter() - t0) * 1000)
                await _enqueue_finalize_turn(conv, self.result, latency_ms, self.message)
            raise
        yield StreamEvent(type="message", message_id=str(self.assistant_message.id))

    async def _stay_silent(self, reason: str | None) -> None:
        """Save-and-stay-quiet: mark the turn silenced, count it, park it in the Inbox, audit once.

        `reason=None` is the per-org burst limit — a flood, not a plan problem — which is dropped
        without counting or routing.
        """
        conv = self.conversation
        self.silenced = True
        conv.last_message_at = dt.datetime.now(tz=dt.UTC)
        await self.session.flush()
        if reason is None:
            return
        unanswered = await usage.record_unanswered(self.session, conv.organization_id)
        # Route it to the Inbox like any human-handoff case, so the owner sees a visitor is
        # waiting. Labelled `plan_limit` so it reads differently from a request for a person.
        await trigger_handoff(self.session, conv, requested_by="system", reason=PLAN_LIMIT_REASON)
        if unanswered == 1:  # the first time this org hit its limit
            await write_audit(
                self.session, conv.organization_id, None, "plan.limit_hit",
                target_type="org", target_id=str(conv.organization_id), meta={"reason": reason},
            )

    async def _org(self) -> Organization | None:
        """The conversation's org. One PK lookup, usually served from the identity map."""
        return await self.session.get(Organization, self.conversation.organization_id)

    async def _variables(self, org: Organization | None) -> dict[str, str]:
        """Values for `{{user_name}}` etc. Every one is escaped inside `build_context()`.

        The contact's name is visitor-supplied, which is the whole reason this is escaped and
        interpolated in a single pass (docs/11 §4b, Phase F).
        """
        contact = (
            await self.session.get(Contact, self.conversation.contact_id)
            if self.conversation.contact_id
            else None
        )
        return variables.build_context(
            user_name=getattr(contact, "display_name", None),
            user_email=getattr(contact, "email", None),
            agent_name=self.agent.name,
            business_name=org.name if org else None,
        )

    async def _pii_allowlist(self) -> set[str]:
        """Contact details this org has published, which the agent may share freely.

        One PK lookup per turn, usually served from the session's identity map. An org that
        cannot be loaded yields an empty set — "share nothing" — because failing open here
        would mean a lookup blip re-opens the exact leak this exists to close.
        """
        org = await self.session.get(Organization, self.conversation.organization_id)
        return build_allowlist(list(org.public_contacts or []) if org else [])

    async def run(self) -> None:
        """Non-streaming: run the turn to completion (channels send `self.result.content`)."""
        async for _ev in self.events():
            pass

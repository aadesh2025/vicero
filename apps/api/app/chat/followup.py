"""A reply nobody asked for in this turn: the outcome of something the agent started earlier (docs/28 T4.3, T4.4).

Two things finish after the visitor's request has already been answered: a tool that runs in the background, and a
tool call a person had to approve. When either lands, the agent says so in the conversation - one extra generation
through the normal pipeline (plan allowance, output guard, PII allowlist, persistence), delivered to the visitor the
way an operator's reply is. The outcome is untrusted data (an API answered, or a person decided), so it reaches the
model wrapped and neutralized, never as an instruction.
"""

from __future__ import annotations

import json
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage
from app.chat.assembly import build_messages, compose_system_prompt
from app.chat.guardrails import wrap_untrusted
from app.chat.pii import build_allowlist
from app.chat.runtime import TurnResult, run_turn
from app.core.config import settings
from app.core.logging import get_logger
from app.models import Agent, AgentVersion, Conversation, Organization, Tool, ToolRun

log = get_logger("chat.followup")

FOLLOWUP_NOTE = (
    "[This is an automatic update, not a message from the customer.] Something you started earlier has now "
    "finished. Tell the customer what happened in one or two plain sentences, in the language they have been "
    "using. Do not claim anything the outcome below does not say."
)
_SILENT_STATES = frozenset({"handoff", "closed"})


async def _published_version(session: AsyncSession, agent: Agent) -> AgentVersion | None:
    return await session.get(AgentVersion, agent.current_version_id) if agent.current_version_id else None


async def generate_followup(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """Say how a finished background or approved action turned out. True when a message was sent.

    Quietly does nothing when there is nobody to tell: no conversation, a human has taken over, the conversation is
    closed, the agent is unpublished, the plan allowance is out, or this run was already announced.
    """
    from app.modules.conversations import service as conv_service
    from app.modules.inbox import service as inbox_service

    run = await session.get(ToolRun, run_id)
    if run is None or run.conversation_id is None:
        return False
    conv = await session.get(Conversation, run.conversation_id)
    if conv is None or conv.organization_id != run.organization_id:
        return False
    announced = list((conv.meta or {}).get("followups") or [])
    if str(run.id) in announced:
        return False
    if conv.status in _SILENT_STATES:
        log.info("followup_skipped", reason=conv.status, run=str(run.id))
        return False
    agent = await session.get(Agent, conv.agent_id)
    org = await session.get(Organization, conv.organization_id)
    version = await _published_version(session, agent) if agent is not None else None
    tool = await session.get(Tool, run.tool_id)
    if agent is None or org is None or version is None or tool is None:
        return False

    ent = await usage.load_entitlements(session, org)
    reserved = False
    if not ent.bot_replies:
        return False
    if ent.is_metered and ent.effective_max_messages is not None:
        if not await usage.reserve(session, org.id, ent.effective_max_messages, 1):
            return False
        reserved = True

    outcome = {"action": tool.name, "outcome": run.status, "result": run.output or {}, "error": run.error}
    note = f"{FOLLOWUP_NOTE}\n\n{wrap_untrusted(json.dumps(outcome, default=str), kind='action-result')}"
    system_prompt = compose_system_prompt(
        version.system_prompt,
        version.persona,
        agent_name=agent.name,
        business_name=org.name,
        variables=conv_service.variables_for(conv, agent_name=agent.name, business_name=org.name),
    )
    history = await conv_service.load_history(session, conv.id)
    messages = build_messages(
        system_prompt=system_prompt,
        context_block="",
        memory_summary=conv.memory_summary,
        history=history,
        user_message=note,
        window_messages=settings.memory_window_messages,
    )
    provider_name = (version.model_config_json or {}).get("provider", "fake")
    provider = await conv_service.resolve_provider(
        session,
        org.id,
        agent,
        provider_name,
        version.model_config_json or {},
        fallback_message=version.fallback_message,
    )
    req = conv_service.build_chat_request(version, messages, stream=True)
    result = TurnResult()
    started = time.perf_counter()
    async for _event in run_turn(
        provider,
        req,
        [],
        result,
        executor=None,
        protected_prompt=system_prompt,
        pii_allowlist=build_allowlist(list(org.public_contacts or [])),
    ):
        pass
    if result.error or not result.content.strip():
        if reserved:
            await usage.refund(session, org.id, 1)
        return False
    msg = await conv_service.finalize_turn(session, conv, result, int((time.perf_counter() - started) * 1000), "")
    conv.meta = {**(conv.meta or {}), "followups": [*announced, str(run.id)][-50:]}
    await session.flush()
    await inbox_service.push_to_visitor(session, conv, result.content, msg.id)
    return True

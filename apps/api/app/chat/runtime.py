"""The streaming turn: run provider passes with an optional tool-calling loop.

A single pass when no tools/executor are supplied; otherwise: stream → if the model requests
tool calls, execute them (via `executor`), append the results, and loop — up to `max_iters`.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.chat import output_guard, pii
from app.chat.budget import AgentBudget
from app.chat.guardrails import neutralize_injections
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import ChatProvider, ProviderError
from app.llm.pricing import compute_cost_micros
from app.llm.types import ChatRequest, Message, StreamEvent, ToolCall, Usage

log = get_logger("chat.runtime")

# executor(call) -> {"output": dict, "status": str, "error": str|None}
ToolExecutor = Callable[[ToolCall], Awaitable[dict[str, Any]]]


@dataclass
class TurnResult:
    """Accumulated outcome of a turn, used by callers to persist the assistant message."""

    content: str = ""
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None
    error: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    tool_runs: list[dict[str, Any]] = field(default_factory=list)
    # The agentic-loop trace (docs/17 §3), populated only when `run_turn` is given a
    # `budget` — i.e. only for orgs with the agentic runtime on. Persisted to `agent_steps`
    # by the caller once the assistant `Message` row exists (see conversations/service.py).
    agent_steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def cost_micros(self) -> int:
        return compute_cost_micros(
            self.provider,
            self.model,
            Usage(prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens),
        )


async def run_turn(
    provider: ChatProvider,
    req: ChatRequest,
    citations: list[dict[str, Any]],
    result: TurnResult,
    *,
    executor: ToolExecutor | None = None,
    max_iters: int = 1,
    budget: AgentBudget | None = None,
    fallback_message: str | None = None,
    protected_prompt: str | None = None,
    guard_output: bool = True,
    pii_allowlist: set[str] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream a turn, forwarding events and accumulating into `result`.

    Emits one aggregated `done` event at the end (intermediate provider `done`s are folded in).

    `fallback_message` is what a *visitor-facing* caller wants said when the provider fails
    outright — pass it from the widget/channel/chat paths, leave it unset for the Playground,
    where an operator is debugging and wants the raw error rather than a soothing sentence.

    `protected_prompt` is the assembled *instruction* prompt for the leak check — deliberately
    not read off `req.messages`, which also holds the retrieved-context system message, and
    echoing knowledge-base content back to a customer is the product working (docs/11 §4-L5).
    Leave it unset to skip the leak check while keeping persona and secret checks.

    `guard_output=False` disables L5 entirely. The Playground passes it for the same reason it
    withholds `fallback_message`: an operator asking "is my agent behaving?" must be shown what
    the model actually said, and a silently regenerated persona break hides the answer.

    `budget` (docs/17 §5, Phase 1) is the agentic runtime's multi-dimensional ceiling — steps,
    tool calls, wall-clock runtime, and cost. `None` (the default) is today's behavior
    unchanged: only `max_iters` bounds the loop, and nothing is traced to `result.agent_steps`.
    Passed, it additionally bounds the loop and every iteration is traced. It is a single
    mutable object the caller owns: pass the SAME instance into a nested/delegated `run_turn`
    call to make that call draw from the parent's remaining allowance rather than a fresh one
    (docs/17 §2 rule 3) — never construct a new `AgentBudget` for a nested call.
    """
    result.provider = provider.name
    result.model = req.model
    result.citations = citations
    if citations:
        yield StreamEvent(type="citations", citations=citations)

    messages = list(req.messages)
    base_iters = max_iters if (executor is not None and req.tools) else 1
    if budget is not None:
        # `can_continue()` checks all four dimensions and sets `.tripped` if any is already
        # exhausted — matters for a nested/delegated call sharing a budget another call has
        # already spent from (docs/17 §2 rule 3): if there is nothing left, this call must run
        # zero further model passes, not silently get one free iteration before the first
        # budget check further down. Bounding by *remaining* steps (not the raw ceiling) keeps
        # a partially-consumed shared budget from being treated as a fresh one.
        iters = 0 if not budget.can_continue() else min(base_iters, budget.max_steps - budget.consumed_steps)
    else:
        iters = base_iters
    step_index = 0

    for iteration in range(iters):
        req_i = req.model_copy(update={"messages": messages})
        tool_calls: list[ToolCall] = []
        pass_content = ""
        iter_t0 = time.perf_counter()
        iter_prompt_tokens = 0
        iter_completion_tokens = 0
        try:
            async for ev in provider.stream(req_i):
                if ev.type == "token" and ev.delta:
                    pass_content += ev.delta
                    result.content += ev.delta
                    yield ev
                elif ev.type == "tool_call" and ev.tool_call is not None:
                    tool_calls.append(ev.tool_call)
                    yield ev
                elif ev.usage is not None:
                    result.prompt_tokens += ev.usage.prompt_tokens
                    result.completion_tokens += ev.usage.completion_tokens
                    iter_prompt_tokens += ev.usage.prompt_tokens
                    iter_completion_tokens += ev.usage.completion_tokens
                if ev.finish_reason:
                    result.finish_reason = ev.finish_reason
        except ProviderError as exc:
            result.error = str(exc)
            # A provider failure must never reach a visitor as an empty reply. That is exactly
            # what happened live (ADR-044): a malformed API key made Gemini 400 on every turn,
            # `content` stayed "", and the widget returned HTTP 200 with nothing in it — silent
            # for an unknown period, because an empty string looks like a quiet bot, not an
            # outage. Say the agent's fallback line instead, and keep `result.error` set so the
            # log and the persisted message still carry the real cause.
            if fallback_message and not result.content.strip():
                result.content += fallback_message
                yield StreamEvent(type="token", delta=fallback_message)
            yield StreamEvent(type="error", error=str(exc))
            break

        iter_cost_usd = 0.0
        if budget is not None:
            iter_cost_usd = compute_cost_micros(
                provider.name, req.model,
                Usage(prompt_tokens=iter_prompt_tokens, completion_tokens=iter_completion_tokens),
            ) / 1_000_000
            budget.record_step(cost_usd=iter_cost_usd)
            result.agent_steps.append({
                "step_index": step_index, "kind": "think", "tool_name": None,
                "tool_input": None, "tool_output": None,
                "latency_ms": int((time.perf_counter() - iter_t0) * 1000),
                "tokens_in": iter_prompt_tokens, "tokens_out": iter_completion_tokens,
                "cost_usd": iter_cost_usd, "status": "completed", "error": None,
            })
            step_index += 1

        # Continue the loop only if the model asked for tools, we have iterations left, and
        # (when an agentic budget is in play) there's budget left to spend on them.
        proceed = bool(tool_calls) and executor is not None and iteration < iters - 1
        if proceed and budget is not None and not budget.can_continue():
            # Never hang the turn (docs/17 §5): stop calling tools and let whatever content
            # has accumulated so far stand as the answer, same fallback shape as ADR-044.
            log.warning(
                "agent_budget_exceeded",
                tripped=budget.tripped,
                provider=provider.name,
                model=req.model,
                consumed_steps=budget.consumed_steps,
                consumed_tool_calls=budget.consumed_tool_calls,
                consumed_cost_usd=round(budget.consumed_cost_usd, 4),
            )
            proceed = False

        if proceed and executor is not None:
            if budget is not None:
                budget.record_tool_calls(len(tool_calls))
            messages.append(Message(role="assistant", content=pass_content or None, tool_calls=tool_calls))
            for call in tool_calls:
                out = await executor(call)
                result.tool_runs.append({"name": call.name, **out})
                yield StreamEvent(
                    type="tool_result",
                    tool_result={
                        "name": call.name,
                        "status": out.get("status"),
                        "output": out.get("output"),
                        "error": out.get("error"),
                    },
                )
                # Tool output is untrusted — MCP, n8n, and any future sub-agent result alike
                # (docs/17 §6, no exceptions): neutralize instruction-override attempts before
                # feeding it back to the model as a tool message (treat it as data, not
                # commands). This line is the one docs/17 §6 calls merge-blocking to bypass.
                tool_content = neutralize_injections(json.dumps(out.get("output") or {}))
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        content=tool_content,
                    )
                )
                if budget is not None:
                    # Sanitized only (rule §2.4) — the same post-guard-only discipline
                    # `documents.pii_flags` follows, never the raw tool output "for debugging".
                    result.agent_steps.append({
                        "step_index": step_index, "kind": "tool_call", "tool_name": call.name,
                        "tool_input": call.arguments, "tool_output": {"sanitized_text": tool_content},
                        "latency_ms": None, "tokens_in": None, "tokens_out": None, "cost_usd": None,
                        "status": out.get("status") or "completed", "error": out.get("error"),
                    })
                    step_index += 1
            continue
        break

    if budget is not None:
        result.agent_steps.append({
            "step_index": step_index, "kind": "final_answer", "tool_name": None,
            "tool_input": None, "tool_output": None, "latency_ms": None,
            "tokens_in": None, "tokens_out": None, "cost_usd": None,
            "status": "budget_exceeded" if budget.tripped else ("failed" if result.error else "completed"),
            "error": result.error,
        })

    # L5 output guard (docs/11 §4-L5). Runs on the accumulated reply, so a streaming client
    # has already rendered it — hence the `replace` event rather than pre-emptive suppression
    # (ADR-049). `result.content` is corrected here, which is what every non-streaming caller
    # and the persistence path read, so those are protected outright.
    if guard_output and settings.guard_output_enabled and result.content.strip():
        verdict = output_guard.inspect(
            result.content, protected_prompt, leak_threshold=settings.guard_output_leak_threshold
        )
        # One silent regeneration for a persona break: the model usually recovers when told,
        # and serving the fallback for "I'm a large language model" throws away a real answer.
        # A prompt leak is not retried — it gets replaced.
        if verdict.persona_break and not verdict.leaked_prompt:
            log.warning("output_guard_persona_break", provider=provider.name, model=req.model)
            retry_messages = [
                *messages,
                Message(role="assistant", content=result.content),
                Message(role="user", content=output_guard.REGENERATION_DIRECTIVE),
            ]
            try:
                retry = ""
                async for ev in provider.stream(
                    req.model_copy(update={"messages": retry_messages, "tools": None})
                ):
                    if ev.type == "token" and ev.delta:
                        retry += ev.delta
                if retry.strip():
                    result.content = retry
            except ProviderError as exc:
                # The first reply is still in hand; fall through and let `apply` replace it.
                log.warning("output_guard_regeneration_failed", error=str(exc))

        # Contact details the CUSTOMER typed in this conversation may be read back to them. Taken
        # only from `user`-role messages: retrieved context and the memory summary are `system`
        # messages and tool output is `tool`, so a number pulled from the KB, a tool or the model's
        # own head can never qualify. Distinct from `pii_allowlist` (the org's public contacts).
        customer_supplied = (
            pii.customer_supplied_contacts(m.content for m in req.messages if m.role == "user")
            if pii_allowlist is not None and settings.guard_pii_egress_enabled
            else None
        )
        final = output_guard.apply(
            result.content,
            protected_prompt,
            leak_threshold=settings.guard_output_leak_threshold,
            fallback_message=fallback_message,
            # `None` skips PII redaction entirely (the Playground); an empty set means the org
            # has published no contacts, so every contact detail is redacted. Those are
            # different states and must not collapse into one.
            pii_allowlist=pii_allowlist if settings.guard_pii_egress_enabled else None,
            pii_regions=[r.strip() for r in settings.guard_pii_phone_regions.split(",") if r.strip()],
            redact_addresses=settings.guard_pii_redact_addresses,
            customer_supplied=customer_supplied,
        )
        if final.pii_echoed:
            # Counts only. Not a warning: reading back what the customer just said is the product working.
            log.info("output_guard_pii_echo", provider=provider.name, model=req.model, echoed=final.pii_echoed)
        if final.pii_redacted:
            # Category and count only. Logging the value would move the leak into the log.
            log.warning(
                "output_guard_pii_egress",
                provider=provider.name,
                model=req.model,
                redacted=final.pii_redacted,
            )
        if final.leaked_prompt:
            log.error(
                "output_guard_prompt_leak",
                provider=provider.name,
                model=req.model,
                leak_score=round(final.leak_score, 3),
            )
        if final.changed:
            result.content = final.text
            # Tell a streaming client to discard what it painted for this turn.
            yield StreamEvent(type="replace", delta=final.text)

    # Re-read after streaming: a fallback chain only knows which link served once it has run,
    # and usage/cost must be attributed to the provider that actually answered.
    result.provider = provider.name
    result.model = getattr(provider, "active_model", None) or req.model

    yield StreamEvent(
        type="done",
        usage=Usage(prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens),
        finish_reason=result.finish_reason or ("error" if result.error else "stop"),
    )


# Backwards-compatible single-pass helper (no tools).
async def stream_turn(
    provider: ChatProvider,
    req: ChatRequest,
    citations: list[dict[str, Any]],
    result: TurnResult,
) -> AsyncIterator[StreamEvent]:
    async for ev in run_turn(provider, req, citations, result):
        yield ev

"""L3 — policy & emotion classifier (docs/11 §4-L3, Phase E).

One call to `openai/gpt-oss-safeguard-20b` grades a message against **our own written policy**
rather than a fixed taxonomy, so the files in `policies/` are the tuning mechanism and an
operator can change behaviour without a deploy. Four concerns come back from that one call:
distress level, abuse, off-topic, PII request.

Runs on the **platform** Groq key via `guard_models.platform_guard_key()` — same reasoning as
ADR-055, and the same failure mode if it did not: an org on a non-Groq provider would silently
lose escalation.

**Fails open** (ADR-051). A classifier outage returns `None`, which means *not graded* — never
"fine". Distress detection is a triage aid for routing a human's attention, and treating a
failure as `none` would quietly stop the queue filling while looking healthy.

> ⚠️ **The crisis holding message below is a first draft and has not been reviewed.** It is what
> a person in genuine distress reads. It is deliberately static rather than model-generated,
> per docs/11 §4-L3 — an 8B model improvising to someone in crisis is not acceptable — but
> static is not the same as *correct*, and the wording needs a human before it goes live.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx

from app.chat import guard_http
from app.chat.guard_models import platform_guard_key
from app.core import metrics
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("chat.policy_guard")

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_POLICY_DIR = Path(__file__).parent / "policies"

DistressLevel = Literal["none", "mild", "elevated", "crisis"]
_LEVEL_ORDER: dict[str, int] = {"none": 0, "mild": 1, "elevated": 2, "crisis": 3}

#: ⚠️ DRAFT — NEEDS HUMAN REVIEW BEFORE THIS IS LIVE.
#:
#: Served *instead of* a generated reply when the classifier returns `crisis`. Written to:
#: acknowledge without diagnosing, avoid coping advice or platitudes, avoid claiming to
#: understand, and hand to a person immediately. It names no helpline on purpose — the right
#: number is country-specific and a wrong one is worse than none, so that belongs in per-client
#: configuration rather than a hardcoded default.
CRISIS_HOLDING_MESSAGE = (
    "I've stopped here because what you've said matters more than the reason you first got in "
    "touch, and it deserves a person rather than an automated reply. I'm bringing in a member "
    "of our team now — please stay in this chat and someone will be with you shortly. "
    "If you are in immediate danger, please contact your local emergency services."
)

#: Prepended to the system prompt for `mild` / `elevated`. The bot keeps answering — only
#: `crisis` suppresses generation (docs/11 §4-L3, and the operator's explicit requirement).
TONE_DIRECTIVES: dict[str, str] = {
    "mild": (
        "This person is frustrated. Acknowledge that briefly and sincerely before answering, "
        "keep the answer short and concrete, and do not be cheerful about it."
    ),
    "elevated": (
        "This person is angry or under real pressure and a colleague has been alerted. Lead by "
        "acknowledging the impact on them specifically, do not defend the company, do not "
        "apologise more than once, and give the most direct next step you actually have. If you "
        "cannot resolve it, say a teammate is already looking rather than promising an outcome."
    ),
}


@dataclass(frozen=True)
class PolicyVerdict:
    """One graded message. Every field is advisory except `distress == "crisis"`."""

    distress: DistressLevel = "none"
    signals: list[str] = field(default_factory=list)
    abuse: bool = False
    abuse_category: str | None = None
    off_topic: bool = False
    off_topic_category: str | None = None
    pii_request: bool = False

    @property
    def needs_attention(self) -> bool:
        """Whether a human should be asked to look. Not the same as pausing the bot."""
        return _LEVEL_ORDER[self.distress] >= _LEVEL_ORDER["elevated"] or self.abuse

    @property
    def suppresses_generation(self) -> bool:
        """Only `crisis`. Everything else keeps answering while a human is alerted."""
        return self.distress == "crisis"


#: The policy prompt is fixed English prose: 5744 chars measured as 1444 tokens on Groq (~4.0).
_SYSTEM_CHARS_PER_TOKEN = 4.0
#: Customer text is not: short chat messages, emoji and non-Latin scripts run denser, so assume less per token.
_TEXT_CHARS_PER_TOKEN = 3.0
#: Left unspent so a mis-estimate lands under the limit rather than over it.
_SAFETY_TOKENS = 60
#: The shortest message window worth classifying, even when the budget is tighter than that.
_MIN_MESSAGE_CHARS = 120
_MAX_MESSAGE_CHARS = 4000
_MAX_HISTORY_LINE_CHARS = 200


def _window(message: str, history: list[str] | None, system_chars: int) -> tuple[str, str]:
    """`(context, message)` sized to fit `guard_policy_max_request_tokens` with the completion reserved.

    The message is the thing being graded, so it gets the room first (head and tail kept, the middle cut:
    a long message states its subject early and its worst part late). Whatever is left holds the most
    recent earlier turns, newest first. A turn that does not fit is dropped whole.
    """
    reserved = settings.guard_policy_max_completion_tokens + _SAFETY_TOKENS + system_chars / _SYSTEM_CHARS_PER_TOKEN
    room = max(int((settings.guard_policy_max_request_tokens - reserved) * _TEXT_CHARS_PER_TOKEN), 0)
    msg_cap = min(_MAX_MESSAGE_CHARS, max(room, _MIN_MESSAGE_CHARS))
    text = message
    if len(text) > msg_cap:
        head = int(msg_cap * 0.6)
        text = f"{text[:head]}\n[...]\n{text[-(msg_cap - head):]}"
    left = room - len(text)
    lines: list[str] = []
    for h in reversed((history or [])[-4:]):
        line = f"- {h[:_MAX_HISTORY_LINE_CHARS]}"
        if len(line) + 1 > left:
            break
        lines.append(line)
        left -= len(line) + 1
    if not lines:
        return "", text
    context = "Earlier messages from this customer, oldest first:\n" + "\n".join(reversed(lines)) + "\n\n"
    return context, text


@lru_cache(maxsize=1)
def _policy_text() -> str:
    """The concatenated policy documents. Cached; `reload_policies()` clears it."""
    parts: list[str] = []
    for name in ("distress", "abuse", "off_topic", "pii_request"):
        path = _POLICY_DIR / f"{name}.md"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n---\n\n".join(parts)


def reload_policies() -> None:
    """Drop the cached policy text so the next call re-reads the files.

    Hot reload is the point of keeping policy in markdown: an operator tuning "what counts as
    elevated" should not need a deploy, because the tuning loop is measured in real
    conversations rather than in tests.
    """
    _policy_text.cache_clear()


_SYSTEM_PREAMBLE = (
    "You are a classifier. Grade the user's message against the policy below and return ONLY a "
    "JSON object, no prose and no markdown fence.\n\n"
    "Schema:\n"
    '{"distress":{"level":"none|mild|elevated|crisis","signals":["..."]},'
    '"abuse":{"violation":false,"category":null},'
    '"off_topic":{"violation":false,"category":null},'
    '"pii_request":{"violation":false}}\n\n'
    "POLICY\n======\n"
)

_JSON_BLOB = re.compile(r"\{.*\}", re.S)


def _parse(content: str) -> PolicyVerdict | None:
    match = _JSON_BLOB.search(content or "")
    if not match:
        return None
    try:
        data: dict[str, Any] = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None

    distress = (data.get("distress") or {}) if isinstance(data.get("distress"), dict) else {}
    level = str(distress.get("level") or "none").lower().strip()
    if level not in _LEVEL_ORDER:
        level = "none"
    signals = [str(s)[:120] for s in (distress.get("signals") or [])][:6]

    def _flag(key: str) -> tuple[bool, str | None]:
        node = data.get(key)
        if not isinstance(node, dict):
            return False, None
        category = node.get("category")
        return bool(node.get("violation")), str(category) if category else None

    abuse, abuse_cat = _flag("abuse")
    off_topic, off_cat = _flag("off_topic")
    pii, _ = _flag("pii_request")
    return PolicyVerdict(
        distress=level,  # type: ignore[arg-type]
        signals=signals,
        abuse=abuse,
        abuse_category=abuse_cat,
        off_topic=off_topic,
        off_topic_category=off_cat,
        pii_request=pii,
    )


# --- resilience (ADR-122): retry, fallback model, last-verdict reuse, repeated-failure alert -------------------

_sticky: dict[str, tuple[float, PolicyVerdict]] = {}  # conversation -> (monotonic time, last elevated+ verdict)
_STICKY_MAX = 2000
_failures: deque[float] = deque(maxlen=200)  # monotonic times of calls that ended with no verdict
_last_alert = -1e9


def _attempt_models() -> list[str]:
    """The primary model (1 + retries times), then the fallback model once (its rate limit is its own)."""
    models = [settings.guard_policy_model] * (1 + max(0, settings.guard_policy_retries))
    fallback = settings.guard_policy_fallback_model.strip()
    if fallback and fallback != settings.guard_policy_model:
        models.append(fallback)
    return models


def _retryable(exc: BaseException) -> bool:
    """Rate limit (429), server error (5xx), timeout, or a network error. A 413/400/401 will fail again."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (TimeoutError, httpx.TransportError))


def _retry_delay(attempt: int, exc: BaseException) -> float:
    """Seconds to wait: the server's `Retry-After` when it is short, otherwise exponential backoff + jitter."""
    base = max(0, settings.guard_policy_retry_backoff_ms) / 1000.0
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            hinted = float(exc.response.headers.get("retry-after", ""))
        except ValueError:
            hinted = 0.0
        if 0 < hinted <= settings.guard_policy_retry_budget_ms / 1000.0:
            return hinted
    jitter: float = 0.75 + random.random() * 0.5
    return float(base * (2**attempt) * jitter)


async def _post_once(
    model: str,
    system: str,
    context: str,
    text: str,
    key: str,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if model.startswith("openai/gpt-oss"):
        extra["reasoning_effort"] = "low"  # a reasoning model: keep it short, it only has to pick a level
    budget_ms = settings.guard_policy_timeout_ms
    client, owned = guard_http.make_client(transport)
    try:
        resp = await guard_http.bounded(
            client.post(
                _GROQ_CHAT_URL,
                timeout=httpx.Timeout(budget_ms / 1000.0),
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    # Groq counts the completion it reserves against the TPM limit, and a reasoning model's
                    # default reservation is large. Cap it.
                    "max_completion_tokens": settings.guard_policy_max_completion_tokens,
                    **extra,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"{context}Message to grade:\n{text}"},
                    ],
                },
            ),
            budget_ms,
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body
    finally:
        if owned:
            await client.aclose()


def _remember(cache_key: str | None, verdict: PolicyVerdict) -> None:
    """Keep the last elevated-or-worse verdict per conversation; a calm one clears it."""
    if not cache_key:
        return
    if verdict.needs_attention or verdict.suppresses_generation:
        if len(_sticky) >= _STICKY_MAX:
            _sticky.pop(next(iter(_sticky)))
        _sticky[cache_key] = (time.monotonic(), verdict)
    else:
        _sticky.pop(cache_key, None)


def _unavailable(cache_key: str | None, exc: BaseException | None) -> PolicyVerdict | None:
    """Every attempt failed. Reuse a recent elevated verdict for this conversation (the safe side); else not graded."""
    global _last_alert
    now = time.monotonic()
    _failures.append(now)
    window = settings.guard_policy_alert_window_seconds
    recent = sum(1 for t in _failures if now - t <= window)
    if recent >= settings.guard_policy_alert_threshold and now - _last_alert > window:
        _last_alert = now
        metrics.observe_policy_call("alert", 0)
        log.error(
            "guard_l3_unavailable_repeated",
            failures=recent,
            window_seconds=window,
            model=settings.guard_policy_model,
            fallback=settings.guard_policy_fallback_model,
            impact="conversations are not being graded for distress; the attention queue is blind",
            fix="check GROQ_API_KEY and the Groq rate limit / status page; raise the plan or change GUARD_POLICY_MODEL",
        )
    sticky = _sticky.get(cache_key or "")
    if sticky is not None and now - sticky[0] <= settings.guard_policy_sticky_ttl_seconds:
        metrics.observe_policy_call("sticky", 0)
        log.warning("guard_l3_sticky_verdict", level=sticky[1].distress, age_seconds=int(now - sticky[0]))
        return sticky[1]
    log.warning(
        "guard_l3_unavailable",
        error=guard_http.describe(exc, settings.guard_policy_timeout_ms) if exc else "unknown",
        model=settings.guard_policy_model,
        impact="this turn was not graded for distress; no alert was raised",
    )
    return None



async def classify(
    message: str,
    *,
    history: list[str] | None = None,
    org_enabled: bool | None = None,
    cache_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PolicyVerdict | None:
    """Grade one message. `None` means **not graded** — never treat it as `none`.

    `history` is the last few customer turns, because distress is a trajectory: "this is the
    third time" reads differently after three failed attempts than as an opener.
    """
    if not settings.guard_policy_enabled or org_enabled is False:
        return None
    key = platform_guard_key()
    if not key or not (message or "").strip():
        if not key:
            metrics.observe_policy_call("unavailable", 0)
        return None

    system = _SYSTEM_PREAMBLE + _policy_text()
    context, text = _window(message, history, len(system))
    queue = _attempt_models()
    started = time.monotonic()
    body: dict[str, Any] | None = None
    last_exc: BaseException | None = None
    model = settings.guard_policy_model
    attempt = 0
    while queue:
        model = queue.pop(0)
        try:
            body = await _post_once(model, system, context, text, key, transport)
            break
        except Exception as exc:
            last_exc = exc
            metrics.observe_policy_call("error", 0)
            retryable = _retryable(exc)
            log.info(
                "guard_l3_attempt_failed",
                attempt=attempt + 1,
                model=model,
                retryable=retryable,
                error=guard_http.describe(exc, settings.guard_policy_timeout_ms),
            )
            timed_out = isinstance(exc, (TimeoutError, httpx.TimeoutException))
            if not retryable or timed_out:
                # The same request fails the same way again (413/400), or just spent the whole per-call budget
                # (timeout): only the fallback model can differ.
                queue = [m for m in queue if m != model]
            remaining_ms = settings.guard_policy_retry_budget_ms - (time.monotonic() - started) * 1000
            # A slow failure must leave room for another full call, or the turn waits on L3 for several seconds.
            needed_ms = settings.guard_policy_timeout_ms + guard_http.BOUND_MARGIN_SECONDS * 1000 if timed_out else 0
            if not queue or remaining_ms <= needed_ms:
                break
            if queue[0] == model:
                await asyncio.sleep(_retry_delay(attempt, exc))
            attempt += 1
            metrics.observe_policy_call("retry", 0)
    if body is None:
        return _unavailable(cache_key, last_exc)
    if model != settings.guard_policy_model:
        metrics.observe_policy_call("fallback_ok", 0)
        log.info("guard_l3_fallback_used", model=model)

    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
    verdict = _parse(content)
    if verdict is None:
        metrics.observe_policy_call("unparseable", tokens)
        log.warning("guard_l3_unparseable", model=settings.guard_policy_model)
        return None
    metrics.observe_policy_call(f"level_{verdict.distress}", tokens)
    _remember(cache_key, verdict)
    return verdict


def is_available() -> bool:
    return bool(settings.guard_policy_enabled and platform_guard_key())


def unavailable_reason() -> str | None:
    if not settings.guard_policy_enabled:
        return "disabled by configuration (GUARD_POLICY_ENABLED=false)"
    if not platform_guard_key():
        return (
            "no platform GROQ_API_KEY — conversations are not being graded for distress, so "
            "the attention queue stays empty whether or not anyone needs help"
        )
    return None

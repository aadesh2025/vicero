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

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx

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


async def classify(
    message: str,
    *,
    history: list[str] | None = None,
    org_enabled: bool | None = None,
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

    context = ""
    if history:
        recent = "\n".join(f"- {h[:300]}" for h in history[-4:])
        context = f"Earlier messages from this customer, oldest first:\n{recent}\n\n"

    timeout = httpx.Timeout(settings.guard_policy_timeout_ms / 1000.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.post(
                _GROQ_CHAT_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": settings.guard_policy_model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PREAMBLE + _policy_text()},
                        {"role": "user", "content": f"{context}Message to grade:\n{message[:4000]}"},
                    ],
                },
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        metrics.observe_policy_call("error", 0)
        log.warning(
            "guard_l3_unavailable",
            error=str(exc)[:200],
            model=settings.guard_policy_model,
            impact="this turn was not graded for distress; no alert was raised",
        )
        return None

    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
    verdict = _parse(content)
    if verdict is None:
        metrics.observe_policy_call("unparseable", tokens)
        log.warning("guard_l3_unparseable", model=settings.guard_policy_model)
        return None
    metrics.observe_policy_call(f"level_{verdict.distress}", tokens)
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

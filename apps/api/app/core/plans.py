"""Plans and entitlements — the ONE place a limit is written down (docs/18 §6, ADR-088).

Everything that gates behaviour by plan calls `get_entitlements()` and reads a field of the
result. No other module contains a plan limit, a plan name comparison or a trial length, so
adding a paid plan later is one entry in `PLANS` plus a payment webhook that sets
`Organization.plan` — nothing else changes.

Status vs. plan
    `Organization.plan` is what is *stored*: `trial` or `legacy`. `trial_expired` is a
    **computed status**, never stored — a cron that flips it would leave a window where a
    finished trial keeps answering visitors, and would fail open if the cron died.

Fail-safe default
    `Organization.plan` defaults to `legacy` (unlimited). Only the self-serve signup path writes
    `trial`, so a code path that forgets to set a plan can never lock a paying client out.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from app.core.errors import AppError

PlanStatus = Literal["trial", "trial_expired", "legacy"]

#: A reply is reserved as a question + answer pair. Also the smallest balance that can still
#: answer one visitor, which is why "exhausted" means fewer than this many messages left.
MESSAGES_PER_EXCHANGE = 2

#: Features a plan can lock. Names are the `feature` value in a `plan_limit` response, so the
#: UI can key its locked state off them.
FEATURES = ("workflows", "n8n", "tool_calling")


@dataclass(frozen=True, slots=True)
class PlanSpec:
    trial_days: int | None
    max_workspaces: int | None
    max_agents: int | None
    max_messages: int | None
    playground_per_day: int | None
    workflows: bool
    n8n: bool
    tool_calling: bool
    #: Publishing an agent to a live channel needs a verified email.
    publish_needs_verified_email: bool


#: `None` = unlimited. `trial_expired` is derived from `trial` by `get_entitlements`, not listed.
PLANS: dict[str, PlanSpec] = {
    "trial": PlanSpec(
        trial_days=10,
        max_workspaces=1,
        max_agents=1,
        max_messages=500,
        playground_per_day=50,
        workflows=False,
        n8n=False,
        tool_calling=False,
        publish_needs_verified_email=True,
    ),
    "legacy": PlanSpec(
        trial_days=None,
        max_workspaces=None,
        max_agents=None,
        max_messages=None,
        playground_per_day=None,
        workflows=True,
        n8n=True,
        tool_calling=True,
        publish_needs_verified_email=False,
    ),
}

#: What a finished trial keeps: read-only access to its one agent, nothing that spends money.
_EXPIRED = PlanSpec(
    trial_days=None,
    max_workspaces=1,
    max_agents=1,
    max_messages=0,
    playground_per_day=0,
    workflows=False,
    n8n=False,
    tool_calling=False,
    publish_needs_verified_email=True,
)

#: The plan trial workspaces are created with. Named here so the signup path does not repeat a literal.
SELF_SERVE_PLAN = "trial"
#: The plan every other path (staff provisioning, migration backfill, unknown values) gets.
DEFAULT_PLAN = "legacy"


@dataclass(frozen=True, slots=True)
class Entitlements:
    status: PlanStatus
    spec: PlanSpec
    messages_used: int
    trial_ends_at: dt.datetime | None
    #: Why a trial is expired: "time" | "messages" | None.
    expired_reason: str | None = None
    #: The cap the usage meter shows (`312 / 500`). Kept after expiry, when the *enforced* cap is 0.
    meter_limit: int | None = None

    # ── Limits, read through here and nowhere else ────────────────────────────────
    @property
    def max_workspaces(self) -> int | None:
        return self.spec.max_workspaces

    @property
    def max_agents(self) -> int | None:
        return self.spec.max_agents

    @property
    def max_messages(self) -> int | None:
        return self.spec.max_messages

    @property
    def playground_per_day(self) -> int | None:
        return self.spec.playground_per_day

    @property
    def is_metered(self) -> bool:
        return self.spec.max_messages is not None

    @property
    def is_expired(self) -> bool:
        return self.status == "trial_expired"

    @property
    def bot_replies(self) -> bool:
        """Whether the bot may answer visitors right now."""
        if self.is_expired:
            return False
        limit = self.spec.max_messages
        return limit is None or limit - self.messages_used >= MESSAGES_PER_EXCHANGE

    @property
    def agents_writable(self) -> bool:
        return not self.is_expired

    @property
    def messages_remaining(self) -> int | None:
        limit = self.spec.max_messages
        return None if limit is None else max(limit - self.messages_used, 0)

    def allows(self, feature: str) -> bool:
        if feature not in FEATURES:
            raise ValueError(f"unknown plan feature {feature!r}")
        return bool(getattr(self.spec, feature))

    def days_left(self, now: dt.datetime) -> int | None:
        """Whole days remaining (rounded up), 0 once over. `None` when the plan has no trial."""
        if self.trial_ends_at is None:
            return None
        seconds = (self.trial_ends_at - now).total_seconds()
        return -(-int(seconds) // 86400) if seconds > 0 else 0


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def get_entitlements(
    plan: str | None,
    *,
    trial_ends_at: dt.datetime | None = None,
    messages_used: int = 0,
    now: dt.datetime | None = None,
) -> Entitlements:
    """Resolve what an org may do. Pure — callers load `messages_used` (see `billing.usage`).

    An unknown plan value resolves to `legacy`, not to the most restrictive plan: a value this
    code does not recognise is far more likely a paid/custom plan added later than a trial.
    """
    now = now or dt.datetime.now(tz=dt.UTC)
    spec = PLANS.get(plan or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])
    if spec is not PLANS["trial"]:
        return Entitlements("legacy", spec, messages_used, None)

    ends = _aware(trial_ends_at) if trial_ends_at else None
    reason: str | None = None
    if ends is not None and now >= ends:
        reason = "time"
    elif spec.max_messages is not None and messages_used >= spec.max_messages:
        reason = "messages"
    if reason is not None:
        return Entitlements("trial_expired", _EXPIRED, messages_used, ends, reason, spec.max_messages)
    return Entitlements("trial", spec, messages_used, ends, None, spec.max_messages)


def plan_limit(feature: str, message: str | None = None) -> AppError:
    """The 402 every gate raises. `details.feature` lets the UI show the right locked state."""
    return AppError(
        "plan_limit",
        message or "This feature isn't included in your free trial. Upgrade to unlock it.",
        402,
        details={"feature": feature},
    )

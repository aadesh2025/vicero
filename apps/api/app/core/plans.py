"""Plans and entitlements — the ONE place a limit is written down (docs/18 §6, docs/22 §4, ADR-088).

Everything that gates behaviour by plan calls `get_entitlements()` and reads a field of the
result. No other module contains a plan limit, a plan name comparison or a trial length, so
adding a plan later is one entry in `PLANS` — nothing else changes.

Status vs. plan
    `Organization.plan` is what is *stored*: `trial`, `legacy`, or one of `PAID_PLANS`.
    `trial_expired` and `plan_expired` are **computed statuses**, never stored — a cron that
    flipped them would leave a window where a finished plan keeps answering visitors, and would
    fail open if the cron died.

Fail-safe default
    `Organization.plan` defaults to `legacy` (unlimited), and an unrecognised plan string
    resolves to `legacy` too. Only the self-serve signup path writes `trial`, so a code path that
    forgets to set a plan — or a typo in a stored value — can never lock a paying client out.

Counts, not booleans (docs/22 §4.1)
    A limit is `0` = not included, a positive integer = that many, `None` = unlimited. The three
    original on/off flags (`workflows`, `n8n`, `tool_calling`) are kept as **derived properties**
    over those counts, so every existing caller of `Entitlements.allows()` works unchanged.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.core.errors import AppError

PlanStatus = Literal[
    "trial", "trial_expired", "legacy", "starter", "pro", "business", "plan_expired"
]

#: A reply is reserved as a question + answer pair. Also the smallest balance that can still
#: answer one visitor, which is why "exhausted" means fewer than this many messages left.
MESSAGES_PER_EXCHANGE = 2

_MB = 1024 * 1024
_GB = 1024 * _MB

#: On/off features. These are exactly the names `Entitlements.allows()` accepts, and each one
#: must be a truthy/falsy attribute or property of `PlanSpec`.
FEATURES = (
    "workflows",
    "n8n",
    "tool_calling",
    "webhooks",
    "api_write",
    "analytics_advanced",
    "analytics_export",
    "remove_branding",
)

#: Countable / sized limits. Never passed to `allows()` — they name the `details.feature` of the
#: `plan_limit()` raised when a cap is hit, so the UI can key a locked state off them.
LIMIT_FEATURES = (
    "workspaces",
    "agents",
    "messages",
    "knowledge_bases",
    "documents",
    "storage",
    "channels",
    "team_members",
)

#: Every value `details.feature` may carry. The frontend's locked states key off this set, so it
#: is an API contract: renaming one is a breaking change.
GATE_FEATURES = FEATURES + LIMIT_FEATURES

#: `feature` name → the `PlanSpec` attribute holding its numeric cap, for `limit_for()`.
_LIMIT_ATTR = {
    "workspaces": "max_workspaces",
    "agents": "max_agents",
    "messages": "max_messages",
    "knowledge_bases": "max_knowledge_bases",
    "documents": "max_documents",
    "storage": "storage_bytes",
    "team_members": "max_team_members",
    "workflows": "max_workflows",
    "tools": "max_tools",
    "webhooks": "max_webhooks",
}


@dataclass(frozen=True, slots=True)
class PlanSpec:
    # ── shape, agents, messages ───────────────────────────────────────────────
    trial_days: int | None
    max_workspaces: int | None
    max_agents: int | None
    max_messages: int | None
    playground_per_day: int | None

    # ── automation counts (0 = the feature is off) ────────────────────────────
    max_workflows: int | None
    max_tools: int | None
    max_webhooks: int | None
    n8n_enabled: bool

    # ── knowledge ─────────────────────────────────────────────────────────────
    max_knowledge_bases: int | None
    #: Soft guide shown in the UI. `storage_bytes` is the hard limit (docs/22 §3 rule 2).
    max_documents: int | None
    storage_bytes: int | None

    # ── reach and team ────────────────────────────────────────────────────────
    #: `None` = every supported channel.
    channels: frozenset[str] | None
    max_team_members: int | None

    # ── tiers ─────────────────────────────────────────────────────────────────
    api_access: Literal["read", "full"]
    analytics: Literal["basic", "advanced", "advanced_export"]
    remove_branding: bool
    support: Literal["email", "priority"]

    #: Publishing an agent to a live channel needs a verified email.
    publish_needs_verified_email: bool

    # ── commercial metadata: DISPLAY AND INVOICING ONLY ───────────────────────
    #: Never read for an access decision. Used by the pricing endpoint and by the admin panel to
    #: say what to invoice for an extra-message pack (docs/22 §7).
    #: Per-currency price lists in INTEGER MINOR UNITS (cents / euro cents / paise), excl. tax.
    #: Never floats. `None` = not a sold plan. Read through `price_for()` / `pack_price_for()`.
    price_minor: Mapping[str, int] | None = None
    extra_message_pack_size: int | None = None
    extra_message_pack_price_minor: Mapping[str, int] | None = None

    # ── derived USD views: whole dollars, kept so every pre-currency caller still works ──
    @property
    def price_usd_month(self) -> int | None:
        return None if self.price_minor is None else self.price_minor["USD"] // 100

    @property
    def extra_message_pack_usd(self) -> int | None:
        if self.extra_message_pack_price_minor is None:
            return None
        return self.extra_message_pack_price_minor["USD"] // 100

    # ── derived on/off flags: keep every pre-existing caller working ──────────
    @property
    def workflows(self) -> bool:
        return self.max_workflows != 0

    @property
    def tool_calling(self) -> bool:
        return self.max_tools != 0

    @property
    def n8n(self) -> bool:
        return self.n8n_enabled

    @property
    def webhooks(self) -> bool:
        return self.max_webhooks != 0

    @property
    def api_write(self) -> bool:
        return self.api_access == "full"

    @property
    def analytics_advanced(self) -> bool:
        return self.analytics in ("advanced", "advanced_export")

    @property
    def analytics_export(self) -> bool:
        return self.analytics == "advanced_export"


#: `None` = unlimited. `trial_expired` / `plan_expired` are derived by `get_entitlements`, so
#: `_EXPIRED` below is not listed here — it is not a plan anyone can be stored as.
PLANS: dict[str, PlanSpec] = {
    "trial": PlanSpec(
        trial_days=10,
        max_workspaces=1,
        max_agents=1,
        max_messages=500,
        playground_per_day=50,
        max_workflows=0,
        max_tools=0,
        max_webhooks=0,
        n8n_enabled=False,
        max_knowledge_bases=1,
        max_documents=10,
        storage_bytes=100 * _MB,
        channels=frozenset({"web"}),
        max_team_members=1,
        api_access="read",
        analytics="basic",
        remove_branding=False,
        support="email",
        publish_needs_verified_email=True,
    ),
    "starter": PlanSpec(
        trial_days=None,
        max_workspaces=1,
        max_agents=3,
        max_messages=2_000,
        playground_per_day=None,
        max_workflows=0,
        max_tools=0,
        max_webhooks=0,
        n8n_enabled=False,
        max_knowledge_bases=1,
        max_documents=20,
        storage_bytes=500 * _MB,
        channels=frozenset({"web"}),
        max_team_members=1,
        api_access="read",
        analytics="basic",
        remove_branding=False,
        support="email",
        publish_needs_verified_email=True,
        price_minor={"USD": 4900, "EUR": 4500, "INR": 149900},
        extra_message_pack_size=500,
        extra_message_pack_price_minor={"USD": 600, "EUR": 500, "INR": 19900},
    ),
    "pro": PlanSpec(
        trial_days=None,
        max_workspaces=2,
        max_agents=10,
        max_messages=10_000,
        playground_per_day=None,
        max_workflows=10,
        max_tools=8,
        max_webhooks=5,
        n8n_enabled=True,
        max_knowledge_bases=5,
        max_documents=100,
        storage_bytes=5 * _GB,
        channels=frozenset({"web", "whatsapp", "instagram", "facebook"}),
        max_team_members=5,
        api_access="full",
        analytics="advanced",
        remove_branding=True,
        support="priority",
        publish_needs_verified_email=True,
        price_minor={"USD": 9900, "EUR": 8900, "INR": 349900},
        extra_message_pack_size=500,
        extra_message_pack_price_minor={"USD": 500, "EUR": 450, "INR": 14900},
    ),
    "business": PlanSpec(
        trial_days=None,
        max_workspaces=5,
        max_agents=30,
        max_messages=30_000,
        playground_per_day=None,
        max_workflows=None,
        max_tools=None,
        max_webhooks=None,
        n8n_enabled=True,
        max_knowledge_bases=20,
        max_documents=500,
        storage_bytes=10 * _GB,
        channels=None,
        max_team_members=15,
        api_access="full",
        analytics="advanced_export",
        remove_branding=True,
        support="priority",
        publish_needs_verified_email=True,
        price_minor={"USD": 19900, "EUR": 17900, "INR": 699900},
        extra_message_pack_size=500,
        extra_message_pack_price_minor={"USD": 400, "EUR": 350, "INR": 9900},
    ),
    "legacy": PlanSpec(
        trial_days=None,
        max_workspaces=None,
        max_agents=None,
        max_messages=None,
        playground_per_day=None,
        max_workflows=None,
        max_tools=None,
        max_webhooks=None,
        n8n_enabled=True,
        max_knowledge_bases=None,
        max_documents=None,
        storage_bytes=None,
        channels=None,
        max_team_members=None,
        api_access="full",
        analytics="advanced_export",
        remove_branding=True,
        support="priority",
        publish_needs_verified_email=False,
    ),
}

#: What a finished trial or a lapsed paid plan keeps: read-only access to what already exists,
#: and nothing that spends money or reaches a visitor. Never deletes anything (docs/22 §11 rule 2).
_EXPIRED = PlanSpec(
    trial_days=None,
    max_workspaces=1,
    max_agents=1,
    max_messages=0,
    playground_per_day=0,
    max_workflows=0,
    max_tools=0,
    max_webhooks=0,
    n8n_enabled=False,
    max_knowledge_bases=1,
    max_documents=0,
    storage_bytes=0,
    channels=frozenset(),
    max_team_members=1,
    api_access="read",
    analytics="basic",
    remove_branding=False,
    support="email",
    publish_needs_verified_email=True,
)

#: Plans that are sold. Ordered cheapest first — the pricing page renders them in this order.
PAID_PLANS = ("starter", "pro", "business")

#: Currencies a price list exists for (docs/22 §3, ADR-106). USD is the default and the fallback.
SUPPORTED_CURRENCIES = ("USD", "EUR", "INR")
DEFAULT_CURRENCY = "USD"


def price_for(plan: str, currency: str) -> int:
    """Monthly price of `plan` in `currency`, in minor units. Raises on an unknown plan, an
    unsold plan or an unknown currency — it never silently falls back to USD, because a wrong
    currency on a ledger row is a wrong amount of money."""
    spec = PLANS.get(plan)
    if spec is None or spec.price_minor is None:
        raise ValueError(f"{plan!r} is not a sold plan")
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported currency {currency!r}")
    return spec.price_minor[currency]


def pack_price_for(plan: str, currency: str) -> int:
    """Price of one extra-message pack on `plan` in `currency`, in minor units. Same raising rules
    as `price_for`."""
    spec = PLANS.get(plan)
    if spec is None or spec.extra_message_pack_price_minor is None:
        raise ValueError(f"{plan!r} does not sell extra-message packs")
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported currency {currency!r}")
    return spec.extra_message_pack_price_minor[currency]
#: What an admin may grant from the panel (docs/22 §8.2). `trial` and `legacy` are included so
#: staff can reset a workspace or mark one of their own unlimited.
GRANTABLE_PLANS = (*PAID_PLANS, "trial", "legacy")
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
    #: Why a plan is expired: "time" | "messages" | None.
    expired_reason: str | None = None
    #: The cap the usage meter shows (`312 / 500`). Kept after expiry, when the *enforced* cap is 0.
    meter_limit: int | None = None
    #: Extra messages bought for the current period (docs/22 §7). Raises the enforced cap; does
    #: not carry over, so a new period resets this to 0.
    extra_messages: int = 0
    #: When a granted paid plan lapses. `None` = no expiry (trial, legacy, or granted open-ended).
    plan_expires_at: dt.datetime | None = None

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
    def max_knowledge_bases(self) -> int | None:
        return self.spec.max_knowledge_bases

    @property
    def max_documents(self) -> int | None:
        return self.spec.max_documents

    @property
    def storage_bytes(self) -> int | None:
        return self.spec.storage_bytes

    @property
    def max_team_members(self) -> int | None:
        return self.spec.max_team_members

    @property
    def effective_max_messages(self) -> int | None:
        """The cap actually enforced: the plan's allowance plus any packs bought this period.

        Every message check reads this, never `spec.max_messages` — a pack that did not raise
        the enforced cap would be money taken for nothing.
        """
        limit = self.spec.max_messages
        return None if limit is None else limit + self.extra_messages

    @property
    def is_metered(self) -> bool:
        return self.spec.max_messages is not None

    @property
    def is_expired(self) -> bool:
        return self.status in ("trial_expired", "plan_expired")

    @property
    def is_paid(self) -> bool:
        return self.status in PAID_PLANS

    @property
    def bot_replies(self) -> bool:
        """Whether the bot may answer visitors right now."""
        if self.is_expired:
            return False
        limit = self.effective_max_messages
        return limit is None or limit - self.messages_used >= MESSAGES_PER_EXCHANGE

    @property
    def agents_writable(self) -> bool:
        return not self.is_expired

    @property
    def messages_remaining(self) -> int | None:
        limit = self.effective_max_messages
        return None if limit is None else max(limit - self.messages_used, 0)

    def allows(self, feature: str) -> bool:
        if feature not in FEATURES:
            raise ValueError(f"unknown plan feature {feature!r}")
        return bool(getattr(self.spec, feature))

    def allows_channel(self, kind: str) -> bool:
        """`channels=None` means every supported channel; an empty set means none."""
        return self.spec.channels is None or kind in self.spec.channels

    def limit_for(self, feature: str) -> int | None:
        """The numeric cap behind a countable feature, for `3 of 10 used` UI. `None` = unlimited."""
        if feature == "messages":
            return self.effective_max_messages
        try:
            attr = _LIMIT_ATTR[feature]
        except KeyError:
            raise ValueError(f"{feature!r} has no numeric limit") from None
        value: int | None = getattr(self.spec, attr)
        return value

    def days_left(self, now: dt.datetime) -> int | None:
        """Whole days remaining (rounded up), 0 once over. `None` when nothing is counting down."""
        deadline = self.trial_ends_at or self.plan_expires_at
        if deadline is None:
            return None
        seconds = (deadline - now).total_seconds()
        return -(-int(seconds) // 86400) if seconds > 0 else 0


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def get_entitlements(
    plan: str | None,
    *,
    trial_ends_at: dt.datetime | None = None,
    plan_expires_at: dt.datetime | None = None,
    messages_used: int = 0,
    extra_messages: int = 0,
    now: dt.datetime | None = None,
) -> Entitlements:
    """Resolve what an org may do. Pure — callers load `messages_used` (see `billing.usage`).

    An unknown plan value resolves to `legacy`, not to the most restrictive plan: a value this
    code does not recognise is far more likely a custom plan added later than a trial, and
    locking a paying client out over a typo is the worse failure.
    """
    now = now or dt.datetime.now(tz=dt.UTC)
    name = plan or DEFAULT_PLAN
    spec = PLANS.get(name)
    if spec is None:
        name, spec = DEFAULT_PLAN, PLANS[DEFAULT_PLAN]

    if name == "trial":
        ends = _aware(trial_ends_at) if trial_ends_at else None
        reason: str | None = None
        if ends is not None and now >= ends:
            reason = "time"
        elif spec.max_messages is not None and messages_used >= spec.max_messages:
            reason = "messages"
        if reason is not None:
            return Entitlements(
                "trial_expired", _EXPIRED, messages_used, ends, reason, spec.max_messages
            )
        return Entitlements("trial", spec, messages_used, ends, None, spec.max_messages)

    if name in PAID_PLANS:
        expires = _aware(plan_expires_at) if plan_expires_at else None
        limit = None if spec.max_messages is None else spec.max_messages + extra_messages
        if expires is not None and now >= expires:
            # The grant lapsed. Everything is kept, read-only — re-granting restores it whole.
            return Entitlements(
                "plan_expired", _EXPIRED, messages_used, None, "time", limit, 0, expires
            )
        return Entitlements(
            name,  # type: ignore[arg-type]  # name is narrowed to PAID_PLANS above
            spec,
            messages_used,
            None,
            None,
            limit,
            extra_messages,
            expires,
        )

    return Entitlements("legacy", spec, messages_used, None)


def plan_limit(feature: str, message: str | None = None) -> AppError:
    """The 402 every gate raises. `details.feature` lets the UI show the right locked state."""
    return AppError(
        "plan_limit",
        message or "This feature isn't included in your plan. Upgrade to unlock it.",
        402,
        details={"feature": feature},
    )

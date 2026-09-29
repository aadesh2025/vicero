"""Admin console schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel


class OrgMemberOut(BaseModel):
    """One person's access to an org — the thing staff actually need to see.

    A member *count* answers "how many", which is never the question. "Who can publish to this
    client's agent, and which address do they use" is.
    """

    email: str
    role: str
    status: str
    is_staff: bool


class OrgAdminOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str | None = None
    members: int
    #: Every active member with their role. Populated on the admin roster so staff can see who
    #: has access to which client without switching org.
    member_list: list[OrgMemberOut] = []
    agents: int
    #: Agents whose newest draft is ahead of what's live. A client can edit but not publish,
    #: so this is how staff notice work waiting for review without opening every builder.
    agents_with_unpublished_changes: int = 0
    #: Vicero's own visual-workflow-builder Workflows (docs/17 §3) whose latest version has
    #: been explicitly submitted for review (`WorkflowVersion.status == "in_review"`) — NOT the
    #: same thing as an n8n automation (see `AutomationOut`). Unlike the agents count above,
    #: this reads a real status a workflow author set, not a version-number proxy.
    workflows_awaiting_review: int = 0
    created_at: dt.datetime
    deleted: bool

    # ── billing (docs/22 §8.2) ────────────────────────────────────────────────
    plan: str
    #: The computed status `get_entitlements()` returns — trial | trial_expired | legacy |
    #: starter | pro | business | plan_expired. Never stored; read fresh every time.
    status: str
    plan_source: str
    plan_expires_at: dt.datetime | None = None
    plan_note: str | None = None
    messages_used: int
    #: `None` = unlimited (legacy). Already includes any packs bought this period
    #: (`Entitlements.effective_max_messages`) — never the raw plan cap.
    messages_limit: int | None = None
    unanswered_messages: int = 0
    storage_bytes_used: int = 0
    #: `None` = unlimited (legacy).
    storage_bytes_limit: int | None = None
    #: The most recent billing cycle's computed state — paid | pending | overdue | waived.
    #: `None` when the org has never had a billing cycle (trial, legacy, or never billed).
    payment_state: str | None = None
    #: The email on the org's `owner` membership. `None` is a data problem (every org should
    #: have exactly one), surfaced rather than hidden behind an empty string.
    owner_email: str | None = None


class UserMembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: str


class UserAdminOut(BaseModel):
    id: uuid.UUID
    email: str
    is_staff: bool
    #: A machine account (provisioning login), not a person. Hidden from the roster by default.
    is_system: bool = False
    is_active: bool
    orgs: int
    #: Which orgs this person belongs to, and as what.
    memberships: list[UserMembershipOut] = []
    created_at: dt.datetime


class OrgUsageRow(BaseModel):
    organization_id: uuid.UUID
    name: str
    tokens_prompt: int
    tokens_completion: int
    requests: int
    cost_micros: int


class PlatformUsageOut(BaseModel):
    organizations: int
    users: int
    agents: int
    conversations: int
    messages: int
    tokens_prompt: int
    tokens_completion: int
    cost_micros: int
    top_orgs: list[OrgUsageRow]


class HealthOut(BaseModel):
    database: bool
    redis: bool
    organizations: int
    users: int
    conversations: int
    messages: int
    #: Whether the L2 injection classifier can actually run (docs/11 §4-L2). It **fails open**,
    #: so an outage or a missing platform key looks exactly like "no attacks today" from the
    #: outside — this is the one place that difference is visible without reading logs.
    guard_injection_available: bool = True
    #: Why it is not running, when it is not. `None` while healthy.
    guard_injection_reason: str | None = None
    guard_injection_model: str | None = None


class N8nSignatureFindingOut(BaseModel):
    """One bound n8n tool and whether its workflow verifies Vicero's signature."""

    organization_slug: str
    organization_name: str
    agent_name: str | None
    tool_name: str
    enabled: bool
    workflow_id: str | None
    workflow_name: str | None
    #: `verified` | `unverified` | `unresolved` (workflow not found in n8n, so it cannot be judged).
    status: str
    #: Why it is not verified. `None` when `status == "verified"`.
    reason: str | None = None


class N8nSignatureAuditOut(BaseModel):
    verified: int = 0
    unverified: int = 0
    unresolved: int = 0
    #: Only the bindings that need attention (unverified + unresolved), unverified first.
    findings: list[N8nSignatureFindingOut] = []
    #: Set when n8n could not be queried at all; the counts are then meaningless.
    error: str | None = None
    fix_hint: str | None = None


class AutomationBindingOut(BaseModel):
    """One Vicero tool pointing at this workflow."""

    organization_slug: str
    organization_name: str
    agent_name: str | None
    tool_name: str
    enabled: bool
    mode: str | None


class AutomationOut(BaseModel):
    """One n8n workflow, resolved to its owning org via its tags."""

    id: str
    name: str
    active: bool
    tags: list[str]
    # The org slug the tags resolve to, or the sentinels below.
    owner: str
    # "org" | "internal" | "shared-template" | "untagged" | "unknown-org"
    owner_kind: str
    organization_name: str | None = None
    webhook_url: str | None = None
    bindings: list[AutomationBindingOut] = []


class AutomationsOverviewOut(BaseModel):
    workflows: list[AutomationOut]
    # Set when n8n can't be reached or has no API key — the page says so rather than
    # rendering an empty table that looks like "no automations exist".
    error: str | None = None


class FeatureFlagOut(BaseModel):
    key: str
    enabled: bool
    description: str | None = None
    updated_at: dt.datetime


class FeatureFlagUpdate(BaseModel):
    enabled: bool
    description: str | None = None


# ── billing: grant / revoke / packs (docs/22 §5, §8.2) ──────────────────────────
class PlanGrantOut(BaseModel):
    """One row of billing evidence — `plan_grants` (docs/22 §5)."""

    id: uuid.UUID
    action: str
    from_plan: str | None = None
    to_plan: str | None = None
    expires_at: dt.datetime | None = None
    extra_messages: int | None = None
    amount_usd_cents: int | None = None
    note: str | None = None
    actor_email: str | None = None
    invoiced: bool = False
    created_at: dt.datetime


class BillingCycleOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str | None = None
    plan: str
    period_start: dt.datetime
    period_end: dt.datetime
    amount_usd_cents: int
    #: The raw stored value — pending | paid | waived.
    status: str
    #: The **computed** value `cycles.payment_state()` returns — adds `overdue` when a
    #: `pending` cycle's `period_end` has passed. This is what the admin panel's filters and
    #: collections list actually key off.
    payment_state: str
    paid_at: dt.datetime | None = None
    method: str | None = None
    reference: str | None = None
    note: str | None = None
    created_at: dt.datetime


class OrgAdminDetailOut(OrgAdminOut):
    """`OrgAdminOut` plus the billing history (docs/22 §8.2)."""

    recent_grants: list[PlanGrantOut] = []
    billing_cycles: list[BillingCycleOut] = []


class GrantPlanIn(BaseModel):
    plan: str
    #: `None` = no expiry (the operator's own workspaces, demos — docs/22 §16.5).
    expires_at: dt.datetime | None = None
    note: str
    #: The normal case: the client already paid, so the opened cycle is `paid`, not `pending`.
    payment_received: bool = False
    method: str | None = None
    reference: str | None = None


class RevokePlanIn(BaseModel):
    note: str


class AddPacksIn(BaseModel):
    #: Number of packs, never a message count — the server computes messages and price from
    #: the org's *current* plan (docs/22 §8.2 rule 6). A typed positive int, not "a number of
    #: messages", so the client can never invent its own price.
    packs: int
    note: str


class MarkCyclePaidIn(BaseModel):
    method: str
    reference: str | None = None
    note: str | None = None
    #: "Mark paid & renew" — also extends `plan_expires_at` by 30 days and opens the next
    #: cycle as `pending`, in one transaction (docs/22 §5.1).
    renew: bool = False


class WaiveCycleIn(BaseModel):
    note: str


class PackOut(BaseModel):
    """One `pack_added` grant, for the admin panel's Uninvoiced packs reconciliation view."""

    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str | None = None
    extra_messages: int | None = None
    amount_usd_cents: int | None = None
    note: str | None = None
    invoiced: bool
    created_at: dt.datetime


class PackInvoicedIn(BaseModel):
    invoiced: bool

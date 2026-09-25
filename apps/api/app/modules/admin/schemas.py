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
    #: BotForge's own visual-workflow-builder Workflows (docs/17 §3) whose latest version has
    #: been explicitly submitted for review (`WorkflowVersion.status == "in_review"`) — NOT the
    #: same thing as an n8n automation (see `AutomationOut`). Unlike the agents count above,
    #: this reads a real status a workflow author set, not a version-number proxy.
    workflows_awaiting_review: int = 0
    created_at: dt.datetime
    deleted: bool


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
    """One bound n8n tool and whether its workflow verifies BotForge's signature."""

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
    """One BotForge tool pointing at this workflow."""

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

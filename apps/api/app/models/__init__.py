"""SQLAlchemy models. Importing this package registers every table on Base.metadata
(used by Alembic autogenerate and by tests)."""

from app.models.agent_steps import AgentStep
from app.models.agent_tests import AgentTest, AgentTestRun
from app.models.agents import Agent, AgentVersion, ProviderCredential
from app.models.campaigns import Campaign
from app.models.canned_responses import CannedResponse
from app.models.channels import Channel
from app.models.contacts import Contact
from app.models.conversations import (
    PLAYGROUND_CHANNEL,
    Conversation,
    ConversationFlag,
    Message,
)
from app.models.crm import CrmContact
from app.models.help_articles import HelpArticle
from app.models.identity import (
    EmailVerificationToken,
    Invitation,
    MagicLinkToken,
    Membership,
    OAuthAccount,
    Organization,
    PasswordResetToken,
    Session,
    User,
)
from app.models.inbox import Handoff
from app.models.knowledge import Chunk, Document, KnowledgeBase
from app.models.macros import Macro
from app.models.mcp_servers import MCPServer
from app.models.platform import (
    ApiKey,
    AuditLog,
    FeatureFlag,
    OrgMessageUsage,
    Quota,
    Subscription,
    UsageRecord,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.models.tools import Tool, ToolRun
from app.models.widget_configs import WidgetConfig
from app.models.workflow_tests import WorkflowTest, WorkflowTestRun
from app.models.workflows import Workflow, WorkflowRun, WorkflowStep, WorkflowVersion

__all__ = [
    "PLAYGROUND_CHANNEL",
    "Agent",
    "AgentStep",
    "AgentTest",
    "AgentTestRun",
    "AgentVersion",
    "ApiKey",
    "AuditLog",
    "Campaign",
    "CannedResponse",
    "Channel",
    "Chunk",
    "Contact",
    "Conversation",
    "ConversationFlag",
    "CrmContact",
    "Document",
    "EmailVerificationToken",
    "FeatureFlag",
    "Handoff",
    "HelpArticle",
    "Invitation",
    "KnowledgeBase",
    "MCPServer",
    "Macro",
    "MagicLinkToken",
    "Membership",
    "Message",
    "OAuthAccount",
    "OrgMessageUsage",
    "Organization",
    "PasswordResetToken",
    "ProviderCredential",
    "Quota",
    "Session",
    "Subscription",
    "Tool",
    "ToolRun",
    "UsageRecord",
    "User",
    "WebhookDelivery",
    "WebhookEndpoint",
    "WidgetConfig",
    "Workflow",
    "WorkflowRun",
    "WorkflowStep",
    "WorkflowTest",
    "WorkflowTestRun",
    "WorkflowVersion",
]

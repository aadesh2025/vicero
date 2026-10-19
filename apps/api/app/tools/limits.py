"""Small shared rules for what an agent may be given (docs/28 section 5.3), kept apart so the tool service and the
MCP service can both use them without importing each other."""

from __future__ import annotations

from app.models import Tool

#: More tools than this and tool-choice accuracy and prompt cost both degrade (docs/28 sections 5.3, 13).
MAX_TOOLS_PER_AGENT = 20


def is_managed_mcp(tool: Tool) -> bool:
    """A `type="mcp"` row that the per-agent MCP binding created and keeps in step with the server's tool cache.
    Older rows (a person pointed one tool at a server by hand) are not managed and keep their old behaviour."""
    return tool.type == "mcp" and bool((tool.config or {}).get("managed"))

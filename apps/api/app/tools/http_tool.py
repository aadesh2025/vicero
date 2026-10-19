"""Execution of user-defined HTTP tools (docs/06 §5).

`type="http"` is the original shape (`{"method", "url", "headers", "body", "timeout"}`); it now runs on the
API tool executor with no connection (docs/28 T1.4), so it gets the egress guard, URL-encoding of
placeholders, header sanitising and the size cap for free. Arguments still render as strings here.
"""

from __future__ import annotations

from typing import Any

from app.tools.api_tool import execute_api_tool
from app.tools.base import ToolResult


async def execute_http_tool(config: dict[str, Any], args: dict[str, Any]) -> ToolResult:
    return await execute_api_tool(config, args, legacy=True)

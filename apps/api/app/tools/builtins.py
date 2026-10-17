"""Built-in tools with JSON-schema declarations (docs/06 §5)."""

from __future__ import annotations

import ast
import datetime as dt
import operator
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.catalog import catalog_enabled
from app.core.config import settings
from app.core.ssrf import is_blocked_host
from app.llm.registry import build_embedding_provider
from app.models import KnowledgeBase
from app.rag import retrieval
from app.tools.base import ToolContext, ToolResult

ToolFn = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]


@dataclass(slots=True)
class BuiltinTool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    run: ToolFn


# ── get_datetime ─────────────────────────────────────────────────────────────────
async def _get_datetime(_ctx: ToolContext, _args: dict[str, Any]) -> ToolResult:
    now = dt.datetime.now(tz=dt.UTC)
    return ToolResult(output={"utc": now.isoformat(), "unix": int(now.timestamp())})


# ── calculator ───────────────────────────────────────────────────────────────────
_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return float(_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right)))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return float(_OPS[type(node.op)](_safe_eval(node.operand)))
    raise ValueError("unsupported expression")


async def _calculator(_ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    expr = str(args.get("expression", "")).strip()
    if not expr:
        return ToolResult(output={}, status="error", error="expression is required")
    try:
        value = _safe_eval(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as exc:
        return ToolResult(output={}, status="error", error=f"cannot evaluate: {exc}")
    return ToolResult(output={"result": value})


# ── knowledge_search ─────────────────────────────────────────────────────────────
async def _knowledge_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(output={}, status="error", error="query is required")
    top_k = int(args.get("top_k", 5))

    rag = ctx.version.rag_config or {}
    kb_stmt = select(KnowledgeBase).where(
        KnowledgeBase.organization_id == ctx.org_id, KnowledgeBase.deleted_at.is_(None)
    )
    configured = rag.get("knowledge_base_ids") or []
    if configured:
        try:
            kb_ids = [uuid.UUID(str(k)) for k in configured]
            kb_stmt = kb_stmt.where(KnowledgeBase.id.in_(kb_ids))
        except (ValueError, TypeError):
            pass
    kbs = list((await ctx.session.execute(kb_stmt)).scalars().all())
    if not kbs:
        return ToolResult(output={"results": [], "note": "no knowledge bases available"})

    embedder = build_embedding_provider(kbs[0].embedding_provider, kbs[0].embedding_model)
    citations = await retrieval.search(
        ctx.session,
        ctx.org_id,
        [kb.id for kb in kbs],
        query,
        embedder,
        top_k=top_k,
        hybrid=True,
        fts_config=kbs[0].fts_config,
    )
    results = [
        {
            "content": c.content,
            "source": c.metadata.get("filename") or c.metadata.get("source_url") or "document",
            "score": c.score,
        }
        for c in citations
    ]
    return ToolResult(output={"results": results})


# ── http_request (guarded) ─────────────────────────────────────────────────────────
async def _http_request(_ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from urllib.parse import urlparse

    import httpx

    url = str(args.get("url", "")).strip()
    method = str(args.get("method", "GET")).upper()
    if not url:
        return ToolResult(output={}, status="error", error="url is required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ToolResult(output={}, status="error", error="only http(s) URLs are allowed")
    if is_blocked_host(parsed.hostname):
        return ToolResult(output={}, status="error", error="refusing to call a private/loopback host")
    headers = args.get("headers") if isinstance(args.get("headers"), dict) else None
    body = args.get("body")
    try:
        async with httpx.AsyncClient(timeout=settings.tool_timeout_seconds, follow_redirects=False) as client:
            resp = await client.request(method, url, headers=headers, json=body if body is not None else None)
            text = resp.text[:4000]
        return ToolResult(output={"status_code": resp.status_code, "body": text})
    except httpx.HTTPError as exc:
        return ToolResult(output={}, status="error", error=f"request failed: {exc}")


# ── web_search (docs/11 §L7, Phase G) ──────────────────────────────────────────────
async def _web_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Search the web, scoped to the agent's allowed domains.

    Off by default and denied outright when no domain is allowlisted — an empty allowlist
    means "nothing is searchable", not "search anything". Results are wrapped as untrusted
    content before the model sees them.
    """
    from app.tools import web_search as ws

    enabled, allowlist, blocklist = ws.agent_policy(ctx.version.features)
    if not enabled:
        return ToolResult(
            output={"results": [], "note": "web search is not enabled for this agent"},
            status="error",
            error="web_search_disabled",
        )
    try:
        results = await ws.search(
            str(args.get("query") or ""), allowlist=allowlist, blocklist=blocklist
        )
    except ws.WebSearchError as exc:
        return ToolResult(output={"results": []}, status="error", error=str(exc))
    except Exception as exc:  # provider/network failure must not break the turn
        return ToolResult(output={"results": []}, status="error", error=f"web search failed: {exc}")
    return ToolResult(output=ws.as_tool_output(results))


# ── request_handoff ──────────────────────────────────────────────────────────────────
async def _request_handoff(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from app.chat.handoff import trigger_handoff
    from app.models import Conversation

    if ctx.conversation_id is None:
        return ToolResult(output={"status": "unavailable"}, status="error", error="no conversation")
    conv = await ctx.session.get(Conversation, ctx.conversation_id)
    if conv is None:
        return ToolResult(output={}, status="error", error="conversation not found")
    await trigger_handoff(
        ctx.session, conv, requested_by="bot", reason=str(args.get("reason") or "agent requested a human")
    )
    return ToolResult(output={"status": "handoff_requested"})


# ── product catalog (docs/26 §3.3) ──────────────────────────────────────────────────
_MAX_COUNT = 3


async def _search_products(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from app.modules.products import service as products

    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(output={}, status="error", error="query is required")
    if not catalog_enabled(ctx.version):
        return ToolResult(output={"results": []}, status="error", error="product catalog is not enabled")
    found = await products.search_products(ctx.session, ctx.org_id, query, limit=5)
    counts = await products.image_counts(ctx.session, [p.id for p in found])
    return ToolResult(output={"results": [products.summarize(p, counts.get(p.id, 0)) for p in found]})


async def _send_product_images(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from app.models import Product
    from app.modules.products import service as products

    queue = ctx.outbound_media
    if queue is None or not catalog_enabled(ctx.version):
        return ToolResult(output={}, status="error", error="photos cannot be sent in this conversation")
    try:
        product_id = uuid.UUID(str(args.get("product_id", "")))
    except ValueError:
        return ToolResult(output={"error": "not_found"})
    # The model can hallucinate ids, so the org (and that the product is live) is re-checked here.
    product = (
        await ctx.session.execute(
            select(Product).where(
                Product.id == product_id, Product.organization_id == ctx.org_id, Product.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    if product is None:
        return ToolResult(output={"error": "not_found"})
    assets = await products.image_assets(ctx.session, product.id)
    if not assets:
        return ToolResult(output={"error": "no_images"})
    try:
        count = max(1, min(int(args.get("count", 1)), _MAX_COUNT))
    except (TypeError, ValueError):
        count = 1
    room = max(settings.max_images_per_reply - len(queue), 0)
    chosen = [a for a in assets if a.id not in queue][: min(count, room)]
    queue.extend(a.id for a in chosen)
    return ToolResult(output={"queued": len(chosen)})


CATALOG_TOOLS: dict[str, BuiltinTool] = {
    "search_products": BuiltinTool(
        name="search_products",
        description="Search this business's product catalog by name, SKU or description. Returns up to 5 products.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "what the customer is looking for"}},
            "required": ["query"],
        },
        run=_search_products,
    ),
    "send_product_images": BuiltinTool(
        name="send_product_images",
        description="Send the customer photos of one product (use an id from search_products).",
        parameters={
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": _MAX_COUNT},
            },
            "required": ["product_id"],
        },
        run=_send_product_images,
    ),
}


BUILTINS: dict[str, BuiltinTool] = {
    "get_datetime": BuiltinTool(
        name="get_datetime",
        description="Get the current UTC date and time.",
        parameters={"type": "object", "properties": {}, "required": []},
        run=_get_datetime,
    ),
    "calculator": BuiltinTool(
        name="calculator",
        description="Evaluate a basic arithmetic expression (+, -, *, /, //, %, **).",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "e.g. (3 + 4) * 2"}},
            "required": ["expression"],
        },
        run=_calculator,
    ),
    "knowledge_search": BuiltinTool(
        name="knowledge_search",
        description="Search the agent's knowledge bases for relevant passages.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
        run=_knowledge_search,
    ),
    "http_request": BuiltinTool(
        name="http_request",
        description="Make a guarded HTTP request to a public URL (no private/loopback hosts).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "headers": {"type": "object"},
                "body": {"type": "object"},
            },
            "required": ["url"],
        },
        run=_http_request,
    ),
    "web_search": BuiltinTool(
        name="web_search",
        description="Search the web, restricted to this agent's allowed domains. Cite source URLs.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        run=_web_search,
    ),
    "request_handoff": BuiltinTool(
        name="request_handoff",
        description="Escalate the conversation to a human agent when you cannot help or the user asks.",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "why a human is needed"}},
            "required": [],
        },
        run=_request_handoff,
    ),
}

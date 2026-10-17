"""Execution of the interactive-block tools (docs/27 Phase 2).

The schemas, caps, guard and persistence live in `app.chat.blocks`; this is only the part that needs a
`ToolContext`, so `app.chat` keeps no import of `app.tools` (tests/test_architecture.py pins the cycles).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.chat.blocks import BLOCK_TOOLS, MAX_BLOCKS_PER_TURN, MAX_CARDS, BlockError, build_block
from app.core.logging import get_logger
from app.tools.base import ToolContext, ToolResult

log = get_logger("tools.blocks")

_SHOWN = {
    "status": "shown",
    "note": "The visitor now sees it and will answer by tapping. Reply with at most one short sentence and wait; "
    "do not repeat the choices in text.",
}


async def _cards_from_catalog(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Cards built from this org's catalog rows — never from model-supplied titles, prices or images."""
    from sqlalchemy import func

    from app.models import Product, ProductImage

    stmt = select(Product).where(Product.organization_id == ctx.org_id, Product.is_active.is_(True))
    ids: list[uuid.UUID] = []
    for raw in args.get("product_ids") or []:
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    if ids:
        stmt = stmt.where(Product.id.in_(ids[:MAX_CARDS]))
    else:
        query = str(args.get("query") or "").strip()[:80]
        if not query:
            raise BlockError("give product_ids or a query")
        like = "%" + query.replace("%", r"\%").replace("_", r"\_") + "%"
        stmt = stmt.where(Product.name.ilike(like) | func.coalesce(Product.sku, "").ilike(like))
    products = list((await ctx.session.execute(stmt.limit(MAX_CARDS))).scalars().all())
    if not products:
        raise BlockError("no matching products in the catalog; answer in text instead")
    first_image = {
        pid: mid
        for pid, mid in (
            await ctx.session.execute(
                select(ProductImage.product_id, ProductImage.media_asset_id)
                .where(ProductImage.product_id.in_([p.id for p in products]))
                .order_by(ProductImage.position)
            )
        ).all()
    }
    cards = []
    for p in products:
        card: dict[str, Any] = {"id": str(p.id), "title": p.name, "actions": [{"label": "Choose"}]}
        if p.price_minor is not None:
            card["price"] = f"{p.currency + ' ' if p.currency else ''}{p.price_minor / 100:g}"
        if (p.description or "").strip():
            card["subtitle"] = " ".join(p.description.split())[:120]
        if not p.in_stock:
            card["badge"] = "Out of stock"
        media_id = first_image.get(p.id)
        if media_id is not None:
            # Only the id is stored: the signed URL is short-lived and is re-made on every read.
            card["image_media_id"] = str(media_id)
        cards.append(card)
    return {"items": cards}


async def run_block_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> ToolResult:
    """Execute a block tool: validate, queue the block on the turn, tell the model it was shown."""
    tool = BLOCK_TOOLS.get(name)
    if tool is None or ctx.blocks is None:
        return ToolResult(output={}, status="error", error="blocks are not available in this conversation")
    if len(ctx.blocks) >= MAX_BLOCKS_PER_TURN:
        return ToolResult(
            output={}, status="error", error=f"at most {MAX_BLOCKS_PER_TURN} interactive blocks per reply"
        )
    try:
        payload = await _cards_from_catalog(ctx, args) if tool.block_type == "cards" else args
        block = build_block(tool.block_type, payload)
    except BlockError as exc:
        log.info("block_rejected", block_type=tool.block_type, reason=str(exc)[:120])
        return ToolResult(output={}, status="error", error=str(exc))
    ctx.blocks.append(block)
    return ToolResult(output=dict(_SHOWN))

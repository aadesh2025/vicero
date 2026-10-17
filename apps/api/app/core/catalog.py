"""Product-catalog switch + prompt text (docs/26). A leaf module: the chat runtime and the tools package both need
these, and importing one from the other would close a package cycle."""

from __future__ import annotations

from typing import Any

from app.core.config import settings

#: Appended to the system prompt of an agent with the catalog on.
CATALOG_INSTRUCTION = (
    "You can look up this business's products. Use search_products for any question about a product, its "
    "price, size, colour or availability, and quote only what it returns - never invent a price or stock "
    "status. Use send_product_images to send photos only when the customer asks to see a product or a photo "
    "clearly helps; do not send photos unprompted."
)


def catalog_enabled(version: Any) -> bool:
    """Both the platform switch and the agent's own toggle (`features.product_catalog_enabled`)."""
    features = getattr(version, "features", None) or {}
    return bool(settings.media_feature_enabled and features.get("product_catalog_enabled"))

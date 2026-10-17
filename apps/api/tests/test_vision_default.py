"""The default vision model must be one the catalog knows can see images, and must not be a pinned version.

Google retired gemini-2.5-flash for new API keys (404 "no longer available") with no warning, which silently
turned every customer photo into "could not be loaded" on a fresh deployment (found live, 2026-10-17).
A `-latest` alias cannot be retired, and a default the catalog does not list as vision-capable is a bug.
"""

from __future__ import annotations

from app.core.config import Settings
from app.llm import catalog
from app.media.vision import model_supports_vision


def test_default_vision_model_is_an_alias_the_catalog_knows_can_see() -> None:
    provider = Settings.model_fields["vision_provider"].default
    model = Settings.model_fields["vision_model"].default
    assert model.endswith("-latest"), "pin-free default: a named version can be retired by the provider"
    assert catalog.find_model(provider, model) is not None
    assert model_supports_vision(provider, model)


def test_retired_gemini_ids_are_still_resolvable_but_marked() -> None:
    """Agents saved with an older id keep working in the UI; the note tells the owner why it may 404."""
    spec = catalog.find_model("gemini", "gemini-2.5-flash")
    assert spec is not None and spec.note

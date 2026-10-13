"""Public (widget) config and chat schemas."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.public import origins

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
WIDGET_STYLES = ("solid", "transparent")
FONT_FAMILIES = ("system", "inter", "arial", "georgia", "courier")
FLOATING_BUTTON_STYLES = (
    "circle-chat",
    "circle-message",
    "circle-dots",
    "rounded-square",
    "pill-text",
    "pulse-ring",
)
INPUT_BAR_BUTTONS = ("attachment", "emoji")


class WidgetTheme(BaseModel):
    """The widget's live config response. Every new field is optional/defaulted so an agent
    that never touches the new controls renders exactly as it does today."""

    primary_color: str = "#1F2937"  # master accent (header, links, send, launcher bubble)
    position: str = "bottom-right"  # bottom-right | bottom-left
    launcher_text: str = "Chat with us"
    branding: bool = True
    mode: str = "light"  # dark | light — matches the light-first dashboard palette

    # ── Extended customization (all optional; null → theme/legacy default) ──────────
    widget_style: str = "solid"  # solid | transparent
    background_color: str | None = None  # panel bg; null → mode default
    text_color: str | None = None
    bubble_color: str | None = None  # user message bubble; null → primary_color
    typing_area_color: str | None = None  # input bar bg; null → mode default
    font_family: str = "system"  # system | inter | arial | georgia | courier
    logo_url: str | None = None  # assistant avatar + launcher fill
    floating_button_style: str | None = None  # null → legacy text pill (today's look)
    floating_button_color: str | None = None  # null → inherit primary_color
    input_bar_buttons: list[str] = Field(default_factory=lambda: ["attachment"])


class WidgetConfigIn(BaseModel):
    """Validation model for an incoming ``persona.widget`` payload (camelCase, all optional).

    Used on write to reject bad hex/enum values with a typed error. Absent keys are left
    untouched (merge-on-write happens in the agents service).
    """

    model_config = ConfigDict(extra="forbid")

    primaryColor: str | None = None
    position: str | None = None
    launcherText: str | None = None
    branding: bool | None = None
    mode: str | None = None
    widgetStyle: str | None = None
    backgroundColor: str | None = None
    textColor: str | None = None
    bubbleColor: str | None = None
    typingAreaColor: str | None = None
    fontFamily: str | None = None
    logoUrl: str | None = None
    floatingButtonStyle: str | None = None
    floatingButtonColor: str | None = None
    inputBarButtons: list[str] | None = None
    #: Websites allowed to embed this widget (hostnames). Empty/absent = any site. Never sent to the widget.
    allowedSites: list[str] | None = None

    @field_validator("allowedSites")
    @classmethod
    def _sites(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else origins.normalize_sites(v)

    @field_validator(
        "primaryColor",
        "backgroundColor",
        "textColor",
        "bubbleColor",
        "typingAreaColor",
        "floatingButtonColor",
    )
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is not None and not _HEX_RE.match(v):
            raise ValueError("must be a #rrggbb hex color")
        return v

    @field_validator("position")
    @classmethod
    def _position(cls, v: str | None) -> str | None:
        if v is not None and v not in ("bottom-right", "bottom-left"):
            raise ValueError("must be bottom-right or bottom-left")
        return v

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("dark", "light"):
            raise ValueError("must be dark or light")
        return v

    @field_validator("widgetStyle")
    @classmethod
    def _style(cls, v: str | None) -> str | None:
        if v is not None and v not in WIDGET_STYLES:
            raise ValueError(f"must be one of {WIDGET_STYLES}")
        return v

    @field_validator("fontFamily")
    @classmethod
    def _font(cls, v: str | None) -> str | None:
        if v is not None and v not in FONT_FAMILIES:
            raise ValueError(f"must be one of {FONT_FAMILIES}")
        return v

    @field_validator("floatingButtonStyle")
    @classmethod
    def _button(cls, v: str | None) -> str | None:
        if v is not None and v not in FLOATING_BUTTON_STYLES:
            raise ValueError(f"must be one of {FLOATING_BUTTON_STYLES}")
        return v

    @field_validator("inputBarButtons")
    @classmethod
    def _buttons(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            bad = [b for b in v if b not in INPUT_BAR_BUTTONS]
            if bad:
                raise ValueError(f"unknown buttons {bad}; allowed {INPUT_BAR_BUTTONS}")
        return v


class WidgetCampaign(BaseModel):
    """A proactive message the widget may show unprompted (see modules/campaigns)."""

    id: uuid.UUID
    message: str
    delay_seconds: int
    url_pattern: str | None = None


class PublicConfig(BaseModel):
    agent_id: uuid.UUID
    name: str
    welcome_message: str
    suggested_prompts: list[Any]
    theme: WidgetTheme
    #: Active widget triggers only. Broadcasts never reach the public config.
    campaigns: list[WidgetCampaign] = []


class Visitor(BaseModel):
    id: str | None = None
    name: str | None = None
    email: str | None = None
    metadata: dict[str, Any] | None = None


class PublicChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: uuid.UUID | None = None
    stream: bool = True
    visitor: Visitor | None = None

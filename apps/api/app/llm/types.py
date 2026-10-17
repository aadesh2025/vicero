"""LLM provider-layer types (docs/06 §1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ImagePart(BaseModel):
    """An image attached to a message (vision calls only; docs/26). Base64 so every adapter can embed it."""

    mime: str
    data_b64: str


class Message(BaseModel):
    role: Role
    content: str | None = None
    #: Only the vision call sets this. Every other caller keeps treating `content` as plain text.
    images: list[ImagePart] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON schema


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 1024
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stop: list[str] | None = None
    tools: list[ToolSpec] | None = None
    tool_choice: str | None = None
    stream: bool = False


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None


class ModelInfo(BaseModel):
    id: str
    provider: str


StreamEventType = Literal[
    "token", "tool_call", "tool_result", "usage", "citations", "conversation", "message", "done", "error",
    # The output guard suppressed or rewrote the reply after streaming had already begun
    # (docs/11 §4-L5, ADR-049). `delta` carries the replacement text: a client that streamed
    # tokens must discard what it rendered for this turn and show this instead. Buffering
    # every reply until it could be checked would have cost first-token latency on every
    # turn to defend against a rare event, so the correction is sent after the fact.
    "replace",
]


class StreamEvent(BaseModel):
    type: StreamEventType
    delta: str | None = None
    tool_call: ToolCall | None = None
    tool_result: dict[str, Any] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    error: str | None = None
    # RAG citations attached to the response (list of citation dicts). Kept as plain dicts so
    # the provider layer stays independent of the RAG package.
    citations: list[dict[str, Any]] | None = None
    # Persistence markers (dashboard chat): the conversation and persisted assistant message.
    conversation_id: str | None = None
    message_id: str | None = None

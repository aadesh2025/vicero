"""Provider protocols and the shared provider error."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from app.llm.types import ChatRequest, ChatResponse, ModelInfo, StreamEvent


def parse_retry_after(headers: Any) -> float | None:
    """`Retry-After` (delta-seconds) as a float, or `None` when absent or not a number."""
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


class ProviderError(Exception):
    """Raised when a provider call fails in a way the fallback chain should handle.

    `retryable` means the *same* provider may succeed on a retry (timeout, 429, 5xx).
    `status` is the upstream HTTP status when there was one, and is what decides whether a
    *different* provider is worth trying: a 400/422 is a malformed request that will fail
    identically everywhere, whereas 401/403/404/429/5xx are provider-specific.
    """

    # Request-level failures — every provider would reject these the same way.
    _REQUEST_ERRORS = frozenset({400, 422})

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        # Seconds the provider asked us to wait (the `Retry-After` header on a 429), if it said.
        self.retry_after = retry_after

    @property
    def try_other_provider(self) -> bool:
        """Whether falling back to a different provider could plausibly succeed."""
        return self.status not in self._REQUEST_ERRORS


@runtime_checkable
class ChatProvider(Protocol):
    name: str

    async def chat(self, req: ChatRequest) -> ChatResponse: ...

    def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]: ...

    def supports_tools(self) -> bool: ...

    async def list_models(self) -> list[ModelInfo]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

"""Anthropic adapter (Messages API)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.base import ProviderError
from app.llm.types import ChatRequest, ChatResponse, ModelInfo, StreamEvent, ToolCall, Usage

BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

_KNOWN_MODELS = ["claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-4-8"]


def to_anthropic_payload(req: ChatRequest) -> dict[str, Any]:
    system = "\n".join(m.content or "" for m in req.messages if m.role == "system") or None
    messages: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content or ""}
                    ],
                }
            )
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            messages.append({"role": "assistant", "content": blocks})
        else:
            role = "assistant" if m.role == "assistant" else "user"
            if m.images:
                messages.append(
                    {
                        "role": role,
                        "content": [
                            *(
                                {
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": img.mime, "data": img.data_b64},
                                }
                                for img in m.images
                            ),
                            {"type": "text", "text": m.content or ""},
                        ],
                    }
                )
            else:
                messages.append({"role": role, "content": m.content or ""})

    payload: dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "top_p": req.top_p,
    }
    if system:
        payload["system"] = system
    if req.tools:
        payload["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in req.tools
        ]
    return payload


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        headers = {"anthropic-version": API_VERSION, "content-type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return httpx.AsyncClient(
            base_url=BASE_URL, headers=headers, timeout=self._timeout, transport=self._transport
        )

    async def chat(self, req: ChatRequest) -> ChatResponse:
        async with self._client() as client:
            try:
                resp = await client.post("/messages", json=to_anthropic_payload(req))
            except httpx.HTTPError as exc:
                raise ProviderError(f"network error: {exc}") from exc
            if resp.status_code >= 400:
                retryable = resp.status_code == 429 or resp.status_code >= 500
                raise ProviderError(
                    f"anthropic {resp.status_code}: {resp.text[:200]}",
                    retryable=retryable,
                    status=resp.status_code,
                )
            data = resp.json()

        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        tool_calls = [
            ToolCall(id=b["id"], name=b["name"], arguments=b.get("input", {}))
            for b in data.get("content", [])
            if b.get("type") == "tool_use"
        ]
        usage = data.get("usage", {})
        return ChatResponse(
            content=text,
            tool_calls=tool_calls,
            model=data.get("model", req.model),
            provider=self.name,
            usage=Usage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
            ),
            finish_reason=data.get("stop_reason", "stop"),
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        result = await self.chat(req)
        for tc in result.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)
        for word in result.content.split():
            yield StreamEvent(type="token", delta=word + " ")
        yield StreamEvent(type="done", usage=result.usage, finish_reason=result.finish_reason)

    def supports_tools(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=m, provider=self.name) for m in _KNOWN_MODELS]

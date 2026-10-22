"""Google Gemini adapter (native generateContent API)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx

from app.llm.base import ProviderError
from app.llm.http import provider_client, with_connect_retry
from app.llm.http import timeout as llm_timeout
from app.llm.types import ChatRequest, ChatResponse, ModelInfo, StreamEvent, ToolCall, Usage

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def to_gemini_payload(req: ChatRequest) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for m in req.messages:
        if m.role == "system":
            if m.content:
                system_parts.append(m.content)
            continue
        role = "model" if m.role == "assistant" else "user"
        parts: list[dict[str, Any]] = [{"text": m.content or ""}]
        parts.extend({"inlineData": {"mimeType": img.mime, "data": img.data_b64}} for img in m.images or [])
        contents.append({"role": role, "parts": parts})

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": req.temperature,
            "topP": req.top_p,
            "maxOutputTokens": req.max_tokens,
        },
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    if req.tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {"name": t.name, "description": t.description, "parameters": t.parameters} for t in req.tools
                ]
            }
        ]
    return payload


class GeminiProvider:
    name = "gemini"

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

    def _client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        return provider_client(transport=self._transport)

    def _params(self) -> dict[str, str] | None:
        return {"key": self.api_key} if self.api_key else None

    async def chat(self, req: ChatRequest) -> ChatResponse:
        async with self._client() as client:
            try:
                payload = to_gemini_payload(req)
                resp = await with_connect_retry(
                    lambda: client.post(
                        f"{BASE_URL}/models/{req.model}:generateContent",
                        json=payload,
                        params=self._params(),
                        timeout=llm_timeout(self._timeout),
                    ),
                    provider=self.name,
                )
            except httpx.HTTPError as exc:
                raise ProviderError(f"network error: {exc}") from exc
            if resp.status_code >= 400:
                retryable = resp.status_code == 429 or resp.status_code >= 500
                raise ProviderError(
                    f"gemini {resp.status_code}: {resp.text[:200]}",
                    retryable=retryable,
                    status=resp.status_code,
                )
            data = resp.json()

        candidate = (data.get("candidates") or [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        tool_calls = [
            ToolCall(
                id=p["functionCall"]["name"],
                name=p["functionCall"]["name"],
                arguments=p["functionCall"].get("args", {}),
            )
            for p in parts
            if "functionCall" in p
        ]
        meta = data.get("usageMetadata", {})
        return ChatResponse(
            content=text,
            tool_calls=tool_calls,
            model=req.model,
            provider=self.name,
            usage=Usage(
                prompt_tokens=meta.get("promptTokenCount", 0),
                completion_tokens=meta.get("candidatesTokenCount", 0),
            ),
            finish_reason=candidate.get("finishReason", "stop"),
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        # Gemini streaming is chunk-based JSON; for now derive a token stream from the full
        # response so callers get the same event contract.
        result = await self.chat(req)
        for tc in result.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)
        for word in result.content.split():
            yield StreamEvent(type="token", delta=word + " ")
        yield StreamEvent(type="done", usage=result.usage, finish_reason=result.finish_reason)

    def supports_tools(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        async with self._client() as client:
            try:
                resp = await with_connect_retry(
                    lambda: client.get(
                        f"{BASE_URL}/models", params=self._params(), timeout=llm_timeout(self._timeout)
                    ),
                    provider=self.name,
                )
            except httpx.HTTPError as exc:
                raise ProviderError(f"network error: {exc}") from exc
            if resp.status_code >= 400:
                raise ProviderError(f"gemini {resp.status_code}")
            data = resp.json()
        return [ModelInfo(id=m["name"].removeprefix("models/"), provider=self.name) for m in data.get("models", [])]

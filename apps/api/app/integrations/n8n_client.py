"""n8n client: list/read workflows, trigger signed webhooks, verify callbacks (docs/07 §1).

Requests to n8n are intentionally NOT SSRF-guarded — the target is the operator-configured,
trusted `N8N_BASE_URL` (usually loopback in dev), not arbitrary user input.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("n8n")

_WEBHOOK_NODE_TYPES = {"n8n-nodes-base.webhook"}


def _webhook_tail(url: str) -> str | None:
    """`https://h/webhook/abc` -> `/webhook/abc`. The `webhook-test` (editor) URL is not a
    production endpoint and is deliberately not matched."""
    path = urlparse(url).path.rstrip("/")
    return path if path.startswith("/webhook/") else None


def _signing_secret() -> str:
    return settings.n8n_webhook_signing_secret or settings.secret_key


def sign(body: bytes, *, timestamp: str | None = None) -> tuple[str, str]:
    """Return (timestamp, hex HMAC-SHA256 of `timestamp.body`) for outbound webhook signing."""
    ts = timestamp or str(int(time.time()))
    mac = hmac.new(_signing_secret().encode(), f"{ts}.".encode() + body, hashlib.sha256)
    return ts, mac.hexdigest()


def verify_callback(signature: str | None, timestamp: str | None, body: bytes, *, max_age: int = 300) -> bool:
    """Verify an inbound callback signature (constant-time), with replay protection."""
    if not signature or not timestamp:
        return False
    try:
        if abs(int(time.time()) - int(timestamp)) > max_age:
            return False
    except ValueError:
        return False
    _, expected = sign(body, timestamp=timestamp)
    return hmac.compare_digest(expected, signature)


class N8nClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = (base_url or settings.n8n_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.n8n_api_key
        self._transport = transport
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _api_client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        return httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=self._timeout, transport=self._transport
        )

    async def list_workflows(self) -> list[dict[str, Any]]:
        if not self.configured:
            raise AppError("n8n.unconfigured", "N8N_API_KEY is not set — cannot list workflows.", 503)
        async with self._api_client() as client:
            try:
                resp = await client.get("/api/v1/workflows")
            except httpx.HTTPError as exc:
                raise AppError("n8n.unreachable", f"n8n unreachable: {exc}", 502) from exc
            if resp.status_code >= 400:
                raise AppError("n8n.api_error", f"n8n API returned {resp.status_code}", 502)
            data = resp.json()
        return list(data.get("data", data if isinstance(data, list) else []))

    async def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        if not self.configured:
            raise AppError("n8n.unconfigured", "N8N_API_KEY is not set.", 503)
        async with self._api_client() as client:
            try:
                resp = await client.get(f"/api/v1/workflows/{workflow_id}")
            except httpx.HTTPError as exc:
                raise AppError("n8n.unreachable", f"n8n unreachable: {exc}", 502) from exc
            if resp.status_code == 404:
                raise AppError("n8n.workflow_not_found", "Workflow not found in n8n.", 404)
            if resp.status_code >= 400:
                raise AppError("n8n.api_error", f"n8n API returned {resp.status_code}", 502)
            return dict(resp.json())

    def extract_webhook_url(self, workflow: dict[str, Any]) -> str | None:
        """Build the production webhook URL from a workflow's Webhook node, if any."""
        for node in workflow.get("nodes", []):
            if node.get("type") in _WEBHOOK_NODE_TYPES:
                params = node.get("parameters", {})
                path = params.get("path") or node.get("webhookId")
                if path:
                    return f"{self.base_url}/webhook/{path}"
        return None

    async def find_workflow_by_webhook_url(self, url: str) -> dict[str, Any] | None:
        """The n8n workflow whose Webhook node serves `url`, or None.

        Matched on the `/webhook/<path>` tail, not the host: an operator can paste the URL n8n
        shows (public hostname) while BotForge's own `N8N_BASE_URL` is an internal one.
        """
        return self.match_workflow_by_webhook_url(await self.list_workflows(), url)

    @staticmethod
    def match_workflow_by_webhook_url(workflows: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
        """Pure half of `find_workflow_by_webhook_url`, so a caller that already holds the
        workflow list (the audit walks every bound tool) does not re-fetch it per tool."""
        tail = _webhook_tail(url)
        if tail is None:
            return None
        for wf in workflows:
            for node in wf.get("nodes", []):
                if node.get("type") in _WEBHOOK_NODE_TYPES:
                    path = (node.get("parameters") or {}).get("path") or node.get("webhookId")
                    if path and f"/webhook/{str(path).strip('/')}" == tail:
                        return wf
        return None

    @staticmethod
    def extract_tags(workflow: dict[str, Any]) -> set[str]:
        """Return a workflow's n8n tag names, lower-cased.

        BotForge is a single shared n8n instance behind every org (docs/07 §1), so
        `list_workflows()` returns every workflow regardless of which client it belongs to.
        Tags are how a workflow is scoped to one org — see `tools/service.workflow_visible_to_org`.
        """
        tags: set[str] = set()
        for t in workflow.get("tags") or []:
            name = t.get("name") if isinstance(t, dict) else t
            if name:
                tags.add(str(name).strip().lower())
        return tags

    async def trigger_webhook(self, url: str, payload: dict[str, Any]) -> tuple[int, Any]:
        """POST a signed payload to an n8n webhook URL. Returns (status_code, parsed body)."""
        body = json.dumps(payload).encode()
        ts, signature = sign(body)
        headers = {
            "Content-Type": "application/json",
            "X-BotForge-Signature": signature,
            "X-BotForge-Timestamp": ts,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, follow_redirects=False
        ) as client:
            try:
                resp = await client.post(url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                raise AppError("n8n.webhook_error", f"webhook call failed: {exc}", 502) from exc
        try:
            data: Any = resp.json()
        except ValueError:
            data = resp.text[:4000]
        return resp.status_code, data


def get_client(**kwargs: Any) -> N8nClient:
    return N8nClient(**kwargs)

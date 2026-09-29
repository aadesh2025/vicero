"""Vicero load/perf harness (Phase 16.3, NFR-1 in docs/01).

Measures two things against a running API:
  - **First-token latency** on the streaming chat endpoint (LLM path).
  - **API latency** on a non-LLM endpoint (`GET /v1/agents`).

Usage (needs a running API + a valid token/org + a published agent):

    pip install locust
    BF_TOKEN=... BF_ORG=... BF_AGENT_ID=... BF_BASE=http://localhost:8000 \
        locust -f infra/perf/locustfile.py --headless -u 10 -r 2 -t 1m --host $BF_BASE

For a quick, dependency-light run that records p50/p95 directly, use
`infra/perf/measure.py` instead (it drives the same endpoints with httpx).
"""

from __future__ import annotations

import os
import time

from locust import HttpUser, between, task


class ViceroUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.token = os.environ["BF_TOKEN"]
        self.org = os.environ["BF_ORG"]
        self.agent_id = os.environ["BF_AGENT_ID"]
        self.headers = {"Authorization": f"Bearer {self.token}", "X-Org-Id": self.org}

    @task(3)
    def list_agents(self) -> None:
        # Non-LLM API latency (NFR-1: p95 < 300ms target).
        self.client.get("/v1/agents", headers=self.headers, name="GET /v1/agents")

    @task(1)
    def chat_first_token(self) -> None:
        # First-token latency on the streaming path.
        start = time.perf_counter()
        with self.client.post(
            f"/v1/agents/{self.agent_id}/chat",
            json={"message": "In one short sentence, what is Vicero?", "stream": True},
            headers=self.headers,
            name="POST /chat (first token)",
            stream=True,
            catch_response=True,
        ) as resp:
            for line in resp.iter_lines():
                if line and line.startswith(b"data:") and b'"type": "token"' in line:
                    resp.response_time = (time.perf_counter() - start) * 1000
                    resp.success()
                    break

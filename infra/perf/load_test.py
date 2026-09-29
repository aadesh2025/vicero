"""Concurrency/load test for RISK-REGISTER.md R4 ("no load or scale testing has ever been
performed... every verification is correctness under low, sequential, test-shaped traffic").

`infra/perf/measure.py` already measures single-request p50/p95 for both the non-LLM API path
and Groq first-token latency (NFR-1) — SEQUENTIALLY, one request at a time. This script measures
the thing R4 says is missing: what happens when many requests land AT ONCE, across two paths the
risk register names explicitly — concurrent chat sessions and concurrent workflow runs.

**Deliberately isolates PLATFORM throughput from LLM-provider latency**, which NFR-1 already
covers separately: every test agent is configured with `provider: "fake"`
(`app.llm.fake.FakeChatProvider`, instant, no network) rather than a real model. This is not a
shortcut — it is the correct isolation. Provider latency is bounded by the vendor, not this
codebase, and a concurrency test that used a real provider would conflate "does Vicero's own
FastAPI/Postgres/Celery stack degrade under concurrent load" with "is Groq slow right now", plus
it would risk repeating the exact incident the 2026-08-10 session log already recorded: "Running
the checklist exhausts the Groq free tier."

**Must be run against a DEDICATED API instance with guard L2/L3 disabled**, not the operator's
normal dev API. `LLM_FORCE_FAKE` only changes chat-provider resolution
(`app.llm.registry.get_chat_provider`) — it does NOT touch `app.chat.guard_models`, which
resolves the L2 injection classifier and L3 policy/distress classifier through a FIXED platform
Groq key (`guard_models.platform_guard_key()`) regardless of the agent's own provider. Left on,
a concurrent load test would fire real Groq calls per message and hit the same quota wall.
Disable them for this run only, on the dedicated instance:

    cd apps/api
    LLM_FORCE_FAKE=true ALLOW_SELF_SERVE_ORGS=true AUTH_RATE_LIMIT=1000000 \\
      GUARD_INJECTION_ENABLED=false GUARD_POLICY_ENABLED=false GUARD_DISTRESS_ENABLED=false \\
      ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8020

Workflow-run concurrency needs a REAL Celery worker on the same Postgres/Redis (not
`CELERY_TASK_ALWAYS_EAGER`) — an eager worker would run every workflow inline in the API's own
request handler, which measures something different (API-process concurrency only) from what
production actually does (dispatch to a worker pool). Start one exactly per CLAUDE.md §12:

    cd apps/api
    ./.venv/Scripts/python.exe -m celery -A app.worker.celery_app worker --pool=solo --loglevel=info

Then:
    BF_BASE=http://localhost:8020 uv run python infra/perf/load_test.py

Env: BF_BASE (default http://localhost:8000 — override to the dedicated instance above),
CHAT_LEVELS / WORKFLOW_LEVELS (comma-separated concurrency levels), WORKFLOW_POLL_TIMEOUT_S.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

import httpx

BASE = os.environ.get("BF_BASE", "http://localhost:8000")
CHAT_LEVELS = [int(x) for x in os.environ.get("CHAT_LEVELS", "1,5,10,25,50").split(",")]
WORKFLOW_LEVELS = [int(x) for x in os.environ.get("WORKFLOW_LEVELS", "1,5,10,25").split(",")]
WORKFLOW_POLL_TIMEOUT_S = float(os.environ.get("WORKFLOW_POLL_TIMEOUT_S", "60"))
N_ORGS = int(os.environ.get("N_ORGS", "8"))  # a pool of distinct orgs, round-robined across requests

SIMPLE_GRAPH = {
    "nodes": [
        {"id": "s1", "type": "start"},
        {"id": "sv1", "type": "set_variable", "config": {"key": "greeting", "value": "hello"}},
        {"id": "e1", "type": "end"},
    ],
    "edges": [{"source": "s1", "target": "sv1"}, {"source": "sv1", "target": "e1"}],
}

TOOL_GRAPH = {
    "nodes": [
        {"id": "s1", "type": "start"},
        {
            "id": "t1", "type": "tool",
            "config": {"tool_name": "calculator", "arguments": {"expression": "6 * 7"}},
        },
        {"id": "e1", "type": "end"},
    ],
    "edges": [{"source": "s1", "target": "t1"}, {"source": "t1", "target": "e1"}],
}


def _pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round((p / 100) * (len(ordered) - 1))))
    return ordered[k]


@dataclass
class LevelResult:
    concurrency: int
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    wall_s: float = 0.0

    def report(self, label: str) -> str:
        n = len(self.latencies_ms) + self.errors
        rps = n / self.wall_s if self.wall_s > 0 else float("nan")
        if not self.latencies_ms:
            return f"{label} c={self.concurrency:>3}: ALL {self.errors} FAILED (wall={self.wall_s:.2f}s)"
        return (
            f"{label} c={self.concurrency:>3}: n={n} errors={self.errors} "
            f"p50={_pct(self.latencies_ms, 50):.0f}ms p95={_pct(self.latencies_ms, 95):.0f}ms "
            f"max={max(self.latencies_ms):.0f}ms wall={self.wall_s:.2f}s throughput={rps:.1f}req/s"
        )


@dataclass
class OrgPoolEntry:
    headers: dict[str, str]
    agent_id: str
    simple_workflow_id: str
    tool_workflow_id: str


async def _signup_org(client: httpx.AsyncClient, tag: str) -> dict[str, str]:
    email = f"loadtest_{tag}_{int(time.time() * 1000)}@example.com"
    r = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    r.raise_for_status()
    token = r.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": f"LoadTest {tag}"}, headers={"Authorization": f"Bearer {token}"})
    org.raise_for_status()
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _setup_org(client: httpx.AsyncClient, idx: int) -> OrgPoolEntry:
    headers = await _signup_org(client, str(idx))

    agent = await client.post(
        "/v1/agents", json={"name": "Load Bot", "description": "load test"}, headers=headers
    )
    agent.raise_for_status()
    aid = agent.json()["id"]
    patched = await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake"},
            "system_prompt": "Answer in one short sentence.",
            "features": {"tools_enabled": True},
        },
        headers=headers,
    )
    patched.raise_for_status()
    published = await client.post(f"/v1/agents/{aid}/versions/1/publish", headers=headers)
    published.raise_for_status()

    tool = await client.post(
        "/v1/tools", json={"name": "calculator", "type": "builtin", "agent_id": aid}, headers=headers
    )
    tool.raise_for_status()

    async def _make_workflow(graph: dict[str, object]) -> str:
        wf = await client.post(
            f"/v1/agents/{aid}/workflows", json={"name": "Load Flow", "description": "load test"}, headers=headers
        )
        wf.raise_for_status()
        wid: str = wf.json()["id"]
        version = await client.post(f"/v1/workflows/{wid}/versions", json={"graph": graph}, headers=headers)
        version.raise_for_status()
        pub = await client.post(f"/v1/workflows/{wid}/versions/1/publish", headers=headers)
        pub.raise_for_status()
        return wid

    simple_wid = await _make_workflow(SIMPLE_GRAPH)
    tool_wid = await _make_workflow(TOOL_GRAPH)

    return OrgPoolEntry(
        headers=headers, agent_id=aid, simple_workflow_id=simple_wid, tool_workflow_id=tool_wid
    )


async def _chat_first_token_ms(client: httpx.AsyncClient, entry: OrgPoolEntry) -> float:
    start = time.perf_counter()
    async with client.stream(
        "POST",
        f"/v1/agents/{entry.agent_id}/chat",
        json={"message": "In one short sentence, what is a chatbot?", "stream": True},
        headers=entry.headers,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data:") and '"token"' in line and "delta" in line:
                return (time.perf_counter() - start) * 1000
    raise RuntimeError("no token event received")


async def _run_chat_level(client: httpx.AsyncClient, pool: list[OrgPoolEntry], concurrency: int) -> LevelResult:
    result = LevelResult(concurrency=concurrency)

    async def _one(i: int) -> None:
        entry = pool[i % len(pool)]
        try:
            ms = await _chat_first_token_ms(client, entry)
            result.latencies_ms.append(ms)
        except Exception:
            result.errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(_one(i) for i in range(concurrency)))
    result.wall_s = time.perf_counter() - t0
    return result


async def _workflow_run_and_wait(client: httpx.AsyncClient, entry: OrgPoolEntry, workflow_id: str) -> float:
    """Returns total wall time from submit to observed terminal status (completed/failed) — the
    figure that actually matters for a client waiting on an automation, not just the POST's own
    (near-instant, since it's async-dispatched) response time."""
    start = time.perf_counter()
    submitted = await client.post(f"/v1/workflows/{workflow_id}/run", json={}, headers=entry.headers)
    submitted.raise_for_status()
    run_id = submitted.json()["id"]

    deadline = start + WORKFLOW_POLL_TIMEOUT_S
    while time.perf_counter() < deadline:
        got = await client.get(f"/v1/workflow-runs/{run_id}", headers=entry.headers)
        got.raise_for_status()
        status = got.json()["status"]
        if status in ("completed", "failed", "cancelled", "budget_exceeded"):
            if status != "completed":
                raise RuntimeError(f"run {run_id} ended {status!r}: {got.json().get('error')}")
            return (time.perf_counter() - start) * 1000
        await asyncio.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not finish within {WORKFLOW_POLL_TIMEOUT_S}s")


async def _run_workflow_level(
    client: httpx.AsyncClient, pool: list[OrgPoolEntry], concurrency: int, *, use_tool_graph: bool
) -> LevelResult:
    result = LevelResult(concurrency=concurrency)

    async def _one(i: int) -> None:
        entry = pool[i % len(pool)]
        wid = entry.tool_workflow_id if use_tool_graph else entry.simple_workflow_id
        try:
            ms = await _workflow_run_and_wait(client, entry, wid)
            result.latencies_ms.append(ms)
        except Exception:
            result.errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(_one(i) for i in range(concurrency)))
    result.wall_s = time.perf_counter() - t0
    return result


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=WORKFLOW_POLL_TIMEOUT_S + 30) as client:
        print(f"# Vicero load test — base={BASE}")
        print(f"# chat concurrency levels: {CHAT_LEVELS}")
        print(f"# workflow concurrency levels: {WORKFLOW_LEVELS}")
        print(f"# org pool size: {N_ORGS}\n")

        print(f"[setup] provisioning {N_ORGS} orgs (agent + 2 workflows each)...")
        t0 = time.perf_counter()
        pool = [await _setup_org(client, i) for i in range(N_ORGS)]
        print(f"[setup] done in {time.perf_counter() - t0:.1f}s\n")

        print("## Concurrent chat sessions (first-token latency, provider=fake)")
        for c in CHAT_LEVELS:
            result = await _run_chat_level(client, pool, c)
            print(result.report("chat "))
        print()

        print("## Concurrent workflow runs — simple graph (start -> set_variable -> end)")
        for c in WORKFLOW_LEVELS:
            result = await _run_workflow_level(client, pool, c, use_tool_graph=False)
            print(result.report("wf-simple "))
        print()

        print("## Concurrent workflow runs — tool graph (start -> tool(calculator) -> end)")
        for c in WORKFLOW_LEVELS:
            result = await _run_workflow_level(client, pool, c, use_tool_graph=True)
            print(result.report("wf-tool   "))


if __name__ == "__main__":
    asyncio.run(main())

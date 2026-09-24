"""Headless perf measurement for NFR-1 (Phase 16.3).

Drives a *running* API with httpx and records:
  - **first-token p50/p95** on the streaming chat path, measured against **Groq** (the LLM
    provider under test) — Ollama is measured separately / excluded because it is local and
    slow (30–90s/turn), which would swamp a blended figure.
  - **API p50/p95** on a non-LLM endpoint (`GET /v1/agents`).

Run against the live stack:  uv run python infra/perf/measure.py
Env: BF_BASE (default http://localhost:8000), PERF_PROVIDER (default groq), PERF_MODEL.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time

import httpx

BASE = os.environ.get("BF_BASE", "http://localhost:8000")
PROVIDER = os.environ.get("PERF_PROVIDER", "groq")
MODEL = os.environ.get("PERF_MODEL", "openai/gpt-oss-20b")
N_API = int(os.environ.get("PERF_N_API", "30"))
N_LLM = int(os.environ.get("PERF_N_LLM", "8"))


def _pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round((p / 100) * (len(ordered) - 1))))
    return ordered[k]


def _report(label: str, samples: list[float], unit: str = "ms") -> None:
    if not samples:
        print(f"{label}: no samples")
        return
    print(
        f"{label}: n={len(samples)} "
        f"p50={_pct(samples, 50):.0f}{unit} p95={_pct(samples, 95):.0f}{unit} "
        f"avg={statistics.mean(samples):.0f}{unit} min={min(samples):.0f}{unit} max={max(samples):.0f}{unit}"
    )


async def _setup(client: httpx.AsyncClient) -> tuple[dict[str, str], str]:
    email = f"perf_{int(time.time())}@example.com"
    r = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = r.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "Perf"}, headers={"Authorization": f"Bearer {token}"})
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    agent = await client.post("/v1/agents", json={"name": "Perf Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={"model_config": {"provider": PROVIDER, "model": MODEL}, "system_prompt": "Answer in one short sentence."},
        headers=headers,
    )
    return headers, aid


async def _first_token_ms(client: httpx.AsyncClient, headers: dict[str, str], aid: str) -> float | None:
    start = time.perf_counter()
    async with client.stream(
        "POST",
        f"/v1/agents/{aid}/chat",
        json={"message": "In one short sentence, what is a chatbot?", "stream": True},
        headers=headers,
    ) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data:") and '"token"' in line and "delta" in line:
                return (time.perf_counter() - start) * 1000
    return None


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=120.0) as client:
        headers, aid = await _setup(client)
        print(f"# BotForge perf — provider={PROVIDER} model={MODEL} base={BASE}\n")

        # --- Non-LLM API latency ---
        await client.get("/v1/agents", headers=headers)  # warm
        api_ms: list[float] = []
        for _ in range(N_API):
            t0 = time.perf_counter()
            r = await client.get("/v1/agents", headers=headers)
            r.raise_for_status()
            api_ms.append((time.perf_counter() - t0) * 1000)
        _report("API  GET /v1/agents (non-LLM)", api_ms)

        # --- First-token latency (LLM path) ---
        first = await _first_token_ms(client, headers, aid)  # warm (also validates provider)
        if first is None:
            print("LLM first-token: NO TOKENS — provider likely unavailable (check GROQ_API_KEY).")
            return
        llm_ms: list[float] = []
        for _ in range(N_LLM):
            ms = await _first_token_ms(client, headers, aid)
            if ms is not None:
                llm_ms.append(ms)
        _report(f"LLM  first-token ({PROVIDER})", llm_ms)


if __name__ == "__main__":
    asyncio.run(main())

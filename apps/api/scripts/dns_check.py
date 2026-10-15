"""Time getaddrinfo for the hosts the API calls out to (ADR-119). The first lookup must be under 0.5 s.

    docker exec vicero-prod-worker-1 python scripts/dns_check.py
"""

from __future__ import annotations

import socket
import time

HOSTS = ("api.groq.com", "graph.facebook.com")
ROUNDS = 5


def main() -> int:
    slow = False
    for host in HOSTS:
        timings = []
        for _ in range(ROUNDS):
            start = time.perf_counter()
            socket.getaddrinfo(host, 443)
            timings.append(time.perf_counter() - start)
        print(f"{host}: " + " ".join(f"{t:.3f}s" for t in timings))
        slow = slow or timings[0] > 0.5
    print("FAIL: first lookup over 0.5 s" if slow else "ok: first lookups under 0.5 s")
    return 1 if slow else 0


if __name__ == "__main__":
    raise SystemExit(main())

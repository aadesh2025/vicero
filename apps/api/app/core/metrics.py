"""Minimal, dependency-free Prometheus metrics.

Enough for a Prometheus/Grafana scrape without pulling in a client library: a request counter
by method/status, a latency histogram, and app/build info. Exposed at ``GET /metrics``.
"""

from __future__ import annotations

import threading
import time

from app import __version__
from app.core.config import settings

# Standard Prometheus histogram buckets (seconds).
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_lock = threading.Lock()
_start = time.time()
# (method, status_class) -> count, e.g. ("GET", "2xx")
_requests: dict[tuple[str, str], int] = {}
# request latency histogram
_bucket_counts: list[int] = [0] * len(_BUCKETS)
_hist_count = 0
_hist_sum = 0.0
# L2 guard calls by outcome, and the tokens they spent (docs/11 §4-L2).
# A SEPARATE bucket on purpose: guard calls run on the *platform* Groq key, not the client's,
# so folding them into per-agent cost would misreport client margin — the same class of
# quiet-wrong-number the `pricing_unknown` work fixed.
_guard_calls: dict[str, int] = {}
_guard_tokens = 0
# L3 policy calls, bucketed by distress level so the escalation rate is graphable.
_policy_calls: dict[str, int] = {}
_policy_tokens = 0
# Reranker calls by outcome, and the milliseconds they spent. Its own bucket for the same
# reason as the guard buckets: rerank runs on the *platform's* credential and infrastructure,
# so folding it into per-agent cost would misreport client margin. The latency total is here
# rather than in the request histogram because it is spent BEFORE first token, i.e. it comes
# straight out of the NFR-1 p50 417 ms budget and has to be attributable on its own.
_rerank_calls: dict[str, int] = {}
_rerank_ms = 0.0


def _status_class(status: int) -> str:
    return f"{status // 100}xx"


def observe_request(method: str, status: int, duration_seconds: float) -> None:
    global _hist_count, _hist_sum
    key = (method.upper(), _status_class(status))
    with _lock:
        _requests[key] = _requests.get(key, 0) + 1
        _hist_count += 1
        _hist_sum += duration_seconds
        for i, edge in enumerate(_BUCKETS):
            if duration_seconds <= edge:
                _bucket_counts[i] += 1


def observe_guard_call(outcome: str, prompt_tokens: int) -> None:
    """Record one L2 guard decision. `outcome` is scored|cache_hit|error|unavailable."""
    global _guard_tokens
    with _lock:
        _guard_calls[outcome] = _guard_calls.get(outcome, 0) + 1
        _guard_tokens += max(0, prompt_tokens)


def observe_policy_call(outcome: str, total_tokens: int) -> None:
    """Record one L3 decision. `outcome` is level_<distress>|error|unparseable|unavailable."""
    global _policy_tokens
    with _lock:
        _policy_calls[outcome] = _policy_calls.get(outcome, 0) + 1
        _policy_tokens += max(0, total_tokens)


def observe_rerank_call(outcome: str, duration_ms: float) -> None:
    """Record one rerank decision. `outcome` is scored|error."""
    global _rerank_ms
    with _lock:
        _rerank_calls[outcome] = _rerank_calls.get(outcome, 0) + 1
        _rerank_ms += max(0.0, duration_ms)


def render() -> str:
    lines: list[str] = []
    lines.append("# HELP vicero_build_info Build/version info.")
    lines.append("# TYPE vicero_build_info gauge")
    lines.append(f'vicero_build_info{{version="{__version__}",env="{settings.env}"}} 1')

    lines.append("# HELP vicero_uptime_seconds Process uptime in seconds.")
    lines.append("# TYPE vicero_uptime_seconds gauge")
    lines.append(f"vicero_uptime_seconds {time.time() - _start:.1f}")

    lines.append("# HELP vicero_http_requests_total Total HTTP requests by method and status class.")
    lines.append("# TYPE vicero_http_requests_total counter")
    with _lock:
        for (method, klass), count in sorted(_requests.items()):
            lines.append(f'vicero_http_requests_total{{method="{method}",status="{klass}"}} {count}')

        lines.append("# HELP vicero_http_request_duration_seconds HTTP request latency.")
        lines.append("# TYPE vicero_http_request_duration_seconds histogram")
        # _bucket_counts[i] is already the cumulative count of observations <= edge[i].
        for i, edge in enumerate(_BUCKETS):
            lines.append(f'vicero_http_request_duration_seconds_bucket{{le="{edge}"}} {_bucket_counts[i]}')
        lines.append(f'vicero_http_request_duration_seconds_bucket{{le="+Inf"}} {_hist_count}')
        lines.append(f"vicero_http_request_duration_seconds_sum {_hist_sum:.4f}")
        lines.append(f"vicero_http_request_duration_seconds_count {_hist_count}")

        # `outcome="error"` and `outcome="unavailable"` are the ones to alert on: the guard
        # fails open, so a rising rate there means traffic is running unguarded rather than
        # that nothing is being attempted.
        lines.append("# HELP vicero_guard_calls_total L2 injection-guard decisions by outcome.")
        lines.append("# TYPE vicero_guard_calls_total counter")
        for outcome, count in sorted(_guard_calls.items()):
            lines.append(f'vicero_guard_calls_total{{outcome="{outcome}"}} {count}')
        lines.append(
            "# HELP vicero_guard_tokens_total Tokens spent on guard models, on the platform key."
        )
        lines.append("# TYPE vicero_guard_tokens_total counter")
        lines.append(f"vicero_guard_tokens_total {_guard_tokens}")

        # level_crisis rising is a product-safety signal, not just a metric.
        lines.append("# HELP vicero_policy_calls_total L3 policy/distress decisions by outcome.")
        lines.append("# TYPE vicero_policy_calls_total counter")
        for outcome, count in sorted(_policy_calls.items()):
            lines.append(f'vicero_policy_calls_total{{outcome="{outcome}"}} {count}')
        lines.append("# HELP vicero_policy_tokens_total Tokens spent on the policy classifier.")
        lines.append("# TYPE vicero_policy_tokens_total counter")
        lines.append(f"vicero_policy_tokens_total {_policy_tokens}")

        # `outcome="error"` is the one to alert on: the reranker fails open, so a rising rate
        # means turns are being ranked by RRF alone, not that there is nothing to rerank.
        lines.append("# HELP vicero_rerank_calls_total Reranker decisions by outcome.")
        lines.append("# TYPE vicero_rerank_calls_total counter")
        for outcome, count in sorted(_rerank_calls.items()):
            lines.append(f'vicero_rerank_calls_total{{outcome="{outcome}"}} {count}')
        lines.append(
            "# HELP vicero_rerank_milliseconds_total Time spent reranking, before first token."
        )
        lines.append("# TYPE vicero_rerank_milliseconds_total counter")
        lines.append(f"vicero_rerank_milliseconds_total {_rerank_ms:.1f}")

    return "\n".join(lines) + "\n"

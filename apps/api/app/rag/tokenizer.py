"""Real token counting, replacing the `len(text) / 4` heuristic (docs/14 K2-1).

## Why the heuristic had to go

`estimate_tokens` returned `len(text) / 4`. That is roughly right for English prose and wrong
in the way that matters most for this product: **it is wildly wrong for the languages Vicero
is deployed into.** cl100k_base spends 3-6 tokens on a single Tamil or Devanagari word, so a
1000-character Tamil chunk that the heuristic reported as 250 tokens is nearer 800. Every
downstream number computed from it — the reported `token_count`, and now the chunker's size
budget — was quietly optimistic for exactly the clients docs/11 §9.2a flags as underserved.

## What "exact" means here, honestly

This counts with **cl100k_base**, OpenAI's tokenizer. The default embedder is
`nomic-embed-text`, which uses a different one, so this is not the embedder's own count and
this module does not claim to be. It is a real tokenizer applied consistently rather than a
character ratio — within a few percent for English and an order of magnitude closer than
`len/4` for Indic scripts. docs/14 §3.8 Trap 2 is why we are not pulling `transformers` in to
get the exact nomic count: it drags `torch` into the API image for a reporting field.

## The network trap

`tiktoken.get_encoding()` **downloads** its BPE ranks on first use. A Celery worker in a
locked-down network would raise at ingest time, turning a document into `status=failed` because
a *counter* could not initialise. So every failure here degrades to the old heuristic and logs
once. Set `TIKTOKEN_CACHE_DIR` to a writable, pre-warmed path to make the download a one-off.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

from app.core.logging import get_logger

log = get_logger("rag.tokenizer")

#: The encoding used for every count. Pinned by name: changing it silently re-scales every
#: `token_count` in the database and every chunk boundary the Docling chunker picks.
ENCODING_NAME = "cl100k_base"

_lock = threading.Lock()
_encoder: Any | None = None
_resolved = False
_warned = False


class _Encoder(Protocol):
    def encode(self, text: str, **kwargs: Any) -> list[int]: ...


def _fallback(text: str) -> int:
    """The pre-K2 heuristic. Kept as the degraded path, not as an equal alternative."""
    return max(1, round(len(text) / 4)) if text else 0


def get_encoder() -> Any | None:
    """The cl100k_base encoder, or `None` if it cannot be loaded. Never raises.

    Resolution is attempted **once** per process. A worker with no network must not retry a
    failing download on every chunk of every document.
    """
    global _encoder, _resolved, _warned
    if _resolved:
        return _encoder
    with _lock:
        if _resolved:  # another thread won the race while we waited
            return _encoder
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding(ENCODING_NAME)
        except Exception as exc:  # ImportError, network failure, cache-dir permissions
            _encoder = None
            if not _warned:
                _warned = True
                log.warning(
                    "tokenizer_unavailable",
                    encoding=ENCODING_NAME,
                    error=str(exc)[:200],
                    impact="token counts fall back to the len/4 heuristic; chunk sizing is approximate",
                    fix="pre-warm TIKTOKEN_CACHE_DIR, or allow the worker to reach the tiktoken CDN",
                )
        finally:
            _resolved = True
    return _encoder


def count_tokens(text: str) -> int:
    """Token count for `text`. Falls back to the character heuristic if tiktoken is unavailable.

    Returns at least 1 for non-empty text: a chunk that reports 0 tokens reads as empty in the
    knowledge UI and would divide badly in any per-token metric.
    """
    if not text:
        return 0
    encoder = get_encoder()
    if encoder is None:
        return _fallback(text)
    try:
        return max(1, len(encoder.encode(text, disallowed_special=())))
    except Exception as exc:  # a pathological input should not fail an ingest over a count
        log.warning("token_count_failed", error=str(exc)[:200])
        return _fallback(text)


def reset_for_tests() -> None:
    """Drop the memoised encoder so a test can exercise both the real and degraded paths."""
    global _encoder, _resolved, _warned
    with _lock:
        _encoder, _resolved, _warned = None, False, False

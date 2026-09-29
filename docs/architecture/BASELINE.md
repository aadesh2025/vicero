# Baseline (before any refactor)

Recorded 2026-09-24 on Windows 11, `master` @ c3b6ec9 with uncommitted edits (see REFACTOR-PLAN blockers).
Anything failing here is **pre-existing**, not a refactor regression.

| Check | Command | Result |
|---|---|---|
| Ruff | `ruff check app tests` (apps/api) | pass |
| Mypy | `mypy app` | pass, 217 files |
| Backend tests | `pytest -q` | **1097 passed, 13 failed, 4 skipped** (19 min) |
| Web typecheck | `npx tsc --noEmit` | pass |
| Web lint | `npx eslint .` | pass (no output) |
| Web unit | `npx vitest run` | 26 files, 187 tests, all pass |
| Migrations | `alembic current` | `0027_workflow_tests (head)` |

## The 13 backend failures: environment, not code

`tests/test_docling_chunking.py` (12) and `tests/test_rag.py::test_estimate_tokens` (1). Cause: the tiktoken
`cl100k_base` encoding cannot be downloaded from this machine (`ConnectionResetError 10054`), so the tokenizer falls back to
the `len/4` heuristic and the tests that assert exact token counts fail. The app logs
`tokenizer_unavailable ... pre-warm TIKTOKEN_CACHE_DIR`. Fix is environmental: pre-warm `TIKTOKEN_CACHE_DIR`.
CI presumably has network access; this was not verified.

## Not run

* Playwright E2E (needs API on :8010 with `LLM_FORCE_FAKE=true` and web on :3001).
* `npm run build`, widget build, Docker image builds, compose validation.
* Red-team corpus and retrieval-eval gates as separate CI steps (they run inside the full `pytest` above, but the
  retrieval keyword-eval script `scripts/eval_retrieval.py` was not run).

## Local environment notes

* Containers up: `vicero-postgres-1` (host port **5750**), `vicero-redis-1`. At the time of the first baseline `.env` said
  5433; the verification pass (see below) changed the gitignored `.env` and `infra/.env` to 5750, so no override is needed now.
* Python 3.14 in `.venv` (CLAUDE.md says 3.11); everything above still passed.

## After the security/architecture work (verification pass, 2026-09-24, HEAD 6a3bfcb)

| Check | Result |
|---|---|
| `ruff check .` (apps/api, whole dir, as CI) | pass |
| `mypy app` | pass, 218 files |
| Backend `pytest -q`, no DB override | 1192 passed, 13 failed, 4 skipped (11m43s). Same 13 tiktoken tests as above; the 4 skips are the opt-in Docling golden files |
| `tests/test_tenant_isolation.py` alone | 69 passed |
| `tests/test_architecture.py` alone | 9 passed; 4 rule classes verified to fail on injected violations, then reverted |
| `test_ssrf` + `test_mcp` + `test_webhooks` + `test_tools` | 50 passed |
| Web `npm run lint` / `tsc --noEmit` / `npm test` | pass / pass / 187 passed |
| Web `npm run build` | pass, lockfile hash unchanged |
| Widget `node build.mjs` | pass (esbuild absent, so unminified); tracked output unchanged |
| Playwright (isolated stack: fake-LLM API :8010, worker on Redis db 5, `next start` :3002) | 66 passed, 7 failed. The same 7 fail identically on the pre-work API code (worktree at f1957d4), so they are pre-existing: `10-sidebar`, `15-canned-responses` x2, `16-macros` x2, `25-dashboard-real-data` (empty-state test), `31-workflow-run-history`. Cause not investigated |

`npm run test:e2e` (the wrapper that sweeps `@example.com` fixtures) was deliberately not used: its cleanup also deletes the
`@vicero.local` seed account. The raw `playwright test` leaves ~53+ fixture orgs in the dev DB; `make clean-devdata` removes them.

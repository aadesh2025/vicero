"""Write a snapshot of the API's OpenAPI schema for the docs site to render.

The docs site renders its API reference from this file rather than from hand-written
prose, because the hand-written one drifted: `docs/04-API-SPEC.md` is currently missing
about ten resource families and still documents a `/v1/billing` router that was never
built. A generated snapshot cannot drift without the check in CI going red.

The schema is taken from the app object directly rather than by curling a running server,
so this works in CI with no Postgres, no Redis and no provider keys — importing
`app.main` builds the routers but never runs the `lifespan` hook that needs them.

Run it with the API's virtualenv, from `apps/api`:

    cd apps/api && ./.venv/Scripts/python.exe ../../scripts/generate-openapi.py

or `make docs-generate` from the repo root, which does both generators.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
OUT_PATH = REPO_ROOT / "apps" / "web" / "content" / "generated" / "openapi.json"


def main() -> int:
    # Importing `app.main` requires `apps/api` on the path. Prepending lets the script be
    # run from anywhere, but it still needs the API venv's interpreter for the deps.
    sys.path.insert(0, str(API_ROOT))
    try:
        from app.main import app
    except ImportError as exc:  # pragma: no cover - operator error, not a code path
        print(
            f"error: could not import the API ({exc}).\n"
            "Run this with the API's virtualenv, e.g.\n"
            "  cd apps/api && ./.venv/Scripts/python.exe ../../scripts/generate-openapi.py",
            file=sys.stderr,
        )
        return 1

    schema = app.openapi()

    # The version string changes on every release and would otherwise make the CI staleness
    # check fail on a bump that changed no endpoint. The docs never show it.
    schema.get("info", {}).pop("version", None)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so the output is byte-stable: FastAPI builds the schema from dicts whose
    # order can shift between runs, and an unstable file makes the CI check meaningless.
    OUT_PATH.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    paths = len(schema.get("paths", {}))
    operations = sum(
        1
        for item in schema.get("paths", {}).values()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    )
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)} — {paths} paths, {operations} operations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# BotForge developer tasks. Run `make help` for the list.
# Two env files: infra/.env (machine-specific host ports) then the root .env (everything else, incl.
# N8N_WEBHOOK_SIGNING_SECRET — its one home; Compose would otherwise interpolate only from infra/.env).
COMPOSE := docker compose --env-file infra/.env --env-file .env -f infra/docker-compose.yml
API := apps/api
WEB := apps/web

.PHONY: help up down logs dev-api dev-web install lint fmt typecheck test test-api test-web test-e2e migrate seed clean-devdata explain-fts eval-retrieval rechunk docs-generate docs-check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Start the full dev stack (postgres, redis, api, worker, web, n8n, ollama)
	$(COMPOSE) up

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Tail stack logs
	$(COMPOSE) logs -f

dev-api: ## Run the API locally with reload
	cd $(API) && uv run uvicorn app.main:app --reload --port 8000

dev-web: ## Run the web app locally
	cd $(WEB) && npm run dev

install: ## Install all dependencies
	cd $(API) && uv sync
	cd $(WEB) && npm install

lint: ## Lint api (ruff) and web (eslint)
	cd $(API) && uv run ruff check .
	cd $(WEB) && npm run lint

fmt: ## Format api (ruff) and web (prettier via eslint)
	cd $(API) && uv run ruff format . && uv run ruff check --fix .

typecheck: ## Typecheck api (mypy) and web (tsc)
	cd $(API) && uv run mypy app
	cd $(WEB) && npx tsc --noEmit

test: test-api test-web ## Run all tests

test-api: ## Run backend tests
	cd $(API) && uv run pytest -q

test-web: ## Run frontend tests (build acts as the check until vitest lands)
	cd $(WEB) && npm run build

migrate: ## Apply database migrations (Phase 1+)
	cd $(API) && uv run alembic upgrade head

seed: ## Seed demo data (Phase 1+)
	cd $(API) && uv run python -m app.db.seed

test-e2e: ## Playwright suite, then sweep the @example.com fixtures it created
	cd apps/web && npm run test:e2e

clean-devdata: ## Remove ephemeral @example.com test users/orgs + the live_demo flag (dev only)
	cd $(API) && uv run python -m app.db.cleanup_devdata

explain-fts: ## EXPLAIN the hybrid-retrieval keyword query — proves it uses the GIN index (P0-1)
	cd $(API) && uv run python ../../scripts/explain_fts.py

eval-retrieval: ## Score retrieval against the frozen eval set — keyword half, no model needed
	cd $(API) && uv run python ../../scripts/eval_retrieval.py --variants fts

eval-retrieval-full: ## Same, plus dense + hybrid. Needs a real embedder (Ollama nomic-embed-text)
	cd $(API) && uv run python ../../scripts/eval_retrieval.py \
		--variants fts,dense,hybrid --embedder-provider ollama

provision: ## Provision a client end-to-end: make provision NAME="Acme Co" EMAIL=owner@acme.com
	node scripts/provision-client.mjs --name "$(NAME)" --email "$(EMAIL)" --plan "$(or $(PLAN),starter)"

audit-kb-pii: ## Scan every knowledge base for contact details and secrets (read-only)
	cd apps/api && ./.venv/Scripts/python.exe ../../scripts/audit_kb_pii.py

rechunk: ## Re-chunk + re-embed from the persisted DoclingDocument — no re-conversion (dry run)
	cd $(API) && uv run python ../../scripts/rechunk_documents.py

test-scripts: ## Unit-test the provisioning script's helpers
	node --test scripts/provision-client.test.mjs scripts/generate-env-reference.test.mjs

docs-generate: ## Regenerate the docs site's OpenAPI + env reference from the code
	cd $(API) && uv run python ../../scripts/generate-openapi.py
	node scripts/generate-env-reference.mjs

docs-check: docs-generate ## Fail if the committed docs reference is stale (CI)
	@git diff --exit-code -- $(WEB)/content/generated \
		|| (echo "\nThe generated docs reference is out of date. Run 'make docs-generate' and commit the result." && exit 1)
